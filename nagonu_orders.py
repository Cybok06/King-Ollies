"""Durable Nagonu submissions shared by wallet and store checkout.

An uncertain POST is never retried: the provider has no documented idempotency key.
"""
import ast
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from bson import ObjectId
from flask import Blueprint, abort, flash, jsonify, redirect, request, session, url_for
from pymongo.errors import DuplicateKeyError
import requests

BASE_URL = 'https://nagonu.site'
DEFAULT_API_KEY = 'NAGONU_X_EsolQUmPFtlw4kg-jzPP1Bdri4zJzP'
SERVICES = {
    'MTN NORMAL': ('MTN', 'bigtime'),
    'MTN EXPRESS': ('MTN', 'regular'),
    'TELECEL': ('TELECEL', 'bigtime'),
}
nagonu_bp = Blueprint('nagonu', __name__)


def status_endpoint(item):
    # The current package catalogue puts MTN NORMAL in bigtime_packages.
    # Resolve by service as well so previously saved orders use the right lookup.
    name = str(item.get('provider_payload', {}).get('service_name') or item.get('serviceName') or '').strip().upper()
    category = SERVICES[name][1] if name in SERVICES else item.get('provider_category')
    return 'response_big_time.php' if category == 'bigtime' else 'response_regular.php'


def status_matches_item(data, item):
    """Provider statuses use either a network or service name, and numeric or unit-labelled sizes."""
    phone = re.sub(r'\D', '', str(data.get('beneficiary') or ''))
    if phone.startswith('233') and len(phone) == 12:
        phone = '0' + phone[3:]
    network = ' '.join(str(data.get('network') or '').upper().split())
    payload = item['provider_payload']
    allowed = {' '.join(str(payload.get(key) or '').upper().split()) for key in ('network', 'service_name')}
    size = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(GB|MB)?\s*', str(data.get('gig') or ''), re.I)
    if not size or phone != item['phone'] or not network or network not in allowed:
        return False
    megabytes = Decimal(size[1]) * (1 if (size[2] or '').upper() == 'MB' else 1000)
    return megabytes == Decimal(str(item['value_obj']['volume']))


@nagonu_bp.app_context_processor
def review_context():
    if session.get('role') != 'admin': return {}
    from applications import csrf_token
    return {'nagonu_csrf': csrf_token()}


@nagonu_bp.route('/admin/nagonu/<order_id>/reconcile', methods=['POST'])
def reconcile(order_id):
    from db import db
    from applications import require_csrf
    if session.get('role') != 'admin': abort(403)
    require_csrf()
    order = db.orders.find_one({'order_id': order_id, 'nagonu_state': {'$in': ['unknown', 'submitting']}})
    if not order: abort(409)
    if order['nagonu_state'] == 'submitting' and order.get('updated_at', datetime.utcnow()) > datetime.utcnow()-timedelta(minutes=2):
        abort(409)
    note = (request.form.get('note') or '').strip()
    if not note or len(note) > 1000 or request.form.get('confirmed') != 'yes': abort(400)
    decision = request.form.get('decision')
    fields = {'nagonu_reviewed_by': str(session.get('user_id')), 'nagonu_review_note': note,
              'updated_at': datetime.utcnow()}
    if decision == 'accepted':
        reference = (request.form.get('reference') or '').strip()
        provider_id = (request.form.get('provider_order_id') or '').strip()
        if not reference or not provider_id or max(len(reference), len(provider_id)) > 150: abort(400)
        if db.orders.find_one({'order_id': {'$ne': order_id}, 'items.provider_reference_id': reference}): abort(409)
        endpoint = status_endpoint(order['items'][0])
        try:
            result = requests.get(BASE_URL+'/api/'+endpoint, params={'reference_id': reference},
                headers={'x-api-key': os.getenv('NAGONU_API_KEY') or DEFAULT_API_KEY}, timeout=(5, 25), allow_redirects=False)
            result.raise_for_status()
            payload = result.json()
            data = payload.get('data') or {}
            expected = order['items'][0]
            if str(payload.get('status')) != '200' or not status_matches_item(data, expected):
                raise ValueError('Provider reference does not match this recipient and bundle.')
        except (requests.RequestException, ValueError, InvalidOperation, KeyError, TypeError, AttributeError):
            flash('Could not verify the matching provider reference. No order or payment changes were made.', 'danger')
            return redirect(url_for('checkout.invoice_view', order_id=order_id))
        fields.update({'nagonu_state': 'accepted', 'items.0.api_status': 'accepted',
            'items.0.provider_reference_id': reference, 'items.0.provider_request_order_id': provider_id,
            'items.0.line_status': 'processing', 'status': 'processing'})
    elif decision == 'rejected':
        fields.update({'nagonu_state': 'rejected', 'items.0.api_status': 'rejected',
                       'items.0.line_status': 'failed', 'status': 'failed'})
    else: abort(400)
    result = db.orders.update_one({'_id': order['_id'], 'nagonu_state': {'$in': ['unknown', 'submitting']}}, {'$set': fields})
    if not result.modified_count: abort(409)
    finish_accounting(db, order_id)
    flash('Provider review saved. Wallet refunds and store profit were updated where applicable.', 'success')
    return redirect(url_for('checkout.invoice_view', order_id=order_id))


def money(value):
    try: amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError): raise ValueError('Invalid package price.')
    if not amount.is_finite() or amount <= 0: raise ValueError('Package price must be positive.')
    return float(amount.quantize(Decimal('0.01')))


def value_object(value):
    if isinstance(value, dict): return value
    if isinstance(value, str):
        try: result = json.loads(value)
        except (ValueError, TypeError):
            try: result = ast.literal_eval(value)
            except (ValueError, SyntaxError): return {}
        return result if isinstance(result, dict) else {}
    return {}


def prepare_cart(db, cart, user_id=None, store=None, store_price=None, store_base=None):
    """Resolve provider IDs and price only from saved service offers."""
    if not isinstance(cart, list) or not 1 <= len(cart) <= 50:
        raise ValueError('Choose between 1 and 50 packages per checkout.')
    user = (db.users.find_one({'_id': user_id}) or {}) if user_id else {}
    stage = str(user.get('customer_stage') or 'normal_agent').strip().lower()
    aliases = {'normal': 'normal_agent', 'normal agent': 'normal_agent', 'elite': 'elite_agent',
               'elite agent': 'elite_agent', 'premium_agent': 'premium'}
    stage = aliases.get(stage, stage)
    if stage not in ('normal_agent', 'elite_agent', 'premium'): stage = 'normal_agent'
    result, seen = [], set()
    for item in cart:
        if not isinstance(item, dict): raise ValueError('Invalid cart item.')
        try: sid = ObjectId(item.get('serviceId') or item.get('service_id'))
        except Exception: raise ValueError('Select a valid service.')
        if store and store.get('service_scope') == 'selected' and str(sid) not in store.get('service_ids', []):
            raise ValueError('This service is not offered by this store.')
        svc = db.services.find_one({'_id': sid})
        name = str((svc or {}).get('name') or '').strip().upper()
        if not svc or name not in SERVICES or svc.get('purchase_mode') in ('form', 'checker'):
            raise ValueError('This service is not enabled for Nagonu data checkout.')
        if svc.get('status', 'OPEN') != 'OPEN' or svc.get('availability', 'AVAILABLE') != 'AVAILABLE':
            raise ValueError(f'{name} is currently unavailable.')
        selected = value_object(item.get('value_obj') or item.get('valueObj') or item.get('value'))
        selected_id = selected.get('id') or item.get('offer_id')
        offers = (svc.get('store_offers') or svc.get('offers') or []) if store else svc.get('offers', [])
        matches = [offer for offer in offers if str(value_object(offer.get('value')).get('id')) == str(selected_id)]
        if len(matches) != 1 or selected_id is None:
            raise ValueError(f'Select a valid saved offer for {name}.')
        offer = matches[0]
        canonical = value_object(offer.get('value'))
        if selected.get('volume') is not None and str(selected['volume']) != str(canonical.get('volume')):
            raise ValueError('The selected package changed. Refresh the available packages.')
        try: offer_id = int(canonical['id'])
        except (ValueError, TypeError, KeyError): raise ValueError('The offer has no valid provider ID.')
        phone = re.sub(r'\D', '', str(item.get('phone') or ''))
        if phone.startswith('233') and len(phone) == 12: phone = '0' + phone[3:]
        if not re.fullmatch(r'0\d{9}', phone): raise ValueError('Enter a valid Ghana recipient number.')
        key = (str(sid), offer_id, phone)
        if key in seen: raise ValueError('Remove duplicate packages for the same recipient from your cart.')
        seen.add(key)
        if store:
            amount = money(store_price(store, svc, canonical, offer['value']))
            base = money(store_base(store, svc, canonical, offer['value']))
        else:
            prices = {aliases.get(str(k).lower(), str(k).lower()): v for k, v in (offer.get('stage_prices') or {}).items()}
            price = prices.get(stage)
            if price is None: price = offer.get('customer_price')
            if price is None: price = offer.get('amount')
            amount, base = money(price), money(offer.get('amount'))
            if money(item.get('amount')) != amount:
                raise ValueError('Package pricing changed. Refresh your dashboard before paying.')
        network, category = SERVICES[name]
        system_offer = next((saved for saved in svc.get('offers', [])
            if str(value_object(saved.get('value')).get('id')) == str(offer_id)), offer)
        system_profit = max(0, round(base-money(system_offer.get('amount')), 2)) if store else max(0, round(amount-base, 2))
        result.append({'phone': phone, 'serviceId': str(sid), 'serviceName': svc['name'],
            'value': offer['value'], 'value_obj': canonical, 'amount': amount, 'base_amount': base,
            'profit_amount': system_profit, 'provider': 'nagonu', 'line_status': 'pending',
            'api_status': 'queued', 'provider_category': category,
            'provider_payload': {'recipient_number': phone, 'service_name': name, 'network': network, 'offer_id': offer_id},
            'ported_confirmed': bool(item.get('ported_confirmed')),
            'store_profit_amount': max(0, round(amount-base, 2)) if store else 0})
    return result


def batch_key(channel, owner, reference):
    if not isinstance(reference, str) or not reference.strip() or len(reference) > 200:
        raise ValueError('Missing checkout reference. Refresh and try again.')
    # Paystack references are unique across stores, not merely within a store.
    scope = str(owner) if channel == 'wallet' else 'paystack'
    return hashlib.sha256(f'{channel}:{scope}:{reference}'.encode()).hexdigest()


def reserve(db, lines, owner, key, reference, store=None):
    """Persist all lines and reserve wallet funds in one transaction, before HTTP."""
    total = round(sum(line['amount'] for line in lines), 2)
    fingerprint = hashlib.sha256(json.dumps([(x['provider_payload'], x['amount']) for x in lines], sort_keys=True).encode()).hexdigest()
    def operation(tx):
        existing = db.nagonu_batches.find_one({'_id': key}, session=tx)
        if existing:
            if existing['fingerprint'] != fingerprint or existing.get('store_slug') != (store or {}).get('slug'):
                raise ValueError('This payment reference belongs to a different checkout.')
            return existing
        now = datetime.utcnow()
        # A short lock also stops a new client reference from duplicating an uncertain order.
        for line in lines:
            lock = hashlib.sha256(json.dumps(line['provider_payload'], sort_keys=True).encode()).hexdigest()
            previous = db.nagonu_recipient_locks.find_one({'_id': lock}, session=tx)
            if previous and previous.get('expires_at', now) > now:
                prior_orders = list(db.orders.find({
                    'nagonu_batch_key': previous['batch_id'],
                    'items.provider_payload': line['provider_payload'],
                }, session=tx))
                delivered = prior_orders and all(
                    str(order.get('status') or '').strip().lower() in ('delivered', 'completed')
                    or all(str(item.get('line_status') or '').strip().lower() in ('delivered', 'completed')
                           for item in order['items'])
                    for order in prior_orders
                )
                if not delivered:
                    raise ValueError('This recipient/package already has a recent order. Check My Orders before submitting again.')
            db.nagonu_recipient_locks.replace_one({'_id': lock}, {'_id': lock, 'batch_id': key,
                'expires_at': datetime.max}, upsert=True, session=tx)
        if not store:
            debit = db.balances.update_one({'user_id': owner, 'amount': {'$gte': total}},
                {'$inc': {'amount': -total}, '$set': {'updated_at': now}}, session=tx)
            if debit.modified_count != 1: raise ValueError('Insufficient wallet balance.')
        ids = []
        for i, line in enumerate(lines, 1):
            order_id = f'KING{key[:16].upper()}{i:03d}'
            ids.append(order_id)
            db.orders.insert_one({'order_id': order_id, 'batch_id': 'NAG'+key, 'batch_position': i,
                'batch_size': len(lines), 'user_id': owner, 'items': [{**line, 'order_id': order_id}],
                'total_amount': line['amount'], 'charged_amount': line['amount'],
                'profit_amount_total': line['profit_amount'],
                'status': 'pending', 'nagonu_state': 'queued', 'nagonu_batch_key': key,
                'paid_from': ('admin_complaint' if store.get('_nagonu_admin_override') else 'paystack_inline') if store else 'wallet', 'store_slug': (store or {}).get('slug'),
                'paystack_reference': reference if store else None, 'client_request_id': reference if not store else None,
                'created_at': now, 'updated_at': now}, session=tx)
        record = {'_id': key, 'order_ids': ids, 'fingerprint': fingerprint, 'user_id': owner,
                  'store_slug': (store or {}).get('slug'), 'created_at': now}
        db.nagonu_batches.insert_one(record, session=tx)
        if not store:
            db.transactions.insert_one({'_id': 'NAG'+key, 'user_id': owner, 'amount': total,
                'reference': 'NAG'+key, 'status': 'success', 'type': 'purchase', 'gateway': 'Wallet',
                'currency': 'GHS', 'created_at': now, 'verified_at': now}, session=tx)
        return record
    try:
        with db.client.start_session() as tx: return tx.with_transaction(operation)
    except DuplicateKeyError:
        existing = db.nagonu_batches.find_one({'_id': key})
        if existing and existing['fingerprint'] == fingerprint and existing.get('store_slug') == (store or {}).get('slug'):
            return existing
        raise ValueError('A matching checkout is already being processed. Check your orders.')


def dispatch_one(db, order_id):
    order = db.orders.find_one_and_update({'order_id': order_id, 'nagonu_state': 'queued'},
        {'$set': {'nagonu_state': 'submitting', 'items.0.api_status': 'submitting', 'updated_at': datetime.utcnow()}})
    if not order: return
    state, details = 'unknown', {'message': 'Provider response is uncertain. Admin reconciliation required; do not resubmit.'}
    try:
        response = requests.post(BASE_URL + '/api/send_order.php',
            headers={'x-api-key': os.getenv('NAGONU_API_KEY') or DEFAULT_API_KEY,
                     'Content-Type': 'application/json'},
            json=order['items'][0]['provider_payload'], timeout=(5, 60), allow_redirects=False)
        details.update({'http_status': response.status_code, 'reason': 'unexpected_response'})
        payload = response.json()
        if isinstance(payload, dict):
            code = str(payload.get('status'))
            if re.fullmatch(r'\d{3}|success|error', code):
                details['status'] = code
            ref, provider_id = payload.get('reference_id'), payload.get('order_id')
            if response.status_code == 200 and code == '200' and isinstance(ref, str) and ref and isinstance(provider_id, str) and provider_id:
                state = 'accepted'
                details = {'reference_id': ref, 'order_id': provider_id, 'message': 'Order accepted by Nagonu.'}
            elif code in ('100', '101', '102', '103', '555') and not ref and not provider_id:
                state = 'rejected'
                details = {'status': code, 'message': 'Nagonu rejected this order.'}
            else:
                details['reason'] = ('missing_order_identifiers' if code == '200' else
                                     'provider_error' if code == '500' else 'unexpected_response')
        else:
            details['reason'] = 'invalid_json_structure'
    except requests.Timeout:
        details['reason'] = 'timeout'
        details['message'] = 'Nagonu did not respond within the request timeout. Acceptance must be verified before retrying.'
    except requests.RequestException:
        details['reason'] = 'connection_error'
    except ValueError:
        details['reason'] = 'invalid_json'
    # No retry on timeout, malformed response, HTTP error, or provider 500.
    fields = {'nagonu_state': state, 'items.0.api_status': state, 'items.0.api_response': details,
              'items.0.line_status': 'failed' if state == 'rejected' else 'processing',
              'status': 'failed' if state == 'rejected' else 'processing', 'updated_at': datetime.utcnow()}
    if state == 'accepted':
        fields.update({'items.0.provider_reference_id': details['reference_id'],
                       'items.0.provider_request_order_id': details['order_id']})
    db.orders.update_one({'_id': order['_id'], 'nagonu_state': 'submitting'}, {'$set': fields})


def finish_accounting(db, order_id):
    def operation(tx):
        order = db.orders.find_one({'order_id': order_id}, session=tx)
        if not order or order.get('nagonu_accounted'): return
        state = order.get('nagonu_state')
        if state not in ('accepted', 'rejected'): return
        now = datetime.utcnow()
        if state == 'rejected':
            db.orders.update_one({'_id': order['_id']}, {'$set': {'profit_amount_total': 0,
                'items.0.profit_amount': 0, 'items.0.store_profit_amount': 0}}, session=tx)
        if state == 'rejected' and order['paid_from'] == 'wallet':
            db.balances.update_one({'user_id': order['user_id']}, {'$inc': {'amount': order['charged_amount']}}, session=tx)
            db.transactions.insert_one({'_id': 'REFUND-'+order_id, 'user_id': order['user_id'],
                'amount': order['charged_amount'], 'reference': 'REFUND-'+order_id, 'type': 'refund',
                'status': 'success', 'gateway': 'Wallet', 'created_at': now}, session=tx)
            db.orders.update_one({'_id': order['_id']}, {'$set': {'charged_amount': 0,
                'status': 'refunded', 'items.0.line_status': 'refunded', 'profit_amount_total': 0}}, session=tx)
        elif state == 'accepted' and order.get('store_slug') and order['paid_from'] == 'paystack_inline':
            profit = order['items'][0].get('store_profit_amount', 0)
            db.store_accounts.update_one({'store_slug': order['store_slug']}, {'$inc': {'total_profit_balance': profit},
                '$set': {'updated_at': now}, '$setOnInsert': {'created_at': now}}, upsert=True, session=tx)
        db.orders.update_one({'_id': order['_id']}, {'$set': {'nagonu_accounted': True,
            'refund_review_required': state == 'rejected' and order['paid_from'] != 'wallet'}}, session=tx)
        lock = hashlib.sha256(json.dumps(order['items'][0]['provider_payload'], sort_keys=True).encode()).hexdigest()
        db.nagonu_recipient_locks.update_one({'_id': lock, 'batch_id': order['nagonu_batch_key']},
            {'$set': {'expires_at': now + timedelta(minutes=30) if state == 'accepted' else now}}, session=tx)
    with db.client.start_session() as tx: tx.with_transaction(operation)


def checkout_response(db, batch):
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda oid: dispatch_one(db, oid), batch['order_ids']))
    for oid in batch['order_ids']: finish_accounting(db, oid)
    orders = list(db.orders.find({'order_id': {'$in': batch['order_ids']}}).sort('batch_position', 1))
    success = all(order.get('nagonu_state') == 'accepted' for order in orders)
    rejected_count = sum(order.get('nagonu_state') == 'rejected' for order in orders)
    message = ('Order placed successfully.' if success else
        'An order could not be completed. View your receipt or contact support.' if rejected_count else
        'Order submitted. Confirmation is pending; view your receipt for updates.')
    return jsonify(success=success, order_recorded=True, message=message,
        order_id=batch['order_ids'][0], order_ids=batch['order_ids'], batch_id=orders[0]['batch_id'],
        redirect_url='/invoice-batch/'+orders[0]['batch_id'], status='processing' if success else 'needs_review',
        rejected_count=rejected_count,
        charged_amount=round(sum(order['charged_amount'] for order in orders), 2),
        items=[item for order in orders for item in order['items']]), 200 if success else 202


def wallet_checkout(db, user_id, data):
    try:
        reference = data.get('client_request_id')
        key = batch_key('wallet', user_id, reference)
        existing = db.nagonu_batches.find_one({'_id': key})
        if existing: return checkout_response(db, existing)
        lines = prepare_cart(db, data.get('cart'), user_id=user_id)
        batch = reserve(db, lines, user_id, key, reference)
        return checkout_response(db, batch)
    except ValueError as exc:
        return jsonify(success=False, message=str(exc)), 400

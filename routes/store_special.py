"""Store AFA and checker checkout using server-priced Paystack payment intents."""
from datetime import datetime
import re
import secrets
import hashlib
from urllib.parse import urlparse

from bson import ObjectId
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
import requests

from db import db
from store_catalog import AFA_ID, OLLINET_LOGO, special_offers, special_price
from applications import csrf_token, require_csrf

store_special_bp = Blueprint('store_special', __name__)


def selection(slug, service_id):
    store = db.stores.find_one({'slug': slug, 'status': 'published'})
    if not store or service_id not in store.get('service_ids', []):
        abort(404)
    if service_id == AFA_ID:
        abort(404)
    else:
        if not ObjectId.is_valid(service_id): abort(404)
        svc = db.services.find_one({'_id': ObjectId(service_id), 'purchase_mode': 'checker'})
    if not svc: abort(404)
    if svc.get('status', 'OPEN') != 'OPEN' or svc.get('availability', 'AVAILABLE') != 'AVAILABLE':
        abort(409, 'This service is currently unavailable.')
    offers = []
    for offer in special_offers(db, svc):
        total = special_price(store, service_id, offer)
        if total is not None and total > 0:
            offer['total'] = total
            offer['available'] = svc['purchase_mode'] == 'afa' or bool(db.wassce_checker.find_one(
                {'type_id': offer['value'], 'status': 'not_sold'}, {'_id': 1}))
            offers.append(offer)
    return store, svc, offers


def fulfill(reference):
    """Credit profit and deliver stock/registration once in one Mongo transaction."""
    def operation(tx):
        intent = db.store_service_payments.find_one({'_id': reference}, session=tx)
        if not intent or intent.get('status') in ('fulfilled', 'review'): return
        if intent.get('status') != 'verified': raise ValueError('Payment has not been verified.')
        now = datetime.utcnow()
        item = None
        if intent['mode'] == 'checker':
            item = db.wassce_checker.find_one_and_update(
                {'type_id': intent['offer_id'], 'status': 'not_sold'},
                {'$set': {'status': 'sold', 'sold_to': intent.get('user_id') or reference,
                          'sold_at': now, 'store_slug': intent['store_slug']}}, session=tx)
        review = intent['mode'] == 'checker' and not item
        state = 'review' if review else 'fulfilled'
        db.store_service_payments.update_one({'_id': reference, 'status': 'verified'},
            {'$set': {'status': state, 'updated_at': now}}, session=tx)
        profit = round(intent['amount'] - intent['base'], 2) if not review else 0
        order_id = reference
        record_id = ObjectId(hashlib.sha256(reference.encode()).hexdigest()[:24])
        order_status = 'processing' if intent['mode'] == 'afa' else ('pending' if review else 'delivered')
        db.orders.insert_one({'_id': record_id, 'order_id': order_id, 'store_slug': intent['store_slug'],
            'owner_id': intent['owner_id'], 'user_id': intent.get('user_id'), 'status': order_status,
            'paystack_reference': reference, 'paid_from': 'paystack', 'charged_amount': intent['amount'], 'total_amount': intent['amount'],
            'profit_amount_total': profit, 'created_at': now, 'updated_at': now,
            'refund_review_required': review,
            'admin_note': 'Paid checker is out of stock. Supply or refund this payment.' if review else '',
            'items': [{'serviceName': intent['service_name'], 'service_id': intent['service_id'],
                       'value': intent['offer_name'], 'phone': intent['details']['phone'],
                       'amount': intent['amount'], 'base_amount': intent['base'],
                       'store_profit_amount': profit, 'line_status': order_status}]}, session=tx)
        db.transactions.insert_one({'_id': reference, 'reference': reference,
            'user_id': intent.get('user_id'), 'store_slug': intent['store_slug'], 'amount': intent['amount'],
            'status': 'success', 'type': 'purchase', 'gateway': 'Paystack', 'currency': 'GHS',
            'created_at': now, 'verified_at': now}, session=tx)
        if item:
            db.purchase_history.insert_one({'_id': reference, 'user_id': intent.get('user_id'),
                'store_slug': intent['store_slug'], 'checker_id': str(item['_id']),
                'service_id': ObjectId(intent['service_id']), 'type_id': intent['offer_id'],
                'type': intent['offer_name'], 'amount': intent['amount'], 'message': item['message'],
                'purchased_at': now}, session=tx)
        elif intent['mode'] == 'afa':
            db.afa_registrations.insert_one({'_id': record_id, **intent['details'],
                'customer_id': ObjectId(intent['user_id']) if intent.get('user_id') else intent['owner_id'],
                'store_slug': intent['store_slug'], 'status': 'pending', 'charged': True,
                'amount': intent['amount'], 'charged_amount': intent['amount'], 'charged_at': now,
                'charged_by': 'Paystack', 'created_at': now, 'updated_at': now}, session=tx)
        if not review:
            db.store_accounts.update_one({'store_slug': intent['store_slug']},
                {'$inc': {'total_profit_balance': profit}, '$set': {'updated_at': now},
                 '$setOnInsert': {'owner_id': intent['owner_id'], 'created_at': now}}, upsert=True, session=tx)
    with db.client.start_session() as tx:
        tx.with_transaction(operation)


@store_special_bp.route('/s/<slug>/service/<service_id>', methods=['GET', 'POST'])
def purchase(slug, service_id):
    store, svc, offers = selection(slug, service_id)
    if request.method == 'POST':
        require_csrf()
        try:
            offer = next((o for o in offers if o['value'] == request.form.get('offer_id') and o['available']), None)
            if not offer: raise ValueError('Choose an available type.')
            if abs(float(request.form.get('expected_amount', '-1')) - offer['total']) > .001:
                raise ValueError('The price changed. Review the updated price before paying.')
            email = request.form.get('email', '').strip()
            phone = request.form.get('phone', '').strip()
            name = request.form.get('name', '').strip()
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email) > 254:
                raise ValueError('Enter your email address for payment.')
            if not re.fullmatch(r'0\d{9}', phone): raise ValueError('Enter a valid 10-digit phone number.')
            if svc['purchase_mode'] == 'afa' and (not name or len(name) > 120):
                raise ValueError('Enter your full name (up to 120 characters).')
            token = request.form.get('purchase_token', '')
            if not re.fullmatch(r'[a-f0-9]{32}', token) or token not in session.get('store_purchase_tokens', []):
                raise ValueError('Refresh this page before paying.')
            reference = 'OLL-' + token
            intent = {'_id': reference, 'store_slug': slug, 'service_id': service_id, 'owner_id': store['owner_id'],
                'mode': svc['purchase_mode'], 'service_name': svc['name'], 'offer_id': offer['value'],
                'offer_name': offer['value_text'], 'base': offer['amount'], 'amount': offer['total'],
                'email': email, 'details': {'name': name, 'phone': phone},
                'user_id': session.get('user_id') if session.get('role') == 'customer' else None,
                'status': 'pending', 'created_at': datetime.utcnow()}
            db.store_service_payments.update_one({'_id': reference}, {'$setOnInsert': intent}, upsert=True)
            intent = db.store_service_payments.find_one({'_id': reference})
            # Repeated submissions always resume the original immutable purchase.
            if intent.get('status') in ('verified', 'fulfilled', 'review'):
                return redirect(url_for('store_special.receipt', reference=reference))
            if intent.get('authorization_url'): return redirect(intent['authorization_url'])
            from paystack_config import secret_key
            response = requests.post('https://api.paystack.co/transaction/initialize',
                headers={'Authorization': 'Bearer ' + secret_key()}, timeout=(5, 25), json={
                    'reference': reference, 'email': intent['email'], 'currency': 'GHS',
                    'amount': int(round(intent['amount'] * 100)),
                    'callback_url': url_for('store_special.receipt', reference=reference, _external=True),
                    'metadata': {'source': 'store_service', 'reference': reference}})
            payload = response.json()
            destination = (payload.get('data') or {}).get('authorization_url', '')
            parsed = urlparse(destination)
            if response.status_code != 200 or not payload.get('status') or parsed.scheme != 'https' or parsed.hostname != 'checkout.paystack.com':
                raise ValueError('Payment could not be started. Retry this purchase; do not create another payment.')
            db.store_service_payments.update_one({'_id': reference}, {'$set': {'authorization_url': destination}})
            return redirect(destination)
        except (ValueError, TypeError):
            flash('Unable to start payment. Check your details, availability and price, then retry. If already paid, use your receipt.', 'danger')
        except requests.RequestException:
            flash('Payment could not be confirmed. Retry with the same details; do not start another purchase if already paid.', 'danger')
    token = request.form.get('purchase_token')
    if token not in session.get('store_purchase_tokens', []):
        token = secrets.token_hex(16)
    session['store_purchase_tokens'] = list(dict.fromkeys(session.get('store_purchase_tokens', []) + [token]))[-30:]
    return render_template('store_service_checkout.html', store=store, service=svc, offers=offers,
                           token=token, csrf=csrf_token(), logo=OLLINET_LOGO)


@store_special_bp.route('/store-service/receipt/<reference>')
def receipt(reference):
    if session.get('role') != 'admin' and reference.removeprefix('OLL-') not in session.get('store_purchase_tokens', []):
        abort(403)
    intent = db.store_service_payments.find_one({'_id': reference})
    if not intent: abort(404)
    if intent['status'] == 'pending':
        from routes.store_page import _verify_paystack
        try:
            ok, data, _, _ = _verify_paystack(reference)
            meta = data.get('metadata') or {}
            if (ok and data.get('reference') == reference and data.get('currency') == 'GHS'
                    and data.get('amount') == int(round(intent['amount'] * 100))
                    and isinstance(meta, dict) and meta.get('source') == 'store_service'
                    and meta.get('reference') == reference):
                db.store_service_payments.update_one({'_id': reference, 'status': 'pending'}, {'$set': {'status': 'verified'}})
        except (requests.RequestException, ValueError, TypeError):
            pass
    if db.store_service_payments.find_one({'_id': reference})['status'] == 'verified':
        try: fulfill(reference)
        except Exception:
            flash('Payment received. Processing needs another check. Refresh this receipt; do not pay again.', 'warning')
    intent = db.store_service_payments.find_one({'_id': reference})
    delivery = db.purchase_history.find_one({'_id': reference}) or {}
    response = render_template('store_service_receipt.html', purchase=intent, delivery=delivery, logo=OLLINET_LOGO)
    return response, 200, {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}

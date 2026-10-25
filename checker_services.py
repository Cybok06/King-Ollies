"""Typed, stock-backed digital products with private delivery after wallet payment."""
from datetime import datetime
import hashlib
import secrets
from bson import ObjectId
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from db import db
from form_services import parse_price
from applications import csrf_token, require_csrf

checker_services_bp = Blueprint('checker_services', __name__)
SLUG = 'checkers-and-forms'


def service():
    result = db.services.find_one({'slug': SLUG, 'purchase_mode': 'checker'})
    if not result: abort(404)
    return result


def oid(value):
    try: return ObjectId(value)
    except Exception: raise ValueError('Invalid selection. Refresh and try again.')


def text_field(name, limit):
    value = (request.form.get(name) or '').strip()
    if not value or len(value) > limit:
        raise ValueError(f'{name.replace("_", " ").title()} is required (up to {limit:,} characters).')
    return value


@checker_services_bp.route('/admin/services/checkers-and-forms', methods=['GET', 'POST'])
def manage():
    if session.get('role') != 'admin': abort(403)
    svc = service()
    if request.method == 'POST':
        require_csrf()
        try:
            action = request.form.get('action')
            if action in ('add_type', 'edit_type'):
                name = text_field('name', 100)
                price = parse_price(request.form.get('price'))
                data = {'name': name, 'price': price, 'enabled': request.form.get('enabled') == 'on',
                        'updated_at': datetime.utcnow()}
                if action == 'add_type':
                    key = hashlib.sha256((str(svc['_id']) + name.casefold()).encode()).hexdigest()
                    if db.checker_types.find_one({'_id': key}):
                        raise ValueError('That type already exists. Edit it below.')
                    db.checker_types.insert_one({'_id': key, 'service_id': svc['_id'], 'version': 1, **data})
                else:
                    result = db.checker_types.update_one(
                        {'_id': request.form.get('type_id'), 'service_id': svc['_id'],
                         'version': int(request.form.get('version', '-1'))},
                        {'$set': data, '$inc': {'version': 1}})
                    if not result.modified_count: raise ValueError('This type changed. Refresh before editing.')
            elif action in ('add_stock', 'edit_stock', 'delete_stock'):
                kind = db.checker_types.find_one({'_id': request.form.get('type_id'), 'service_id': svc['_id']})
                if not kind: raise ValueError('Choose an existing type.')
                if action == 'add_stock':
                    message = text_field('message', 10000)
                    token = text_field('stock_token', 64)
                    key = hashlib.sha256((str(svc['_id']) + token).encode()).hexdigest()
                    db.wassce_checker.update_one({'_id': key}, {'$setOnInsert': {
                        'type_id': kind['_id'], 'type': kind['name'], 'status': 'not_sold',
                        'message': message, 'created_at': datetime.utcnow()}}, upsert=True)
                else:
                    raw_id = request.form.get('stock_id', '')
                    stock_id = ObjectId(raw_id) if ObjectId.is_valid(raw_id) else raw_id
                    query = {'_id': stock_id, 'type_id': kind['_id'], 'status': 'not_sold'}
                    if action == 'edit_stock':
                        result = db.wassce_checker.update_one(query, {'$set': {'message': text_field('message', 10000)}})
                        if not result.matched_count: raise ValueError('This item is no longer available to edit.')
                    else:
                        if not db.wassce_checker.delete_one(query).deleted_count:
                            raise ValueError('This item is no longer available to remove.')
            else: raise ValueError('Unknown action.')
            flash('Checkers and Forms updated.', 'success')
        except (ValueError, TypeError) as exc:
            flash(str(exc), 'danger')
        return redirect(url_for('checker_services.manage'))
    kinds = list(db.checker_types.find({'service_id': svc['_id']}).sort('name', 1))
    for kind in kinds:
        kind['stock'] = list(db.wassce_checker.find({'type_id': kind['_id'], 'status': 'not_sold'}))
        kind['sold'] = db.wassce_checker.count_documents({'type_id': kind['_id'], 'status': 'sold'})
        kind['stock_token'] = secrets.token_hex(16)
    return render_template('checker_service.html', service=svc, kinds=kinds, is_admin=True, csrf=csrf_token())


def buy(svc_id, user_id, type_id, version, token):
    if len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('Invalid purchase request. Refresh this page.')
    purchase_id = hashlib.sha256((str(user_id) + token).encode()).hexdigest()
    def operation(tx):
        if db.purchase_history.find_one({'_id': purchase_id}, session=tx): return
        svc = db.services.find_one({'_id': svc_id, 'purchase_mode': 'checker', 'status': 'OPEN',
                                    'availability': 'AVAILABLE'}, session=tx)
        if not svc: raise ValueError('This service is currently unavailable.')
        kind = db.checker_types.find_one({'_id': type_id, 'service_id': svc_id, 'enabled': True, 'version': version}, session=tx)
        if not kind: raise ValueError('The price or type changed. Refresh and choose again.')
        price = parse_price(kind['price'])
        item = db.wassce_checker.find_one_and_update({'type_id': type_id, 'status': 'not_sold'},
            {'$set': {'status': 'sold', 'sold_to': str(user_id), 'sold_at': datetime.utcnow()}}, session=tx)
        if not item: raise ValueError('This type is sold out. Please choose another type.')
        debit = db.balances.update_one({'user_id': user_id, 'amount': {'$gte': price}},
            {'$inc': {'amount': -price}, '$set': {'updated_at': datetime.utcnow()}}, session=tx)
        if debit.modified_count != 1: raise ValueError('Insufficient wallet balance. Add funds and try again.')
        # Writes conflict with concurrent price/availability changes and force a transaction retry.
        db.checker_types.update_one({'_id': type_id}, {'$inc': {'purchase_count': 1}}, session=tx)
        db.services.update_one({'_id': svc_id}, {'$inc': {'purchase_count': 1}}, session=tx)
        now = datetime.utcnow()
        db.purchase_history.insert_one({'_id': purchase_id, 'user_id': str(user_id), 'checker_id': str(item['_id']),
            'service_id': svc_id, 'type_id': type_id, 'type': kind['name'], 'amount': price,
            'message': item['message'], 'purchased_at': now}, session=tx)
        db.transactions.insert_one({'_id': purchase_id, 'user_id': user_id, 'amount': price,
            'reference': 'CHK-' + purchase_id, 'status': 'success', 'type': 'purchase', 'gateway': 'Wallet',
            'currency': 'GHS', 'created_at': now, 'verified_at': now}, session=tx)
    with db.client.start_session() as tx: tx.with_transaction(operation)
    return purchase_id


@checker_services_bp.route('/services/checkers-and-forms', methods=['GET', 'POST'])
def purchase():
    if not session.get('user_id'): return redirect(url_for('login.login'))
    if session.get('role') != 'customer': abort(403)
    svc = service()
    if request.method == 'POST':
        require_csrf()
        try:
            purchase_id = buy(svc['_id'], oid(session['user_id']), request.form.get('type_id'),
                int(request.form.get('version', '-1')), request.form.get('purchase_token', ''))
            session['new_checker_purchase'] = purchase_id
            flash('Purchase successful. Your delivery message is in My Purchases.', 'success')
            return redirect(url_for('purchases.view_purchases'))
        except (ValueError, TypeError) as exc: flash(str(exc), 'danger')
        except Exception: flash('Purchase could not be confirmed. Check My Purchases before trying again.', 'danger')
        return redirect(url_for('checker_services.purchase'))
    kinds = list(db.checker_types.find({'service_id': svc['_id'], 'enabled': True}).sort('name', 1))
    for kind in kinds:
        kind['stock_count'] = db.wassce_checker.count_documents({'type_id': kind['_id'], 'status': 'not_sold'})
    balance = db.balances.find_one({'user_id': oid(session['user_id'])}) or {}
    return render_template('checker_service.html', service=svc, kinds=kinds, is_admin=False, csrf=csrf_token(),
                           balance=balance.get('amount', 0), purchase_token=secrets.token_hex(16))

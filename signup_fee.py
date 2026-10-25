"""One-time registration payment, enforced before customer routes run."""
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlparse
import requests
from bson import ObjectId
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for
from db import db
from applications import csrf_token, require_csrf, secret_key

signup_fee_bp = Blueprint('signup_fee', __name__)


def current_user():
    if session.get('role') != 'customer' or not ObjectId.is_valid(session.get('user_id', '')):
        abort(403)
    user = db.users.find_one({'_id': ObjectId(session['user_id'])})
    if not user: abort(403)
    return user


def outstanding(user):
    return user.get('signup_fee_status') == 'unpaid' and float(user.get('signup_fee_amount', 0)) > 0


@signup_fee_bp.before_app_request
def enforce_fee():
    if session.get('role') != 'customer': return
    if request.endpoint in ('static', 'login.logout', 'signup_fee.pay', 'signup_fee.verify'): return
    user = current_user()
    if not outstanding(user): return
    if request.endpoint == 'customer_dashboard.customer_dashboard' and request.method == 'GET':
        # Render the blocking dashboard dialog without running dashboard actions.
        return render_template('signup_fee.html', amount=user['signup_fee_amount'], csrf=csrf_token())
    if request.is_json or request.path.startswith('/api/'):
        return jsonify(success=False, message='Pay the One Time Sign Up fee to continue.',
                       redirect_url=url_for('customer_dashboard.customer_dashboard')), 402
    return redirect(url_for('customer_dashboard.customer_dashboard'))


@signup_fee_bp.route('/signup-fee/pay', methods=['POST'])
def pay():
    user = current_user()
    require_csrf()
    if not outstanding(user): return redirect(url_for('customer_dashboard.customer_dashboard'))
    reference = 'SIGNUP-' + str(user['_id'])
    if user.get('signup_fee_url'): return redirect(user['signup_fee_url'])
    try:
        response = requests.post('https://api.paystack.co/transaction/initialize',
            headers={'Authorization': 'Bearer ' + secret_key()}, timeout=(5,25), json={
                'email': user['email'], 'amount': int(Decimal(str(user['signup_fee_amount']))*100),
                'currency': 'GHS', 'reference': reference,
                'callback_url': url_for('signup_fee.verify', _external=True),
                'metadata': {'source': 'signup_fee', 'user_id': str(user['_id'])}})
        response.raise_for_status()
        result = response.json()
        destination = (result.get('data') or {}).get('authorization_url', '')
        parsed = urlparse(destination)
        if not result.get('status') or parsed.scheme != 'https' or parsed.hostname != 'checkout.paystack.com':
            raise ValueError('Invalid payment response')
        db.users.update_one({'_id': user['_id'], 'signup_fee_status': 'unpaid'},
                            {'$set': {'signup_fee_url': destination}})
        return redirect(destination)
    except (requests.RequestException, ValueError, TypeError):
        flash('Could not start payment. If you already paid, use Check payment status. Otherwise retry.', 'warning')
        return redirect(url_for('customer_dashboard.customer_dashboard'))


def settle(user, data):
    reference = 'SIGNUP-' + str(user['_id'])
    metadata = data.get('metadata') or {}
    if (data.get('status') != 'success' or data.get('reference') != reference or data.get('currency') != 'GHS'
            or data.get('amount') != int(Decimal(str(user['signup_fee_amount']))*100)
            or not isinstance(metadata, dict) or metadata.get('source') != 'signup_fee'
            or metadata.get('user_id') != str(user['_id'])
            or str((data.get('customer') or {}).get('email', '')).lower() != user['email'].lower()):
        raise ValueError('Payment does not match this account.')
    def operation(tx):
        now = datetime.utcnow()
        updated = db.users.update_one({'_id': user['_id'], 'signup_fee_status': 'unpaid'},
            {'$set': {'signup_fee_status': 'paid', 'signup_fee_paid_at': now,
                      'signup_fee_reference': reference}}, session=tx)
        if not updated.modified_count: return
        db.transactions.insert_one({'_id': reference, 'reference': reference, 'user_id': user['_id'],
            'amount': user['signup_fee_amount'], 'type': 'signup_fee', 'description': 'One Time Sign Up fee',
            'status': 'success', 'gateway': 'Paystack', 'currency': 'GHS', 'created_at': now, 'verified_at': now}, session=tx)
    with db.client.start_session() as tx: tx.with_transaction(operation)


@signup_fee_bp.route('/signup-fee/verify')
def verify():
    user = current_user()
    if outstanding(user):
        try:
            response = requests.get('https://api.paystack.co/transaction/verify/SIGNUP-'+str(user['_id']),
                headers={'Authorization': 'Bearer '+secret_key()}, timeout=(5,25))
            response.raise_for_status()
            result = response.json()
            if not result.get('status'): raise ValueError('Unconfirmed')
            settle(user, result.get('data') or {})
            flash('One Time Sign Up fee paid. Your account is ready.', 'success')
        except Exception:
            flash('Payment is not confirmed yet. Check again after paying. Do not pay twice.', 'warning')
    return redirect(url_for('customer_dashboard.customer_dashboard'))

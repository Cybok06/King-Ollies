"""Application review, quoting, private messages, and atomic payment settlement."""
from datetime import datetime
from decimal import Decimal
import hashlib
import hmac
import secrets
import os
import io
from PIL import Image, UnidentifiedImageError
from urllib.parse import urlparse
import requests
from bson import ObjectId
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for, jsonify, send_file
from db import db
from form_services import parse_price

applications_bp = Blueprint('applications', __name__)


def csrf_token():
    return session.setdefault('form_service_csrf', secrets.token_urlsafe(32))


def require_csrf():
    if not secrets.compare_digest(request.form.get('csrf', ''), csrf_token()):
        abort(400)


def authorized(application_id, admin_only=False):
    if session.get('role') not in ('admin', 'customer'):
        abort(403)
    if admin_only and session.get('role') != 'admin':
        abort(403)
    try:
        application = db.service_applications.find_one({'_id': ObjectId(application_id)})
    except Exception:
        abort(404)
    if not application:
        abort(404)
    if session.get('role') != 'admin' and str(application['user_id']) != str(session.get('user_id', '')):
        abort(403)
    return application


def secret_key():
    from paystack_config import secret_key as configured_secret_key
    return configured_secret_key()


def pesewas(amount):
    return int(Decimal(str(parse_price(amount))) * 100)


def settle(application_id, gateway, reference=None, verified=None, expected_version=None):
    """Commit the payment, order, and transaction together, or not at all."""
    def operation(tx):
        app = db.service_applications.find_one({'_id': application_id}, session=tx)
        if not app:
            raise ValueError('Application not found.')
        if app['status'] == 'paid':
            return
        if gateway == 'Manual':
            if app['status'] != 'manual_review' or app.get('payment_mode') != 'manual' or app.get('quote_version') != expected_version:
                raise ValueError('This manual payment is no longer awaiting review.')
            if not db.application_proofs.find_one({'_id': application_id, 'quote_version': expected_version}, session=tx):
                raise ValueError('Payment proof is missing. Ask the customer to resubmit.')
        elif gateway == 'Wallet':
            if app.get('payment_mode', 'online') != 'online':
                raise ValueError('This quote requires manual payment.')
            if app['status'] != 'quoted' or app.get('quote_version') != expected_version:
                raise ValueError('The quote changed or a Paystack payment is pending. Refresh this page.')
        else:
            if app.get('payment_mode', 'online') != 'online':
                raise ValueError('This quote requires manual payment.')
            if app['status'] != 'payment_pending' or app.get('payment_reference') != reference:
                raise ValueError('This payment does not match the application.')
            if (not verified or verified.get('status') != 'success' or verified.get('reference') != reference
                    or verified.get('currency') != 'GHS' or verified.get('amount') != pesewas(app['quote_amount'])
                    or str((verified.get('customer') or {}).get('email', '')).lower() != app.get('payment_email', '').lower()):
                raise ValueError('Payment details do not match the quoted amount or customer.')
        amount = parse_price(app['quote_amount'])
        now = datetime.utcnow()
        if gateway == 'Wallet':
            debit = db.balances.update_one({'user_id': app['user_id'], 'amount': {'$gte': amount}},
                                          {'$inc': {'amount': -amount}, '$set': {'updated_at': now}}, session=tx)
            if debit.modified_count != 1:
                raise ValueError('Insufficient wallet balance. Add money or choose Paystack.')
        order_id = 'FORM' + str(application_id).upper()
        item = {'serviceId': str(app['service_id']), 'serviceName': app['service_name'],
                'purchase_mode': 'form', 'application_fields': app['answers'], 'application_id': str(application_id),
                'amount': amount, 'value': 'Application', 'phone': '', 'line_status': 'pending', 'provider': 'manual'}
        db.orders.insert_one({'_id': application_id, 'order_id': order_id, 'user_id': app['user_id'],
                              'items': [item], 'total_amount': amount, 'charged_amount': amount,
                              'profit_amount_total': 0, 'status': 'pending', 'paid_from': gateway.lower(),
                              'created_at': now, 'updated_at': now}, session=tx)
        db.transactions.insert_one({'_id': application_id, 'user_id': app['user_id'], 'amount': amount,
                                    'reference': reference or order_id, 'status': 'success', 'type': 'purchase',
                                    'gateway': gateway, 'currency': 'GHS', 'created_at': now, 'verified_at': now,
                                    'application_id': str(application_id)}, session=tx)
        db.service_applications.update_one({'_id': application_id}, {'$set': {
            'status': 'paid', 'paid_at': now, 'paid_amount': amount, 'payment_method': gateway,
            'order_id': order_id, 'updated_at': now}}, session=tx)
    with db.client.start_session() as tx:
        tx.with_transaction(operation)


def application_progress(rows):
    """Keep payment status intact; derive completion from the existing order workflow."""
    order_ids = [row['order_id'] for row in rows if row.get('order_id')]
    orders = {order['order_id']: order for order in db.orders.find({'order_id': {'$in': order_ids}})}
    terminal = {'completed', 'delivered', 'failed', 'refunded', 'cancelled', 'canceled'}
    for row in rows:
        order = orders.get(row.get('order_id'), {})
        progress = str(order.get('status') or row.get('status') or 'submitted').lower()
        row['progress_status'] = progress
        row['ongoing'] = progress not in terminal
    return rows


@applications_bp.app_context_processor
def sidebar_application_counts():
    role = session.get('role')
    if role not in ('admin', 'customer'):
        return {'sidebar_application_count': 0}
    query = {}
    if role == 'customer':
        if not ObjectId.is_valid(session.get('user_id', '')):
            return {'sidebar_application_count': 0}
        query['user_id'] = ObjectId(session['user_id'])
    rows = application_progress(list(db.service_applications.find(query,
        {'status': 1, 'order_id': 1, 'quote_amount': 1})))
    ongoing = [row for row in rows if row['ongoing']]
    if role == 'admin':
        return {'sidebar_application_count': len(ongoing)}
    attention = {row['_id'] for row in ongoing
                 if row.get('status') in ('quoted', 'payment_pending') and row.get('quote_amount') is not None}
    unread = db.application_messages.find({'application_id': {'$in': [row['_id'] for row in ongoing]},
        'sender_role': 'admin', 'customer_read_at': {'$exists': False}}, {'application_id': 1})
    attention.update(message['application_id'] for message in unread)
    return {'sidebar_application_count': len(attention)}


@applications_bp.route('/applications')
@applications_bp.route('/admin/applications', endpoint='admin_index')
def index():
    role = session.get('role')
    if role not in ('admin', 'customer'):
        abort(403)
    is_admin = role == 'admin'
    if request.path.startswith('/admin/') and not is_admin:
        abort(403)
    if is_admin and not request.path.startswith('/admin/'):
        return redirect(url_for('applications.admin_index'))
    query = {}
    if not is_admin:
        try: query['user_id'] = ObjectId(session.get('user_id'))
        except Exception: abort(403)
    # The list only needs summary fields; private answers and chat stay on the profile.
    fields = {'user_id': 1, 'service_name': 1, 'status': 1, 'order_id': 1, 'created_at': 1,
              'quote_amount': 1, 'paid_amount': 1, 'paid_at': 1}
    rows = application_progress(list(db.service_applications.find(query, fields).sort([('created_at', -1), ('_id', -1)])))
    ongoing = sum(row['ongoing'] for row in rows)
    view = request.args.get('view', 'ongoing')
    if view not in ('ongoing', 'closed', 'all'): view = 'ongoing'
    visible = [row for row in rows if view == 'all' or row['ongoing'] == (view == 'ongoing')]
    filtered_count = len(visible)
    per_page = 12
    pages = max(1, (filtered_count + per_page - 1) // per_page)
    try: page = min(max(int(request.args.get('page', 1)), 1), pages)
    except (ValueError, TypeError): page = 1
    visible = visible[(page-1)*per_page:page*per_page]
    if is_admin:
        users = {str(user['_id']): user for user in db.users.find({'_id': {'$in': [row['user_id'] for row in visible]}})}
        for row in visible:
            row['customer_name'] = users.get(str(row['user_id']), {}).get('username', 'Customer')
    issued = [row for row in rows if row.get('quote_amount') is not None]
    paid = [row for row in rows if row.get('paid_at') and row.get('paid_amount') is not None]
    issued_total = sum((Decimal(str(row['quote_amount'])) for row in issued), Decimal(0))
    paid_total = sum((Decimal(str(row['paid_amount'])) for row in paid), Decimal(0))
    refunded_total = sum((Decimal(str(row['paid_amount'])) for row in paid if row['progress_status'] == 'refunded'), Decimal(0))
    return render_template('applications.html', applications=visible, is_admin=is_admin,
                           ongoing_count=ongoing, closed_count=len(rows)-ongoing, total_count=len(rows), view=view,
                           page=page, pages=pages, filtered_count=filtered_count, per_page=per_page,
                           issued_count=len(issued), issued_total=float(issued_total), paid_count=len(paid),
                           paid_total=float(paid_total), revenue=float(paid_total-refunded_total), refunded_total=float(refunded_total))


@applications_bp.route('/applications/<application_id>/export/<format>')
def export(application_id, format):
    application = authorized(application_id, admin_only=True)
    if format not in ('xlsx', 'pdf'): abort(404)
    application_progress([application])
    customer = db.users.find_one({'_id': application['user_id']}) or {}
    from application_exports import export_excel, export_pdf
    content = export_excel(application, customer) if format == 'xlsx' else export_pdf(application, customer)
    response = send_file(content, as_attachment=True, download_name=f'application-{application_id}.{format}',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if format == 'xlsx' else 'application/pdf', max_age=0)
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@applications_bp.route('/applications/<application_id>')
def detail(application_id):
    application = authorized(application_id)
    application_progress([application])
    messages = list(db.application_messages.find({'application_id': application['_id']}).sort('created_at', 1))
    if session.get('role') == 'customer':
        # Only mark messages actually loaded here; a concurrent new reply stays unread.
        db.application_messages.update_many({'_id': {'$in': [m['_id'] for m in messages if m.get('sender_role') == 'admin']},
            'customer_read_at': {'$exists': False}}, {'$set': {'customer_read_at': datetime.utcnow()}})
    customer = db.users.find_one({'_id': application['user_id']}) or {}
    balance = db.balances.find_one({'user_id': application['user_id']}) or {}
    return render_template('application_detail.html', application=application, messages=messages, customer=customer,
                           balance=balance.get('amount', 0), csrf=csrf_token(), message_token=secrets.token_hex(16),
                           is_admin=session.get('role') == 'admin')


@applications_bp.route('/applications/<application_id>/quote', methods=['POST'])
def quote(application_id):
    app = authorized(application_id, admin_only=True)
    require_csrf()
    try:
        amount = parse_price(request.form.get('amount'))
        version = int(request.form.get('quote_version', '-1'))
        mode = request.form.get('payment_mode', 'online')
        if mode not in ('online', 'manual'):
            raise ValueError('Choose Online or Manual payment.')
        momo = {key: (request.form.get('momo_' + key) or '').strip() for key in ('number', 'network', 'name')}
        if mode == 'manual' and any(not value or len(value) > 120 for value in momo.values()):
            raise ValueError('Enter the MoMo number, network and account name (up to 120 characters each).')
        result = db.service_applications.update_one(
            {'_id': app['_id'], 'status': {'$in': ['submitted', 'quoted']}, 'quote_version': version},
            {'$set': {'quote_amount': amount, 'status': 'quoted', 'payment_mode': mode,
                      'momo': momo if mode == 'manual' else {}, 'quoted_by': str(session.get('user_id') or 'admin'),
                      'updated_at': datetime.utcnow()}, '$inc': {'quote_version': 1}})
        if not result.modified_count:
            raise ValueError('The application changed or payment has started. Refresh before quoting.')
        flash('Quote sent. The customer can now pay from their application page.', 'success')
    except (ValueError, TypeError) as exc:
        flash(str(exc), 'danger')
    return redirect(url_for('applications.detail', application_id=application_id))


@applications_bp.route('/applications/<application_id>/messages', methods=['POST'])
def message(application_id):
    app = authorized(application_id)
    require_csrf()
    text = (request.form.get('message') or '').strip()
    token = request.form.get('message_token', '')
    if not text or len(text) > 4000 or len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
        abort(400)
    role = 'admin' if session.get('role') == 'admin' else 'customer'
    key = hashlib.sha256((application_id + role + str(session.get('user_id', '')) + token).encode()).hexdigest()
    db.application_messages.update_one({'_id': key}, {'$setOnInsert': {
        'application_id': app['_id'], 'sender_role': role, 'sender_id': str(session.get('user_id', '')),
        'text': text, 'created_at': datetime.utcnow()}}, upsert=True)
    return redirect(url_for('applications.detail', application_id=application_id) + '#conversation')


@applications_bp.route('/applications/<application_id>/pay/wallet', methods=['POST'])
def wallet(application_id):
    app = authorized(application_id)
    if session.get('role') != 'customer': abort(403)
    require_csrf()
    try:
        settle(app['_id'], 'Wallet', expected_version=int(request.form.get('quote_version', '-1')))
        flash('Payment received. Your application is queued for processing.', 'success')
    except ValueError as exc:
        flash(str(exc), 'danger')
    except Exception:
        flash('Payment could not be confirmed. Refresh to check its status before retrying.', 'danger')
    return redirect(url_for('applications.detail', application_id=application_id))


def verify_payment(app):
    reference = app.get('payment_reference')
    if not reference: raise ValueError('There is no Paystack payment to check.')
    result = requests.get('https://api.paystack.co/transaction/verify/' + reference,
                          headers={'Authorization': 'Bearer ' + secret_key()}, timeout=25)
    result.raise_for_status()
    data = result.json()
    if not data.get('status'): raise ValueError('Payment is not confirmed yet. Please try checking again.')
    verified = data.get('data') or {}
    if verified.get('status') != 'success':
        raise ValueError('Payment is not complete. Resume Paystack or check again after paying.')
    settle(app['_id'], 'Paystack', reference=reference, verified=verified)


@applications_bp.route('/applications/<application_id>/pay/paystack', methods=['POST'])
def paystack(application_id):
    app = authorized(application_id)
    if session.get('role') != 'customer': abort(403)
    require_csrf()
    try:
        if app['status'] == 'paid': return redirect(url_for('applications.detail', application_id=application_id))
        if app.get('payment_mode', 'online') != 'online':
            raise ValueError('This quote requires manual payment.')
        if app['status'] == 'quoted':
            customer = db.users.find_one({'_id': app['user_id']}) or {}
            email = (customer.get('email') or '').strip()
            if not email: raise ValueError('An email address is required for Paystack. Contact support to update your profile.')
            version = int(request.form.get('quote_version', '-1'))
            reference = 'APP-' + secrets.token_hex(20)
            result = db.service_applications.update_one(
                {'_id': app['_id'], 'status': 'quoted', 'quote_version': version, 'payment_mode': {'$ne': 'manual'}},
                {'$set': {'status': 'payment_pending', 'payment_reference': reference, 'payment_email': email,
                          'updated_at': datetime.utcnow()}})
            if not result.modified_count: raise ValueError('The quote changed. Refresh to review the current amount.')
            app = db.service_applications.find_one({'_id': app['_id']})
        if app['status'] != 'payment_pending': raise ValueError('Wait for an admin quote before paying.')
        if app.get('authorization_url'): return redirect(app['authorization_url'])
        # One persisted reference is reused after errors, preventing separate payable sessions.
        payload = {'email': app['payment_email'], 'amount': pesewas(app['quote_amount']), 'currency': 'GHS',
                   'reference': app['payment_reference'],
                   'callback_url': url_for('applications.callback', application_id=application_id, _external=True),
                   'metadata': {'source': 'service_application', 'application_id': application_id}}
        response = requests.post('https://api.paystack.co/transaction/initialize', json=payload,
                                 headers={'Authorization': 'Bearer ' + secret_key()}, timeout=25)
        response.raise_for_status()
        result = response.json()
        url = (result.get('data') or {}).get('authorization_url', '')
        if not result.get('status') or urlparse(url).scheme != 'https' or urlparse(url).hostname != 'checkout.paystack.com':
            raise ValueError('Could not open Paystack. Check payment status before retrying.')
        db.service_applications.update_one({'_id': app['_id'], 'payment_reference': app['payment_reference']},
                                          {'$set': {'authorization_url': url}})
        return redirect(url)
    except ValueError as exc:
        flash(str(exc), 'danger')
    except Exception:
        flash('Paystack could not be reached. Your payment reference is saved; check status or retry.', 'danger')
    return redirect(url_for('applications.detail', application_id=application_id))


@applications_bp.route('/applications/<application_id>/paystack/return')
def callback(application_id):
    app = authorized(application_id)
    try:
        if app['status'] != 'paid': verify_payment(app)
        flash('Payment confirmed. Your application is queued for processing.', 'success')
    except ValueError as exc: flash(str(exc), 'warning')
    except Exception: flash('Unable to verify payment now. Use Check payment status to retry.', 'warning')
    return redirect(url_for('applications.detail', application_id=application_id))


@applications_bp.route('/applications/<application_id>/pay/manual', methods=['POST'])
def manual_payment(application_id):
    app = authorized(application_id)
    if session.get('role') != 'customer': abort(403)
    require_csrf()
    try:
        version = int(request.form.get('quote_version', '-1'))
        if app['status'] != 'quoted' or app.get('payment_mode') != 'manual' or app.get('quote_version') != version:
            raise ValueError('The quote changed or proof was already submitted. Refresh this page.')
        upload = request.files.get('proof')
        if not upload:
            raise ValueError('Upload a screenshot or photo of your payment receipt.')
        raw = upload.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError('The proof image must be 4 MB or smaller.')
        try:
            with Image.open(io.BytesIO(raw)) as picture:
                if picture.format not in ('JPEG', 'PNG', 'WEBP') or picture.width * picture.height > 20000000:
                    raise ValueError('Use a JPG, PNG or WebP image up to 20 megapixels.')
                picture.load()
                picture.thumbnail((2000, 2000))
                output = io.BytesIO()
                picture.convert('RGB').save(output, format='JPEG', quality=85)
                data = output.getvalue()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise ValueError('Upload a valid JPG, PNG or WebP payment receipt.')
        def operation(tx):
            result = db.service_applications.update_one(
                {'_id': app['_id'], 'status': 'quoted', 'payment_mode': 'manual', 'quote_version': version},
                {'$set': {'status': 'manual_review', 'manual_submitted_at': datetime.utcnow(),
                          'updated_at': datetime.utcnow()}}, session=tx)
            if not result.modified_count:
                raise ValueError('The quote changed or proof was already submitted. Refresh this page.')
            db.application_proofs.replace_one({'_id': app['_id']},
                {'_id': app['_id'], 'data': data, 'quote_version': version, 'created_at': datetime.utcnow()},
                upsert=True, session=tx)
        with db.client.start_session() as tx:
            tx.with_transaction(operation)
        flash('Marked as paid. Your proof has been sent to admin for confirmation.', 'success')
    except (ValueError, TypeError) as exc:
        flash(str(exc), 'danger')
    return redirect(url_for('applications.detail', application_id=application_id))


@applications_bp.route('/applications/<application_id>/proof')
def proof(application_id):
    app = authorized(application_id)
    stored = db.application_proofs.find_one({'_id': app['_id']})
    if not stored: abort(404)
    response = send_file(io.BytesIO(stored['data']), mimetype='image/jpeg', download_name='payment-proof.jpg')
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@applications_bp.route('/applications/<application_id>/pay/manual/review', methods=['POST'])
def review_manual(application_id):
    app = authorized(application_id, admin_only=True)
    require_csrf()
    try:
        version = int(request.form.get('quote_version', '-1'))
        if request.form.get('decision') == 'confirm':
            settle(app['_id'], 'Manual', expected_version=version)
            flash('Manual payment confirmed. The order is ready for processing.', 'success')
        elif request.form.get('decision') == 'reject':
            result = db.service_applications.update_one(
                {'_id': app['_id'], 'status': 'manual_review', 'quote_version': version},
                {'$set': {'status': 'quoted', 'updated_at': datetime.utcnow()}, '$inc': {'quote_version': 1}})
            if not result.modified_count: raise ValueError('This payment has already been reviewed.')
            flash('Proof returned for correction. Message the customer with the reason.', 'warning')
        else:
            raise ValueError('Choose a review decision.')
    except ValueError as exc:
        flash(str(exc), 'danger')
    return redirect(url_for('applications.detail', application_id=application_id))


@applications_bp.route('/webhooks/paystack/applications', methods=['POST'])
def webhook():
    expected = hmac.new(secret_key().encode(), request.get_data(), hashlib.sha512).hexdigest()
    if not hmac.compare_digest(expected, request.headers.get('x-paystack-signature', '')): abort(401)
    payload = request.get_json(silent=True) or {}
    if payload.get('event') == 'charge.success':
        reference = (payload.get('data') or {}).get('reference')
        app = db.service_applications.find_one({'payment_reference': reference}) if reference else None
        if app and app['status'] != 'paid':
            try: verify_payment(app)
            except Exception: return jsonify(success=False), 503
    return jsonify(success=True)

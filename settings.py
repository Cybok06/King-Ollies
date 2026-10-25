from datetime import datetime
from bson import ObjectId
from werkzeug.security import check_password_hash
import hashlib
import re
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort
from db import db
from applications import csrf_token, require_csrf
from store_catalog import money

settings_bp = Blueprint("settings", __name__)

@settings_bp.route("/admin/settings", methods=["GET", "POST"])
def manage_api():
    if session.get("role") != "admin": abort(403)
    if request.method == "POST":
        require_csrf()
        if request.form.get('action') == 'api_key':
            try:
                admin = db.users.find_one({'_id': ObjectId(session.get('user_id'))})
            except Exception:
                admin = None
            if not admin or admin.get('role') != 'admin' or not check_password_hash(admin.get('password', ''), request.form.get('confirm_password', '')):
                flash('Your current admin password is incorrect. No changes were saved.', 'danger')
                return redirect(url_for('settings.manage_api'))
            key = request.form.get('api_key', '').strip()
            public = request.form.get('public_key', '').strip()
            if not re.fullmatch(r'sk_(live|test)_[A-Za-z0-9]+', key) or not re.fullmatch(r'pk_(live|test)_[A-Za-z0-9]+', public) or key.split('_')[1] != public.split('_')[1]:
                flash('Enter the secret and public keys from the same Paystack account and mode (test or live).', 'danger')
            else:
                now = datetime.utcnow()
                event = {'changed_at': now, 'changed_by': str(admin['_id']), 'username': admin.get('username', 'Admin'),
                         'mode': key.split('_')[1], 'fingerprint': hashlib.sha256(key.encode()).hexdigest()[:12]}
                db.settings.update_one({'_id': 'paystack'}, {'$set': {'secret_key': key, 'public_key': public,
                    'updated_at': now, 'updated_by': str(admin['_id'])}, '$push': {'history': event}}, upsert=True)
                flash('Paystack API keys updated for deposits, signup fees, stores and application invoices.', 'success')
            return redirect(url_for('settings.manage_api'))
        try:
            amount = money(request.form.get("signup_fee"))
            db.settings.update_one({"_id": "signup_fee"}, {"$set": {
                "amount": amount, "updated_at": datetime.utcnow(), "updated_by": session.get("user_id")}}, upsert=True)
            flash("Sign up fee saved. This amount applies to new registrations.", "success")
        except (ValueError, TypeError):
            flash("Enter a valid fee between GHS 0 and GHS 1,000,000.", "danger")
        return redirect(url_for("settings.manage_api"))
    config = db.settings.find_one({"_id": "signup_fee"}) or {}
    payment = db.settings.find_one({'_id': 'paystack'}, {'history': 1, 'updated_at': 1}) or {}
    response = render_template("settings.html", signup_fee=config.get("amount", 0), csrf=csrf_token(),
        payment_configured=bool(payment), key_history=list(reversed(payment.get('history', []))))
    return response, 200, {'Cache-Control': 'no-store'}

"""One runtime Paystack configuration for every payment flow."""
import os

def credentials():
    from db import db
    saved = db.settings.find_one({'_id': 'paystack'})
    if saved:
        return saved.get('secret_key', ''), saved.get('public_key', '')
    return (os.getenv('PAYSTACK_SECRET_KEY') or os.getenv('PAYSTACK_SK') or '').strip(), (os.getenv('PAYSTACK_PUBLIC_KEY') or os.getenv('PAYSTACK_PK') or '').strip()

def secret_key():
    return credentials()[0]

def public_key():
    return credentials()[1]

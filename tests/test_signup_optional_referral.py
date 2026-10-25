"""Optional referrals must not block signup or bypass supplied-code validation."""
import importlib.util
import sys
import types
from unittest.mock import patch
import mongomock
import pytest
from flask import Flask

@pytest.mark.parametrize('code', [None, '', '   ', ' friend ', 'INVALID'])
def test_signup_optional_referral(code):
    fake = types.ModuleType('db')
    fake.db = mongomock.MongoClient().test
    spec = importlib.util.spec_from_file_location('signup_under_test', 'signup.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'db': fake}):
        spec.loader.exec_module(module)
    app = Flask(__name__)
    app.secret_key = 'test'
    app.register_blueprint(module.signup_bp)
    app.add_url_rule('/login', endpoint='login.login', view_func=lambda: 'Login')
    app.add_url_rule('/customer_dashboard', endpoint='customer_dashboard.customer_dashboard', view_func=lambda: 'Dashboard')
    fake.db.referrals.insert_one({'ref_code': 'FRIEND', 'signups': 0})
    data = dict(first_name='Test', last_name='User', username='testuser', email='test@example.com', phone='0551234567', whatsapp='0551234567', business_name='Test Store', password='TestPassword123!', confirm_password='TestPassword123!')
    if code is not None:
        data['referral'] = code
    response = app.test_client().post('/signup', data=data)
    if code == 'INVALID':
        assert fake.db.users.count_documents({}) == 0
        assert fake.db.balances.count_documents({}) == 0
    else:
        assert response.location == '/customer_dashboard'
        user = fake.db.users.find_one({'username': 'testuser'})
        assert user['referral'] == (code or '').strip().upper()
        assert fake.db.balances.count_documents({'user_id': user['_id']}) == 1
    assert fake.db.referrals.find_one()['signups'] == (1 if code == ' friend ' else 0)

import importlib.util
import sys
import types
from unittest.mock import patch
import mongomock
import pytest
from bson import ObjectId
from flask import Flask, abort, request
from werkzeug.security import generate_password_hash

@pytest.fixture
def setup():
    db = mongomock.MongoClient().test
    fake = types.ModuleType('db'); fake.db = db
    applications = types.ModuleType('applications')
    applications.csrf_token = lambda: 'token'
    def csrf():
        if request.form.get('csrf') != 'token': abort(400)
    applications.require_csrf = csrf
    spec = importlib.util.spec_from_file_location('settings_test', 'settings.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'db': fake, 'applications': applications}): spec.loader.exec_module(module)
    app = Flask(__name__); app.secret_key = 'test'; app.register_blueprint(module.settings_bp)
    uid = ObjectId()
    db.users.insert_one({'_id': uid, 'role': 'admin', 'username': 'Admin', 'password': generate_password_hash('correct')})
    client = app.test_client()
    with client.session_transaction() as session: session.update(user_id=str(uid), role='admin')
    return db, fake, client

def payload(**changes):
    return dict(action='api_key', csrf='token', api_key='sk_test_example', public_key='pk_test_example', confirm_password='correct', **changes)

def test_password_and_csrf_required(setup):
    db, _, client = setup
    data = payload(); data['confirm_password'] = 'wrong'
    assert client.post('/admin/settings', data=data).status_code == 302
    assert db.settings.count_documents({}) == 0
    data = payload(); data['csrf'] = 'bad'
    assert client.post('/admin/settings', data=data).status_code == 400
    assert db.settings.count_documents({}) == 0

def test_customer_cannot_change_keys(setup):
    db, _, client = setup
    with client.session_transaction() as session: session['role'] = 'customer'
    assert client.post('/admin/settings', data=payload()).status_code == 403
    assert db.settings.count_documents({}) == 0

def test_rotation_is_immediate_and_history_has_no_secrets(setup):
    db, fake, client = setup
    import paystack_config
    with patch.dict(sys.modules, {'db': fake}):
        for key in ['first', 'second']:
            data = payload(); data['api_key'] = 'sk_test_' + key
            assert client.post('/admin/settings', data=data).status_code == 302
            assert paystack_config.secret_key() == data['api_key']
            assert paystack_config.public_key() == data['public_key']
    history = db.settings.find_one({'_id': 'paystack'})['history']
    assert len(history) == 2
    assert history[0]['changed_at'] and history[0]['username'] == 'Admin'
    assert 'sk_test_' not in str(history)

def test_mixed_mode_rejected(setup):
    db, _, client = setup
    data = payload(); data['public_key'] = 'pk_live_example'
    client.post('/admin/settings', data=data)
    assert db.settings.count_documents({}) == 0

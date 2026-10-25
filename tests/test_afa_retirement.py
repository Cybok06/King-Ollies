"""Old customer registration URLs must never create records or debit wallets."""
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import mongomock
import pytest
from bson import ObjectId
from flask import Flask


@pytest.mark.parametrize('filename,blueprint', [
    ('afa_routes.py', 'afa_bp'),
    ('customer_dashboard.py', 'customer_dashboard_bp'),
])
def test_retired_customer_registration(filename, blueprint):
    database = mongomock.MongoClient().test
    uid = ObjectId()
    database.balances.insert_one({'user_id': uid, 'amount': 100})
    with patch.dict(sys.modules, {'db': types.SimpleNamespace(db=database)}):
        spec = importlib.util.spec_from_file_location('retired_afa_test', filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    app = Flask(__name__)
    app.secret_key = 'test'
    app.register_blueprint(getattr(module, blueprint))
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=str(uid), role='customer')
    response = client.post('/api/afa/register', json={'name': 'Customer', 'phone': '0551234567'})
    assert response.status_code == 410
    assert not response.json['success']
    assert database.afa_registrations.count_documents({}) == 0
    assert database.balances.find_one({'user_id': uid})['amount'] == 100


def test_edited_templates_compile():
    app = Flask(__name__, template_folder=str(Path('templates').resolve()))
    for name in ('index.html', 'customer_dashboard.html', 'store_page.html',
                 'store_create.html', 'legal/terms.html', 'legal/privacy.html',
                 'admin_external_api.html'):
        app.jinja_env.get_template(name)

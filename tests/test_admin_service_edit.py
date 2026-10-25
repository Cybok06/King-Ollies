"""Editing service details must preserve read-only default pricing metadata."""
import importlib.util
import sys
import types
from unittest.mock import patch

import mongomock
from flask import Flask


def test_edit_preserves_default_offers_and_stage_prices():
    fake = types.ModuleType('db')
    fake.db = mongomock.MongoClient().test
    spec = importlib.util.spec_from_file_location('service_edit_test', 'admin_services.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'db': fake}):
        spec.loader.exec_module(module)
    app = Flask(__name__)
    app.secret_key = 'test'
    app.register_blueprint(module.admin_services_bp)
    offers = [{'offer_id': 'stable-id', 'amount': 10, 'customer_price': 12, 'value': '1GB', 'stage_prices': {'elite': 11}}]
    sid = fake.db.services.insert_one({'name': 'MTN', 'offers': offers, 'store_offers': []}).inserted_id
    client = app.test_client()
    with client.session_transaction() as session:
        session['role'] = 'admin'
    response = client.post(f'/admin/services/{sid}/update', data={'service_name': 'MTN NORMAL', 'image_url': 'https://example.com/logo.png', 'service_type': 'API'})
    assert response.status_code == 302
    saved = fake.db.services.find_one({'_id': sid})
    assert saved['offers'] == offers
    assert saved['name'] == 'MTN NORMAL'

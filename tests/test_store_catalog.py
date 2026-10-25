import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest
from bson import ObjectId
from flask import Flask
from test_applications import Database
from store_catalog import allowed_service, special_price, validate_pricing, afa_service


def test_catalog_excludes_applications():
    assert allowed_service({'name': 'MTN NORMAL'})
    assert allowed_service({'name': 'Checkers and Forms', 'purchase_mode': 'checker'})
    assert not allowed_service({'name': 'Passport', 'purchase_mode': 'form'})
    assert not allowed_service({'name': 'Other'})
    assert not allowed_service({'name': 'AFA Registration', 'purchase_mode': 'afa'})
    assert allowed_service({'name': 'AFA TALKTIME'})


def test_retired_afa_cannot_be_purchased(store_flow):
    _, db, client, _ = store_flow
    for method in (client.get, client.post):
        assert method('/s/campus/service/afa-registration').status_code == 404
    assert db.store_service_payments.count_documents({}) == 0
    assert db.afa_registrations.count_documents({}) == 0


def test_checker_prices_use_stable_type_ids():
    offer = {'value': 'bece', 'amount': 20}
    store = {'pricing': {'per_service': [{'service_id': 'checker', 'percent': 10}]}}
    assert special_price(store, 'checker', offer) == 22
    store['pricing']['per_service'][0] = {'service_id': 'checker', 'offers': [
        {'value': 'wassce', 'profit': 9}, {'value': 'bece', 'profit': 3}]}
    assert special_price(store, 'checker', offer) == 23
    assert special_price(store, 'checker', {'value': 'new-type', 'amount': 10}) is None


@pytest.mark.parametrize('change', ['service', 'low', 'nan', 'negative', 'stale'])
def test_invalid_editor_prices(change):
    catalog = [{'_id_str': 'checker', 'purchase_mode': 'checker', 'offers': [{'value': 'bece', 'amount': 20}]}]
    data = {'service_ids': ['checker'], 'pricing': {'per_service': [{'service_id': 'checker', 'offers': [{'value': 'bece', 'total': 25}]}]}}
    override = data['pricing']['per_service'][0]['offers'][0]
    if change == 'service': data['service_ids'] = ['passport']
    if change == 'low': override['total'] = 10
    if change == 'nan': override['total'] = 'NaN'
    if change == 'negative': override['profit'] = -1
    if change == 'stale': override['value'] = 'missing'
    with pytest.raises(ValueError): validate_pricing(data, catalog)


@pytest.fixture
def store_flow():
    db = Database()
    modules = {'db': types.SimpleNamespace(db=db)}
    with patch.dict(sys.modules, modules):
        for name, filename in [('form_services','form_services.py'), ('applications','applications.py'), ('store_special_test','routes/store_special.py')]:
            spec = importlib.util.spec_from_file_location(name, filename)
            module = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules, modules): spec.loader.exec_module(module)
            modules[name] = module
    m = module
    app = Flask(__name__, template_folder=str(Path('templates').resolve()))
    app.secret_key = 'test'
    app.register_blueprint(m.store_special_bp)
    app.add_url_rule('/s/<slug>', 'stores.store_public_page', lambda slug: 'store')
    client = app.test_client()
    sid = ObjectId()
    db.stores.insert_one({'slug': 'campus', 'name': 'Store', 'owner_id': ObjectId(), 'status': 'published',
                         'service_ids': [str(sid), 'afa-registration'], 'pricing': {'percent_default': 10}})
    db.services.insert_one({'_id': sid, 'name': 'Checkers and Forms', 'purchase_mode': 'checker', 'status': 'OPEN', 'availability': 'AVAILABLE'})
    db.checker_types.insert_one({'_id': 'bece', 'name': 'BECE', 'price': 20, 'enabled': True, 'service_id': sid})
    db.wassce_checker.insert_one({'type_id': 'bece', 'status': 'not_sold', 'message': 'PRIVATE MESSAGE'})
    return m, db, client, sid


def intent(db, sid, mode='checker', state='verified'):
    store = db.stores.find_one()
    row = {'_id': 'OLL-'+'a'*32, 'owner_id': store['owner_id'], 'status': state, 'store_slug': 'campus',
           'service_id': str(sid), 'service_name': 'Checkers and Forms', 'offer_id': 'bece', 'offer_name': 'BECE',
           'mode': mode, 'base': 20, 'amount': 22, 'details': {'name': 'Person', 'phone': '0551234567'}, 'user_id': None}
    db.store_service_payments.insert_one(row)
    return row['_id']


def test_store_fulfillment_once_and_private_receipt(store_flow):
    m, db, client, sid = store_flow
    ref = intent(db, sid)
    m.fulfill(ref); m.fulfill(ref)
    assert db.store_accounts.find_one()['total_profit_balance'] == 2
    assert db.orders.count_documents({}) == db.transactions.count_documents({}) == db.purchase_history.count_documents({}) == 1
    assert isinstance(db.orders.find_one()['_id'], ObjectId)
    assert client.get('/store-service/receipt/'+ref).status_code == 403
    with client.session_transaction() as s: s['store_purchase_tokens'] = ['a'*32]
    response = client.get('/store-service/receipt/'+ref)
    assert b'PRIVATE MESSAGE' in response.data and response.headers['Cache-Control'] == 'no-store'


def test_no_stock_paid_review_no_profit(store_flow):
    m, db, client, sid = store_flow
    ref = intent(db, sid)
    db.wassce_checker.delete_many({})
    m.fulfill(ref)
    assert db.orders.find_one()['refund_review_required']
    assert db.store_service_payments.find_one()['status'] == 'review'
    assert db.store_accounts.count_documents({}) == db.purchase_history.count_documents({}) == 0


def test_fulfillment_rolls_back_on_error(store_flow):
    m, db, client, sid = store_flow
    ref = intent(db, sid)
    with patch.object(db.raw.purchase_history, 'insert_one', side_effect=RuntimeError):
        with pytest.raises(RuntimeError): m.fulfill(ref)
    assert db.wassce_checker.find_one()['status'] == 'not_sold'
    assert db.store_service_payments.find_one()['status'] == 'verified'
    assert db.orders.count_documents({}) == 0


def test_afa_registration_and_catalog(store_flow):
    m, db, client, sid = store_flow
    ref = intent(db, sid, mode='afa')
    m.fulfill(ref); m.fulfill(ref)
    reg = db.afa_registrations.find_one()
    assert isinstance(reg['_id'], ObjectId) and reg['charged'] and reg['name'] == 'Person'
    assert db.afa_registrations.count_documents({}) == 1
    db.afa_settings.insert_one({'_id': 'AFA_SETTINGS', 'price': 7, 'is_open': False})
    assert afa_service(db)['status'] == 'CLOSED'


def test_public_catalog_and_payment_guard(store_flow):
    m, db, client, sid = store_flow
    response = client.get('/s/campus/service/'+str(sid))
    assert response.status_code == 200 and b'22.00' in response.data and b'PRIVATE MESSAGE' not in response.data
    assert client.post('/s/campus/service/'+str(sid), data={}).status_code == 400
    assert client.get('/s/campus/service/'+str(ObjectId())).status_code == 404


@pytest.mark.parametrize('bad', ['amount', 'source', 'reference', 'currency', 'unpaid'])
def test_payment_verification_rejects_mismatch(store_flow, bad):
    m, db, client, sid = store_flow
    ref = intent(db, sid, state='pending')
    with client.session_transaction() as s: s['store_purchase_tokens'] = ['a'*32]
    data = {'reference': ref, 'currency': 'GHS', 'amount': 2200,
            'metadata': {'source': 'store_service', 'reference': ref}}
    if bad == 'amount': data['amount'] = 2000
    if bad == 'source': data['metadata']['source'] = 'deposit'
    if bad == 'reference': data['reference'] = 'different'
    if bad == 'currency': data['currency'] = 'USD'
    fake = types.SimpleNamespace(_verify_paystack=lambda _: (bad != 'unpaid', data, '', {}))
    with patch.dict(sys.modules, {'routes.store_page': fake}):
        client.get('/store-service/receipt/'+ref)
    assert db.orders.count_documents({}) == 0
    assert db.store_service_payments.find_one()['status'] == 'pending'

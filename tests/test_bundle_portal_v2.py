"""Checkout routing uses Nagonu and ignores retired provider settings."""
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import mongomock
from test_applications import Database
import nagonu_orders
import pytest
from bson import ObjectId
from flask import Flask


@pytest.fixture
def checkout_env():
    wrapped = Database()
    db = wrapped.raw
    fake = types.ModuleType("db")
    fake.db = db
    spec = importlib.util.spec_from_file_location("manual_checkout_test", Path("checkout.py"))
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"db": fake}):
        spec.loader.exec_module(module)
    module.db = wrapped
    module.first_blocked_phone = lambda phones: None
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(module.checkout_bp)
    client = app.test_client()
    uid = ObjectId()
    db.balances.insert_one({"user_id": uid, "amount": 100})
    with client.session_transaction() as session:
        session.update(user_id=str(uid), role="customer")
    return client, db, uid


@pytest.mark.parametrize("provider", ["dataconnect", "codecraft", "datakazina", "skplug", "portal02", "bundleportal", "bundleportal_mtn2"])
def test_configured_provider_orders_use_nagonu(checkout_env, provider):
    client, db, uid = checkout_env
    sid = db.services.insert_one({"name":"MTN NORMAL","type":"API","provider":provider,"network_id":3,
        "offers":[{"value":{"id":1,"volume":1000},"amount":9,"customer_price":10}]}).inserted_id
    item = {"serviceId":str(sid),"phone":"0241234567","amount":10,"value_obj":{"id":1,"volume":1000},"provider":provider}
    accepted = types.SimpleNamespace(status_code=200,json=lambda:{"status":200,"reference_id":"API-ref","order_id":"NAN1"})
    with patch.object(nagonu_orders.requests,"post",return_value=accepted) as post:
        response = client.post("/checkout",json={"cart":[item],"client_request_id":"test-request"})
        assert response.status_code==200,response.json
        assert response.json['success']
        response = client.post("/checkout",json={"cart":[item],"client_request_id":"test-request"})
        assert post.call_count==1
        assert post.call_args.args[0]=='https://nagonu.site/api/send_order.php'
    assert db.orders.find_one()['items'][0]['provider']=='nagonu'
    assert db.balances.find_one({'user_id':uid})['amount']==90
    assert db.provider_transactions.count_documents({})==0


def test_unavailable_service_still_rejected(checkout_env):
    client, db, uid = checkout_env
    sid = db.services.insert_one({"name": "MTN", "status": "CLOSED"}).inserted_id
    response = client.post("/checkout", json={"cart": [{"serviceId": str(sid), "phone": "0241234567", "amount": 10}]})
    assert response.status_code == 400
    assert db.balances.find_one({"user_id": uid})["amount"] == 100
    assert db.orders.count_documents({}) == 0


def test_store_checkout_uses_nagonu_after_payment():
    import mongomock.gridfs
    mongomock.gridfs.enable_gridfs_integration()
    fake = types.ModuleType("db")
    wrapped = Database()
    fake.db = wrapped.raw
    with patch.dict(sys.modules, {"db": fake}):
        checkout_spec = importlib.util.spec_from_file_location("checkout", Path("checkout.py"))
        checkout = importlib.util.module_from_spec(checkout_spec)
        checkout_spec.loader.exec_module(checkout)
        with patch.dict(sys.modules, {"checkout": checkout}):
            spec = importlib.util.spec_from_file_location("manual_store_test", Path("routes/store_page.py"))
            store = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(store)
    store.db = wrapped
    store.first_blocked_phone = lambda phones: None
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(checkout.checkout_bp)
    uid = ObjectId()
    fake.db.stores.insert_one({"slug": "demo", "owner_id": uid})
    sid = fake.db.services.insert_one({"name":"MTN NORMAL","type":"API","provider":"codecraft","network_id":3,
        "offers":[{"value":{"id":1,"volume":1000},"amount":9,"customer_price":10}]}).inserted_id
    item = {"serviceId":str(sid),"phone":"0241234567","amount":10,"base_amount":9,"value_obj":{"id":1,"volume":1000}}
    accepted = types.SimpleNamespace(status_code=200,json=lambda:{"status":200,"reference_id":"API-ref","order_id":"NAN1"})
    verified = (True,{"status":"success","amount":1000,"currency":"GHS","channel":"mobile_money"},"ok",{})
    with app.test_request_context("/"), patch.object(store,"_canonical_store_total_for_offer",return_value=10), patch.object(store,"_canonical_store_base_for_offer",return_value=9), patch.object(store,"_verify_paystack",return_value=verified), patch.object(nagonu_orders.requests,"post",return_value=accepted) as post:
        response,status=store._store_checkout_handler("demo",{"method":"paystack_inline","cart":[item],"paystack":{"reference":"PS-test"}})
        assert status==200,response.json
        assert response.json['success']
        store._store_checkout_handler("demo",{"method":"paystack_inline","cart":[item],"paystack":{"reference":"PS-test"}})
        assert post.call_count==1
    assert fake.db.orders.find_one()['items'][0]['provider']=='nagonu'
    assert fake.db.store_accounts.find_one()['total_profit_balance']==1
    assert fake.db.provider_transactions.count_documents({})==0

"""Digital stock delivery, access control and atomic wallet purchases."""
import importlib.util
import sys
import types
from unittest.mock import patch
from pathlib import Path
import pytest
from bson import ObjectId
from flask import Flask
from test_applications import Database

@pytest.fixture
def shop():
    db=Database();fake=types.ModuleType('db');fake.db=db;modules={}
    with patch.dict(sys.modules,{'db':fake}):
        for name in ['form_services','applications','checker_services']:
            spec=importlib.util.spec_from_file_location(name,name+'.py');module=importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules,modules):spec.loader.exec_module(module)
            modules[name]=module
    m=modules['checker_services'];app=Flask(__name__,template_folder=str(Path('templates').resolve()));app.secret_key='test';app.register_blueprint(m.checker_services_bp)
    from jinja2 import ChoiceLoader,DictLoader
    app.jinja_loader=ChoiceLoader([DictLoader({'admin_sidebar.html':'','customer_sidebar.html':''}),app.jinja_loader])
    for endpoint in ['purchases.view_purchases','customer_dashboard.customer_dashboard','admin_services.manage_services','login.login']:
        app.add_url_rule('/test/'+endpoint,endpoint,lambda:'ok')
    uid=ObjectId();sid=ObjectId()
    db.services.insert_one({'_id':sid,'slug':m.SLUG,'name':'Checkers and Forms','purchase_mode':'checker','status':'OPEN','availability':'AVAILABLE','image_url':''})
    db.checker_types.insert_one({'_id':'bece','service_id':sid,'name':'BECE','price':25,'enabled':True,'version':1})
    db.wassce_checker.insert_one({'type_id':'bece','message':'PRIVATE PIN 12345','status':'not_sold'})
    db.balances.insert_one({'user_id':uid,'amount':100})
    c=app.test_client()
    with c.session_transaction() as s:s.update(role='customer',user_id=str(uid),form_service_csrf='csrf')
    return m,db,c,uid,sid

def test_atomic_purchase_once(shop):
    m,db,c,uid,sid=shop
    for _ in range(2):m.buy(sid,uid,'bece',1,'a'*32)
    assert db.balances.find_one()['amount']==75
    assert db.purchase_history.count_documents({})==db.transactions.count_documents({})==1
    assert db.purchase_history.find_one()['message']=='PRIVATE PIN 12345'
    assert db.wassce_checker.find_one()['status']=='sold'
    with pytest.raises(ValueError):m.buy(sid,uid,'bece',1,'b'*32)
    assert db.balances.find_one()['amount']==75

@pytest.mark.parametrize('case',['funds','stale','closed','disabled','missing','token'])
def test_invalid_purchase_rolls_back(shop,case):
    m,db,c,uid,sid=shop;version=1;token='a'*32
    if case=='funds':db.balances.update_one({}, {'$set':{'amount':0}})
    if case=='stale':version=0
    if case=='closed':db.services.update_one({}, {'$set':{'status':'CLOSED'}})
    if case=='disabled':db.checker_types.update_one({}, {'$set':{'enabled':False}})
    if case=='missing':db.wassce_checker.delete_many({})
    if case=='token':token='wrong'
    before=db.balances.find_one()['amount']
    with pytest.raises(ValueError):m.buy(sid,uid,'bece',version,token)
    assert db.balances.find_one()['amount']==before
    assert db.purchase_history.count_documents({})==0
    assert db.wassce_checker.count_documents({'status':'sold'})==0

def test_customer_page_hides_stock_messages_and_admin_routes(shop):
    m,db,c,uid,sid=shop
    r=c.get('/services/checkers-and-forms');assert r.status_code==200
    assert b'PRIVATE PIN' not in r.data and b'25.00' in r.data and b'BECE' in r.data
    assert c.get('/admin/services/checkers-and-forms').status_code==403
    assert c.post('/services/checkers-and-forms',data={'type_id':'bece'}).status_code==400

def test_admin_type_and_stock_management(shop):
    m,db,c,uid,sid=shop
    with c.session_transaction() as s:s['role']='admin'
    data={'csrf':'csrf','action':'add_type','name':'Uni Forms','price':'60','enabled':'on'}
    c.post('/admin/services/checkers-and-forms',data=data)
    kind=db.checker_types.find_one({'name':'Uni Forms'});assert kind['price']==60
    for _ in range(2):c.post('/admin/services/checkers-and-forms',data={'csrf':'csrf','action':'add_stock','type_id':kind['_id'],'message':'UNIQUE VOUCHER','stock_token':'a'*32})
    assert db.wassce_checker.count_documents({'type_id':kind['_id']})==1
    assert c.get('/admin/services/checkers-and-forms').status_code==200
    c.post('/admin/services/checkers-and-forms',data={**data,'name':'Invalid','price':'NaN'})
    assert db.checker_types.count_documents({'name':'Invalid'})==0
    m.buy(sid,uid,'bece',1,'a'*32)
    item=db.wassce_checker.find_one({'type_id':'bece'})
    c.post('/admin/services/checkers-and-forms',data={'csrf':'csrf','action':'edit_stock','type_id':'bece','stock_id':str(item['_id']),'message':'REPLACEMENT'})
    assert db.wassce_checker.find_one({'_id':item['_id']})['message']=='PRIVATE PIN 12345'

def test_delivery_failure_rolls_back_stock_and_wallet(shop):
    m,db,c,uid,sid=shop
    with patch.object(db.raw.purchase_history,'insert_one',side_effect=RuntimeError):
        with pytest.raises(RuntimeError):m.buy(sid,uid,'bece',1,'a'*32)
    assert db.balances.find_one()['amount']==100
    assert db.wassce_checker.find_one()['status']=='not_sold'

def test_seed_is_repeatable_and_preserves_legacy_stock(shop):
    m,db,c,uid,sid=shop
    fake=types.ModuleType('db');fake.db=db.raw
    fake_form=types.ModuleType('form_services');fake_form.parse_price=m.parse_price
    with patch.dict(sys.modules,{'db':fake,'form_services':fake_form}):
        spec=importlib.util.spec_from_file_location('seed_checker_service','seed_checker_service.py');seed=importlib.util.module_from_spec(spec);spec.loader.exec_module(seed)
    db.wassce_checker.insert_one({'type':'wassce','amount':20,'message':'LEGACY','status':'not_sold'})
    db.wassce_checker.insert_one({'type':'wassce','amount':30,'message':'LEGACY PREMIUM','status':'not_sold'})
    db.wassce_checker.insert_one({'type':'bece','amount':20,'message':'SOLD','status':'sold'})
    seed.seed();seed.seed()
    assert db.services.count_documents({'slug':m.SLUG})==1
    assert db.checker_types.count_documents({'service_id':sid})==3
    assert db.wassce_checker.count_documents({'type_id':{'$exists':True}})==3
    assert db.wassce_checker.find_one({'message':'SOLD'}).get('type_id') is None

def test_purchase_delivery_popup_is_owned_and_shown_once(shop):
    m,db,c,uid,sid=shop
    fake=types.ModuleType('db');fake.db=db.raw
    with patch.dict(sys.modules,{'db':fake}):
        spec=importlib.util.spec_from_file_location('purchases','purchases.py');history=importlib.util.module_from_spec(spec);spec.loader.exec_module(history)
    app=Flask(__name__);app.secret_key='test';app.register_blueprint(m.checker_services_bp);app.register_blueprint(history.purchases_bp)
    client=app.test_client()
    with client.session_transaction() as s:s.update(role='customer',user_id=str(uid),form_service_csrf='csrf')
    response=client.post('/services/checkers-and-forms',data={'csrf':'csrf','type_id':'bece','version':'1','purchase_token':'a'*32})
    assert response.location.endswith('/purchases')
    with patch.object(history,'render_template',return_value='ok') as render:
        client.get('/purchases')
        assert render.call_args.kwargs['delivery']['message']=='PRIVATE PIN 12345'
        client.get('/purchases')
        assert render.call_args.kwargs['delivery'] is None
        with client.session_transaction() as s:s.update(user_id=str(ObjectId()),new_checker_purchase=db.purchase_history.find_one()['_id'])
        client.get('/purchases')
        assert render.call_args.kwargs['delivery'] is None
        assert render.call_args.kwargs['purchases']==[]

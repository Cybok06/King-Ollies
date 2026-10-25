import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch
import pytest
from flask import Flask
from bson import ObjectId
from test_applications import Database

@pytest.fixture
def fee():
    db=Database();modules={'db':types.SimpleNamespace(db=db)}
    for name in ['form_services','applications','signup_fee']:
        spec=importlib.util.spec_from_file_location(name,name+'.py');m=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,modules):spec.loader.exec_module(m)
        modules[name]=m
    app=Flask(__name__,template_folder=str(Path('templates').resolve()));app.secret_key='test'
    app.register_blueprint(m.signup_fee_bp)
    app.add_url_rule('/customer_dashboard','customer_dashboard.customer_dashboard',lambda:'ACCESS')
    app.add_url_rule('/private','private',view_func=lambda:'ACCESS')
    app.add_url_rule('/api/action','action',view_func=lambda:'ACCESS',methods=['POST'])
    app.add_url_rule('/logout','login.logout',lambda:'LOGOUT')
    uid=ObjectId();db.users.insert_one({'_id':uid,'email':'test@example.com','signup_fee_amount':20,'signup_fee_status':'unpaid'})
    c=app.test_client()
    with c.session_transaction() as s:s.update(role='customer',user_id=str(uid),form_service_csrf='csrf')
    return m,db,c,uid

def paid(uid):
    return {'status':'success','reference':'SIGNUP-'+str(uid),'amount':2000,'currency':'GHS',
            'metadata':{'source':'signup_fee','user_id':str(uid)},'customer':{'email':'test@example.com'}}

def test_gate_cannot_be_bypassed(fee):
    m,db,c,uid=fee
    assert b'One Time Sign Up fee' in c.get('/customer_dashboard').data
    assert c.get('/private').location.endswith('/customer_dashboard')
    assert c.post('/api/action',json={}).status_code==402
    assert c.get('/logout').data==b'LOGOUT'
    assert c.post('/signup-fee/pay').status_code==400
    db.users.update_one({}, {'$unset':{'signup_fee_status':''}})
    assert c.get('/private').data==b'ACCESS'

def test_paid_once_and_no_wallet_credit(fee):
    m,db,c,uid=fee;user=db.users.find_one()
    m.settle(user,paid(uid));m.settle(user,paid(uid))
    assert db.transactions.count_documents({})==1
    assert db.balances.count_documents({})==0
    assert c.get('/private').data==b'ACCESS'

@pytest.mark.parametrize('key,value',[('amount',1),('reference','OTHER'),('currency','USD'),('status','pending'),('metadata',{}),('customer',{'email':'other@example.com'})])
def test_mismatched_payment_does_not_unlock(fee,key,value):
    m,db,c,uid=fee;data=paid(uid);data[key]=value
    with pytest.raises(ValueError):m.settle(db.users.find_one(),data)
    assert db.users.find_one()['signup_fee_status']=='unpaid'

def test_transaction_failure_keeps_gate(fee):
    m,db,c,uid=fee
    with patch.object(db.raw.transactions,'insert_one',side_effect=RuntimeError):
        with pytest.raises(RuntimeError):m.settle(db.users.find_one(),paid(uid))
    assert db.users.find_one()['signup_fee_status']=='unpaid'

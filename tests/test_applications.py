"""Application authorization, quoting, conversations and atomic payment tests."""
import importlib.util
import sys
import types
from contextlib import nullcontext
from copy import deepcopy
from unittest.mock import patch
import pytest
import mongomock
from PIL import Image
from bson import ObjectId
from flask import Flask

class Collection:
    def __init__(self,raw):self.raw=raw
    def __getattr__(self,name):
        def call(*args,**kwargs):
            kwargs.pop('session',None)
            return getattr(self.raw,name)(*args,**kwargs)
        return call
class Transaction:
    def __init__(self,db):self.db=db
    def with_transaction(self,fn):
        snapshot={name:list(self.db.raw[name].find()) for name in self.db.raw.list_collection_names()}
        try:return fn(self)
        except Exception:
            for name in self.db.raw.list_collection_names():self.db.raw.drop_collection(name)
            for name,docs in snapshot.items():
                if docs:self.db.raw[name].insert_many(deepcopy(docs))
            raise
class Database:
    def __init__(self):
        self.raw=mongomock.MongoClient().test
        self.client=types.SimpleNamespace(start_session=lambda:nullcontext(Transaction(self)))
    def __getattr__(self,name):return Collection(self.raw[name])

@pytest.fixture
def flow():
    db=Database();fake=types.ModuleType('db');fake.db=db
    modules={}
    with patch.dict(sys.modules,{'db':fake}):
        for name in ['form_services','applications']:
            spec=importlib.util.spec_from_file_location(name,name+'.py');module=importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules,modules):spec.loader.exec_module(module)
            modules[name]=module
    module=modules['applications']
    app=Flask(__name__);app.secret_key='test';app.register_blueprint(module.applications_bp)
    uid=ObjectId();aid=ObjectId()
    db.users.insert_one({'_id':uid,'email':'customer@example.com'})
    db.balances.insert_one({'user_id':uid,'amount':100})
    db.service_applications.insert_one({'_id':aid,'user_id':uid,'service_id':ObjectId(),'service_name':'Application',
        'answers':[{'key':'name','label':'Name','value':'Customer'}],'status':'submitted','quote_amount':None,'quote_version':0})
    client=app.test_client()
    with client.session_transaction() as session:session.update(role='customer',user_id=str(uid),form_service_csrf='csrf')
    return module,db,client,uid,aid

def quoted(db,aid):
    db.service_applications.update_one({'_id':aid},{'$set':{'status':'quoted','quote_amount':25,'quote_version':1}})

def pending(db,aid):
    quoted(db,aid)
    db.service_applications.update_one({'_id':aid},{'$set':{'status':'payment_pending','payment_reference':'APP-test','payment_email':'customer@example.com'}})
    return {'status':'success','reference':'APP-test','currency':'GHS','amount':2500,'customer':{'email':'customer@example.com'}}

def test_wallet_settlement_once(flow):
    m,db,c,uid,aid=flow;quoted(db,aid)
    for _ in range(2):m.settle(aid,'Wallet',expected_version=1)
    assert db.balances.find_one()['amount']==75
    assert db.orders.count_documents({})==db.transactions.count_documents({})==1
    assert db.service_applications.find_one()['status']=='paid'

def test_transaction_failure_rolls_back_debit(flow):
    m,db,c,uid,aid=flow;quoted(db,aid)
    db.orders.insert_one({'_id':aid})
    with pytest.raises(Exception):m.settle(aid,'Wallet',expected_version=1)
    assert db.balances.find_one()['amount']==100
    assert db.service_applications.find_one()['status']=='quoted'
    assert db.transactions.count_documents({})==0

@pytest.mark.parametrize('case',['funds','stale','pending','unquoted'])
def test_wallet_rejects_invalid_states(flow,case):
    m,db,c,uid,aid=flow;quoted(db,aid)
    version=1
    if case=='funds':db.balances.update_one({'user_id':uid},{'$set':{'amount':1}})
    if case=='stale':version=0
    if case=='pending':pending(db,aid)
    if case=='unquoted':db.service_applications.update_one({'_id':aid},{'$set':{'status':'submitted'}})
    before=db.balances.find_one()['amount']
    with pytest.raises(ValueError):m.settle(aid,'Wallet',expected_version=version)
    assert db.balances.find_one()['amount']==before
    assert db.orders.count_documents({})==0

def test_paystack_settlement_once_without_wallet_debit(flow):
    m,db,c,uid,aid=flow;data=pending(db,aid)
    for _ in range(2):m.settle(aid,'Paystack',reference='APP-test',verified=data)
    assert db.balances.find_one()['amount']==100
    assert db.orders.count_documents({})==db.transactions.count_documents({})==1

@pytest.mark.parametrize('field,value',[('amount',2400),('currency','NGN'),('reference','wrong'),('status','pending'),('customer',{'email':'other@example.com'})])
def test_mismatched_paystack_details_rejected(flow,field,value):
    m,db,c,uid,aid=flow;data=pending(db,aid);data[field]=value
    with pytest.raises(ValueError):m.settle(aid,'Paystack',reference='APP-test',verified=data)
    assert db.orders.count_documents({})==0
    assert db.service_applications.find_one()['status']=='payment_pending'

def test_admin_quote_and_customer_cannot_quote(flow):
    m,db,c,uid,aid=flow
    payload={'csrf':'csrf','amount':'25','quote_version':'0'}
    assert c.post(f'/applications/{aid}/quote',data=payload).status_code==403
    with c.session_transaction() as session:session['role']='admin'
    assert c.post(f'/applications/{aid}/quote',data=payload).status_code==302
    assert db.service_applications.find_one()['quote_amount']==25
    pending(db,aid)
    payload.update(amount='40',quote_version='1')
    c.post(f'/applications/{aid}/quote',data=payload)
    assert db.service_applications.find_one()['quote_amount']==25

def test_ownership_and_csrf(flow):
    m,db,c,uid,aid=flow
    assert c.post(f'/applications/{aid}/pay/wallet',data={'csrf':'wrong'}).status_code==400
    with c.session_transaction() as session:session['user_id']=str(ObjectId())
    for path in ['', '/paystack/return']:
        assert c.get(f'/applications/{aid}'+path).status_code==403
    assert c.post(f'/applications/{aid}/messages',data={'csrf':'csrf','message':'x','message_token':'a'*32}).status_code==403

def test_messages_two_way_and_deduplicated(flow):
    m,db,c,uid,aid=flow
    payload={'csrf':'csrf','message':'Question','message_token':'a'*32}
    for _ in range(2):assert c.post(f'/applications/{aid}/messages',data=payload).status_code==302
    with c.session_transaction() as session:session['role']='admin'
    payload['message']='Reply'
    assert c.post(f'/applications/{aid}/messages',data=payload).status_code==302
    assert db.application_messages.count_documents({})==2
    assert {d['sender_role'] for d in db.application_messages.find()}=={'customer','admin'}

def test_paystack_initialize_uses_quote_and_reuses_session(flow):
    m,db,c,uid,aid=flow;quoted(db,aid)
    response=types.SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'status':True,'data':{'authorization_url':'https://checkout.paystack.com/test'}})
    with patch.object(m,'secret_key',return_value='test'),patch.object(m.requests,'post',return_value=response) as post:
        for _ in range(2):
            r=c.post(f'/applications/{aid}/pay/paystack',data={'csrf':'csrf','quote_version':'1','amount':'1'})
            assert r.location=='https://checkout.paystack.com/test'
        assert post.call_count==1
        assert post.call_args.kwargs['json']['amount']==2500
        assert post.call_args.kwargs['json']['reference'].startswith('APP-')

def test_invalid_webhook_signature(flow):
    m,db,c,uid,aid=flow
    with patch.object(m,'secret_key',return_value='test'):
        assert c.post('/webhooks/paystack/applications',json={'event':'charge.success'}).status_code==401

def test_verified_callback_and_webhook_are_idempotent(flow):
    import hashlib,hmac,json
    m,db,c,uid,aid=flow;data=pending(db,aid)
    response=types.SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'status':True,'data':data})
    payload=json.dumps({'event':'charge.success','data':{'reference':'APP-test'}}).encode()
    signature=hmac.new(b'test',payload,hashlib.sha512).hexdigest()
    with patch.object(m,'secret_key',return_value='test'),patch.object(m.requests,'get',return_value=response):
        assert c.get(f'/applications/{aid}/paystack/return?reference=untrusted').status_code==302
        assert c.post('/webhooks/paystack/applications',data=payload,content_type='application/json',headers={'x-paystack-signature':signature}).status_code==200
    assert db.orders.count_documents({})==1
    assert db.transactions.count_documents({})==1
    assert db.balances.find_one()['amount']==100

def test_gateway_outage_keeps_reference_for_retry(flow):
    m,db,c,uid,aid=flow;quoted(db,aid)
    with patch.object(m,'secret_key',return_value='test'),patch.object(m.requests,'post',side_effect=TimeoutError):
        c.post(f'/applications/{aid}/pay/paystack',data={'csrf':'csrf','quote_version':'1'})
        reference=db.service_applications.find_one()['payment_reference']
        c.post(f'/applications/{aid}/pay/paystack',data={'csrf':'csrf'})
        assert db.service_applications.find_one()['payment_reference']==reference
    assert db.balances.find_one()['amount']==100
    assert db.orders.count_documents({})==0

def manual_quote(flow):
    m,db,c,uid,aid=flow
    with c.session_transaction() as s:s['role']='admin'
    c.post(f'/applications/{aid}/quote',data={'csrf':'csrf','quote_version':0,'amount':25,'payment_mode':'manual','momo_number':'0556531766','momo_network':'MTN','momo_name':'King Ollie'})
    with c.session_transaction() as s:s['role']='customer'

def receipt():
    import io
    from PIL import Image
    f=io.BytesIO();Image.new('RGB',(20,20)).save(f,format='PNG');f.seek(0)
    return (f,'proof.png')

def test_manual_receipt_and_admin_confirmation(flow):
    m,db,c,uid,aid=flow;manual_quote(flow)
    c.post(f'/applications/{aid}/pay/manual',data={'csrf':'csrf','quote_version':1,'proof':receipt()})
    assert db.service_applications.find_one()['status']=='manual_review'
    assert db.orders.count_documents({})==0
    assert c.get(f'/applications/{aid}/proof').status_code==200
    assert c.post(f'/applications/{aid}/pay/manual/review',data={'csrf':'csrf','quote_version':1,'decision':'confirm'}).status_code==403
    with c.session_transaction() as s:s['role']='admin'
    for _ in range(2):c.post(f'/applications/{aid}/pay/manual/review',data={'csrf':'csrf','quote_version':1,'decision':'confirm'})
    assert db.service_applications.find_one()['status']=='paid'
    assert db.orders.count_documents({})==db.transactions.count_documents({})==1
    assert db.balances.find_one()['amount']==100

def test_manual_blocks_online_and_private_proof(flow):
    m,db,c,uid,aid=flow;manual_quote(flow)
    with pytest.raises(ValueError):m.settle(aid,'Wallet',expected_version=1)
    with patch.object(m.requests,'post') as post:
        c.post(f'/applications/{aid}/pay/paystack',data={'csrf':'csrf','quote_version':1})
        post.assert_not_called()
    c.post(f'/applications/{aid}/pay/manual',data={'csrf':'csrf','quote_version':1,'proof':receipt()})
    with c.session_transaction() as s:s['user_id']=str(ObjectId())
    assert c.get(f'/applications/{aid}/proof').status_code==403

def test_manual_reject_and_resubmit(flow):
    m,db,c,uid,aid=flow;manual_quote(flow)
    c.post(f'/applications/{aid}/pay/manual',data={'csrf':'csrf','quote_version':1,'proof':receipt()})
    with c.session_transaction() as s:s['role']='admin'
    c.post(f'/applications/{aid}/pay/manual/review',data={'csrf':'csrf','quote_version':1,'decision':'reject'})
    assert db.service_applications.find_one()['quote_version']==2
    with c.session_transaction() as s:s['role']='customer'
    c.post(f'/applications/{aid}/pay/manual',data={'csrf':'csrf','quote_version':1,'proof':receipt()})
    assert db.service_applications.find_one()['status']=='quoted'
    c.post(f'/applications/{aid}/pay/manual',data={'csrf':'csrf','quote_version':2,'proof':receipt()})
    assert db.service_applications.find_one()['status']=='manual_review'

def test_manual_invalid_proof_and_missing_details(flow):
    import io
    m,db,c,uid,aid=flow
    with c.session_transaction() as s:s['role']='admin'
    c.post(f'/applications/{aid}/quote',data={'csrf':'csrf','quote_version':0,'amount':25,'payment_mode':'manual'})
    assert db.service_applications.find_one()['status']=='submitted'
    manual_quote(flow)
    for data in ({'csrf':'wrong','quote_version':1,'proof':receipt()}, {'csrf':'csrf','quote_version':1,'proof':(io.BytesIO(b'<script>bad</script>'),'proof.png')}):
        c.post(f'/applications/{aid}/pay/manual',data=data)
    assert db.service_applications.find_one()['status']=='quoted'
    assert db.application_proofs.count_documents({})==0

def test_manual_template_controls(flow):
    from jinja2 import Environment, FileSystemLoader, ChoiceLoader, DictLoader
    m,db,c,uid,aid=flow;manual_quote(flow)
    env=Environment(loader=ChoiceLoader([DictLoader({'admin_sidebar.html':'','customer_sidebar.html':''}),FileSystemLoader('templates')]),autoescape=True)
    env.globals.update(url_for=lambda endpoint,**kw:'/'+endpoint,get_flashed_messages=lambda **kw:[])
    template=env.get_template('application_detail.html')
    for admin in (False,True):
        html=template.render(application=db.service_applications.find_one(),is_admin=admin,customer={},messages=[],balance=100,csrf='csrf',message_token='a'*32)
        if admin:assert 'name="payment_mode"' in html and 'momo_number' in html
        else:
            assert 'https://wa.me/233556531766' in html
            assert 'Upload proof &amp; Mark as Paid' in html
            assert 'Pay from wallet' not in html
            assert 'data-copy="copy-number"' in html and 'data-copy="copy-name"' in html
    c.post(f'/applications/{aid}/pay/manual',data={'csrf':'csrf','quote_version':1,'proof':receipt()})
    html=template.render(application=db.service_applications.find_one(),is_admin=True,customer={},messages=[],csrf='csrf')
    assert 'Confirm payment' in html and 'View uploaded payment proof' in html

def test_application_pages_scope_counts_and_order_completion(flow):
    m,db,c,uid,aid=flow
    other=ObjectId()
    db.service_applications.insert_one({'_id':other,'user_id':ObjectId(),'service_name':'Other customer','status':'submitted'})
    paid=ObjectId()
    db.service_applications.insert_one({'_id':paid,'user_id':uid,'service_name':'Finished service','status':'paid','order_id':'DONE'})
    db.orders.insert_one({'order_id':'DONE','status':'completed'})
    with patch.object(m,'render_template',return_value='ok') as render:
        assert c.get('/applications').status_code==200
        data=render.call_args.kwargs
        assert data['ongoing_count']==1 and data['closed_count']==1 and data['total_count']==2
        assert [a['_id'] for a in data['applications']]==[aid]
        c.get('/applications?view=closed')
        assert render.call_args.kwargs['applications'][0]['progress_status']=='completed'
        assert c.get('/admin/applications').status_code==403
        with c.session_transaction() as s:s['role']='admin'
        assert c.get('/admin/applications?view=all').status_code==200
        assert render.call_args.kwargs['total_count']==3
        assert render.call_args.kwargs['ongoing_count']==2
        assert c.get('/applications').location.endswith('/admin/applications')
    with c.session_transaction() as s:s.clear()
    assert c.get('/applications').status_code==403


def test_sidebar_application_attention_counts(flow):
    from datetime import datetime
    m,db,c,uid,aid=flow
    def count(role='customer'):
        with c.application.test_request_context('/'):
            from flask import session
            session.update(role=role,user_id=str(uid))
            return m.sidebar_application_counts()['sidebar_application_count']
    assert count()==0 and count('admin')==1
    # Other customers' messages never contribute.
    db.application_messages.insert_one({'application_id':ObjectId(),'sender_role':'admin','text':'Other'})
    mid=db.application_messages.insert_one({'application_id':aid,'sender_role':'admin','text':'Please review','created_at':datetime.utcnow()}).inserted_id
    assert count()==1
    with patch.object(m,'render_template',return_value='ok'):
        assert c.get(f'/applications/{aid}').status_code==200
    assert db.application_messages.find_one({'_id':mid}).get('customer_read_at')
    assert count()==0 and count('admin')==1
    quoted(db,aid)
    assert count()==1
    # A quote plus a reply is one application, not two notifications.
    db.application_messages.insert_one({'application_id':aid,'sender_role':'admin','text':'Invoice issued','created_at':datetime.utcnow()})
    assert count()==1
    with patch.object(m,'render_template',return_value='ok'):c.get(f'/applications/{aid}')
    assert count()==1  # Invoice still needs payment after reading.
    db.service_applications.update_one({'_id':aid},{'$set':{'status':'paid','order_id':'DONE'}})
    db.orders.insert_one({'order_id':'DONE','status':'delivered'})
    assert count()==0 and count('admin')==0

"""Nagonu payment reservation, saved-offer mapping and at-most-once submission."""
from unittest.mock import patch
import types
from datetime import datetime,timedelta
import pytest
from bson import ObjectId
from flask import Flask
import requests
from test_applications import Database
import nagonu_orders as n

@pytest.fixture
def flow():
    db=Database();uid=ObjectId();sid=ObjectId()
    db.users.insert_one({'_id':uid,'customer_stage':'premium'})
    db.balances.insert_one({'user_id':uid,'amount':100})
    db.services.insert_one({'_id':sid,'name':'MTN NORMAL','status':'OPEN','availability':'AVAILABLE',
        'offers':[{'value':"{'id': 1, 'volume': 1000}",'amount':3.9,'customer_price':4.5,'stage_prices':{'premium':4}}],
        'store_offers':[{'value':"{'id': 1, 'volume': 1000}",'amount':4,'customer_price':4.5}]})
    cart=[{'serviceId':str(sid),'phone':'0554226398','value_obj':{'id':1,'volume':1000},'amount':4,'base_amount':0.01}]
    return db,uid,sid,cart

def response(payload,http=200):return types.SimpleNamespace(status_code=http,json=lambda:payload)
def accepted():return response({'status':200,'reference_id':'API-example','order_id':'NAN123'})

def reserve(flow,store=None,ref='checkout-one'):
    db,uid,sid,cart=flow
    if store:lines=n.prepare_cart(db,cart,store=store,store_price=lambda *a:5,store_base=lambda *a:4)
    else:lines=n.prepare_cart(db,cart,user_id=uid)
    key=n.batch_key('store' if store else 'wallet',uid,ref)
    return n.reserve(db,lines,uid,key,ref,store=store)

def finish(db,batch):
    with Flask(__name__).app_context():
        result,status=n.checkout_response(db,batch)
        return result.get_json(),status

def test_mapping_server_prices_and_single_submission(flow):
    db,uid,sid,cart=flow
    batch=reserve(flow)
    assert db.balances.find_one()['amount']==96
    with patch.object(n.requests,'post',return_value=accepted()) as post:
        result,status=finish(db,batch);again=reserve(flow);finish(db,again)
        assert post.call_count==1
        assert post.call_args.kwargs['json']=={'recipient_number':'0554226398','service_name':'MTN NORMAL','network':'MTN','offer_id':1}
        assert post.call_args.kwargs['timeout']==(5,60)
        assert post.call_args.kwargs['headers']['Content-Type']=='application/json'
    assert result['success'] and status==200
    assert db.balances.find_one()['amount']==96
    assert db.orders.find_one()['items'][0]['provider_reference_id']=='API-example'
    assert db.orders.count_documents({})==1

@pytest.mark.parametrize('name,offer,network,category',[('MTN EXPRESS',1,'MTN','regular'),('TELECEL',24,'TELECEL','bigtime')])
def test_other_networks(flow,name,offer,network,category):
    db,uid,sid,cart=flow
    db.services.update_one({'_id':sid},{'$set':{'name':name,'offers':[{'value':{'id':offer,'volume':1000},'amount':4}]}})
    cart[0]['value_obj']['id']=offer
    line=n.prepare_cart(db,cart,user_id=uid)[0]
    assert line['provider_payload']['network']==network
    assert line['provider_payload']['offer_id']==offer
    assert line['provider_category']==category

@pytest.mark.parametrize('case',['price','id','volume','closed','missing','duplicate','phone'])
def test_bad_cart_never_debits_or_sends(flow,case):
    db,uid,sid,cart=flow
    if case=='price':cart[0]['amount']=0.01
    if case=='id':cart[0]['value_obj']['id']=999
    if case=='volume':cart[0]['value_obj']['volume']=99999
    if case=='closed':db.services.update_one({}, {'$set':{'status':'CLOSED'}})
    if case=='missing':cart[0]['serviceId']=str(ObjectId())
    if case=='duplicate':cart.append(dict(cart[0]))
    if case=='phone':cart[0]['phone']='wrong'
    with pytest.raises(ValueError):n.prepare_cart(db,cart,user_id=uid)
    assert db.balances.find_one()['amount']==100

def test_insufficient_funds_transaction_rolls_back(flow):
    db,uid,sid,cart=flow;db.balances.update_one({}, {'$set':{'amount':1}})
    with pytest.raises(ValueError):reserve(flow)
    assert db.orders.count_documents({})==db.nagonu_recipient_locks.count_documents({})==0

def test_explicit_rejection_refunds_once(flow):
    db,uid,sid,cart=flow;batch=reserve(flow)
    with patch.object(n.requests,'post',return_value=response({'status':100})) as post:
        result,status=finish(db,batch);finish(db,batch)
        assert post.call_count==1
    assert not result['success'] and status==202
    assert db.balances.find_one()['amount']==100
    assert db.orders.find_one()['status']=='refunded'
    assert db.transactions.count_documents({'type':'refund'})==1

@pytest.mark.parametrize('outcome',['timeout','500','invalid','missing_ref'])
def test_uncertain_response_not_retried_or_refunded(flow,outcome):
    db,uid,sid,cart=flow;batch=reserve(flow)
    mock={'return_value':response({'status':500})}
    if outcome=='timeout':mock={'side_effect':requests.Timeout()}
    if outcome=='invalid':mock={'return_value':types.SimpleNamespace(status_code=200,json=lambda:(_ for _ in ()).throw(ValueError()))}
    if outcome=='missing_ref':mock={'return_value':response({'status':200,'order_id':'NAN'})}
    with patch.object(n.requests,'post',**mock) as post:
        result,status=finish(db,batch);finish(db,batch);assert post.call_count==1
    assert not result['success'] and result['order_recorded']
    assert db.orders.find_one()['nagonu_state']=='unknown'
    diagnostic=db.orders.find_one()['items'][0]['api_response']
    assert diagnostic['reason']=={'timeout':'timeout','500':'provider_error','invalid':'invalid_json','missing_ref':'missing_order_identifiers'}[outcome]
    assert db.balances.find_one()['amount']==96
    with pytest.raises(ValueError):reserve(flow,ref='new-attempt')

def test_store_acceptance_profit_once_and_no_wallet_debit(flow):
    db,uid,sid,cart=flow;batch=reserve(flow,store={'slug':'shop','owner_id':uid})
    assert db.balances.find_one()['amount']==100
    with patch.object(n.requests,'post',return_value=accepted()):finish(db,batch);finish(db,batch)
    assert db.store_accounts.find_one()['total_profit_balance']==1
    with pytest.raises(ValueError):reserve(flow,store={'slug':'another','owner_id':uid})

def test_store_rejection_needs_refund_review_without_profit(flow):
    db,uid,sid,cart=flow;batch=reserve(flow,store={'slug':'shop','owner_id':uid})
    with patch.object(n.requests,'post',return_value=response({'status':101})):finish(db,batch)
    assert db.orders.find_one()['refund_review_required']
    assert db.store_accounts.count_documents({})==0
    assert db.balances.find_one()['amount']==100

def test_partial_cart_tracks_each_line(flow):
    db,uid,sid,cart=flow;cart.append({**cart[0],'phone':'0554226399'})
    batch=reserve(flow)
    def send(*args,**kwargs):return accepted() if kwargs['json']['recipient_number'].endswith('8') else response({'status':101})
    with patch.object(n.requests,'post',side_effect=send):result,status=finish(db,batch)
    assert not result['success'] and len(result['order_ids'])==2
    assert db.balances.find_one()['amount']==96
    assert db.orders.count_documents({'nagonu_state':'accepted'})==1
    assert db.orders.count_documents({'nagonu_state':'rejected'})==1

def test_admin_reconciliation_requires_auth_csrf_and_refunds_once(flow):
    import sys,importlib.util
    db,uid,sid,cart=flow;batch=reserve(flow)
    with patch.object(n.requests,'post',side_effect=requests.Timeout()):finish(db,batch)
    fake=types.ModuleType('db');fake.db=db
    with patch.dict(sys.modules,{'db':fake}):
        spec=importlib.util.spec_from_file_location('applications','applications.py');applications=importlib.util.module_from_spec(spec);spec.loader.exec_module(applications)
    app=Flask(__name__);app.secret_key='test';app.register_blueprint(n.nagonu_bp)
    app.add_url_rule('/invoice/<order_id>','checkout.invoice_view',lambda order_id:'ok')
    client=app.test_client();url=f"/admin/nagonu/{batch['order_ids'][0]}/reconcile"
    data={'csrf':'csrf','note':'Provider confirmed no order was created','confirmed':'yes','decision':'rejected'}
    with patch.dict(sys.modules,{'db':fake,'applications':applications}):
        assert client.post(url,data=data).status_code==403
        with client.session_transaction() as s:s.update(role='admin',user_id=str(uid),form_service_csrf='csrf')
        assert client.post(url,data={**data,'csrf':'wrong'}).status_code==400
        assert client.post(url,data=data).status_code==302
        assert client.post(url,data=data).status_code==409
    assert db.balances.find_one()['amount']==100
    assert db.transactions.count_documents({'type':'refund'})==1

def test_reservation_insert_failure_restores_wallet(flow):
    db,uid,sid,cart=flow
    with patch.object(db.raw.orders,'insert_one',side_effect=RuntimeError('storage unavailable')):
        with pytest.raises(RuntimeError):reserve(flow)
    assert db.balances.find_one()['amount']==100
    assert db.nagonu_batches.count_documents({})==0
    assert db.nagonu_recipient_locks.count_documents({})==0

def test_concurrent_dispatch_claim_sends_one_post(flow):
    from concurrent.futures import ThreadPoolExecutor
    db,uid,sid,cart=flow;batch=reserve(flow)
    with patch.object(n.requests,'post',return_value=accepted()) as post:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _:n.dispatch_one(db,batch['order_ids'][0]),range(2)))
        assert post.call_count==1


def test_provider_http_error_is_visible_and_never_retried(flow):
    db,uid,sid,cart=flow;batch=reserve(flow)
    with patch.object(n.requests,'post',return_value=response({'status':401,'message':'secret'},http=401)) as post:
        finish(db,batch);finish(db,batch)
        assert post.call_count==1
    diagnostic=db.orders.find_one()['items'][0]['api_response']
    assert diagnostic['http_status']==401 and diagnostic['status']=='401'
    assert 'secret' not in str(diagnostic)
    assert db.balances.find_one()['amount']==96


@pytest.mark.parametrize('channel',['wallet','store'])
@pytest.mark.parametrize('status',['delivered','completed'])
def test_delivered_package_can_be_purchased_again_immediately(flow,channel,status):
    db,uid,sid,cart=flow
    store={'slug':'shop','owner_id':uid} if channel=='store' else None
    first=reserve(flow,store=store)
    with patch.object(n.requests,'post',return_value=accepted()) as post:
        finish(db,first)
        db.orders.update_one({'order_id':first['order_ids'][0]}, {'$set':{'status':status,'items.0.line_status':status}})
        second=reserve(flow,store=store,ref='next-purchase')
        finish(db,second)
        assert post.call_count==2
        assert reserve(flow,store=store,ref='next-purchase')['order_ids']==second['order_ids']
        finish(db,second)
        assert post.call_count==2
    assert db.orders.count_documents({})==2
    assert db.balances.find_one()['amount']==(92 if channel=='wallet' else 100)
    with pytest.raises(ValueError):reserve(flow,store=store,ref='still-processing')


def test_delivered_line_in_batch_can_repeat_while_other_line_is_pending(flow):
    db,uid,sid,cart=flow
    cart.append({**cart[0],'phone':'0554226399'})
    first=reserve(flow)
    with patch.object(n.requests,'post',return_value=accepted()):finish(db,first)
    db.orders.update_one({'order_id':first['order_ids'][0]}, {'$set':{'status':'delivered','items.0.line_status':'delivered'}})
    cart.pop()
    second=reserve(flow,ref='repeat-delivered-line')
    assert second['order_ids']!=first['order_ids']


def test_delivered_line_allows_repeat_when_order_status_has_not_updated(flow):
    db,uid,sid,cart=flow
    first=reserve(flow)
    with patch.object(n.requests,'post',return_value=accepted()):finish(db,first)
    db.orders.update_one({'order_id':first['order_ids'][0]}, {'$set':{'items.0.line_status':'delivered'}})
    second=reserve(flow,ref='repeat-after-line-delivery')
    assert second['order_ids']!=first['order_ids']

from datetime import datetime,timedelta
from unittest.mock import patch
import types
import mongomock
import pytest
import order_status as worker

@pytest.mark.parametrize('category,state,expected',[('regular','Pending','pending'),('regular','Completed','delivered'),('bigtime','Processing','processing'),('bigtime','Failed','failed'),('regular','Refunded','failed')])
def test_status_update(category,state,expected):
 db=mongomock.MongoClient().test
 db.orders.insert_one({'order_id':'KING1','nagonu_state':'accepted','status':'processing','items':[{'phone':'0551234567','provider_category':category,'provider_reference_id':'API1','provider_payload':{'network':'MTN'},'value_obj':{'volume':1000},'line_status':'processing'}]})
 response=types.SimpleNamespace(status_code=200,json=lambda:{'status':200,'success':True,'data':{'beneficiary':'0551234567','gig':'1','network':'MTN','order_status':state}})
 with patch.object(worker.requests,'get',return_value=response) as get:
  assert worker.poll_nagonu_orders(db)['updated']==1
  assert ('response_big_time.php' if category=='bigtime' else 'response_regular.php') in get.call_args.args[0]
  assert get.call_args.kwargs['params']=={'reference_id':'API1'}
  worker.poll_nagonu_orders(db);assert get.call_count==1
 saved=db.orders.find_one();assert saved['status']==expected and saved['items'][0]['line_status']==expected
 assert db.transactions.count_documents({})==0
 if expected=='failed':assert saved['refund_review_required']

@pytest.mark.parametrize('case',['error','mismatch','unknown','terminal'])
def test_bad_lookup_preserves_status(case):
 db=mongomock.MongoClient().test
 status='delivered' if case=='terminal' else 'processing'
 db.orders.insert_one({'order_id':'KING1','nagonu_state':'accepted','status':status,'items':[{'phone':'0551234567','provider_category':'regular','provider_reference_id':'API1','provider_payload':{'network':'MTN'},'value_obj':{'volume':1000},'line_status':status}]})
 payload={'status':200,'success':True,'data':{'beneficiary':'0551234567','gig':'1','network':'MTN','order_status':'Completed'}}
 if case=='error':payload['status']=100
 if case=='mismatch':payload['data']['beneficiary']='0550000000'
 if case=='unknown':payload['data']['order_status']='unrecognized'
 with patch.object(worker.requests,'get',return_value=types.SimpleNamespace(status_code=200,json=lambda:payload)) as get:
  worker.poll_nagonu_orders(db)
  if case=='terminal':get.assert_not_called()
 assert db.orders.find_one()['status']==status

def test_wallet_cache_and_admin_access():
 import sys,importlib.util
 from flask import Flask
 fake=types.ModuleType('db');fake.db=mongomock.MongoClient().test
 with patch.dict(sys.modules,{'db':fake}):
  spec=importlib.util.spec_from_file_location('wallet_dashboard','admin_dashboard.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 app=Flask(__name__);app.secret_key='test';app.register_blueprint(m.admin_dashboard_bp);c=app.test_client()
 assert c.get('/admin/provider-wallet').status_code==403
 with c.session_transaction() as s:s['role']='admin'
 response=types.SimpleNamespace(status_code=200,json=lambda:{'status':'success','data':{'wallet':10}})
 with patch.object(m.requests,'get',return_value=response) as get:
  assert c.get('/admin/provider-wallet').json['balance']==10
  c.get('/admin/provider-wallet');assert get.call_count==1
 fake.db.provider_wallet_cache.update_one({}, {'$set':{'attempted_at':datetime.utcnow()-timedelta(minutes=2)}})
 with patch.object(m.requests,'get',side_effect=TimeoutError):
  data=c.get('/admin/provider-wallet').json;assert not data['success'] and data['balance']==10

@pytest.mark.parametrize('size,network,expected',[('1GB','MTN EXPRESS',True),('1','MTN',True),('1000 MB','MTN EXPRESS',True),('2GB','MTN EXPRESS',False),('1GB','MTN NORMAL',False),('bad','MTN',False)])
def test_live_status_formats(size,network,expected):
 from nagonu_orders import status_matches_item
 item={'phone':'0530393625','value_obj':{'volume':1000},'provider_payload':{'network':'MTN','service_name':'MTN EXPRESS'}}
 assert status_matches_item({'beneficiary':'0530393625','gig':size,'network':network},item) is expected


def test_mtn_normal_existing_regular_order_uses_current_bigtime_endpoint():
 db=mongomock.MongoClient().test
 db.orders.insert_one({'order_id':'KING1','nagonu_state':'accepted','status':'processing','items':[{'phone':'0551234567','provider_category':'regular','provider_reference_id':'API1','provider_payload':{'network':'MTN','service_name':'MTN NORMAL'},'value_obj':{'volume':1000}}]})
 response=types.SimpleNamespace(status_code=200,json=lambda:{'status':200,'success':True,'data':{'beneficiary':'0551234567','gig':'1','network':'MTN','order_status':'Completed'}})
 with patch.object(worker.requests,'get',return_value=response) as get:
  assert worker.poll_nagonu_orders(db)['updated']==1
  assert get.call_args.args[0]=='https://nagonu.site/api/response_big_time.php'

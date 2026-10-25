import importlib.util
import sys
import types
from unittest.mock import patch
from contextlib import nullcontext
import mongomock
import pytest
from bson import ObjectId
from flask import Flask

class Collection:
    def __init__(self, raw): self.raw=raw
    def __getattr__(self,name):
        def call(*args,**kwargs):
            kwargs.pop('session',None)
            return getattr(self.raw,name)(*args,**kwargs)
        return call
class Session:
    def with_transaction(self,fn): return fn(self)
class Database:
    def __init__(self):
        self.raw=mongomock.MongoClient().test
        self.client=types.SimpleNamespace(start_session=lambda:nullcontext(Session()))
    def __getattr__(self,name):return Collection(self.raw[name])

@pytest.fixture
def setup():
    db=Database();fake=types.ModuleType('db');fake.db=db
    spec=importlib.util.spec_from_file_location('form_service_test','form_services.py');module=importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules,{'db':fake}):spec.loader.exec_module(module)
    app=Flask(__name__);app.secret_key='test';app.register_blueprint(module.form_services_bp)
    app.add_url_rule('/applications/<application_id>',endpoint='applications.detail',view_func=lambda application_id:application_id)
    app.add_url_rule('/login',endpoint='login.login',view_func=lambda:'login')
    uid=ObjectId();sid=ObjectId();service={'_id':sid,'name':'NSS Special Postings','purchase_mode':'form','price':25,'status':'OPEN','availability':'AVAILABLE','form_fields':module.parse_fields(['Name','NSS Number'])}
    db.services.insert_one(service);db.balances.insert_one({'user_id':uid,'amount':100})
    client=app.test_client()
    with client.session_transaction() as sess:sess.update(role='customer',user_id=str(uid),form_service_csrf='csrf')
    data={'data_consent':'yes','csrf':'csrf','request_token':'a'*32,'version':module.config_version(service),**{field['key']:'Example' for field in service['form_fields']}}
    return module,db,client,uid,sid,data

def test_submission_is_idempotent_and_does_not_charge(setup):
    module,db,client,uid,sid,data=setup
    data['price']='0.01'
    for _ in range(2):assert client.post(f'/customer/services/{sid}/apply',data=data).status_code==302
    assert db.balances.find_one({'user_id':uid})['amount']==100
    assert db.orders.count_documents({})==0
    assert db.transactions.count_documents({})==0
    application=db.service_applications.find_one()
    assert db.service_applications.count_documents({})==1
    assert application['quote_amount']==25
    assert application['data_consent']['accepted'] is True
    assert application['data_consent']['text']==module.APPLICATION_CONSENT
    assert application['data_consent']['accepted_at'] is not None
    assert application['answers'][1]['label']=='NSS Number'

def test_unpriced_submission_and_zero_balance_allowed(setup):
    module,db,client,uid,sid,data=setup
    db.services.update_one({'_id':sid},{'$set':{'price':None}})
    db.balances.update_one({'user_id':uid},{'$set':{'amount':0}})
    data['version']=module.config_version(db.services.find_one())
    assert client.post(f'/customer/services/{sid}/apply',data=data).status_code==302
    application=db.service_applications.find_one()
    assert application['status']=='submitted' and application['quote_amount'] is None
    assert db.transactions.count_documents({})==0

@pytest.mark.parametrize('case',['empty','closed','stale'])
def test_rejected_purchase_does_not_charge(setup,case):
    module,db,client,uid,sid,data=setup
    if case=='empty':data[next(k for k in data if k.startswith('field_'))]=' '
    if case=='insufficient':db.balances.update_one({'user_id':uid},{'$set':{'amount':1}})
    if case=='closed':db.services.update_one({'_id':sid},{'$set':{'status':'CLOSED'}})
    if case=='stale':data['version']='old'
    if case=='unpriced':db.services.update_one({'_id':sid},{'$set':{'price':None}})
    before=db.balances.find_one()['amount']
    with patch.object(module,'render_template',return_value='validation error'):
        assert client.post(f'/customer/services/{sid}/apply',data=data).status_code==200
    assert db.balances.find_one()['amount']==before
    assert db.orders.count_documents({})==0

def test_csrf_required(setup):
    _,db,client,_,sid,data=setup;data['csrf']='wrong'
    assert client.post(f'/customer/services/{sid}/apply',data=data).status_code==400
    assert db.orders.count_documents({})==0

@pytest.mark.parametrize('price',['NaN','Infinity','-1','0','0.001',None])
def test_invalid_prices(setup,price):
    with pytest.raises(ValueError):setup[0].parse_price(price)

def test_field_labels_validate(setup):
    module=setup[0]
    for labels in [[],['Name','name'],['']]:
        with pytest.raises(ValueError):module.parse_fields(labels)

def test_repeated_labels_in_different_sections_keep_separate_answers(setup):
    module=setup[0]
    fields=module.parse_fields(['Name','Nationality','Name','Nationality'],['Father','Father','Mother','Mother'])
    assert len({f['key'] for f in fields})==4
    answers=module.validate_answers({'form_fields':fields},{f['key']:f['section']+' '+f['label'] for f in fields})
    assert answers[0]['value']=='Father Name'
    assert answers[2]['value']=='Mother Name'
    assert answers[2]['section']=='Mother'
    with pytest.raises(ValueError):module.parse_fields(['Name','name'],['Father','Father'])

def test_long_application_form(setup):
    module=setup[0]
    assert len(module.parse_fields([f'Field {i}' for i in range(54)]))==54
    with pytest.raises(ValueError):module.parse_fields([f'Field {i}' for i in range(101)])

@pytest.mark.parametrize('consent', [None, '', 'no'])
def test_application_requires_explicit_consent(setup, consent):
    module, db, client, uid, sid, data = setup
    data.pop('data_consent')
    if consent is not None:
        data['data_consent'] = consent
    with patch.object(module, 'render_template', return_value='validation error') as render:
        assert client.post(f'/customer/services/{sid}/apply', data=data).status_code == 200
        assert 'confirm your consent' in render.call_args.kwargs['error']
    assert db.service_applications.count_documents({}) == 0
    assert db.services.find_one().get('application_count', 0) == 0

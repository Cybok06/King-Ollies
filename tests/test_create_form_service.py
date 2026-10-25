"""Admin-created application services use validated fields and server pricing."""
import importlib.util
import sys
import types
from unittest.mock import patch
import mongomock
import pytest
from flask import Flask

@pytest.mark.parametrize('price,labels,expected', [('25',['Name','Contact'],1),('', ['Name','Contact'],1),('NaN',['Name'],0),('25',[],0),('25',['Name','Name'],0)])
def test_create_form_service(price,labels,expected):
    fake=types.ModuleType('db');fake.db=mongomock.MongoClient().test
    with patch.dict(sys.modules,{'db':fake}):
        modules={}
        for name in ['form_services','admin_services']:
            spec=importlib.util.spec_from_file_location(name,name+'.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);modules[name]=m
        with patch.dict(sys.modules,modules):
            app=Flask(__name__);app.secret_key='test';app.register_blueprint(modules['admin_services'].admin_services_bp)
            client=app.test_client()
            with client.session_transaction() as sess:sess['role']='admin'
            response=client.post('/admin/services/create',data={'service_name':'New application','image_url':'https://imagedelivery.net/example/public','purchase_mode':'form','form_price':price,'field_label[]':labels,'field_section[]':['']*len(labels),'service_notes':'Bring documents'})
            assert response.status_code==302
    assert fake.db.services.count_documents({})==expected
    if expected:
        svc=fake.db.services.find_one();assert svc['purchase_mode']=='form';assert svc['price']==(25 if price else None);assert len(svc['form_fields'])==2;assert svc['notes']=='Bring documents'

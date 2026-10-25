import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree
import mongomock
from flask import Flask

def test_public_service_discovery():
    fake=types.ModuleType('db');fake.db=mongomock.MongoClient().test
    sid=fake.db.services.insert_one({'name':'Passport Application','purchase_mode':'form','image_url':'https://example.com/passport.png','status':'OPEN','availability':'AVAILABLE','notes':'PRIVATE INTERNAL NOTE'}).inserted_id
    retired_id=fake.db.services.insert_one({'name':'MTN AFA','purchase_mode':'afa'}).inserted_id
    with patch.dict(sys.modules,{'db':fake}):
        spec=importlib.util.spec_from_file_location('public_services','public_services.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    app=Flask(__name__,template_folder=str(Path('templates').resolve()));app.register_blueprint(m.public_services_bp)
    app.add_url_rule('/customer/dashboard','customer_dashboard.customer_dashboard',lambda:'dashboard')
    c=app.test_client()
    with patch.dict('os.environ',{'PUBLIC_SITE_URL':'https://kingollies.example'}):
        r=c.get('/sitemap.xml');assert r.status_code==200
        urls=[node.text for node in ElementTree.fromstring(r.data).findall('{*}url/{*}loc')]
        assert len(urls)==3 and all(url.startswith('https://kingollies.example/') for url in urls)
        path=f'/our-services/{sid}/passport-application'
        assert 'https://kingollies.example'+path in urls
        r=c.get(path);assert r.status_code==200
        assert b'King Ollie' in r.data and b'"@type": "Service"' in r.data
        assert b'PRIVATE INTERNAL NOTE' not in r.data
        assert b'wa.me/233556531766' in r.data
        assert c.get(f'/our-services/{sid}/old-name').status_code==301
        assert c.get('/our-services/invalid/test').status_code==404
        assert c.get('/our-services').status_code==200
        assert b'MTN AFA' not in c.get('/our-services').data
        assert c.get(f'/our-services/{retired_id}/mtn-afa').status_code==404
        assert b'Sitemap: https://kingollies.example/sitemap.xml' in c.get('/robots.txt').data
        fake.db.services.insert_one({'name':'New service'})
        assert len(ElementTree.fromstring(c.get('/sitemap.xml').data).findall('{*}url'))==4

import importlib.util
import sys
import types
from unittest.mock import patch

import mongomock
import pytest
from flask import Flask


@pytest.fixture
def advert_app():
    fake = types.ModuleType('db')
    fake.db = mongomock.MongoClient().test
    spec = importlib.util.spec_from_file_location('adverts_test', 'adverts.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'db': fake}):
        spec.loader.exec_module(module)
    app = Flask(__name__)
    app.secret_key = 'test'
    app.register_blueprint(module.adverts_bp)
    module.ghana_today = lambda: '2026-10-09'
    client = app.test_client()
    return module, client


def login(client):
    with client.session_transaction() as session:
        session['role'] = 'admin'
        session['advert_csrf'] = 'test-token'
    return {'X-CSRF-Token': 'test-token'}


def payload(**extra):
    return dict(title='October promotion', image_url='https://imagedelivery.net/hash/id/public',
                text='Extra details', pages=['index', 'store', 'dashboard'], end_date='2026-10-09', **extra)


def test_targeting_expiry_and_forever(advert_app):
    module, client = advert_app
    headers = login(client)
    response = client.post('/admin/api/adverts', json=payload(), headers=headers)
    assert response.status_code == 200
    assert len(client.get('/api/adverts?page=index').json['adverts']) == 1
    module.ghana_today = lambda: '2026-10-10'
    assert client.get('/api/adverts?page=index').json['adverts'] == []
    body = payload(); body.update(end_date=None, pages=['store'])
    assert client.post('/admin/api/adverts', json=body, headers=headers).status_code == 200
    assert client.get('/api/adverts?page=index').json['adverts'] == []
    assert len(client.get('/api/adverts?page=store').json['adverts']) == 1
    assert client.get('/api/adverts?page=store').headers['Cache-Control'] == 'no-store'


def test_admin_access_csrf_and_customer_delivery(advert_app):
    _, client = advert_app
    assert client.get('/admin/api/adverts').status_code == 403
    assert client.post('/admin/api/adverts/upload').status_code == 403
    assert client.get('/api/adverts?page=dashboard').status_code == 403
    headers = login(client)
    assert client.post('/admin/api/adverts', json=payload()).status_code == 403
    client.post('/admin/api/adverts', json=payload(), headers=headers)
    with client.session_transaction() as session:
        session['role'] = 'customer'
    assert len(client.get('/api/adverts?page=dashboard').json['adverts']) == 1
    assert client.delete('/admin/api/adverts/000000000000000000000000', headers=headers).status_code == 403


def test_edit_toggle_delete_and_validation(advert_app):
    _, client = advert_app
    headers = login(client)
    ad_id = client.post('/admin/api/adverts', json=payload(), headers=headers).json['id']
    url = '/admin/api/adverts/' + ad_id
    body = payload(); body.update(title='Updated', pages=['index'], end_date=None)
    assert client.put(url, json=body, headers=headers).status_code == 200
    assert client.get('/api/adverts?page=index').json['adverts'][0]['title'] == 'Updated'
    assert client.get('/api/adverts?page=store').json['adverts'] == []
    assert client.patch(url, json={'active':False}, headers=headers).status_code == 200
    assert client.get('/api/adverts?page=index').json['adverts'] == []
    assert client.delete(url, headers=headers).status_code == 200
    assert client.delete(url, headers=headers).status_code == 404
    for change in [{'pages':[]}, {'pages':['invalid']}, {'pages':[{}]}, {'end_date':'2026-10-08'}, {'end_date':'bad'}, {'image_url':'javascript:alert(1)'}, {'title':''}]:
        invalid = payload(); invalid.update(change)
        assert client.post('/admin/api/adverts', json=invalid, headers=headers).status_code == 400


def test_upload_reuses_existing_cloudflare_helper(advert_app):
    _, client = advert_app
    headers = login(client)
    helper = types.ModuleType('routes.store_create')
    calls = []
    helper.upload_cloudflare_image = lambda module: (calls.append(module) or {'success':True, 'image_url':'https://imagedelivery.net/hash/test/public'})
    with patch.dict(sys.modules, {'routes.store_create':helper}):
        response = client.post('/admin/api/adverts/upload', headers=headers)
    assert response.status_code == 200
    assert calls == ['adverts']

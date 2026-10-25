"""Read-only browser checks using fixture data; no real database or payments."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from threading import Thread
from flask import Flask, render_template, request, jsonify
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
from store_catalog import OLLINET_LOGO
from datetime import datetime
from bson import ObjectId
from application_exports import export_excel, export_pdf

root=Path(__file__).resolve().parents[1]
app=Flask(__name__,template_folder=str(root/'templates'),static_folder=str(root/'static'))
app.secret_key='preview-only'
catalog_slides=[{'name':'Birth Certificate','image_url':OLLINET_LOGO},{'name':'Passport','image_url':OLLINET_LOGO}]
catalog=[{'_id_str':'mtn','name':'MTN NORMAL','purchase_mode':'bundle','offers':[{'value':{'id':1,'volume':1000},'amount':5,'value_text':'1 GB'}]},
         {'_id_str':'afa-registration','name':'AFA Registration','purchase_mode':'afa','offers':[{'value':'afa-registration','value_text':'AFA Registration','amount':3}]},
         {'_id_str':'checker','name':'Checkers and Forms','purchase_mode':'checker','offers':[{'value':'bece','value_text':'BECE','amount':20},{'value':'wassce','value_text':'WASSCE','amount':25}]}]
store={'slug':'preview','name':'Campus Store','logo_url':OLLINET_LOGO,'service_ids':['mtn','afa-registration','checker'],'status':'published','pricing':{'percent_default':10,'per_service':[]},'theme':{},'hero':{}}
def urls(endpoint,**kw):
    if endpoint=='static': return '/static/'+kw['filename']
    if endpoint=='store_special.purchase': return '/s/preview/service/'+kw['service_id']
    return '/'+endpoint.replace('.','/')
app.jinja_env.globals['url_for']=urls
@app.context_processor
def ctx(): return dict(BRAND_NAME="King Ollie's",BRAND_LOGO_URL=OLLINET_LOGO,undelivered_orders_count=0,pending_withdrawals_count=0,pending_complaints_count=0)
@app.get('/create-store')
def builder(): return render_template('store_create.html',services=catalog,store=store)
@app.get('/api/stores/mine')
def mine(): return jsonify(success=True,store=store)
@app.post('/api/stores/preview')
def preview(): store.update(request.json);return jsonify(success=True,slug='preview')
@app.post('/api/media')
def media(): return jsonify(success=True,url=OLLINET_LOGO)
@app.get('/s/preview')
def public():
    services=[dict(s,_id=s['_id_str'],can_order=True,disabled_reason='',image_url=OLLINET_LOGO,
                   offers=[dict(o,total=round(o['amount']*1.1,2)) for o in s['offers']]) for s in catalog]
    return render_template('store_page.html',store=store,services=services,application_services=[{"name":"Birth Certificate Assistance","image_url":OLLINET_LOGO},{"name":"Passport Application","image_url":OLLINET_LOGO}],service_slides=catalog_slides,products=[],paystack_pk='',canonical_url='',whatsapp_number='',whatsapp_number_digits='',whatsapp_number_link='',whatsapp_group_link='',owner_email='',store_email='',ps_email='',ps_first='',ps_last='')

application={'_id':ObjectId(), 'service_name':'Passport Application', 'status':'quoted', 'created_at':datetime(2026,10,1,10,30),
             'quote_amount':500, 'quote_version':1, 'payment_mode':'online', 'answers':[
                 {'section':'Personal details','label':'Full name','value':'Ama Mensah'},
                 {'section':'Personal details','label':'Date of birth','value':'15 June 1998'},
                 {'section':'Contact details','label':'Address','value':'12 Example Road\nAccra, Ghana'}]}
customer={'username':'Ama Mensah','email':'ama@example.com','phone':'0550000000'}
@app.get('/applications')
def applications():
    return render_template('applications.html',applications=[dict(application,customer_name='Ama Mensah')]*12,
        is_admin=True,ongoing_count=18,closed_count=6,total_count=24,view='all',issued_count=20,issued_total=4000,
        paid_count=15,paid_total=3000,revenue=2800,refunded_total=200,page=1,pages=2,filtered_count=24,per_page=12)
@app.get('/application-profile')
def detail():
    return render_template('application_detail.html',application=application,customer=customer,is_admin=True,messages=[],csrf='preview',message_token='preview',balance=0)

if __name__=='__main__':
    server=make_server('127.0.0.1',5099,app,threaded=True)
    Thread(target=server.serve_forever,daemon=True).start()
    output=root/'artifacts'/'store-updates';output.mkdir(exist_ok=True)
    (output/'sample-application.pdf').write_bytes(export_pdf(application,customer).getvalue())
    (output/'sample-application.xlsx').write_bytes(export_excel(application,customer).getvalue())
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge')
        page=browser.new_page()
        errors=[];page.on('pageerror',lambda exc:errors.append(str(exc)))
        for width in [1440,390]:
            page.set_viewport_size({'width':width,'height':1000})
            page.goto('http://127.0.0.1:5099/create-store',wait_until='domcontentloaded')
            page.wait_for_selector('#pricingCache .svc-card',state='attached')
            assert page.locator('.builder-section[open]').count()==0
            assert page.locator('#default_percent').count()==0
            page.locator('#section-services summary').click()
            page.locator('.picked-service-row button').first.click()
            page.wait_for_selector('#servicePricingModal.show')
            price=page.locator('#pricingEditor .total').first
            page.locator('#pricingEditor .svc-manual').check()
            original=price.input_value()
            price.fill('12.00')
            page.locator('#saveServicePricing').click()
            page.wait_for_selector('#servicePricingModal',state='hidden')
            page.locator('.picked-service-row button').first.click()
            page.wait_for_selector('#servicePricingModal.show')
            assert price.input_value()=='12.00'
            price.fill('13.00')
            page.locator('#servicePricingModal .modal-footer [data-bs-dismiss]').click()
            page.wait_for_selector('#servicePricingModal',state='hidden')
            page.locator('.picked-service-row button').first.click()
            page.wait_for_selector('#servicePricingModal.show')
            assert price.input_value()=='12.00'
            page.wait_for_function("getComputedStyle(document.getElementById('servicePricingModal')).opacity === '1'")
            assert page.locator('#servicePricingModal .modal-dialog').bounding_box()['y'] >= 0
            page.screenshot(path=str(output/f'pricing-modal-{width}.png'),animations='disabled')
            page.locator('#servicePricingModal .modal-footer [data-bs-dismiss]').click()
            page.wait_for_selector('#servicePricingModal',state='hidden')
            checker_row=page.locator('.picked-service-row').filter(has_text='Checkers and Forms')
            checker_row.get_by_role('button',name='Remove').click()
            page.locator('#svcPicker').select_option('checker')
            page.wait_for_selector('#servicePricingModal.show')
            page.locator('#servicePricingModal .modal-footer [data-bs-dismiss]').click()
            page.wait_for_selector('#servicePricingModal',state='hidden')
            page.wait_for_function("document.querySelectorAll('.picked-service-row').length === 2")
            page.locator('#svcPicker').select_option('checker')
            page.wait_for_selector('#servicePricingModal.show')
            page.locator('#pricingEditor .svc-percent').fill('12')
            page.locator('#saveServicePricing').click()
            page.wait_for_selector('#servicePricingModal',state='hidden')
            assert page.locator('.picked-service-row').count()==3
            page.locator('#section-services summary').click()
            page.locator('#section-identity summary').click()
            page.locator('#section-preview summary').click()
            assert page.locator('#pane-products').count()==0
            assert page.locator('#livePreview').evaluate('(e)=>e.getBoundingClientRect().height')>=560
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Builder overflow'
            page.locator('#logo_file').set_input_files({'name':'logo.png','mimeType':'image/png','buffer':b'preview'})
            page.wait_for_selector('#logoUploadState.ok')
            assert page.locator('#logoUploadState').evaluate('(e)=>getComputedStyle(e).color')=='rgb(255, 255, 255)'
            page.screenshot(path=str(output/f'builder-{width}.png'),full_page=True)
            page.goto('http://127.0.0.1:5099/s/preview',wait_until='domcontentloaded')
            assert page.locator('#pane-products').count()==0
            assert page.locator('a[href="/s/preview/service/checker"]').count()==1
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Store overflow'
            page.screenshot(path=str(output/f'store-{width}.png'),full_page=True)
            for path in ['applications','application-profile']:
                response=page.goto('http://127.0.0.1:5099/'+path,wait_until='domcontentloaded')
                assert response.status==200
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), path+' overflow'
                page.screenshot(path=str(output/f'{path}-{width}.png'),full_page=True)
        browser.close()
    server.shutdown()
    assert not errors, errors
    print('Desktop/mobile store views, upload contrast and preview passed.')

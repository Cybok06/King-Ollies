"""Configurable form services with wallet purchases and immutable answer snapshots."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import secrets
from bson import ObjectId
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from db import db

APPLICATION_CONSENT = 'I consent to King Ollies collecting and using the details I provide to process my application. My information will be handled with appropriate safeguards and shared only as necessary with organisations involved in processing my application, or where required by law.'

form_services_bp = Blueprint('form_services', __name__)

def parse_price(value):
    try:
        price = Decimal(str(value))
        if not price.is_finite() or price <= 0 or price > 1000000 or price != price.quantize(Decimal('0.01')):
            raise ValueError('Price must be a positive GHS amount with at most two decimal places.')
        return float(price)
    except (InvalidOperation, TypeError):
        raise ValueError('Enter a valid price in GHS.')

def parse_fields(labels, sections=None):
    labels = [str(label).strip() for label in labels]
    if not 1 <= len(labels) <= 100 or any(not label or len(label) > 100 for label in labels):
        raise ValueError('Provide 1 to 100 fields, each with a label of 1 to 100 characters.')
    sections = sections or [''] * len(labels)
    if len(sections) != len(labels) or any(len(str(section).strip()) > 100 for section in sections):
        raise ValueError('Each field section must be at most 100 characters.')
    sections = [str(section).strip() for section in sections]
    identities = [(section.casefold(), label.casefold()) for label, section in zip(labels, sections)]
    if len(set(identities)) != len(identities):
        raise ValueError('Field labels must be unique within each section.')
    # Preserve legacy label-only keys for unique labels; scope repeated labels by section.
    folded = [label.casefold() for label in labels]
    return [{'key': 'field_' + hashlib.sha256(
                (label.casefold() if folded.count(label.casefold()) == 1 else
                 json.dumps([section.casefold(), label.casefold()])).encode()).hexdigest()[:16],
             'label': label, 'required': True, 'section': section}
            for label, section in zip(labels, sections)]


def config_version(service):
    return hashlib.sha256(json.dumps([service.get('price'), service.get('form_fields'), service.get('status'), service.get('availability'), service.get('notes')], sort_keys=True).encode()).hexdigest()

def validate_answers(service, form):
    answers=[]
    for field in service.get('form_fields', []):
        value=(form.get(field['key']) or '').strip()
        if not value or len(value)>500:
            raise ValueError(f"Enter {field['label']} (maximum 500 characters).")
        answers.append({'key':field['key'], 'label':field['label'], 'section':field.get('section',''), 'value':value})
    if not answers:
        raise ValueError('This service has no application fields configured.')
    return answers

@form_services_bp.route('/services/nss-special-postings')
def nss_service():
    service=db.services.find_one({'slug':'nss-special-postings','purchase_mode':'form'})
    if not service: abort(404)
    return redirect(url_for('form_services.purchase', service_id=str(service['_id'])))

@form_services_bp.route('/services/shs-placement')
def shs_service():
    service=db.services.find_one({'slug':'shs-placement','purchase_mode':'form'})
    if not service: abort(404)
    return redirect(url_for('form_services.purchase', service_id=str(service['_id'])))

@form_services_bp.route('/services/business-registration')
def business_service():
    service=db.services.find_one({'slug':'business-registration','purchase_mode':'form'})
    if not service: abort(404)
    return redirect(url_for('form_services.purchase', service_id=str(service['_id'])))

@form_services_bp.route('/services/passport-application')
def passport_service():
    service=db.services.find_one({'slug':'passport-application','purchase_mode':'form'})
    if not service: abort(404)
    return redirect(url_for('form_services.purchase', service_id=str(service['_id'])))

@form_services_bp.route('/services/birth-certificate-application')
def birth_service():
    service=db.services.find_one({'slug':'birth-certificate-application','purchase_mode':'form'})
    if not service: abort(404)
    return redirect(url_for('form_services.purchase', service_id=str(service['_id'])))

@form_services_bp.route('/services/affidavit-gazette-acquisition')
def affidavit_service():
    service=db.services.find_one({'slug':'affidavit-gazette-acquisition','purchase_mode':'form'})
    if not service: abort(404)
    return redirect(url_for('form_services.purchase', service_id=str(service['_id'])))

@form_services_bp.route('/customer/services/<service_id>/apply', methods=['GET','POST'])
def purchase(service_id):
    if session.get('role')!='customer' or not session.get('user_id'):
        return redirect(url_for('login.login'))
    try: sid=ObjectId(service_id); uid=ObjectId(session['user_id'])
    except Exception: abort(404)
    service=db.services.find_one({'_id':sid,'purchase_mode':'form'})
    if not service: abort(404)
    csrf=session.setdefault('form_service_csrf',secrets.token_urlsafe(32))
    token=request.form.get('request_token','') if request.method=='POST' else secrets.token_hex(16)
    error=None
    if request.method=='POST':
        if not secrets.compare_digest(request.form.get('csrf',''),csrf): abort(400)
        if len(token)!=32 or any(c not in '0123456789abcdef' for c in token): abort(400)
        oid=ObjectId(hashlib.sha256((str(uid)+str(sid)+token).encode()).hexdigest()[:24])
        try:
            if request.form.get('data_consent') != 'yes':
                raise ValueError('Please confirm your consent to share your details before submitting your application.')
            def submit(tx):
                existing=db.service_applications.find_one({'_id':oid,'user_id':uid},session=tx)
                if existing:return
                current=db.services.find_one({'_id':sid,'purchase_mode':'form'},session=tx)
                if not current or current.get('status')!='OPEN' or current.get('availability')!='AVAILABLE':
                    raise ValueError('This service is not currently accepting applications.')
                if request.form.get('version')!=config_version(current):
                    raise ValueError('The service changed. Review the updated form and submit again.')
                price=parse_price(current['price']) if current.get('price') is not None else None
                answers=validate_answers(current,request.form)
                db.services.update_one({'_id':sid},{'$inc':{'application_count':1}},session=tx)
                now=datetime.utcnow()
                db.service_applications.insert_one({
                    '_id':oid,'user_id':uid,'service_id':sid,'service_name':current['name'],
                    'image_url':current.get('image_url',''),'notes':current.get('notes',''),
                    'data_consent':{'accepted':True,'accepted_at':now,'version':'2026-10-01','text':APPLICATION_CONSENT},
                    'answers':answers,'quote_amount':price,'quote_version':1 if price else 0,
                    'status':'quoted' if price else 'submitted','created_at':now,'updated_at':now,
                },session=tx)
            with db.client.start_session() as tx:
                tx.with_transaction(submit)
            flash('Application submitted. Review payment details or message our team below.','success')
            return redirect(url_for('applications.detail',application_id=str(oid)))
        except ValueError as exc:error=str(exc)
        except Exception:
            error='We could not confirm your application. Retry this form; it will not create a duplicate.'
    balance=db.balances.find_one({'user_id':uid}) or {}
    enabled=service.get('status')=='OPEN' and service.get('availability')=='AVAILABLE'
    return render_template('form_service.html',service=service,balance=balance.get('amount',0),csrf=csrf,request_token=token,version=config_version(service),error=error,enabled=enabled,values=request.form,consent_text=APPLICATION_CONSENT)

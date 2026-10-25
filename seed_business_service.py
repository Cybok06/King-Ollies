"""Create Business Registration, preserving subsequent admin edits."""
from datetime import datetime
from db import db
from form_services import parse_fields


def seed():
    personal = ['Full name', 'Date of birth', 'House number', 'Box number', 'Landmark',
                'Tel number', 'Place of birth', 'TIN number', 'GPS', 'Email',
                'Ghana card', "Mother's name"]
    business = ['Business name', 'Business activities']
    fields = parse_fields(personal + business, ['Personal Details'] * len(personal) + ['Business Details'] * len(business))
    result = db.services.update_one({'slug': 'business-registration'}, {'$setOnInsert': {
        'slug': 'business-registration', 'name': 'BUSINESS REGISTRATION',
        'purchase_mode': 'form', 'type': 'OFF', 'status': 'OPEN',
        'availability': 'AVAILABLE', 'price': None,
        'image_url': 'https://orc.gov.gh/img/logo-full-01.png',
        'form_fields': fields,
        'notes': 'Kindly sign on a white sheet I will scan and paste it on your document.. Thank you',
        'offers': [], 'store_offers': [],
        'created_at': datetime.utcnow(), 'updated_at': datetime.utcnow(),
    }}, upsert=True)
    saved = db.services.find_one({'slug': 'business-registration'})
    print('BUSINESS REGISTRATION created.' if result.upserted_id else 'BUSINESS REGISTRATION already exists; settings preserved.')
    print('Database:', db.name, 'Service ID:', str(saved['_id']), 'Price:', saved.get('price'), 'Fields:', len(saved['form_fields']))


if __name__ == '__main__':
    seed()

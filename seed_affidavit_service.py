"""Create Affidavit and Gazette Acquisition, preserving existing admin settings."""
from datetime import datetime
from db import db
from form_services import parse_fields

LABELS = [
    'Name', 'GPS Address', 'School Attended', 'Contact',
    'State the wrong Name or Date of Birth',
    'State the Correct Name or Date of Birth', 'Occupation',
    'Landmark in your area: write the name of a school or church of the area',
    'Name of Town', 'Region of Town',
]
NOTE = 'ONLY AFFIDAVIT IS NEEDED TO DO A GAZETTE FOR YOU'


def seed():
    result = db.services.update_one({'slug': 'affidavit-gazette-acquisition'}, {'$setOnInsert': {
        'slug': 'affidavit-gazette-acquisition', 'name': 'Affidavit and Gazette Acquisition',
        'purchase_mode': 'form', 'type': 'OFF', 'status': 'OPEN', 'availability': 'AVAILABLE',
        'price': None,
        'image_url': 'https://www.lululabels.co.za/cdn/shop/articles/commissioner-of-oaths-stamp-on-official-documents.png?v=1755207882',
        'form_fields': parse_fields(LABELS), 'notes': NOTE,
        'offers': [], 'store_offers': [],
        'created_at': datetime.utcnow(), 'updated_at': datetime.utcnow(),
    }}, upsert=True)
    saved = db.services.find_one({'slug': 'affidavit-gazette-acquisition'})
    print('Affidavit and Gazette Acquisition created.' if result.upserted_id else 'Service already exists; settings preserved.')
    print('Database:', db.name, 'Service ID:', str(saved['_id']), 'Price:', saved.get('price'), 'Fields:', len(saved['form_fields']))
    print('Note:', saved.get('notes'))


if __name__ == '__main__':
    seed()

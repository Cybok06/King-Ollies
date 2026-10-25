"""Create Birth Certificate Application, preserving existing admin settings."""
from datetime import datetime
from db import db
from form_services import parse_fields

SECTIONS = [
    ('Applicant Details', ['Name', 'Date of Birth', 'Place of Birth', 'Occupation']),
    ('Mother Details', ["Mother's Name", 'Occupation']),
    ('Father Details', ["Father's Name", 'Occupation']),
]


def seed():
    fields = parse_fields(
        [label for _, labels in SECTIONS for label in labels],
        [section for section, labels in SECTIONS for _ in labels],
    )
    result = db.services.update_one({'slug': 'birth-certificate-application'}, {'$setOnInsert': {
        'slug': 'birth-certificate-application', 'name': 'BIRTH CERTIFICATE APPLICATION',
        'purchase_mode': 'form', 'type': 'OFF', 'status': 'OPEN', 'availability': 'AVAILABLE',
        'price': None,
        'image_url': 'https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcT-p8X1eE8em-A2_WOL6geeyqKFEy0FKkcaFdLbe2ZI8aqzfkAYYWqeKBwt&s=10',
        'form_fields': fields, 'notes': '', 'offers': [], 'store_offers': [],
        'created_at': datetime.utcnow(), 'updated_at': datetime.utcnow(),
    }}, upsert=True)
    saved = db.services.find_one({'slug': 'birth-certificate-application'})
    assert len(saved['form_fields']) == 8
    assert len({field['key'] for field in saved['form_fields']}) == 8
    print('BIRTH CERTIFICATE APPLICATION created.' if result.upserted_id else 'Service already exists; settings preserved.')
    print('Database:', db.name, 'Service ID:', str(saved['_id']), 'Price:', saved.get('price'), 'Fields:', len(saved['form_fields']))


if __name__ == '__main__':
    seed()

"""Create Passport Application without overwriting subsequent admin edits."""
from datetime import datetime
from db import db
from form_services import parse_fields

SECTIONS = [
    ('Applicant Details', [
        'Name', 'Telephone Number', 'E-mail Address', 'Date of Birth', 'Gender',
        'City of Birth', 'Country', 'County of Residence', 'City of Residence',
        'Suburb', 'House Number', 'Digital House Address', 'Postal Address',
        'Height', 'Nationality', 'Current Profession', 'Ghana Card Number',
        'Last Institution Attended', 'From (M/Y)', 'To (M/Y)',
    ]),
    ('Evidence of Citizenship - Father', [
        "Father's Name", 'Nationality', 'Alive/Dead', 'Residential Address', 'Hometown', 'Telephone Number',
    ]),
    ('Evidence of Citizenship - Mother', [
        "Mother's Name", 'Nationality', 'Alive/Dead', 'Residential Address', 'Hometown', 'Telephone Number',
    ]),
    ('One Grandparent', ['Name', 'Nationality', 'Alive/Dead', 'Residential Address', 'Hometown', 'Telephone Number']),
    ('Dual Citizenship', ['Dual Citizenship (Yes/No)']),
    ('Guarantor 1', ['Name', 'Residential Address', 'Telephone Number', 'Occupation']),
    ('Guarantor 2', ['Name', 'Residential Address', 'Telephone Number', 'Occupation']),
    ('Witness', ['Name', 'Occupation', 'Position', 'Business Name', 'Business Telephone Number',
                 'Residential Address', 'Residential Address Telephone Number']),
]


def seed():
    labels = [label for _, fields in SECTIONS for label in fields]
    sections = [section for section, fields in SECTIONS for _ in fields]
    result = db.services.update_one({'slug': 'passport-application'}, {'$setOnInsert': {
        'slug': 'passport-application', 'name': 'PASSPORT APPLICATION',
        'purchase_mode': 'form', 'type': 'OFF', 'status': 'OPEN', 'availability': 'AVAILABLE',
        'price': None,
        'image_url': 'https://cdn.modernghana.com/content__/640/457/10212025110912-1i830o4bav-screenshot20251021-0906051.png',
        'form_fields': parse_fields(labels, sections), 'notes': '',
        'offers': [], 'store_offers': [], 'created_at': datetime.utcnow(), 'updated_at': datetime.utcnow(),
    }}, upsert=True)
    saved = db.services.find_one({'slug': 'passport-application'})
    print('PASSPORT APPLICATION created.' if result.upserted_id else 'PASSPORT APPLICATION already exists; settings preserved.')
    print('Database:', db.name, 'Service ID:', str(saved['_id']), 'Price:', saved.get('price'), 'Fields:', len(saved['form_fields']))


if __name__ == '__main__':
    seed()

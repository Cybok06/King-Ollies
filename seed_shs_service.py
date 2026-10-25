"""Create SHS Placement without overwriting prices or fields edited by admins."""
from datetime import datetime
from db import db
from form_services import parse_fields


def seed():
    result = db.services.update_one({'slug': 'shs-placement'}, {'$setOnInsert': {
        'slug': 'shs-placement', 'name': 'SHS PLACEMENT', 'purchase_mode': 'form',
        'type': 'OFF', 'status': 'OPEN', 'availability': 'AVAILABLE', 'price': None,
        'image_url': 'https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSQzqQgX7MOjoqa-rlOAOUtpKAPXXF8MT8HmmBAAIwXlw&s',
        'form_fields': parse_fields([
            'Candidate Name', 'Index Number', 'SHS Placement', 'Program',
            'Grade', 'Date of Birth', 'Status',
        ]),
        'offers': [], 'store_offers': [],
        'created_at': datetime.utcnow(), 'updated_at': datetime.utcnow(),
    }}, upsert=True)
    saved = db.services.find_one({'slug': 'shs-placement'})
    print('SHS PLACEMENT created.' if result.upserted_id else 'SHS PLACEMENT already exists; settings preserved.')
    print('Database:', db.name, 'Service ID:', str(saved['_id']), 'Price:', saved.get('price'))
    print('Fields:', ', '.join(field['label'] for field in saved.get('form_fields', [])))


if __name__ == '__main__':
    seed()

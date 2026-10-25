"""Create the service and attach legacy stock without changing delivered purchases."""
from datetime import datetime
import hashlib
from db import db
from form_services import parse_price


def seed():
    db.services.update_one({'slug': 'checkers-and-forms'}, {'$setOnInsert': {
        'name': 'Checkers and Forms', 'purchase_mode': 'checker', 'type': 'OFF',
        'status': 'OPEN', 'availability': 'AVAILABLE', 'offers': [], 'store_offers': [],
        'image_url': 'https://imagedelivery.net/h9fmMoa1o2c2P55TcWJGOg/826a4c98-1628-47a2-cbf8-8d4724909100/public',
        'created_at': datetime.utcnow(), 'updated_at': datetime.utcnow()}}, upsert=True)
    svc = db.services.find_one({'slug': 'checkers-and-forms'})
    migrated = skipped = 0
    for item in db.wassce_checker.find({'type_id': {'$exists': False}, 'status': 'not_sold'}):
        try: price = parse_price(item.get('amount'))
        except (ValueError, TypeError):
            skipped += 1
            continue
        if not str(item.get('message') or '').strip():
            skipped += 1
            continue
        name = str(item.get('type') or 'Checker').strip().upper()
        # Different legacy prices stay separate so no existing price is silently changed.
        key = hashlib.sha256((str(svc['_id']) + name.casefold() + str(price)).encode()).hexdigest()
        db.checker_types.update_one({'_id': key}, {'$setOnInsert': {
            'service_id': svc['_id'], 'name': f'{name} (GHS {price:.2f})', 'price': price,
            'enabled': True, 'version': 1, 'updated_at': datetime.utcnow()}}, upsert=True)
        result = db.wassce_checker.update_one({'_id': item['_id'], 'type_id': {'$exists': False}},
                                             {'$set': {'type_id': key}})
        migrated += result.modified_count
    print(f'Checkers and Forms ready. Legacy stock attached: {migrated}; invalid stock skipped: {skipped}.')


if __name__ == '__main__': seed()

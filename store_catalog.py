"""The store catalog and pricing shared by the editor and checkout."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

OLLINET_LOGO = 'https://imagedelivery.net/h9fmMoa1o2c2P55TcWJGOg/1567c007-4593-48d3-1e67-18655c1bf500/public'
AFA_ID = 'afa-registration'
BUNDLE_NAMES = {'MTN NORMAL', 'MTN EXPRESS', 'TELECEL', 'AT - ISHARE', 'AT - BIGTIME', 'AFA TALKTIME'}


def is_afa_registration(service):
    return (service.get('purchase_mode') == 'afa' or
            str(service.get('name', '')).strip().upper() in
            {'MTN AFA', 'AFA REGISTRATION', 'MTN AFA REGISTRATION'})


def allowed_service(service):
    if is_afa_registration(service):
        return False
    return (service.get('purchase_mode') == 'checker' or
            (service.get('purchase_mode') != 'form' and
             str(service.get('name', '')).strip().upper() in BUNDLE_NAMES))


def money(value):
    try:
        value = Decimal(str(value))
        if not value.is_finite() or value < 0 or value > 1000000:
            raise ValueError('Enter a valid non-negative price or percentage.')
        return float(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError):
        raise ValueError('Enter a valid price or percentage.')


def afa_service(database):
    settings = (database.afa_settings.find_one({'_id': 'AFA_SETTINGS'}) or
                database.settings.find_one({'key': 'afa_settings'}) or
                database.settings.find_one({'key': 'afa'}) or {})
    return {'_id': AFA_ID, '_id_str': AFA_ID, 'name': 'AFA Registration', 'purchase_mode': 'afa',
            'image_url': OLLINET_LOGO, 'status': 'OPEN' if settings.get('is_open', True) else 'CLOSED',
            'availability': 'AVAILABLE' if settings.get('in_stock', True) else 'OUT_OF_STOCK',
            'offers': [{'value': AFA_ID, 'value_text': 'AFA Registration',
                        'amount': money(settings.get('price', 2))}]}


def special_offers(database, service):
    if service.get('purchase_mode') == 'afa':
        return service['offers']
    return [{'value': str(kind['_id']), 'value_text': kind['name'], 'amount': money(kind['price'])}
            for kind in database.checker_types.find({'service_id': service['_id'], 'enabled': True}).sort('name', 1)]


def special_price(store, service_id, offer):
    pricing = store.get('pricing') or {}
    config = next((x for x in pricing.get('per_service', []) if x['service_id'] == str(service_id)), {})
    base = money(offer['amount'])
    if config.get('offers'):
        # Stable type IDs prevent a rename/new checker type from inheriting another type's price.
        override = next((x for x in config['offers'] if str(x.get('value')) == str(offer['value'])), None)
        if override is None:
            return None  # Newly added types need an explicit price in manual mode.
        total = money(base + money(override['profit'])) if 'profit' in override else money(override['total'])
    else:
        percent = money(config.get('percent', pricing.get('percent_default', 0)))
        total = money(Decimal(str(base)) * (1 + Decimal(str(percent)) / 100))
    return total if total >= base else None


def validate_pricing(data, catalog):
    ids = data.get('service_ids', [])
    available = {s['_id_str']: s for s in catalog}
    if not isinstance(ids, list) or any(not isinstance(sid, str) or sid not in available for sid in ids):
        raise ValueError('Only data bundles and Checkers and Forms can be added.')
    pricing = data.get('pricing') or {}
    money(pricing.get('percent_default', 0))
    for config in pricing.get('per_service', []):
        sid = config.get('service_id')
        if sid not in ids:
            raise ValueError('Pricing must belong to a selected service.')
        if config.get('percent') is not None:
            money(config['percent'])
        offers = available[sid]['offers']
        for override in config.get('offers', []):
            if available[sid].get('purchase_mode') in ('afa', 'checker'):
                offer = next((o for o in offers if str(o['value']) == str(override.get('value'))), None)
            else:
                index = override.get('index')
                offer = offers[index] if isinstance(index, int) and 0 <= index < len(offers) else None
            if not offer:
                raise ValueError('An offer changed. Refresh the editor and set its price again.')
            if 'profit' in override:
                money(override['profit'])
            elif money(override.get('total')) < money(offer['amount']):
                raise ValueError('Selling price cannot be below the base price.')
    return ids

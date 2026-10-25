"""Admin adverts and page-targeted public delivery. Ghana uses UTC year-round."""
from datetime import datetime, timezone, date
from functools import wraps
from secrets import token_urlsafe, compare_digest
from urllib.parse import urlparse

from bson import ObjectId
from flask import Blueprint, jsonify, render_template, request, session
from db import db

adverts_bp = Blueprint('adverts', __name__)
adverts_col = db['adverts']
PAGES = {'dashboard': 'Customer Dashboard', 'store': 'Store pages', 'index': 'Index / Home page'}


def ghana_today():
    return datetime.now(timezone.utc).date().isoformat()


def admin_only(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if session.get('role') != 'admin':
            return jsonify(error='Admin access required'), 403
        if request.method != 'GET':
            token = session.get('advert_csrf', '')
            if not token or not compare_digest(token, request.headers.get('X-CSRF-Token', '')):
                return jsonify(error='Refresh this page and try again'), 403
        return fn(*args, **kwargs)
    return wrapped


def serialize(doc):
    return {key: (str(doc['_id']) if key == 'id' else doc.get(key))
            for key in ('id', 'title', 'image_url', 'image_id', 'text', 'pages', 'active', 'end_date')}


@adverts_bp.get('/admin/advert')
@admin_only
def admin_page():
    session.setdefault('advert_csrf', token_urlsafe(32))
    return render_template('admin_adverts.html', csrf=session['advert_csrf'], pages=PAGES)


@adverts_bp.post('/admin/api/adverts/upload')
@admin_only
def upload():
    from routes.store_create import upload_cloudflare_image
    return upload_cloudflare_image('adverts')


@adverts_bp.route('/admin/api/adverts', methods=['GET', 'POST'])
@admin_only
def collection():
    if request.method == 'GET':
        return jsonify(adverts=[serialize(d) for d in adverts_col.find().sort('created_at', -1)], today=ghana_today())
    return save()


def save(advert_id=None):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify(error='Invalid advert data'), 400
    title = str(payload.get('title') or '').strip()
    text = str(payload.get('text') or '').strip()
    pages = payload.get('pages')
    image_url = str(payload.get('image_url') or '').strip()
    end = payload.get('end_date') or None
    if not title or len(title) > 160 or len(text) > 5000:
        return jsonify(error='Enter a title up to 160 characters and text up to 5,000 characters'), 400
    if not isinstance(pages, list) or not pages or any(not isinstance(p, str) or p not in PAGES for p in pages):
        return jsonify(error='Select at least one valid page'), 400
    try:
        parsed = urlparse(image_url)
    except ValueError:
        return jsonify(error='Upload an image using the Cloudflare uploader'), 400
    if parsed.scheme != 'https' or parsed.netloc != 'imagedelivery.net':
        return jsonify(error='Upload an image using the Cloudflare uploader'), 400
    if end:
        try:
            if date.fromisoformat(end).isoformat() != end or end < ghana_today():
                raise ValueError()
        except (ValueError, TypeError):
            return jsonify(error='Choose today or a future end date'), 400
    doc = dict(title=title, text=text, pages=list(dict.fromkeys(pages)), image_url=image_url,
               image_id=str(payload.get('image_id') or ''), end_date=end,
               active=payload.get('active', True) is True, updated_at=datetime.now(timezone.utc))
    if advert_id:
        if not adverts_col.update_one({'_id': advert_id}, {'$set': doc}).matched_count:
            return jsonify(error='Advert not found'), 404
    else:
        doc['created_at'] = doc['updated_at']
        doc['created_by'] = session.get('user_id')
        advert_id = adverts_col.insert_one(doc).inserted_id
    return jsonify(success=True, id=str(advert_id))


@adverts_bp.route('/admin/api/adverts/<advert_id>', methods=['PUT', 'PATCH', 'DELETE'])
@admin_only
def item(advert_id):
    if not ObjectId.is_valid(advert_id):
        return jsonify(error='Advert not found'), 404
    oid = ObjectId(advert_id)
    if request.method == 'DELETE':
        result = adverts_col.delete_one({'_id': oid})
        found = result.deleted_count
    elif request.method == 'PATCH':
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict) or not isinstance(payload.get('active'), bool):
            return jsonify(error='Active must be true or false'), 400
        found = adverts_col.update_one({'_id': oid}, {'$set': {'active': payload['active']}}).matched_count
    else:
        return save(oid)
    return (jsonify(success=True), 200) if found else (jsonify(error='Advert not found'), 404)


@adverts_bp.get('/api/adverts')
def public_adverts():
    page = request.args.get('page')
    if page not in PAGES:
        return jsonify(error='Invalid page'), 400
    if page == 'dashboard' and session.get('role') != 'customer':
        return jsonify(error='Customer access required'), 403
    today = ghana_today()
    docs = adverts_col.find({'active': True, 'pages': page,
                             '$or': [{'end_date': None}, {'end_date': {'$gte': today}}]}).sort('created_at', -1)
    response = jsonify(adverts=[serialize(d) for d in docs], today=today)
    response.headers['Cache-Control'] = 'no-store'
    return response

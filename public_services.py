"""Public service information, canonical URLs and search-engine discovery."""
import os
import re
from datetime import datetime
from urllib.parse import urlsplit, quote
from xml.etree.ElementTree import Element, SubElement, tostring
from bson import ObjectId
from flask import Blueprint, Response, abort, redirect, render_template, url_for
from db import db
from store_catalog import is_afa_registration

public_services_bp = Blueprint('public_services', __name__)


def public_origin():
    origin = os.getenv('PUBLIC_SITE_URL', 'https://campusdata.store').rstrip('/')
    parts = urlsplit(origin)
    if parts.scheme not in ('http', 'https') or not parts.netloc or parts.path or parts.query or parts.fragment:
        raise ValueError('PUBLIC_SITE_URL must be an absolute site origin, e.g. https://campusdata.store')
    return origin


def service_slug(name):
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'service'


def catalogue():
    # Only public service information is read; application and purchase records are never exposed.
    rows = list(db.services.find({'name': {'$type': 'string'}}, {
        'name': 1, 'image_url': 1, 'purchase_mode': 1, 'status': 1, 'availability': 1,
        'updated_at': 1, 'created_at': 1, 'public_description': 1}))
    rows = [row for row in rows if not is_afa_registration(row)]
    for row in rows:
        row['slug'] = service_slug(row['name'])
        row['path'] = url_for('public_services.detail', service_id=str(row['_id']), slug=row['slug'])
        row['url'] = public_origin() + row['path']
        description = row.get('public_description')
        if not isinstance(description, str) or not description.strip():
            description = {
                'form': f"Enquire about {row['name']} with King Ollies in Ghana. Contact our team about the details required, pricing and application updates.",
                'checker': "Explore Checkers and Forms from King Ollies in Ghana. Choose an available checker or form type, review its price and receive your purchased details in My Purchases.",
            }.get(row.get('purchase_mode'), f"Explore {row['name']} from King Ollies in Ghana. View available options through your customer dashboard or contact our team for details.")
        row['description'] = description
    return sorted(rows, key=lambda row: row['name'].casefold())


@public_services_bp.app_context_processor
def canonical_context():
    return {'PUBLIC_SITE_URL': public_origin()}


@public_services_bp.route('/our-services')
def directory():
    rows = catalogue()
    canonical = public_origin() + url_for('public_services.directory')
    schema = {'@context': 'https://schema.org', '@type': 'ItemList', 'name': "King Ollies services",
              'itemListElement': [{'@type': 'ListItem', 'position': i+1, 'name': row['name'], 'url': row['url']} for i, row in enumerate(rows)]}
    return render_template('public_services.html', services=rows, service=None, canonical=canonical,
                           page_title="Services in Ghana | King Ollies", description="Explore King Ollies data bundles, checkers, forms and document application services in Ghana.", schema=schema)


@public_services_bp.route('/our-services/<service_id>/<slug>')
def detail(service_id, slug):
    if not ObjectId.is_valid(service_id): abort(404)
    row = next((row for row in catalogue() if str(row['_id']) == service_id), None)
    if not row: abort(404)
    if slug != row['slug']: return redirect(row['path'], code=301)
    schema = {'@context': 'https://schema.org', '@type': 'Service', '@id': row['url'] + '#service',
              'name': row['name'], 'serviceType': row['name'], 'description': row['description'], 'url': row['url'],
              'areaServed': {'@type': 'Country', 'name': 'Ghana'},
              'provider': {'@type': 'Organization', '@id': public_origin() + '/#organization',
                           'name': "King Ollies", 'url': public_origin() + '/', 'telephone': '+233556531766'}}
    if row.get('image_url'): schema['image'] = row['image_url']
    return render_template('public_services.html', service=row, services=[], canonical=row['url'],
                           page_title=f"{row['name']} in Ghana | King Ollies", description=row['description'], schema=schema,
                           enquiry_url='https://wa.me/233556531766?text=' + quote("Hello King Ollies, I would like to enquire about " + row['name'] + '.'))


@public_services_bp.route('/sitemap.xml')
def sitemap():
    root = Element('urlset', xmlns='http://www.sitemaps.org/schemas/sitemap/0.9')
    rows = [{'url': public_origin() + '/'}, {'url': public_origin() + '/our-services'}] + catalogue()
    for row in rows:
        node = SubElement(root, 'url')
        SubElement(node, 'loc').text = row['url']
        modified = row.get('updated_at') or row.get('created_at')
        if isinstance(modified, datetime): SubElement(node, 'lastmod').text = modified.date().isoformat()
    return Response(tostring(root, encoding='utf-8', xml_declaration=True), mimetype='application/xml')


@public_services_bp.route('/robots.txt')
def robots():
    return Response('User-agent: *\nAllow: /\nDisallow: /admin/\nDisallow: /customer/\nDisallow: /applications\nDisallow: /api/\n\nSitemap: ' + public_origin() + '/sitemap.xml\n', mimetype='text/plain')

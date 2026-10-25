"""Browser verification with an isolated in-memory database and simulated uploads."""
from pathlib import Path
import sys
import types
from threading import Thread
from unittest.mock import patch
from datetime import datetime, timezone

import mongomock
from flask import Flask, render_template, session, jsonify
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
fake = types.ModuleType('db'); fake.db = mongomock.MongoClient().test
with patch.dict(sys.modules, {'db': fake}):
    import adverts
app = Flask(__name__, template_folder=str(root / 'templates'), static_folder=str(root / 'static'))
app.secret_key = 'fixture'
app.register_blueprint(adverts.adverts_bp)
adverts.ghana_today = lambda: '2026-10-09'
app.jinja_env.globals['url_for'] = lambda endpoint, **kw: '/static/' + kw['filename'] if endpoint == 'static' else '/' + endpoint.replace('.', '/')
@app.context_processor
def context():
    return dict(BRAND_NAME='King Ollies', BRAND_LOGO_URL='/static/test-ad.svg', undelivered_orders_count=0, pending_complaints_count=0)
@app.get('/fixture-login')
def login():
    session['role'] = 'admin'
    return 'Ready'
@app.get('/fixture/<page>')
def public(page):
    return render_template('partials/advert_popup.html', advert_page=page)
@app.get('/static/test-ad.svg')
def image():
    return '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="350"><rect width="600" height="350" fill="#135da3"/><text x="50" y="180" font-size="40" fill="white">October promotion</text></svg>', 200, {'Content-Type':'image/svg+xml'}

if __name__ == '__main__':
    server = make_server('127.0.0.1', 5102, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    output = root / 'artifacts' / 'adverts'; output.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge')
        page = browser.new_page()
        errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
        page.clock.install(time=datetime(2026, 10, 9, 12, tzinfo=timezone.utc))
        page.route('https://imagedelivery.net/**', lambda route: route.fulfill(status=200, content_type='image/svg+xml', body=image()[0]))
        page.route('**/admin/api/adverts/upload', lambda route: route.fulfill(json={'success':True, 'image_url':'https://imagedelivery.net/hash/test/public', 'image_id':'test'}))
        page.goto('http://127.0.0.1:5102/fixture-login')
        page.goto('http://127.0.0.1:5102/admin/advert')
        page.locator('#ad-title').fill('October promotion')
        page.locator('#ad-text').fill('Special offers for everyone.')
        page.locator('#ad-all').check()
        page.locator('#ad-file').set_input_files({'name':'test.png','mimeType':'image/png','buffer':b'fixture'})
        page.wait_for_function("document.getElementById('upload-state').textContent === 'Image uploaded.'")
        page.locator('#ad-save').click()
        page.get_by_role('heading', name='October promotion', exact=True).wait_for()
        page.get_by_role('button', name='Preview', exact=True).click()
        assert page.locator('#admin-ad-preview').is_visible()
        page.locator('#preview-okay').click()
        for width in (1440, 390):
            page.set_viewport_size({'width':width, 'height':900})
            page.screenshot(path=str(output / f'admin-{width}.png'), full_page=True)
        page.goto('http://127.0.0.1:5102/fixture/index')
        page.locator('#advert-popup[open]').wait_for()
        page.clock.run_for(300)
        page.screenshot(path=str(output / 'popup-390.png'), animations='disabled')
        page.set_viewport_size({'width':1440, 'height':900})
        page.screenshot(path=str(output / 'popup-1440.png'), animations='disabled')
        page.locator('#advert-okay').click()
        page.reload(); page.wait_for_timeout(500)
        assert not page.locator('#advert-popup').is_visible()
        page.goto('http://127.0.0.1:5102/fixture/store')
        page.locator('#advert-popup[open]').wait_for()
        page.locator('#advert-okay').click()
        page.goto('http://127.0.0.1:5102/fixture/index')
        page.clock.set_system_time(datetime(2026, 10, 10, 12, tzinfo=timezone.utc))
        page.reload(); page.locator('#advert-popup[open]').wait_for()
        # Expired adverts never appear, even with no daily dismissal.
        fake.db.adverts.update_many({}, {'$set': {'end_date':'2026-10-08'}})
        page.reload(); page.wait_for_timeout(500)
        assert not page.locator('#advert-popup').is_visible()
        # Welcome dialog blocks the advert until acknowledged.
        fake.db.adverts.update_many({}, {'$set': {'end_date':None}})
        page.evaluate("localStorage.clear()")
        page.route('**/fixture/index', lambda route: route.fulfill(content_type='text/html', body='<dialog id="welcomeModal" open>Welcome</dialog>' + app.jinja_env.get_template('partials/advert_popup.html').render(advert_page='index')))
        page.reload(); page.wait_for_timeout(500)
        assert not page.locator('#advert-popup').is_visible()
        page.evaluate("document.getElementById('welcomeModal').close()")
        page.clock.run_for(600)
        page.locator('#advert-popup[open]').wait_for()
        assert not errors, errors
        browser.close()
    server.shutdown()
    print('Browser checks passed: admin save/preview, mobile layout, page-specific dismissal, next day, expiry, welcome coordination; no JavaScript errors.')

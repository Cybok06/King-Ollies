"""Fixture-only browser checks. No real orders, wallet charges, or payments."""
from pathlib import Path
import runpy
import sys
from threading import Thread
from flask import render_template
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
fixture = runpy.run_path(str(root / 'artifacts/preview_store_updates.py'))
app = fixture['app']
catalog = fixture['catalog']
catalog[:] = [dict(catalog[0], name=name, _id_str=str(i),
    offers=[{'value': {'id': 1, 'volume': 1000}, 'value_text': '1 GB', 'amount': 5, 'total': 5.5}])
    for i, name in enumerate(['MTN NORMAL', 'MTN EXPRESS', 'TELECEL'])]

@app.get('/dashboard')
def dashboard():
    return render_template('customer_dashboard.html', services=catalog, express_services=[],
        balance=100, username='Preview', orders=[], checker_types=[], store_recent_orders_view=[])

if __name__ == '__main__':
    server = make_server('127.0.0.1', 5098, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    output = root / 'artifacts/direct-purchase'
    output.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge')
        page = browser.new_page()
        calls = []
        errors = []
        failure = {'next': False}
        page.on('pageerror', lambda err: errors.append(str(err)))
        def route(r):
            url = r.request.url
            if '/api/phone-order-eligibility' in url:
                r.fulfill(json={'success': True, 'allowed': True})
            elif '/api/store-paystack-public-key/' in url:
                r.fulfill(json={'success': True, 'public_key': 'pk_test_fixture', 'key_used': 'test', 'day_key': 'test'})
            elif '/store-checkout/' in url or url.endswith('/checkout'):
                calls.append(r.request.post_data_json)
                if failure['next']:
                    failure['next'] = False
                    r.fulfill(status=503, json={'success': False, 'message': 'Temporary confirmation failure'})
                    return
                r.fulfill(json={'success': True, 'order_recorded': True, 'order_id': 'TEST',
                    'message': 'Your order is processing.', 'redirect_url': '/invoice-batch/TEST'})
            elif '/api/' in url:
                r.fulfill(json={'success': True, 'items': [], 'notifications': []})
            else:
                r.continue_()
        page.route('**/*', route)
        for width in [390, 1440]:
            page.set_viewport_size({'width': width, 'height': 950})
            for path in ['/dashboard', '/s/preview']:
                page.goto('http://127.0.0.1:5098' + path, wait_until='domcontentloaded')
                page.wait_for_function('!!window.directDataPurchase')
                assert page.locator('.floating-cart, #fabCart').count() == 0
                page.evaluate("""() => {const original = window.directDataPurchase.purchase; window.directDataPurchase.purchase = async (...args) => {window.spinnerSeen = !!document.querySelector('#dataPurchasePay .spinner-border'); return original(...args);};}""")
                page.evaluate("""window.PaystackPop={setup:opts=>({openIframe:()=>opts.callback({reference:opts.reference})})};""")
                selector = '.select-service-btn' if path == '/dashboard' else '.select-btn'
                page.locator(selector).first.click()
                page.wait_for_selector('#dataPurchaseModal.show')
                page.locator('#dataPurchasePay').click()
                assert 'valid Ghana' in page.locator('#dataPurchaseError').inner_text()
                page.locator('#dataPurchasePhone').fill('0241234567')
                page.locator('#dataPurchaseOffer').select_option('0')
                assert page.locator('#dataPurchaseTotal').inner_text() == '5.50'
                page.screenshot(path=str(output / f"{'dashboard' if path == '/dashboard' else 'store'}-form-{width}.png"))
                before = len(calls)
                page.locator('#dataPurchasePay').click()
                page.wait_for_selector('#dataPurchaseResult:not([hidden])')
                assert page.locator('#dataPurchaseResultTitle').inner_text() == 'Order Placed Successfully'
                assert page.evaluate('window.spinnerSeen')
                assert len(calls) == before + 1
                assert len(calls[-1]['cart']) == 1
                assert page.locator('#dataPurchaseReceipt').get_attribute('href') == '/invoice-batch/TEST'
                page.screenshot(path=str(output / f"{'dashboard' if path == '/dashboard' else 'store'}-{width}.png"))
                page.locator('#dataPurchaseResult button').click()
                page.wait_for_selector('#dataPurchaseModal', state='hidden')
                page.locator(selector + '[data-service*="TELECEL"]').click()
                page.locator('#dataBulkTab').click()
                page.locator('#dataPurchasePhones').fill('0201234567\n0201234568')
                page.locator('#dataPurchaseOffer').select_option('0')
                assert page.locator('#dataPurchaseTotal').inner_text() == '11.00'
                page.locator('#dataPurchasePay').click()
                page.wait_for_selector('#dataPurchaseResult:not([hidden])')
                assert len(calls[-1]['cart']) == 2
                assert 'TELECEL' == calls[-1]['cart'][0]['serviceName']
        # A cancelled gateway must not submit an order.
        page.locator('#dataPurchaseResult button').click()
        page.wait_for_selector('#dataPurchaseModal', state='hidden')
        page.locator('.select-btn[data-service*="MTN EXPRESS"]').click()
        page.locator('#dataPurchasePhone').fill('0241234567')
        page.locator('#dataPurchaseOffer').select_option('0')
        page.evaluate("window.PaystackPop={setup:opts=>({openIframe:()=>opts.onClose()})}")
        before = len(calls)
        page.locator('#dataPurchasePay').click()
        page.wait_for_function("document.getElementById('dataPurchasePay').disabled === false")
        assert len(calls) == before
        # Confirmation retries must reuse the already paid reference.
        page.evaluate("window.paymentWindows=0; window.PaystackPop={setup:opts=>({openIframe:()=>{window.paymentWindows++; opts.callback({reference:opts.reference});}})}")
        failure['next'] = True
        page.locator('#dataPurchasePay').click()
        page.wait_for_selector('#dataPurchaseError:not([hidden])')
        reference = calls[-1]['paystack']['reference']
        page.locator('#dataPurchasePay').click()
        page.wait_for_selector('#dataPurchaseResult:not([hidden])')
        assert calls[-1]['paystack']['reference'] == reference
        assert page.evaluate('window.paymentWindows') == 1
        # Retain the network mismatch confirmation for ported recipients.
        page.locator('#dataPurchaseResult button').click()
        page.wait_for_selector('#dataPurchaseModal', state='hidden')
        page.locator('.select-btn[data-service*="MTN NORMAL"]').click()
        page.locator('#dataPurchasePhone').fill('0201234567')
        page.locator('#dataPurchaseOffer').select_option('0')
        page.locator('#dataPurchasePay').click()
        page.wait_for_selector('#portedModal.show')
        page.locator('#portedConfirmBtn').click()
        page.wait_for_selector('#dataPurchaseResult:not([hidden])')
        assert calls[-1]['cart'][0]['ported_confirmed']
        for rejected_count, title in [(0, 'Order submitted'), (1, 'Order could not be completed')]:
            page.locator('#dataPurchaseResult button').click()
            page.wait_for_selector('#dataPurchaseModal', state='hidden')
            page.locator('.select-btn[data-service*="MTN NORMAL"]').click()
            page.locator('#dataPurchasePhone').fill('0241234567')
            page.locator('#dataPurchaseOffer').select_option('0')
            page.evaluate('(count) => {window.directDataPurchase.purchase = async () => ({success:false, order_recorded:true, rejected_count:count, redirect_url:"/invoice-batch/TEST"});}', rejected_count)
            page.locator('#dataPurchasePay').click()
            page.wait_for_selector('#dataPurchaseResult:not([hidden])')
            assert page.locator('#dataPurchaseResultTitle').inner_text() == title
            assert 'Some orders were rejected' not in page.locator('#dataPurchaseResult').inner_text()
        assert not errors, errors
        browser.close()
    server.shutdown()
    print('Single/bulk purchases passed on mobile/desktop for both pages. Cancellation, paid-reference retries, and ported confirmation passed. No browser errors.')

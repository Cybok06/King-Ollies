from datetime import datetime, timedelta
from io import BytesIO
from unittest.mock import patch
from bson import ObjectId
from openpyxl import load_workbook
from application_exports import export_excel, export_pdf
from test_applications import flow


def example():
    return {'_id': ObjectId(), 'service_name': 'Passport Application', 'status': 'quoted',
            'created_at': datetime(2026, 10, 1, 10, 30), 'quote_amount': 500,
            'answers': [{'section': 'Personal details', 'label': 'Name', 'value': '=HYPERLINK("bad")'},
                        {'section': 'Contact', 'label': 'Address', 'value': 'A & B <Road>\nAccra'}]}


def test_excel_preserves_text_and_prevents_formulas():
    workbook = load_workbook(export_excel(example(), {'username': '=1+1'}))
    sheet = workbook['Submitted details']
    assert sheet['C4'].value == '=HYPERLINK("bad")' and sheet['C4'].data_type == 's'
    assert sheet['C5'].value == 'A & B <Road>\nAccra'
    assert sheet.freeze_panes == 'A4'
    assert workbook['Application summary']['B6'].data_type == 's'


def test_pdf_many_sections_and_long_answers():
    application = example()
    application['answers'] *= 30
    application['answers'].append({'label': 'Long response', 'value': '<&>' * 160})
    pdf = export_pdf(application, {'username': 'Ama Mensah'})
    assert pdf.getvalue().startswith(b'%PDF')
    # Multiple pages have a PDF Page object and keep all answers flowing.
    assert pdf.getvalue().count(b'/Type /Page') > 2


def test_exports_admin_only_and_private(flow):
    module, db, client, uid, aid = flow
    for extension in ['pdf', 'xlsx']:
        path = f'/applications/{aid}/export/{extension}'
        assert client.get(path).status_code == 403
        with client.session_transaction() as s: s['role'] = 'admin'
        response = client.get(path)
        assert response.status_code == 200
        assert 'attachment' in response.headers['Content-Disposition']
        assert response.headers['Cache-Control'] == 'private, no-store'
        with client.session_transaction() as s: s['role'] = 'customer'
    with client.session_transaction() as s: s.clear()
    assert client.get(f'/applications/{aid}/export/pdf').status_code == 403


def test_pagination_and_financial_totals(flow):
    module, db, client, uid, aid = flow
    now = datetime(2026, 10, 1)
    for index in range(25):
        db.service_applications.insert_one({'_id': ObjectId(), 'user_id': uid, 'service_name': f'Service {index}',
            'status': 'quoted', 'quote_amount': 10, 'created_at': now+timedelta(minutes=index)})
    db.service_applications.update_one({'_id': aid}, {'$set': {'status': 'paid', 'paid_at': now,
        'quote_amount': 50, 'paid_amount': 50, 'order_id': 'REFUNDED'}})
    db.orders.insert_one({'order_id': 'REFUNDED', 'status': 'refunded'})
    db.service_applications.insert_one({'_id': ObjectId(), 'user_id': ObjectId(), 'quote_amount': 9999,
                                       'status': 'paid', 'paid_at': now, 'paid_amount': 9999})
    with patch.object(module, 'render_template', return_value='ok') as render:
        client.get('/applications?view=all&page=2')
        result = render.call_args.kwargs
        assert result['page'] == 2 and result['pages'] == 3 and len(result['applications']) == 12
        assert result['total_count'] == 26 and result['issued_count'] == 26
        assert result['issued_total'] == 300 and result['paid_total'] == 50 and result['revenue'] == 0
        assert result['refunded_total'] == 50
        client.get('/applications?view=all&page=99999')
        assert render.call_args.kwargs['page'] == 3 and len(render.call_args.kwargs['applications']) == 2
        client.get('/applications?page=bad')
        assert render.call_args.kwargs['page'] == 1

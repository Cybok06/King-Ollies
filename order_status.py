"""Shared order status helpers and three-minute Nagonu status synchronization."""
from typing import Any, Dict, List

FINAL_STATUS = "delivered"

def _normalize_status(s: str | None) -> str:
    val = (s or "").strip().lower()
    if val == "completed":
        return "delivered"
    return val

def _compute_order_status_from_items(items: List[Dict[str, Any]], current_status: str | None = None) -> str:
    if _normalize_status(current_status) == FINAL_STATUS:
        return FINAL_STATUS

    statuses = [_normalize_status(i.get("line_status")) for i in items]
    if not statuses:
        return "processing"

    if all(s == "delivered" for s in statuses):
        return "delivered"

    if all(s == "pending" for s in statuses):
        return "pending"

    if any(s in {"processing", "queued"} for s in statuses):
        return "processing"

    if all(s == "failed" for s in statuses):
        return "failed"

    return "processing"

# Nagonu polling deliberately uses GET only. Financial settlement stays separate.
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
import logging
import os
import uuid
import re
import requests


def poll_nagonu_orders(database=None):
    if database is None:
        from db import db as database
    from nagonu_orders import BASE_URL, DEFAULT_API_KEY, status_matches_item, status_endpoint
    now = datetime.utcnow()
    query = {'nagonu_state': 'accepted', 'status': {'$in': ['pending','processing','queued']},
             'items.0.provider_reference_id': {'$exists': True},
             '$and': [{'$or': [{'nagonu_checked_at': {'$exists': False}}, {'nagonu_checked_at': {'$lte': now-timedelta(minutes=3)}}]},
                      {'$or': [{'nagonu_poll_until': {'$exists': False}}, {'nagonu_poll_until': {'$lte': now}}]}]}
    candidates = list(database.orders.find(query, {'_id': 1}).sort('nagonu_checked_at', 1).limit(200))
    def check(candidate):
        token = uuid.uuid4().hex
        order = database.orders.find_one_and_update({**query, '_id': candidate['_id']},
            {'$set': {'nagonu_poll_token': token, 'nagonu_poll_until': datetime.utcnow()+timedelta(minutes=2)}})
        if not order: return 'skipped'
        fields = {'nagonu_checked_at': datetime.utcnow()}
        outcome = 'error'
        try:
            item = order['items'][0]
            endpoint = status_endpoint(item)
            response = requests.get(BASE_URL+'/api/'+endpoint,
                params={'reference_id': item['provider_reference_id']},
                headers={'x-api-key': os.getenv('NAGONU_API_KEY') or DEFAULT_API_KEY},
                timeout=(5, 25), allow_redirects=False)
            if response.status_code != 200: raise ValueError('Provider lookup HTTP error')
            payload = response.json()
            if str(payload.get('status')) != '200' or payload.get('success') is not True:
                raise ValueError('Provider did not confirm a successful lookup')
            data = payload.get('data') or {}
            if not status_matches_item(data, item):
                raise ValueError('Provider lookup does not match the saved order')
            raw = str(data.get('order_status') or '').strip().lower()
            mapping = {'pending':'pending','processing':'processing','queued':'processing',
                       'completed':'delivered','delivered':'delivered','successful':'delivered','success':'delivered',
                       'failed':'failed','cancelled':'failed','canceled':'failed','refunded':'failed'}
            if raw not in mapping: raise ValueError('Unknown provider order status')
            status = mapping[raw]
            fields.update({'status':status,'items.0.line_status':status,'items.0.provider_order_status':raw,
                           'items.0.provider_status_checked_at':datetime.utcnow(), 'updated_at':datetime.utcnow(),
                           'nagonu_poll_error':None})
            if status=='failed': fields['refund_review_required']=True
            outcome='updated'
        except Exception as exc:
            # Do not expose response bodies, request headers or credentials in diagnostics.
            fields['nagonu_poll_error']='Status lookup unavailable or invalid; will retry.'
            logging.getLogger(__name__).warning('Nagonu lookup failed for %s (%s)',order.get('order_id'),type(exc).__name__)
        database.orders.update_one({'_id':order['_id'],'nagonu_poll_token':token,
            'status': {'$in':['pending','processing','queued']}},
            {'$set':fields,'$unset':{'nagonu_poll_until':'','nagonu_poll_token':''}})
        return outcome
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(check,candidates))
    return {name:results.count(name) for name in ('updated','error','skipped')}


def start_nagonu_scheduler(app):
    if os.getenv('NAGONU_STATUS_POLLING','true').lower() not in ('true','1','yes'): return
    if 'nagonu_status_scheduler' in app.extensions: return
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler=BackgroundScheduler(daemon=True)
    scheduler.add_job(poll_nagonu_orders,'interval',minutes=3,id='nagonu_order_status',
                      max_instances=1,coalesce=True,next_run_time=datetime.utcnow()+timedelta(seconds=15))
    scheduler.start()
    app.extensions['nagonu_status_scheduler']=scheduler


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Update unfinished Nagonu orders every three minutes.')
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    if args.once:
        print(poll_nagonu_orders())
    else:
        from apscheduler.schedulers.blocking import BlockingScheduler
        scheduler=BlockingScheduler()
        scheduler.add_job(poll_nagonu_orders,'interval',minutes=3,max_instances=1,coalesce=True,next_run_time=datetime.utcnow())
        scheduler.start()

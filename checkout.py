from flask import Blueprint, request, jsonify, session, render_template, abort
from bson import ObjectId
from datetime import datetime, timedelta
import os, random, requests, traceback, json, ast, re

from db import db
from phone_order_guard import BLOCK_NEW_NUMBERS_MESSAGE, first_blocked_phone

checkout_bp = Blueprint("checkout", __name__)

# MongoDB Collections
balances_col        = db["balances"]
orders_col          = db["orders"]
transactions_col    = db["transactions"]
services_col        = db["services"]
service_profits_col = db["service_profits"]  # per-customer overrides
users_col           = db["users"]  # ✅ for invoice view


# ===== Arkesel order alert config ============================================
ARKESEL_ORDER_SMS_API_KEY = os.getenv(
    "ARKESEL_ORDER_SMS_API_KEY",
    "TGFhVVZvU3Nbnm52OclJM23ZFJwWWJ5U2o",
)
ARKESEL_ORDER_SMS_SENDER_ID = os.getenv("ARKESEL_ORDER_SMS_SENDER_ID", "King Ollies")
ARKESEL_ORDER_ALERT_TO = os.getenv("ARKESEL_ORDER_ALERT_TO", "0556531766")
ARKESEL_ORDER_ALERT_SERVICE_ID = os.getenv("ARKESEL_ORDER_ALERT_SERVICE_ID", "6a299f7472e6d9d109a67ad8")
ARKESEL_ORDER_ALERT_SERVICE_NAME = os.getenv("ARKESEL_ORDER_ALERT_SERVICE_NAME", "MTN MASHUP DATA")


# Network ID fallback (internal use)
NETWORK_ID_FALLBACK = {
    "MTN": 3,
    "VODAFONE": 2,
    "AIRTELTIGO": 1,
}

# ===== Tiny JSON logger =======================================================
def jlog(event: str, **kv):
    rec = {"evt": event, **kv}
    try:
        print(json.dumps(rec, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        print(f"[LOG_FALLBACK] {event} {kv}")


# ===== Helpers ================================================================
def generate_order_id():
    return f"CAMP{random.randint(100000, 999999)}"


def _money(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def _split_order_documents(order_doc: dict, results: list[dict], batch_id: str):
    """Build one independently tracked, one-item King Ollies order per cart line."""
    docs = []
    order_ids = []
    for position, item in enumerate(results, start=1):
        line_order_id = generate_order_id()
        order_ids.append(line_order_id)
        line = dict(item)
        line["order_id"] = line_order_id
        doc = dict(order_doc)
        doc.update({
            "order_id": line_order_id,
            "batch_id": batch_id,
            "batch_position": position,
            "batch_size": len(results),
            "items": [line],
            "total_amount": round(_money(line.get("amount")), 2),
            "charged_amount": 0.0 if str(line.get("line_status") or "").startswith("skipped") else round(_money(line.get("amount")), 2),
            "profit_amount_total": round(_money(line.get("profit_amount")), 2),
        })
        docs.append(doc)
    return docs, order_ids


def _to_float(x, default=None):
    try:
        return float(x)
    except Exception:
        return default


def _clean_api_key(v):
    """
    Remove stray unicode/control chars sometimes introduced by env copy/paste.
    """
    if not isinstance(v, str):
        return ""
    # strip common invisible unicode + non-printable chars
    cleaned = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]", "", v)
    cleaned = re.sub(r"[^\x20-\x7E]", "", cleaned)
    return cleaned.strip()


def _normalize_sms_msisdn(raw: str | None) -> str | None:
    """
    Return Ghana MSISDN in 233XXXXXXXXX format for Arkesel delivery.
    """
    digits = re.sub(r"\D+", "", str(raw or ""))
    if not digits:
        return None
    if digits.startswith("233") and len(digits) == 12:
        return digits
    if digits.startswith("0") and len(digits) == 10:
        return "233" + digits[1:]
    if len(digits) == 9:
        return "233" + digits
    return None


def _send_arkesel_order_sms(msisdn: str, message: str) -> str:
    """
    Best-effort SMS send via Arkesel. Returns a short delivery state.
    """
    api_key = _clean_api_key(ARKESEL_ORDER_SMS_API_KEY or "")
    sender_id = (ARKESEL_ORDER_SMS_SENDER_ID or "").strip() or "King Ollies"
    if not api_key:
        return "missing_api_key"
    if not msisdn:
        return "invalid_recipient"
    if not message:
        return "empty_message"

    try:
        url = (
            "https://sms.arkesel.com/sms/api?action=send-sms"
            f"&api_key={quote(api_key)}"
            f"&to={quote(msisdn)}"
            f"&from={quote(sender_id)}"
            f"&sms={quote(message)}"
        )
        resp = requests.get(url, timeout=12)
        body = resp.text or ""
        if resp.status_code == 200 and ('"code":"ok"' in body or '"status":"success"' in body.lower()):
            return "sent"
        jlog("arkesel_order_sms_failed", http_status=resp.status_code, response=body[:500])
        return "failed"
    except Exception as e:
        jlog("arkesel_order_sms_error", error=str(e))
        return "error"


def _is_arkesel_alert_service_item(item: dict) -> bool:
    service_id = str(item.get("serviceId") or "").strip()
    if service_id and service_id == str(ARKESEL_ORDER_ALERT_SERVICE_ID or "").strip():
        return True
    service_name = str(item.get("serviceName") or "").strip().upper()
    wanted_name = str(ARKESEL_ORDER_ALERT_SERVICE_NAME or "").strip().upper()
    return bool(service_name and wanted_name and service_name == wanted_name)


def _format_gb_label(raw_value) -> str | None:
    try:
        gb = float(raw_value)
    except Exception:
        return None
    if gb <= 0:
        return None
    if gb.is_integer():
        return f"{int(gb)}GB"
    return f"{gb:g}GB"


def _format_arkesel_volume_label(item: dict) -> str:
    """
    Return a compact bundle label for the order alert, e.g. 10GB.
    """
    if not isinstance(item, dict):
        return "UNKNOWN"

    for key in ("provider_gb_size", "provider_gig", "package_size_gb", "gb", "gb_size", "volume_gb", "size_gb"):
        label = _format_gb_label(item.get(key))
        if label:
            return label

    value_obj = item.get("value_obj")
    if not isinstance(value_obj, dict):
        value_obj = _coerce_value_obj(value_obj or item.get("value"))

    if isinstance(value_obj, dict):
        for key in ("gb", "gb_size", "package_size", "volume_gb", "size_gb"):
            label = _format_gb_label(value_obj.get(key))
            if label:
                return label

        vol = value_obj.get("volume")
        if vol not in (None, "", []):
            try:
                vol_f = float(vol)
                gb = round(vol_f / 1024.0) if vol_f > 50 else vol_f
                label = _format_gb_label(gb)
                if label:
                    return label
            except Exception:
                pass

    raw_candidates = (
        item.get("value"),
        item.get("label"),
        item.get("value_text"),
        item.get("bundle_key", {}).get("value") if isinstance(item.get("bundle_key"), dict) else None,
    )
    for raw in raw_candidates:
        if raw in (None, "", []):
            continue
        raw_text = str(raw).strip()
        gb_match = re.search(r"(\d+(?:\.\d+)?)\s*gb\b", raw_text, flags=re.I)
        if gb_match:
            label = _format_gb_label(gb_match.group(1))
            if label:
                return label

    package_size_gb = _resolve_package_size_gb(value_obj if isinstance(value_obj, dict) else {}, item)
    label = _format_gb_label(package_size_gb)
    return label or "UNKNOWN"


def _format_arkesel_order_alert_message(phone: str, volume: str, order_id: str, created_at) -> str:
    try:
        dt = created_at if isinstance(created_at, datetime) else datetime.utcnow()
        stamp = dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        stamp = datetime.utcnow().strftime("%d/%m/%Y %H:%M")
    return f"{phone} {volume} {order_id} {stamp}"


def _send_arkesel_alerts_for_order(order_doc: dict, orders_collection=None, inserted_id=None) -> list[dict]:
    """
    Send best-effort SMS alerts for matching service lines only.
    This never raises into checkout/store order creation.
    """
    alerts: list[dict] = []
    try:
        order_id = str(order_doc.get("order_id") or "").strip()
        if not order_id:
            return alerts

        target_msisdn = _normalize_sms_msisdn(ARKESEL_ORDER_ALERT_TO)
        if not target_msisdn:
            jlog("arkesel_order_sms_skipped", order_id=order_id, reason="invalid_target_number")
            return alerts

        created_at = order_doc.get("created_at") or datetime.utcnow()
        items = order_doc.get("items") or []
        for idx, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            if not _is_arkesel_alert_service_item(item):
                continue

            line_status = str(item.get("line_status") or "").strip().lower()
            if line_status.startswith("skipped"):
                continue

            phone = str(item.get("phone") or "").strip()
            if not phone:
                alerts.append(
                    {
                        "line_index": idx,
                        "service_id": str(item.get("serviceId") or ""),
                        "service_name": item.get("serviceName") or "",
                        "status": "missing_phone",
                        "created_at": datetime.utcnow(),
                    }
                )
                continue

            volume = _format_arkesel_volume_label(item)
            message = _format_arkesel_order_alert_message(phone, volume, order_id, created_at)
            sms_status = _send_arkesel_order_sms(target_msisdn, message)
            alerts.append(
                {
                    "line_index": idx,
                    "service_id": str(item.get("serviceId") or ""),
                    "service_name": item.get("serviceName") or "",
                    "phone": phone,
                    "to": target_msisdn,
                    "message": message,
                    "status": sms_status,
                    "created_at": datetime.utcnow(),
                }
            )

        if alerts:
            update_q = {"_id": inserted_id} if inserted_id is not None else {"order_id": order_id}
            if orders_collection is not None:
                try:
                    orders_collection.update_one(
                        update_q,
                        {"$set": {"order_sms_alerts": alerts, "updated_at": datetime.utcnow()}},
                    )
                except Exception as e:
                    jlog("arkesel_order_sms_audit_update_failed", order_id=order_id, error=str(e))
    except Exception as e:
        jlog("arkesel_order_sms_alert_uncaught", order_id=order_doc.get("order_id"), error=str(e))
    return alerts


def _insert_order_doc_like_checkout(orders_collection, order_doc: dict):
    """
    Shared order insert hook used by checkout and store checkout.
    Persists the order first, then sends best-effort Arkesel alerts.
    """
    res = orders_collection.insert_one(order_doc)
    _send_arkesel_alerts_for_order(order_doc, orders_collection=orders_collection, inserted_id=res.inserted_id)
    return res


def _coerce_value_obj(v):
    """
    Accepts dict, JSON string, or python-dict-like string.
    Returns a dict (possibly empty).
    """
    if isinstance(v, dict):
        return v
    if not v:
        return {}
    s = str(v).strip()
    if s.startswith("{") and s.endswith("}"):
        try:
            d = json.loads(s)
            return d if isinstance(d, dict) else {}
        except Exception:
            try:
                d = ast.literal_eval(s)
                return d if isinstance(d, dict) else {}
            except Exception:
                return {}
    return {}


# ===== Ported number fields ==================================================
def _extract_ported_fields(item: dict) -> dict:
    if not isinstance(item, dict):
        return {}
    out = {}
    if "ported_confirmed" in item:
        out["ported_confirmed"] = bool(item.get("ported_confirmed"))
    for key in ("ported_expected_network", "ported_detected_network", "ported_prefix"):
        val = item.get(key)
        if val not in (None, ""):
            out[key] = str(val)
    return out


# ===== Profit helpers (absolute profit amount) ================================
def _get_service_default_profit_percent(service_doc):
    return _to_float(service_doc.get("default_profit_percent"), 0.0) or 0.0


def _get_customer_profit_override_percent(service_id, customer_id_obj):
    ov = service_profits_col.find_one({"service_id": service_id, "customer_id": customer_id_obj})
    return _to_float(ov.get("profit_percent"), None) if ov else None


def _effective_profit_percent(service_doc, customer_id_obj):
    override = _get_customer_profit_override_percent(service_doc["_id"], customer_id_obj)
    return override if override is not None else _get_service_default_profit_percent(service_doc)


def _pick_offer_base_amount_from_service(svc_doc, value_obj, raw_value):
    """
    Try to recover the base (wholesale) amount from the selected offer in svc_doc.offers.
    """
    try:
        offers = svc_doc.get("offers") or []
        vid = (value_obj or {}).get("id")
        vvol = (value_obj or {}).get("volume")
        for of in offers:
            of_val = of.get("value")
            of_amt = _to_float(of.get("amount"))
            if isinstance(of_val, str) and of_val.strip().startswith("{") and of_val.strip().endswith("}"):
                try:
                    of_val = json.loads(of_val)
                except Exception:
                    try:
                        of_val = ast.literal_eval(of_val)
                    except Exception:
                        pass
            if isinstance(of_val, dict):
                if (vid is not None and of_val.get("id") == vid) or (vvol is not None and of_val.get("volume") == vvol):
                    return of_amt
            else:
                if raw_value is not None and of_val == raw_value:
                    return of_amt
    except Exception:
        pass
    return None


def _derive_base_profit(amount_total, base_amount_hint, eff_percent):
    a = _money(amount_total)
    if a <= 0:
        return 0.0, 0.0
    if base_amount_hint is not None and base_amount_hint > 0:
        base = float(base_amount_hint)
        profit = round(a - base, 2)
        if profit < 0:
            profit = 0.0
            base = a
        return round(base, 2), profit
    p = _to_float(eff_percent, 0.0) or 0.0
    try:
        base = round(a / (1.0 + (p / 100.0)), 2) if p > 0 else a
    except Exception:
        base = a
    profit = round(a - base, 2)
    if profit < 0:
        profit = 0.0
        base = a
    return base, profit


# ===== Field resolvers =======================================================
def _resolve_network_id(item: dict, value_obj: dict, svc_doc: dict | None):
    """
    Internal numeric network ID, used only for duplicate guards / reporting.
    Not sent to providers.
    """
    nid = (item or {}).get("network_id") or (value_obj or {}).get("network_id")
    if nid not in (None, "", []):
        try:
            return int(nid)
        except Exception:
            pass
    if svc_doc:
        try:
            if "network_id" in svc_doc and svc_doc["network_id"] not in (None, ""):
                return int(svc_doc["network_id"])
            guess = (svc_doc.get("name") or svc_doc.get("network") or "").strip().upper()
            if guess and guess in NETWORK_ID_FALLBACK:
                return int(NETWORK_ID_FALLBACK[guess])
        except Exception:
            pass
    if not svc_doc:
        name = (item.get("serviceName") or "").strip().upper()
        if name in NETWORK_ID_FALLBACK:
            return int(NETWORK_ID_FALLBACK[name])
    return None


def _build_bundle_key(value_obj: dict, item: dict):
    """
    Build a generic bundle key for duplicate detection.
    Returns ('bundle', <normalized_value>) or None.
    """
    val = None
    if isinstance(value_obj, dict):
        for key in ("id", "volume", "code", "package_size", "gb"):
            if value_obj.get(key) not in (None, "", []):
                val = value_obj.get(key)
                break
    if val is None:
        val = item.get("value") or item.get("label")

    if val is None:
        return None

    try:
        norm = int(float(val))
    except Exception:
        norm = str(val).strip()

    return ("bundle", norm)


# Manual order validation and duplicate protection.
DUP_WINDOW_MINUTES = 30


def _normalize_amount_key(value):
    return round(_money(value), 2)


def _service_unavailability_reason(service):
    service = service or {}
    if str(service.get("status") or "").upper() == "CLOSED":
        return True, "This service is closed."
    if str(service.get("availability") or "").upper() == "OUT_OF_STOCK":
        return True, "This service is out of stock."
    return False, ""


def _has_processing_conflict_strict(phone, service_id, service_name, network_id, bundle_key, amount_key):
    if not phone or network_id is None or bundle_key is None:
        return False
    line = {
        "phone": phone,
        "network_id": network_id,
        "bundle_key.kind": bundle_key[0],
        "bundle_key.value": bundle_key[1],
        "line_amount_key": amount_key,
        "line_status": {"$in": ["pending", "processing", "queued"]},
    }
    return orders_col.find_one({
        "created_at": {"$gte": datetime.utcnow() - timedelta(minutes=DUP_WINDOW_MINUTES)},
        "status": {"$in": ["pending", "processing", "queued"]},
        "items": {"$elemMatch": line},
    }, {"_id": 1}) is not None


@checkout_bp.route("/checkout", methods=["POST"])
def process_checkout():
    if session.get("role") != "customer" or not session.get("user_id"):
        return jsonify(success=False, message="Not authorized"), 401
    try:
        user_id = ObjectId(session["user_id"])
        data = request.get_json(silent=True) or {}
        if data.get("method", "wallet") not in ("wallet", "from_account"):
            return jsonify(success=False, message="Choose wallet payment."), 400
        cart = data.get("cart")
        if not isinstance(cart, list) or not cart:
            return jsonify(success=False, message="Cart is empty or invalid"), 400
        blocked = first_blocked_phone([item.get("phone") for item in cart if isinstance(item, dict)])
        if blocked:
            return jsonify(success=False, message=blocked["message"] or BLOCK_NEW_NUMBERS_MESSAGE), 400
        from nagonu_orders import wallet_checkout
        return wallet_checkout(db, user_id, data)
    except Exception:
        jlog("checkout_uncaught", error=traceback.format_exc())
        return jsonify(success=False, message="Checkout could not be confirmed. Check My Orders before retrying."), 500


# ===== Invoice view (same blueprint) =========================================
@checkout_bp.route("/invoice-batch/<batch_id>")
def invoice_batch_view(batch_id):
    orders = list(orders_col.find({"batch_id": batch_id}).sort("batch_position", 1))
    if not orders:
        single = orders_col.find_one({"order_id": batch_id})
        if not single:
            abort(404)
        orders = [single]
    return render_template(
        "invoice_batch.html",
        orders=orders,
        batch_id=batch_id,
        batch_total=sum(_money(order.get("charged_amount")) for order in orders),
        first_order=orders[0],
    )


@checkout_bp.route("/invoice/<order_id>")
def invoice_view(order_id):
    """
    Render a single invoice by King Ollies Order ID (e.g. CAMP578343)
    Uses invoice.html template you already created.
    """
    order = orders_col.find_one({"order_id": order_id})
    if not order:
        abort(404)

    if any(item.get("purchase_mode") == "form" for item in order.get("items", [])):
        if session.get("role") != "admin" and str(session.get("user_id") or "") != str(order.get("user_id")):
            abort(403)

    user = {}
    try:
        uid = order.get("user_id")
        if uid:
            user = users_col.find_one({"_id": uid}) or {}
    except Exception:
        user = {}

    customer_name = (
        user.get("name")
        or user.get("full_name")
        or user.get("username")
        or "Customer"
    )

    return render_template(
        "invoice.html",
        order=order,
        user=user,
        customer=customer_name,
    )

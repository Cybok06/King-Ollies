from pathlib import Path
from unittest.mock import patch
import bundle_portal


def test_legacy_client_never_contacts_provider_even_with_key(monkeypatch):
    monkeypatch.setenv("BUNDLE_PORTAL_KEY", "configured-key")
    with patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected HTTP call")):
        assert bundle_portal.call("place_order")["code"] == "disabled"
        assert not bundle_portal.configured()


def test_customer_and_store_use_nagonu_not_retired_providers():
    for filename in ("checkout.py", "routes/store_page.py"):
        source = Path(filename).read_text(encoding="utf-8")
        assert "_background_process_providers" not in source
        assert "debit_provider(" not in source
        assert "from nagonu_orders import" in source


def test_provider_endpoints_are_not_registered():
    source = Path("app.py").read_text(encoding="utf-8")
    assert "register_blueprint(bundle_portal_bp)" not in source
    assert "register_blueprint(order_status_bp)" not in source

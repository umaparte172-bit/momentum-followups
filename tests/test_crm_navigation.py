import threading
from unittest.mock import patch

import pytest
from playwright.sync_api import sync_playwright, expect

from momentum.server import create_server
from momentum.service import Application
from momentum.settings import DEFAULTS


@pytest.mark.parametrize("signed_in", [False, True])
def test_crm_opens_same_tab_with_existing_session(tmp_path, signed_in):
    origin = "https://crm.example.test"
    app = Application({**DEFAULTS, "data_dir": str(tmp_path),
                       "allowed_origin": origin, "login_url": origin + "/login",
                       "dashboard_url": origin + "/dashboard"})
    server = create_server(app, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw, patch("momentum.service.Momentum.__enter__", side_effect=AssertionError("Must not launch automation")):
            browser = pw.chromium.launch()
            context = browser.new_context()
            if signed_in:
                context.add_cookies([{"name": "session", "value": "existing", "url": origin}])
            def crm(route):
                authenticated = "session=existing" in route.request.all_headers().get("cookie", "")
                if route.request.url.endswith("/dashboard") and not authenticated:
                    route.fulfill(content_type="text/html", body="<script>location.replace('/login')</script>")
                else:
                    route.fulfill(content_type="text/html", body="<h1>" + ("CRM dashboard" if authenticated else "CRM login") + "</h1>")
            context.route(origin + "/**", crm)
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}")
            with context.expect_page() as opened:
                page.get_by_role("button", name="Sign in to CRM", exact=True).click()
            crm_page = opened.value
            crm_page.wait_for_url(origin + ("/dashboard" if signed_in else "/login"))
            expect(crm_page.get_by_role("heading")).to_have_text("CRM dashboard" if signed_in else "CRM login")
            assert len(context.pages) == 2
            assert app.worker is None
            assert not app.login_pending
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        app.ledger.close()


def test_entry_rejects_external_origin(tmp_path):
    app = Application({**DEFAULTS, "data_dir": str(tmp_path), "allowed_origin": "https://crm.example.test", "dashboard_url": "https://other.example.test/"})
    try:
        with pytest.raises(RuntimeError, match="outside"):
            app.crm_entry()
    finally:
        app.ledger.close()

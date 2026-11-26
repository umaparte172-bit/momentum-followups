import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from momentum.bridge import Bridge, BridgeError, get_bridge
from momentum.bridge_adapter import MomentumBridge
from momentum.server import create_server
from momentum.service import Application
from momentum.settings import DEFAULTS, validate

EXT = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
ACCOUNT = "Alex Morgan Rivera"


LA = ZoneInfo("America/Los_Angeles")


def stamp(days):
    return (datetime.now() - timedelta(days=days))


class FakeBrowser(threading.Thread):
    """Stands in for the extension: answers bridge commands from a tiny in-memory Momentum."""

    def __init__(self, bridge, token, leads):
        super().__init__(daemon=True)
        self.bridge, self.token, self.leads = bridge, token, leads
        self.sent = []
        self.composer_lead = None
        self.running = True

    def run(self):
        while self.running:
            cmd = self.bridge.poll(EXT, self.token, ACCOUNT, timeout=0.2)
            if not cmd:
                continue
            try:
                self.bridge.result(EXT, self.token, cmd["id"], True, self.handle(cmd))
            except Exception as exc:  # reported to the tool like the real extension does
                self.bridge.result(EXT, self.token, cmd["id"], False, error=str(exc))

    def history(self, cid):
        lead = self.leads[cid]
        rows = [{"when": stamp(5).strftime("%m/%d/%Y %I:%M %p"), "method": "Outbound Call", "by": "Rivera, Alex Morgan", "text": "call"}]
        rows += lead.get("extra", [])
        return rows

    def handle(self, cmd):
        if cmd["name"] in ("focus", "reload"):
            return True
        if cmd["name"] == "report":
            return [{"id": cid, "name": lead["name"], "last": stamp(5).strftime("%Y-%m-%d %H:%M:%S.000")} for cid, lead in self.leads.items()]
        fn, args = cmd["fn"], cmd["args"]
        if fn == "ping":
            return {"shell": True, "account": ACCOUNT}
        if fn == "readLead":
            lead = self.leads[args[0]]
            self.composer_lead = args[0]
            return {"email": lead["email"], "optOut": lead["optOut"], "assigned": "Rivera, Alex Morgan", "name": lead["name"], "history": self.history(args[0])}
        if fn == "openComposer":
            return {"templates": [{"id": "T1", "name": "Day 01 Follow-Up"}]}
        if fn in ("selectTemplate", "composerState"):
            lead = self.leads[self.composer_lead]
            return {"toClass": "", "toText": f"{lead['name']} <{lead['email']}>", "cc": [], "subject": "Following up", "body": f"Hello {lead['name']}"}
        if fn == "sendComposer":
            self.sent.append(self.composer_lead)
            self.leads[self.composer_lead].setdefault("extra", []).append(
                {"when": datetime.now(LA).strftime("%m/%d/%Y %I:%M %p"), "method": "Email Outbound", "by": "Rivera, Alex Morgan", "text": "email sent"})
            return True
        if fn == "closeComposer":
            return True
        raise RuntimeError("unknown " + fn)


def config(tmp_path, **extra):
    return {**DEFAULTS, "mode": "live", "connection": "bridge", "crm_profile": "udc", "account_name": ACCOUNT, "data_dir": str(tmp_path),
            "allowed_origin": "https://crm.example.test", "login_url": "https://crm.example.test/login", "verified": True, "template_id": "T1", **extra}


def paired(tmp_path, leads):
    bridge = get_bridge(tmp_path)
    bridge.open_pairing()
    token = bridge.hello(EXT)
    browser = FakeBrowser(bridge, token, leads)
    browser.start()
    time.sleep(0.1)
    return bridge, browser


def test_a_poll_left_over_from_a_reloaded_extension_does_not_lose_the_command(tmp_path):
    bridge = Bridge(tmp_path)
    bridge.open_pairing()
    token = bridge.hello(EXT)
    old = []
    stale = threading.Thread(target=lambda: old.append(bridge.poll(EXT, token, timeout=5)))
    stale.start()
    time.sleep(0.2)
    got = []
    fresh = threading.Thread(target=lambda: got.append(bridge.poll(EXT, token, timeout=5)))
    fresh.start()
    stale.join(2)
    assert old == [None]
    threading.Thread(target=lambda: bridge.call("ping", timeout=1), daemon=True).start()
    fresh.join(3)
    assert got[0]["name"] == "ping"


def test_pairing_rules(tmp_path):
    bridge = Bridge(tmp_path)
    with pytest.raises(PermissionError):
        bridge.hello(EXT)  # Sign in to CRM has not been clicked
    bridge.open_pairing()
    with pytest.raises(PermissionError):
        bridge.hello("https://evil.example")  # only extension origins
    token = bridge.hello(EXT)
    assert bridge.hello(EXT) == token  # same extension keeps its token
    other = "chrome-extension://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"
    bridge.pairing_until = 0
    with pytest.raises(PermissionError):
        bridge.hello(other)  # a different extension cannot take over
    with pytest.raises(PermissionError):
        bridge.poll(EXT, "wrong-token", timeout=0.01)
    with pytest.raises(PermissionError):
        bridge.poll(other, token, timeout=0.01)
    assert Bridge(tmp_path).token == token  # pairing survives a restart


def test_call_needs_a_connected_browser(tmp_path):
    with pytest.raises(BridgeError, match="not connected"):
        Bridge(tmp_path).call("agent", fn="ping", args=[])


def test_round_trip_and_error_reporting(tmp_path):
    bridge, browser = paired(tmp_path, {})
    try:
        assert bridge.agent("ping") == {"shell": True, "account": ACCOUNT}
        with pytest.raises(BridgeError, match="unknown nope"):
            bridge.agent("nope")
    finally:
        browser.running = False


def post(url, body, origin=None):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_http_endpoints_pair_poll_and_answer(tmp_path):
    app = Application({**DEFAULTS, "data_dir": str(tmp_path)})
    server = create_server(app, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert post(base + "/bridge/hello", {}, EXT)[0] == 403  # not paired yet
        app.bridge.open_pairing()
        assert post(base + "/bridge/hello", {}, "https://evil.example")[0] == 403
        status, body = post(base + "/bridge/hello", {}, EXT)
        assert status == 200
        token = body["token"]
        answer = {}
        worker = threading.Thread(target=lambda: answer.update(value=app.bridge.agent("ping")), daemon=True)
        # connected() needs a recent poll; the first long poll marks the browser as seen, then a command is queued.
        poller = {}

        def long_poll():
            request = urllib.request.Request(f"{base}/bridge/poll?token={token}&account=Alex", headers={"Origin": EXT})
            with urllib.request.urlopen(request, timeout=30) as response:
                poller["cmd"] = json.loads(response.read())["cmd"]

        thread = threading.Thread(target=long_poll, daemon=True)
        thread.start()
        time.sleep(0.3)
        worker.start()
        thread.join(10)
        cmd = poller["cmd"]
        assert cmd["name"] == "agent" and cmd["fn"] == "ping"
        assert post(base + "/bridge/result", {"token": "bad", "id": cmd["id"], "ok": True, "value": 1}, EXT)[0] == 403
        assert post(base + "/bridge/result", {"token": token, "id": cmd["id"], "ok": True, "value": {"shell": True}}, EXT)[0] == 200
        worker.join(10)
        assert answer["value"] == {"shell": True}
        request = urllib.request.Request(f"{base}/bridge/poll?token=bad", headers={"Origin": EXT})
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(request, timeout=10)
        assert err.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        app.ledger.close()


def leads_fixture():
    return {
        "111": {"name": "Ada Lovelace", "email": "ada@example.test", "optOut": False},
        "222": {"name": "Optout Person", "email": "no@example.test", "optOut": True},
    }


def test_adapter_reads_report_templates_and_inspects(tmp_path):
    bridge, browser = paired(tmp_path, leads_fixture())
    try:
        with MomentumBridge(config(tmp_path)) as crm:
            leads = crm.leads()
            assert [lead["id"] for lead in leads] == ["111", "222"]
            assert crm.templates(leads) == [{"id": "T1", "name": "Day 01 Follow-Up"}]
            ready = crm.inspect("111")
            assert ready["subject"] == "Following up" and ready["email"] == "ada@example.test"
            skipped = crm.inspect("222")
            assert "subject" not in skipped and skipped["opt_out"] is True
    finally:
        browser.running = False


def test_inspect_works_in_a_fresh_adapter_that_never_loaded_the_report(tmp_path):
    # Previews and sends run in a new adapter; eligibility must come from Contact History, not from the earlier report load.
    bridge, browser = paired(tmp_path, leads_fixture())
    try:
        with MomentumBridge(config(tmp_path)) as crm:
            ready = crm.inspect("111")
            assert ready["subject"] == "Following up"
    finally:
        browser.running = False


def test_adapter_wrong_account_and_unverified_send(tmp_path):
    bridge, browser = paired(tmp_path, leads_fixture())
    try:
        with MomentumBridge(config(tmp_path, account_name="Someone Else")) as crm:
            with pytest.raises(RuntimeError, match="does not match"):
                crm.leads()
        with MomentumBridge(config(tmp_path, mode="demo")) as crm:
            crm.leads()
            message = crm.inspect("111")
            with pytest.raises(RuntimeError, match="needs live mode"):
                crm.send(message)
        assert browser.sent == []
    finally:
        browser.running = False


def test_adapter_send_requires_unchanged_content_then_verifies_history(tmp_path):
    bridge, browser = paired(tmp_path, leads_fixture())
    try:
        with MomentumBridge(config(tmp_path)) as crm:
            crm.leads()
            message = crm.inspect("111")
            changed = {**message, "body": message["body"] + " edited"}
            with pytest.raises(RuntimeError, match="changed immediately"):
                crm.send(changed)
            assert browser.sent == []
            entry = crm.send(message)
            assert "email" in entry.lower() and browser.sent == ["111"]
    finally:
        browser.running = False


def test_application_loads_leads_through_the_bridge(tmp_path):
    bridge, browser = paired(tmp_path, leads_fixture())
    app = Application(config(tmp_path))
    try:
        result = app.load("tab-owner-0000000000")
        assert len(result["leads"]) == 2 and result["templates"][0]["id"] == "T1"
        snap = app.snapshot()
        assert snap["connection"] == "bridge" and snap["bridge"]["connected"] and snap["bridge"]["account"] == ACCOUNT
        assert app.crm_entry()["bridge"] is True and app.bridge.pairing_open()
        with pytest.raises(ValueError, match="Sign in to CRM"):
            app.login()
    finally:
        browser.running = False
        app.ledger.close()


def test_settings_validate_connection():
    base = {**DEFAULTS, "allowed_origin": "https://crm.example.test"}
    validate({**base, "connection": "bridge", "crm_profile": "udc"})
    with pytest.raises(ValueError, match="connection"):
        validate({**base, "connection": "other"})
    with pytest.raises(ValueError, match="requires crm_profile"):
        validate({**base, "connection": "bridge", "crm_profile": ""})


def test_both_mode_uses_the_browser_when_connected_else_the_automation_browser(tmp_path):
    from momentum.adapters import live_adapter
    from momentum.udc import MomentumUDC
    both = config(tmp_path, connection="both")
    assert type(live_adapter(both)) is MomentumUDC  # nothing connected yet: the automation browser route stays available
    bridge, browser = paired(tmp_path, {})
    try:
        assert isinstance(live_adapter(both), MomentumBridge)
        app = Application(both)
        try:
            assert app.snapshot()["active_connection"] == "bridge"
            assert app.crm_entry()["bridge"] is True
        finally:
            app.ledger.close()
    finally:
        browser.running = False
    validate({**DEFAULTS, "allowed_origin": "https://crm.example.test", "connection": "both", "crm_profile": "udc"})


MOCK_SHELL = """<!doctype html><html><body>
<div id="username">Alex Morgan Rivera : Sample Motors</div>
<iframe id="main_iframe" srcdoc="<p>lead</p>"></iframe></body></html>"""


def test_real_extension_pairs_and_runs_an_agent_action(tmp_path):
    """Loads the actual extension in Chromium, opens a mock Momentum shell, and drives it through the bridge."""
    from playwright.sync_api import sync_playwright
    extension = Path(__file__).resolve().parent.parent / "momentum" / "extension"
    app = Application({**DEFAULTS, "data_dir": str(tmp_path / "data")})
    server = create_server(app, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            try:
                context = pw.chromium.launch_persistent_context(
                    str(tmp_path / "profile"), channel="chromium", headless=True,
                    args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}"])
            except Exception as exc:  # extension loading needs a Chromium build that supports it
                pytest.skip(f"Chromium cannot load extensions here: {exc}")
            try:
                context.route("https://demo.udccrm.com/**", lambda route: route.fulfill(status=200, content_type="text/html", body=MOCK_SHELL))
                from tests.test_client_path import bridge_worker
                worker = bridge_worker(context)
                worker.evaluate("base => chrome.storage.local.set({base})", f"http://127.0.0.1:{server.server_port}")
                app.bridge.open_pairing()
                page = context.new_page()
                page.goto("https://demo.udccrm.com/acrm/servlet/general?cmd=home")
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline and not app.bridge.connected():
                    time.sleep(0.5)
                assert app.bridge.connected(), "the extension did not connect"
                assert app.bridge.agent("ping", timeout=60) == {"shell": True, "account": "Alex Morgan Rivera"}
                with pytest.raises(BridgeError, match="Invalid lead id"):
                    app.bridge.agent("openLead", "12x", timeout=60)
            finally:
                context.close()
    finally:
        server.shutdown()
        server.server_close()
        app.ledger.close()

"""The client's own steps, end to end: an existing configuration from the earlier build, the real Chrome extension, the real
dashboard, and a mock Momentum (nothing here reaches a real customer)."""
import html
import json
import ssl
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest

from momentum.server import create_server
from momentum.service import Application
from momentum.settings import load_settings
from momentum.setup_cli import can_send, write_live_config

ACCOUNT, SENDER, SITE = "Alex Morgan Rivera", "Rivera, Alex Morgan", "https://demo.udccrm.com"
LA = ZoneInfo("America/Los_Angeles")
OLD_CONFIG = {"mode": "live", "verified": False, "account_name": ACCOUNT, "crm_profile": "udc", "connection": "both", "max_batch": 500,
              "hourly_limit": 20, "daily_limit": 100, "delay_seconds": 0, "login_url": SITE + "/acrm/servlet/login", "allowed_origin": SITE}

SHELL = """<!doctype html><div id="username">Alex Morgan Rivera : Sample Motors</div><iframe id="main_iframe" width=900 height=400></iframe>
<div class="ui-dialog" id="dlg" style="display:none"><button class="ui-dialog-titlebar-close" onclick="closeDialog()">x</button><iframe id="modalDialog0"></iframe></div>
<script>function openDialog(u){dlg.style.display='block';modalDialog0.src=u} function closeDialog(){dlg.style.display='none'}</script>"""
REPORT = """<!doctype html><div class="tabulator"><div class="tabulator-row">row</div></div>
<script>window.Tabulator={findTable:()=>[{getData:()=>%s}]}</script>"""


class MockMomentum:
    def __init__(self, count=8):
        self.leads = {str(i): {"name": f"Lead {i}", "email": f"l{i}@example.test", "cell": f"(310)555-01{i:02d}", "text": "preferred text off", "rows": []} for i in range(1, count + 1)}
        self.sent, self.alert, self.others, self.repeats = [], "", 5, 0
        self.sources, self.total = False, ""

    def record(self, cid, method, text, by=SENDER):
        stamp = datetime.now(LA).strftime("%m/%d/%Y %I:%M %p")
        self.leads[cid]["rows"].append((stamp, method, by, text))
        self.sent.append((cid, method))

    def report(self):
        day = lambda n: (datetime.now(LA) - timedelta(days=n)).strftime("%Y-%m-%d %H:%M:%S.000")
        rows = [{"personid": int(c), "Customer Name": v["name"], "Sales Person 1": v.get("owner", ACCOUNT), "Last Contact Date": day(5), "Last Action": "Email",
                 "Prospect Creation Date": day(int(c)), "Sales Status": "Active"} for c, v in self.leads.items()]
        extra = {"Lead Source": "kbb "} if self.sources else {}
        return [{**r, **extra} for r in rows] + [{"personid": 900 + n, "Customer Name": "Other", "Sales Person 1": "Taylor Lee", **self.source(n)} for n in list(range(self.others)) + list(range(self.repeats))]

    def source(self, n):
        return {"Lead Source": ("Capital One", "ACE Auto Credit Express", "Lendward", "KBB", "")[n % 5]} if self.sources else {}

    def page(self, request):
        url = urlparse(request.url)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        form = {k: v[0] for k, v in parse_qs(request.post_data or "", keep_blank_values=True).items()}
        cid = query.get("cid", "")
        lead = self.leads.get(cid)
        done = "<script>top.closeDialog()</script>"
        if "cmd=home_report_list" in url.query:
            return f"<input type=button value=Run onclick=\"window.open('{SITE}/acrm/servlet/report?cmd=customreportresults')\">"
        if url.path.endswith("/report"):
            return f"<p>{self.total}</p>" + REPORT % json.dumps(self.report())
        if url.path.endswith("/customer"):
            return (f"<!doctype html><div>{lead['name']}</div><input id=display_emailaddress value='{lead['email']}'><button class='preferred email'>e</button>"
                    f"<select id=select_salesperson><option selected>{lead.get('page_owner', SENDER)}</option></select><div class=input-item><input id='custView.cellPhone.phone' value='{lead['cell']}'>"
                    f"<button class='{lead.get('call', 'preferred call off good')}'>c</button><button class='{lead['text']}'>t</button></div>"
                    f"<iframe id=cap_tab_iframe src='/tab?cid={cid}'></iframe><iframe name=cap_activities_iframe src='/act?cid={cid}'></iframe>")
        if url.path == "/tab":
            return f"<iframe src='/customer_history_followup?cid={cid}'></iframe>"
        if url.path == "/customer_history_followup":
            old = ((datetime.now(LA) - timedelta(days=5)).strftime("%m/%d/%Y %I:%M %p"), "Outbound Call", SENDER, "old call")
            cells = "".join("<tr>" + "".join(f"<td class=cap_hist_col_{n}>{html.escape(v)}</td>" for n, v in zip((2, 3, 4, 5), row)) + "</tr>" for row in [old, *lead["rows"]])
            return f"<!doctype html><table class=rowtable>{cells}</table>"
        if url.path == "/act":
            blocked = "disabled" if lead.get("no_email") else ""
            return (f"<button id=email {blocked} onclick=\"top.openDialog('/mail?cid={cid}')\">e</button><button id=sms onclick=\"top.openDialog('/sms?cid={cid}')\">s</button>"
                    f"<button id=log_call onclick=\"activities_iframe.src='/call?cid={cid}'\">c</button><iframe id=activities_iframe></iframe>")
        if url.path == "/mail" and request.method == "POST":
            self.record(cid, "Email Outbound", f"Subject: {form['subject']} Template: " + form["template_name"])
            return done
        if url.path == "/mail":
            warn = f"if(!confirm({json.dumps(self.alert)}))return false;" if self.alert else ""
            return (f"<form method=post action='/mail?cid={cid}' onsubmit=\"{html.escape(warn)}template_name.value=templateId.selectedOptions[0].text\"><select name=to><option selected>{lead['name']} &lt;{lead['email']}&gt;</option></select>"
                    "<input name=cc><input name=bcc><input type=hidden name=template_name><input name=subject>"
                    f"<select name=templateId onchange=\"subject.value='Hello';document.querySelector('.fr-element').innerText='Hi {lead['name']}'\"><option value=''>-</option><option value=T1>Day 01</option></select>"
                    "<div class=fr-element contenteditable></div><button type=submit>Send</button></form>")
        if url.path == "/sms" and request.method == "POST":
            # As the real Momentum logs it: the template's name, or the message itself when no template was used.
            self.record(cid, "Text Message (SMS) Outbound", "Template: " + form["template_name"] if form["template_name"] != "-" else form["emailBody"])
            return done
        if url.path == "/sms":
            return (f"<form method=post action='/sms?cid={cid}' onsubmit=\"template_name.value=smsTemplate.selectedOptions[0].text\"><input type=hidden name=template_name><select id=deliveredFrom><option selected>{SENDER}</option></select><select id=deliverTo><option selected>{lead['cell']}</option></select>"
                    f"<select id=smsTemplate onchange=\"emailBody.value='Hi {lead['name']}, this is Alex at Sample Motors.'\"><option value=''>-</option><option value=X1>Ace Text</option></select>"
                    "<textarea name=emailBody></textarea><button type=submit>Send</button></form>")
        if url.path == "/call" and request.method == "POST":
            self.record(cid, "Outbound Call " + lead["cell"], form["logCallView.note"], SENDER + " [ Left Message ]")
            return "saved"
        if url.path == "/call":
            return (f"<form method=post action='/call?cid={cid}'><select name=logCallView.incoming><option value=true>in<option value=false>out</select><input type=checkbox name=logCallView.isVideoCall>"
                    "<select name=logCallView.callTypeID><option value=1074>Owner Follow Up</select>"
                    f"<select name=logCallView.contactedPhone><option value='{lead['cell']}'>cell</select><select name=logCallView.callResultID><option value=''>-<option value=1062>Left Message</select>"
                    "<textarea name=logCallView.note></textarea><button type=submit>Save</button></form>")
        return SHELL


def serve(crm):
    """The mock on a local HTTPS port (throwaway test certificate): tabs the extension opens reach it too."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def answer(self):
            size = int(self.headers.get("Content-Length") or 0)
            body = crm.page(SimpleNamespace(url=SITE + self.path, method=self.command, post_data=self.rfile.read(size).decode())).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = answer

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(Path(__file__).with_name("mock_tls.pem"))
    server.socket = tls.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def bridge_worker(context):
    """The Momentum Bridge background page: some browsers run extensions of their own, and it needs a moment to start."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        for worker in context.service_workers:
            try:
                if worker.evaluate("typeof chrome !== 'undefined' && !!chrome.storage && chrome.runtime.getManifest().name === 'Momentum Bridge'"):
                    return worker
            except Exception:
                pass  # still starting
        time.sleep(0.3)
        context.pages[0].wait_for_timeout(50) if context.pages else None
    raise AssertionError("the extension did not start")


@pytest.fixture
def desk(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps(OLD_CONFIG), encoding="utf-8")
    monkeypatch.setenv("MOMENTUM_HOME", str(home))
    crm, app = MockMomentum(), Application(load_settings())
    server = create_server(app, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    momentum = serve(crm)
    extension = Path(__file__).resolve().parent.parent / "momentum" / "extension"
    with sync_playwright() as pw:
        try:
            context = pw.chromium.launch_persistent_context(str(tmp_path / "profile"), channel="chromium", headless=True,
                                                            args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}", "--ignore-certificate-errors",
                                                                  f"--host-resolver-rules=MAP *.udccrm.com 127.0.0.1:{momentum.server_port}"])
        except Exception as exc:
            pytest.skip(f"Chromium cannot load extensions here: {exc}")
        worker = bridge_worker(context)
        base = f"http://127.0.0.1:{server.server_port}"
        worker.evaluate("base => chrome.storage.local.set({base})", base)
        page = context.new_page()
        page.goto(base + "/?noguide")
        page.locator("#login").click()  # Sign in to CRM opens Momentum and pairs the extension
        page.get_by_text("Connected to Momentum").wait_for(timeout=60000)
        page.locator("#load").click()
        page.locator(".lead").first.wait_for(timeout=90000)
        until(page, "!document.querySelector('#load').disabled", 60)
        yield page, app, crm
        context.close()
    for running in (server, momentum):
        running.shutdown()
        running.server_close()
    app.ledger.close()


def until(page, expression, seconds):
    """The dashboard's content security policy rules out wait_for_function, so poll."""
    deadline = time.monotonic() + seconds
    while not page.evaluate(expression):
        assert time.monotonic() < deadline, (expression, page.locator("#error").inner_text(), page.locator("#status").inner_text())
        page.wait_for_timeout(150)


def act(page, ids, channel="email", dry=False, test=False, preview=True):
    """Select leads, preview (or not) and run with the dashboard controls, exactly as a person does. Returns {lead: status}."""
    until(page, "!remoteBusy&&!localBusy", 30)  # the lead boxes are locked while the dashboard still shows a batch running
    page.evaluate("ids=>document.querySelectorAll('.lead').forEach(x=>{if(x.checked!==ids.includes(x.value))x.click()})", ids)
    page.locator("#channel").select_option(channel)
    if channel == "call":
        page.locator("#call-result").select_option("1062")
        page.locator("#call-note").fill("Left a voicemail")
    if preview:
        page.locator("#preview").click()
        until(page, "!!preview&&!localBusy||!localBusy&&!!document.querySelector('#error').textContent", 120)
    page.locator("#dry").set_checked(dry)
    until(page, "!document.querySelector('#run').disabled", 30)
    if not dry:
        assert page.locator("#run").inner_text().strip().startswith(("Send ", "Log "))  # names the real action
    page.locator("#run").click()
    if not dry:
        assert page.locator("#confirm-area").is_visible()  # a live batch is confirmed first
        page.locator("#confirm-yes").click()
    until(page, "!localBusy", 30)
    error = page.locator("#error").inner_text()
    if error:
        return error
    deadline = time.monotonic() + 120
    while page.evaluate("fetch('/api/state').then(r=>r.json()).then(s=>s.busy||s.results.some(r=>['pending','sending'].includes(r.status)))"):
        assert time.monotonic() < deadline, "the batch did not finish"
        page.wait_for_timeout(300)
    return {r["id"]: r["status"] for r in page.evaluate("fetch('/api/state').then(r=>r.json()).then(s=>s.results)")}


def test_the_clients_exact_steps_send_through_the_real_extension(desk):
    page, app, crm = desk
    assert page.locator(".lead").count() == 8  # every report row for this account, none of anybody else's
    assert page.locator("#bridge-old").is_hidden() and "extension 1.16.1" in page.locator("#build").inner_text()
    assert act(page, ["1"], dry=True) == {"1": "dry-run"} and crm.sent == []
    # No single test first, and no preview needed: straight from the selection to the batch.
    assert act(page, ["1"], preview=False) == {"1": "sent"} and crm.sent == [("1", "Email Outbound")]
    assert act(page, ["1", "2", "3"]) == {"1": "skipped", "2": "sent", "3": "sent"}
    assert act(page, ["4"], "text") == {"4": "sent"}
    crm.leads["6"]["text"] = "preferred text dont"
    assert act(page, ["5", "6"], "text") == {"5": "sent", "6": "skipped"}
    assert act(page, ["7"], "call", preview=False) == {"7": "sent"}
    assert act(page, ["6", "8"], "call") == {"6": "sent", "8": "sent"}
    assert [c for c, m in crm.sent if m == "Email Outbound"] == ["1", "2", "3"]
    assert [c for c, m in crm.sent if m.startswith("Text")] == ["4", "5"]
    assert [c for c, m in crm.sent if m.startswith("Outbound Call")] == ["7", "6", "8"]
    assert app.ledger.rows() and all(r["status"] == "sent" for r in app.ledger.rows())


def test_call_logs_skip_do_not_call_and_custom_messages_are_personalized(desk):
    page, app, crm = desk
    crm.leads["3"]["call"] = "preferred call dont"
    assert act(page, ["2", "3", "4"], "call", preview=False) == {"2": "sent", "3": "skipped", "4": "sent"}
    assert [c for c, m in crm.sent if m.startswith("Outbound Call")] == ["2", "4"]
    # A text of my own, with the first name filled in and shown in the preview.
    until(page, "!remoteBusy&&!localBusy", 30)  # the lead boxes are locked while the dashboard still shows a batch running
    page.evaluate("ids=>document.querySelectorAll('.lead').forEach(x=>{if(x.checked!==ids.includes(x.value))x.click()})", ["5"])
    page.locator("#channel").select_option("text")
    page.locator("#template").select_option("custom")
    page.locator("#custom-body").fill("Hi ")
    page.locator("#custom-body").press("End")
    page.get_by_role("button", name="First name", exact=True).click()
    page.locator("#custom-body").press("End")
    page.locator("#custom-body").type(", the car is ready.")
    assert page.locator("#custom-body").input_value() == "Hi {customer_first_name}, the car is ready."
    page.locator("#custom-name").fill("Car ready")
    page.locator("#custom-save").click()
    until(page, "[...document.querySelectorAll('#template optgroup[label=\"My templates\"] option')].some(o=>o.text==='Car ready'&&o.selected)", 20)
    page.locator("#preview").click()
    until(page, "!localBusy&&!!document.querySelector('#messages article')", 120)
    assert "Hi Lead, the car is ready." in page.locator("#messages").inner_text(), page.locator("#messages").inner_text()
    assert act(page, ["5"], "text", preview=False) == {"5": "sent"}
    assert crm.leads["5"]["rows"][-1][3] == "Hi Lead, the car is ready."
    # An email of my own: the subject and body are written into Momentum's email window.
    page.locator("#channel").select_option("email")
    page.locator("#template").select_option("custom")
    page.locator("#custom-subject").fill("Still looking, {customer_first_name}?")
    page.locator("#custom-body").fill("Hi {customer_first_name},\nThe car is here.")
    assert act(page, ["6"], "email", preview=False) == {"6": "sent"}
    assert crm.leads["6"]["rows"][-1][3] == "Subject: Still looking, Lead? Template: -"
    assert [t["name"] for t in app.my_templates()] == ["Car ready"]


def test_a_momentum_warning_is_shown_and_never_accepted(desk):
    page, app, crm = desk
    crm.alert = "This customer asked not to be contacted"
    # Momentum's question is answered No; nothing reaches Contact History, so that lead is skipped and the batch goes on.
    assert act(page, ["1", "2"]) == {"1": "skipped", "2": "skipped"} and crm.sent == []
    assert all("Momentum did not allow it: This customer asked not to be contacted" == r["detail"] for r in app.ledger.results(app.active_batch))
    assert app.ledger.rows() == []  # nothing is held back: the leads can be worked again


def test_stop_cancels_a_preview_from_the_dashboard(desk):
    page, app, crm = desk
    page.locator("#all").check()
    page.locator("#preview").click()
    until(page, "remoteBusy", 20)
    page.locator("#stop").click()
    until(page, "document.querySelector('#error').textContent.includes('Preview stopped')", 60)
    assert crm.sent == []


def test_the_extension_is_reloaded_from_the_dashboard(desk, monkeypatch):
    page, app, crm = desk
    monkeypatch.setattr("momentum.bridge.expected_version", lambda: "9.9.9")  # as if a newer build had been installed
    page.locator("#ext-reload").click(timeout=20000)
    deadline = time.monotonic() + 60
    while not any(t["command"] == "reload" and t["ok"] for t in app.bridge.timings):
        assert time.monotonic() < deadline
        page.wait_for_timeout(200)
    # Chromium does not bring back an extension loaded from the command line; in Chrome it reconnects (checked live).


def test_configurations_from_earlier_builds_can_send(tmp_path, monkeypatch):
    monkeypatch.setenv("MOMENTUM_HOME", str(tmp_path))
    path = tmp_path / "config.json"
    for change in ({}, {"verified": "true"}, {"crm_profile": "", "connection": "playwright"}):
        path.write_text(json.dumps({**OLD_CONFIG, **change}), encoding="utf-8")
        assert can_send() is None and load_settings()["connection"] == "both"
    path.write_text(json.dumps({"mode": "demo"}), encoding="utf-8")
    assert "demo mode" in can_send()  # setup replaces this one instead of keeping it, and leaves a backup
    write_live_config(ACCOUNT, tmp_path, force=True, crm=SITE)
    assert can_send() is None and len(list(tmp_path.glob("config.backup-*.json"))) == 1


def test_unreadable_phone_permission_is_not_permission():
    from momentum.bridge_adapter import MomentumBridge
    from momentum.core import eligible
    lead = {"assigned_to": ACCOUNT, "channel": "text", "report_eligible": True, "recent_text": False}
    phone = {"kind": "cell", "phone": "(310)555-0101", "call": "", "text": ""}
    assert "could not be read" in eligible({**lead, **MomentumBridge._phone_fields({"phones": [phone]}, "text")}, ACCOUNT)
    assert eligible({**lead, **MomentumBridge._phone_fields({"phones": [{**phone, "text": "preferred text off"}]}, "text")}, ACCOUNT) is None


def test_a_long_preview_does_not_expire_while_it_is_being_read(tmp_path):
    app = Application({**load_defaults(), "data_dir": str(tmp_path), "demo_leads": 120})
    owner = "owner-long-000000000"
    try:
        ids = [x["id"] for x in app.load(owner)["leads"]]
        job = app.start_preview(owner, ids, "follow-up")["job"]
        deadline = time.monotonic() + 20
        while not (state := app.preview_result(owner, job))["done"]:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert len(state["preview"]["messages"]) == 120 and state["preview"]["expires"] - time.time() > 7000
    finally:
        app.ledger.close()


def load_defaults():
    from momentum.settings import DEFAULTS
    return DEFAULTS


def test_the_whole_report_can_be_searched_but_only_own_leads_selected(desk):
    """The size of the client's real report, through the real extension, bridge, server and dashboard."""
    page, app, crm = desk
    app.config["lead_scope"] = "mine"  # the restriction, when it is put back
    crm.others, crm.repeats = 65007, 26
    page.locator("#load").click()
    until(page, "!localBusy", 120)
    assert page.locator("#error").inner_text() == ""
    assert page.locator(".lead").count() == 8
    page.locator("#view-full").click()
    until(page, "document.querySelectorAll('#full-rows tbody tr').length===50", 20)
    assert page.locator("#full-count").inner_text() == "65,015 of 65,015 unique people (65,041 report rows)"
    page.locator("#full-next").click()
    until(page, "document.querySelector('#full-page').textContent==='Page 2 of 1,301'", 20)
    page.locator("#full-search").fill("lead 3")
    until(page, "document.querySelectorAll('#full-rows tbody tr').length===1", 20)
    assert "Yours" in page.locator("#full-rows").inner_text()
    page.locator("#full-search").fill("65906")
    until(page, "document.querySelector('#full-rows').innerText.includes('Customer 65906')", 20)
    assert page.locator("#full-rows input").count() == 0
    error = page.evaluate("api('preview-start',{ids:['65906'],template:document.querySelector('#template').value,channel:'email',call:null}).then(waitPreview).then(()=>'accepted',e=>e.message)")
    assert "outside the loaded report" in error
    assert crm.sent == []


def test_lead_sources_combine_without_reloading_and_the_total_is_checked(desk):
    page, app, crm = desk
    page.locator("#view-full").click()
    page.locator("#source-note").wait_for(timeout=10000)  # no source column: say so, with the columns that did arrive
    assert "Sales Status" in page.locator("#source-note").inner_text()
    crm.others, crm.repeats, crm.sources, crm.total = 65007, 26, True, "65,015 unique people"
    page.locator("#load").click()
    until(page, "!localBusy", 120)
    until(page, "document.querySelector('#full-check').textContent.startsWith('Total matches Momentum: 65,015 unique people.')", 20)
    calls = len(app.bridge.timings)
    count = lambda n: until(page, f"document.querySelector('#full-count').textContent.startsWith('{n:,} of 65,015')", 20)
    page.locator("#source-summary").click()
    box = lambda name: page.locator(f"#source-options input[data-source='{name}']")
    box("Capital One").click()
    count(13002)
    box("Lendward").click()
    count(13002 + 13001)
    assert page.locator("#source-summary").inner_text() == "Capital One, Lendward"
    box("Capital One").click()
    count(13001)
    page.locator("#source-clear").click()
    count(65015)
    page.locator("#source-find").fill("kb")
    assert page.locator("#source-options input").count() == 1
    page.locator("#source-all").click()
    count(13001 + 8)  # "KBB" and "kbb " are one source
    page.locator("#full-search").fill("lead 3")
    count(1)
    assert len(app.bridge.timings) == calls  # Momentum was not asked again
    crm.total = "70,000 unique people"
    page.locator("#load").click()
    until(page, "!localBusy", 120)
    until(page, "document.querySelector('#full-check').textContent.includes('Momentum shows 70,000, loaded 65,015')", 20)
    assert page.evaluate("[...sourcesOn].length") == 1 and crm.sent == []


def test_lead_source_buttons_and_search(desk):
    page, app, crm = desk
    plain = crm.report
    names = ["ACE SPFI", "Capital One", "TrueCar/Beneplace"]
    crm.report = lambda: [{**r, "Lead Source": names[r["personid"] % 3] if r["personid"] < 900 else "Other"} for r in plain()]
    page.locator("#load").click()
    until(page, "!localBusy&&!!lastReport&&!remoteBusy", 60)
    page.locator("#view-full").click()
    until(page, "document.querySelectorAll('#source-groups button').length === 2", 20)
    groups = page.locator("#source-groups button")
    assert "Capital One" in groups.nth(0).inner_text() and "ACE Auto Credit Express" in groups.nth(1).inner_text()
    groups.nth(1).click()  # Lead 3 and Lead 6
    until(page, "document.querySelector('#full-count').textContent.startsWith('2 of 13')", 20)
    page.locator("#source-groups button").nth(0).click()  # and Leads 1, 4 and 7
    until(page, "document.querySelector('#full-count').textContent.startsWith('5 of 13')", 20)
    find = "t=>{document.querySelector('#source-find').value=t;return shownSources().map(s=>s.name)}"
    assert page.evaluate(find, "ace") == ["ACE SPFI"]  # not TrueCar/Beneplace
    assert page.evaluate(find, "auto credit express") == ["ACE SPFI"]
    assert page.evaluate(find, "beneplace") == ["TrueCar/Beneplace"]


def other_owner(crm, *ids):
    for i in ids:
        crm.leads[i].update(owner="Taylor Lee", page_owner="Lee, Taylor")


def test_leads_assigned_to_others_can_be_worked_from_the_full_report(desk):
    page, app, crm = desk
    other_owner(crm, "5", "6", "7", "8")
    crm.leads["8"]["no_email"] = True
    page.locator("#load").click()
    until(page, "!localBusy&&leadRows.length===4&&!remoteBusy", 90)
    page.locator("#view-full").click()
    page.locator("#full-search").fill("taylor lee lead")
    until(page, "document.querySelector('#full-count').textContent.startsWith('4 of 13')", 20)
    page.locator("#full-select").click()
    until(page, "!localBusy&&picked.size===4", 20)
    page.locator("#channel").select_option("email")
    page.locator("#preview").click()
    until(page, "!localBusy", 90)
    messages = {m["id"]: m["exclusion"] for m in page.evaluate("preview.messages")}
    assert [messages[i] for i in ("5", "6", "7")] == [None] * 3
    assert "Momentum does not allow Send Email" in messages["8"]  # Momentum's own refusal, shown per lead
    assert "Assigned to Taylor Lee" in page.locator("#messages").inner_text()
    pick = lambda *ids: page.evaluate("ids=>{picked.clear();ids.forEach(i=>picked.add(i));pickedChanged()}", list(ids))
    for channel, lead, method in (("email", "5", "Email Outbound"), ("text", "6", "Text"), ("call", "7", "Outbound Call")):
        pick(lead)
        assert act(page, [], channel, test=True) == {lead: "sent"}
        assert crm.sent[-1][0] == lead and crm.sent[-1][1].startswith(method)
    crm.leads["8"]["text"] = "preferred text dont"
    pick("8")
    page.locator("#channel").select_option("text")
    page.locator("#preview").click()
    until(page, "!localBusy", 60)
    assert "do-not-text" in page.locator("#messages").inner_text()  # opt-outs hold for everyone
    assert len(crm.sent) == 3


def test_lead_scope_defaults_to_everyone_and_can_be_put_back(tmp_path, monkeypatch):
    monkeypatch.setenv("MOMENTUM_HOME", str(tmp_path))
    path = tmp_path / "config.json"
    path.write_text(json.dumps(OLD_CONFIG), encoding="utf-8")
    assert load_settings()["lead_scope"] == "all"
    path.write_text(json.dumps({**OLD_CONFIG, "lead_scope": "mine"}), encoding="utf-8")
    assert load_settings()["lead_scope"] == "mine"

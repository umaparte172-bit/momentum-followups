"""A whole working day of the client, Dry run OFF throughout: the real dashboard, the real Chrome extension, and a mock
Momentum that behaves like the real one where it matters (an editor that submits what is in it, name fields, several phone
numbers, a do-not-call pop-up when Log Call is opened, a question when a call log is saved). Nothing reaches a real customer."""
import html
import json
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from momentum.server import create_server
from momentum.service import Application
from momentum.settings import load_settings
from tests.test_client_path import LA, OLD_CONFIG, SENDER, MockMomentum, bridge_worker, serve, until

FIRST = ["Maria", "Sam", "Dana", "Luis", "Priya", "Tom", "Ana", "Kevin", "Rosa", "Omar", "Jill", "Chen",
         "Nina", "Paul", "Ivy", "Raj", "Lena", "Hugo", "Zoe", "Eli", "Mia", "Ben"]


class RealisticMomentum(MockMomentum):
    def __init__(self):
        super().__init__(len(FIRST))
        self.others = 0
        for i, (cid, lead) in enumerate(self.leads.items()):
            lead.update(first=FIRST[i].upper() if i % 3 == 0 else FIRST[i], last=f"Test{cid}", name=f"Test{cid}, {FIRST[i]}",
                        call="preferred call off good", home="", home_call="preferred call off good", optout="")
        self.posted = []  # (cid, what, subject, body) exactly as Momentum received it

    def record_body(self, cid, what, subject, body):
        self.posted.append((cid, what, subject, body))

    def page(self, request):
        time.sleep(0.05)  # Momentum is never instant
        from urllib.parse import parse_qs, urlparse
        url = urlparse(request.url)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        form = {k: v[0] for k, v in parse_qs(request.post_data or "", keep_blank_values=True).items()}
        cid = query.get("cid", "")
        lead = self.leads.get(cid)
        done = "<script>top.closeDialog()</script>"
        if url.path.endswith("/customer") and lead.get("broken"):
            return "<!doctype html><p>An error occurred. Please try again.</p>"
        if url.path.endswith("/customer"):
            home = (f"<div class=input-item><input id='custView.homePhone.phone' value='{lead['home']}'><button class='{lead['home_call']}'>c</button></div>"
                    if lead["home"] else "")
            warn = "<script>alert('This customer asked not to be contacted')</script>" if lead.get("open_alert") else ""
            return (f"<!doctype html>{warn}<div>{html.escape(lead['name'])}</div><input id='custView.firstName' value='{lead['first']}'><input id='custView.lastName' value='{lead['last']}'>"
                    f"<input id=display_emailaddress value='{lead['email']}'><button class='preferred email {lead['optout']}'>e</button>"
                    f"<select id=select_salesperson><option selected>{SENDER}</option></select>{home}"
                    f"<div class=input-item><input id='custView.cellPhone.phone' value='{lead['cell']}'><button class='{lead['call']}'>c</button><button class='{lead['text']}'>t</button></div>"
                    f"<iframe id=cap_tab_iframe src='/tab?cid={cid}'></iframe><iframe name=cap_activities_iframe src='/act?cid={cid}'></iframe>")
        if url.path == "/act":
            # Like Momentum, opening Log Call for some customers first shows a message box.
            call = f"alert('This customer has asked not to be called');" if lead.get("dnc_alert") else f"activities_iframe.src='/call?cid={cid}'"
            return (f"<button id=email onclick=\"top.openDialog('/mail?cid={cid}')\">e</button><button id=sms onclick=\"top.openDialog('/sms?cid={cid}')\">s</button>"
                    f"<button id=log_call onclick=\"{call}\">c</button><iframe id=activities_iframe></iframe>")
        if url.path == "/mail" and request.method == "POST":
            body = re.sub(r"<br\s*/?>", "\n", form.get("body", ""))
            body = html.unescape(re.sub(r"<[^>]+>", "", body))
            self.record_body(cid, "email", form["subject"], body)
            self.record(cid, "Email Outbound", f"Subject: {form['subject']}" + (f" Template: {form['template_name']}" if form["template_name"] != "-" else ""))
            return done
        if url.path == "/mail":
            # A Froala-style editor: what is typed lives in the .fr-element and is copied into the form when it is sent.
            return (f"<form method=post action='/mail?cid={cid}' onsubmit=\"body.value=document.querySelector('.fr-element').innerHTML;template_name.value=templateId.selectedOptions[0].text\">"
                    f"<select name=to><option selected>{html.escape(lead['name'])} &lt;{lead['email']}&gt;</option></select>"
                    "<input name=cc><input name=bcc><input type=hidden name=template_name><input name=subject>"
                    f"<select name=templateId onchange=\"subject.value='Hello';FroalaEditor.INSTANCES[0].html.set('Hi {lead['first']}, your saved template.')\"><option value=''>-</option><option value=T1>Day 01</option></select>"
                    "<textarea name=body style=display:none></textarea><div class=fr-box><div class=fr-element contenteditable></div></div><button type=submit>Send</button></form>"
                    "<script>window.FroalaEditor={INSTANCES:[{el:document.querySelector('.fr-element'),html:{set(h){this.el.innerHTML=h}},undo:{saveStep(){}}}]};"
                    "FroalaEditor.INSTANCES[0].html.el=FroalaEditor.INSTANCES[0].el;</script>")
        if url.path == "/sms" and request.method == "POST":
            self.record_body(cid, "text", "", form["emailBody"])
            self.record(cid, "Text Message (SMS) Outbound", "Template: " + form["template_name"] if form["template_name"] != "-" else form["emailBody"])
            return done
        refused = ""
        if url.path == "/call" and request.method == "POST":
            time.sleep(1.5)  # Momentum takes its time to save
            if not form["logCallView.note"].strip() or lead.get("refuse_save"):
                # As the real Momentum does: Call Notes are required, and a refused save comes back as a line of text, no message box.
                refused = "<p>Please fill out the form completely.</p>"
            else:
                self.record_body(cid, "call", form["logCallView.contactedPhone"], form["logCallView.note"])
                self.record(cid, "Outbound Call " + form["logCallView.contactedPhone"], form["logCallView.note"], SENDER + " [ Left Message ]")
        if url.path == "/call":  # like Momentum, after a save the panel comes back as a fresh Log Call form
            phones = "".join(f"<option value='{p}'>{p}</option>" for p in (lead["cell"], lead["home"]) if p)
            # Like Momentum, saving a call for a number on the do-not-call list asks first.
            ask = "if(!confirm('This number is on the do-not-call list. Save anyway?'))return false;" if lead.get("save_confirm") else ""
            warn = "onchange=\"alert('This number is on the Do Not Call list')\"" if lead.get("phone_alert") else ""
            # Like a modern page, odd-numbered leads save in the background and the panel stays as it is.
            ajax = ("var x=new XMLHttpRequest();x.open('POST',this.action);x.setRequestHeader('Content-Type','application/x-www-form-urlencoded');"
                    "x.send(new URLSearchParams(new FormData(this)).toString());return false;") if int(cid) % 2 else ""
            return (f"{refused}<form method=post action='/call?cid={cid}' onsubmit=\"{ask}{ajax}\"><select name=logCallView.incoming><option value=true>in<option value=false>out</select><input type=checkbox name=logCallView.isVideoCall>"
                    "<select name=logCallView.callTypeID><option value=1074>Owner Follow Up</select>"
                    f"<select name=logCallView.contactedPhone {warn}>{phones}</select><select name=logCallView.callResultID><option value=''>-<option value=1062>Left Message</select>"
                    "<textarea name=logCallView.note></textarea><button type=submit>Save</button></form>")
        return super().page(request)


@pytest.fixture(params=["guard", "no-guard"])
def day(request, tmp_path, monkeypatch):
    """Runs every test twice: with the extension as shipped, and with its guard.js removed (as in a Momentum tab that
    was opened before the update), so nothing may depend on the guard alone."""
    from playwright.sync_api import sync_playwright
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps(OLD_CONFIG), encoding="utf-8")
    monkeypatch.setenv("MOMENTUM_HOME", str(home))
    crm = RealisticMomentum()
    state = {"app": Application(load_settings())}
    servers = []

    def start_tool():
        server = create_server(state["app"], 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}"

    momentum = serve(crm)
    extension = Path(__file__).resolve().parent.parent / "momentum" / "extension"
    if request.param == "no-guard":
        import shutil
        copy = tmp_path / "extension"
        shutil.copytree(extension, copy)
        (copy / "guard.js").write_text("", encoding="utf-8")
        extension = copy
    crm.guarded = request.param == "guard"
    with sync_playwright() as pw:
        try:
            context = pw.chromium.launch_persistent_context(str(tmp_path / "profile"), channel="chromium", headless=True,
                                                            args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}", "--ignore-certificate-errors",
                                                                  f"--host-resolver-rules=MAP *.udccrm.com 127.0.0.1:{momentum.server_port}"])
        except Exception as exc:
            pytest.skip(f"Chromium cannot load extensions here: {exc}")
        worker = bridge_worker(context)
        # Like a real browser, a message box from Momentum stays open (and freezes that tab) until someone answers it.
        dialogs = []
        watch = lambda p: p.on("dialog", lambda d: dialogs.append(d.message))
        for existing in context.pages:
            watch(existing)
        context.on("page", watch)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        def open_tool():
            base = start_tool()
            worker.evaluate("base => chrome.storage.local.set({base})", base)
            page.goto(base + "/?noguide")
            page.locator("#login").click()
            page.get_by_text("Connected to Momentum").wait_for(timeout=60000)
            page.locator("#load").click()
            page.locator(".lead").first.wait_for(timeout=90000)
            until(page, "!document.querySelector('#load').disabled&&!localBusy", 90)

        def restart():
            """Close the tool and start it again, as after a Mac restart: the send history and own templates stay."""
            for s in servers:
                s.shutdown()
                s.server_close()
            servers.clear()
            state["app"].ledger.close()
            state["app"] = Application(load_settings())
            open_tool()

        open_tool()
        yield page, state, crm, restart, errors
        assert dialogs == [], f"a Momentum message box was left open: {dialogs}"
        context.close()
    for running in servers + [momentum]:
        running.shutdown()
        running.server_close()
    state["app"].ledger.close()


def select(page, ids):
    until(page, "!remoteBusy&&!localBusy", 60)
    page.evaluate("ids=>document.querySelectorAll('.lead').forEach(x=>{if(x.checked!==ids.includes(x.value))x.click()})", ids)


def finish(page, seconds=240):
    deadline = time.monotonic() + seconds
    while page.evaluate("fetch('/api/state').then(r=>r.json()).then(s=>s.busy||s.results.some(r=>['pending','sending'].includes(r.status)))"):
        assert time.monotonic() < deadline, "the batch did not finish"
        page.wait_for_timeout(300)
    page.wait_for_timeout(1500)  # the dashboard shows the outcome
    return {r["id"]: (r["status"], r["detail"]) for r in page.evaluate("fetch('/api/state').then(r=>r.json()).then(s=>s.results)")}


def send(page, label_start):
    """Dry run OFF, click the main button, confirm. Returns once the batch has started."""
    page.locator("#dry").set_checked(False)
    until(page, "!document.querySelector('#run').disabled", 30)
    assert page.locator("#run").inner_text().strip().startswith(label_start), page.locator("#run").inner_text()
    page.locator("#run").click()
    assert page.locator("#confirm-area").is_visible()
    page.locator("#confirm-yes").click()
    until(page, "!localBusy", 30)
    assert page.locator("#error").inner_text() == "", page.locator("#error").inner_text()


def write(page, channel, subject, body):
    page.locator("#channel").select_option(channel)
    page.locator("#template").select_option("custom")
    if channel == "email":
        page.locator("#custom-subject").fill(subject)
    page.locator("#custom-body").fill(body)


def test_a_whole_day_with_dry_run_off(day):
    page, state, crm, restart, errors = day
    leads = crm.leads
    # The list as it really is: some do-not-call, one only on the cell, a pop-up, a question on save, recent contact.
    leads["3"]["call"] = "preferred call dont"
    leads["4"]["call"], leads["4"]["home"] = "preferred call dont", "(310)555-0404"
    leads["5"]["dnc_alert"] = True
    leads["6"]["save_confirm"] = True
    leads["10"]["phone_alert"] = True  # Momentum warns when this number is picked in Log Call
    if crm.guarded:  # a warning while the page itself loads can only be caught by guard.js, which runs first
        leads["11"]["open_alert"] = True  # Momentum warns as soon as this customer's page opens
    opened = {"11"} if crm.guarded else set()
    leads["7"]["rows"].append(((datetime.now(LA) - timedelta(hours=3)).strftime("%m/%d/%Y %I:%M %p"), "Text Message (SMS) Outbound", "Lee, Taylor", "hi"))
    leads["15"]["optout"] = "dont"
    leads["16"]["text"] = "preferred text dont"
    everyone = [str(i) for i in range(1, 13)]

    # 1. Log calls for all 12, no preview, with a pause in the middle.
    select(page, everyone)
    page.locator("#channel").select_option("call")
    page.locator("#call-result").select_option("1062")
    page.locator("#call-note").fill("Left a voicemail")
    started = time.monotonic()
    send(page, "Log up to 12 call logs now")
    until(page, "fetch('/api/state').then(r=>r.json()).then(s=>s.results.some(r=>r.status==='sent'))", 120)
    page.locator("#pause").click()
    page.wait_for_timeout(1500)
    held = len(crm.posted)
    page.wait_for_timeout(3000)
    assert len(crm.posted) == held  # paused means nothing more is done
    page.locator("#resume").click()
    calls = finish(page)
    took = time.monotonic() - started
    print(f"12-lead call batch: {took:.0f} s including a 4.5 s pause")
    assert took < 75, took  # was about 30 s a lead while it waited for the Log Call panel to close
    assert {i: s for i, (s, _) in calls.items()} == {**{i: "sent" for i in everyone}, "3": "skipped", "5": "skipped", "6": "skipped", "10": "skipped", **{i: "skipped" for i in opened}}
    assert "do-not-call" in calls["3"][1]
    assert "asked not to be called" in calls["5"][1]
    assert "do-not-call list" in calls["6"][1]
    logged = {c: phone for c, what, phone, _ in crm.posted if what == "call"}
    assert sorted(logged, key=int) == sorted(["1", "2", "4", "7", "8", "9", "12"] + ([] if opened else ["11"]), key=int)
    assert "Do Not Call list" in calls["10"][1] and (not opened or "asked not to be contacted" in calls["11"][1])
    assert logged["4"] == "(310)555-0404"  # the cell is do-not-call, the home number may be called
    assert all(note == "Left a voicemail" for c, what, _, note in crm.posted if what == "call")
    assert page.locator("#metric-sent").inner_text() == str(len(logged))

    # 2. The same call batch again: nothing is logged twice.
    select(page, everyone)
    page.locator("#channel").select_option("call")
    page.locator("#call-result").select_option("1062")
    send(page, "Log up to 12 call logs now")
    again = finish(page)
    assert all(s == "skipped" for s, _ in again.values()) and len([p for p in crm.posted if p[2] and p[1] == "call"]) == len(logged)
    assert "Duplicate" in again["1"][1]
    assert "do-not-call list" in again["6"][1]  # Momentum's refusal held nothing back, so it is asked again

    # 3. A custom email, previewed first: each person's first name, as Momentum spells it.
    select(page, ["13", "14", "15"])
    write(page, "email", "Still looking, {customer_first_name}?", "Hi {customer_first_name},\nThe car you asked about is here.\n{my_first_name}")
    page.locator("#preview").click()
    until(page, "!localBusy&&!!document.querySelector('#messages article')", 120)
    shown = page.locator("#messages").text_content()
    assert "Still looking, Nina?" in shown and "Hi Paul," in shown and "opted out" in shown, shown
    send(page, "Send 2 emails now")
    emails = finish(page)
    assert {i: s for i, (s, _) in emails.items()} == {"13": "sent", "14": "sent", "15": "skipped"}
    sent = {c: (subject, body) for c, what, subject, body in crm.posted if what == "email"}
    assert sent == {"13": ("Still looking, Nina?", "Hi Nina,\nThe car you asked about is here.\nAlex"),
                    "14": ("Still looking, Paul?", "Hi Paul,\nThe car you asked about is here.\nAlex")}

    # 4. A text of my own, saved as a template; after a restart it is still there and goes out without a preview.
    select(page, ["16"])
    write(page, "text", "", "Hi {customer_first_name}, it's {my_first_name}. Still interested?")
    page.locator("#custom-name").fill("Still interested")
    page.locator("#custom-save").click()
    until(page, "!localBusy", 20)
    restart()
    select(page, ["16", "17", "18", "19"])
    page.locator("#channel").select_option("text")
    page.locator("#template").select_option(label="Still interested")
    assert page.locator("#custom-body").input_value() == "Hi {customer_first_name}, it's {my_first_name}. Still interested?"
    send(page, "Send up to 4 texts now")
    texts = finish(page)
    assert {i: s for i, (s, _) in texts.items()} == {"16": "skipped", "17": "sent", "18": "sent", "19": "sent"}
    assert "do-not-text" in texts["16"][1]
    bodies = {c: body for c, what, _, body in crm.posted if what == "text"}
    assert bodies == {"17": "Hi Lena, it's Alex. Still interested?", "18": "Hi Hugo, it's Alex. Still interested?", "19": "Hi Zoe, it's Alex. Still interested?"}

    # 5. A saved Momentum email template, straight to the batch.
    select(page, ["20", "21"])
    page.locator("#channel").select_option("email")
    page.locator("#template").select_option("T1")
    send(page, "Send up to 2 emails now")
    saved = finish(page)
    assert {i: s for i, (s, _) in saved.items()} == {"20": "sent", "21": "sent"}
    assert [(c, s, b) for c, w, s, b in crm.posted if w == "email" and c in ("20", "21")] == [("20", "Hello", "Hi Eli, your saved template."), ("21", "Hello", "Hi Mia, your saved template.")]

    # Nothing is left uncertain, every action has its own Contact History entry, and the page never broke.
    assert all(r["status"] == "sent" for r in state["app"].ledger.rows())
    assert len(state["app"].ledger.rows()) == len(logged) + 2 + 3 + 2
    assert errors == []


def test_a_long_call_batch_goes_on_past_a_lead_that_will_not_open(day):
    page, state, crm, restart, errors = day
    crm.leads["7"]["broken"] = True
    crm.leads["12"]["call"] = "preferred call dont"
    crm.leads["8"]["refuse_save"] = True  # Momentum refuses this save with a line of text in the panel, not a message box
    until(page, "!remoteBusy&&!localBusy", 60)
    page.locator("#all").check()
    page.locator("#channel").select_option("call")
    page.locator("#call-result").select_option("1062")
    # Momentum saves nothing without Call Notes: the tool will not start without one.
    page.locator("#dry").set_checked(False)
    until(page, "!document.querySelector('#run').disabled", 30)
    page.locator("#run").click()
    page.locator("#confirm-yes").click()
    until(page, "!localBusy", 30)
    assert "Momentum requires Call notes" in page.locator("#error").inner_text()
    assert not crm.posted
    page.locator("#call-note").fill("Left a voicemail")
    send(page, "Log up to 22 call logs now")
    rows = finish(page, 400)
    status = {i: s for i, (s, _) in rows.items()}
    assert status == {**{str(i): "sent" for i in range(1, 23)}, "7": "failed", "8": "skipped", "12": "skipped"}, status
    assert "lead page" in rows["7"][1]
    assert "Please fill out the form completely" in rows["8"][1]
    assert len([c for c, w, _, _ in crm.posted if w == "call"]) == 19
    assert state["app"].snapshot()["error"] is None and errors == []

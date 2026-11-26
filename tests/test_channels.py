"""Texts and call logs through the bridge: the same preview, recheck, opt-out, duplicate and Contact History rules as email."""
import time
from datetime import datetime

from momentum.service import Application
from tests.test_bridge import EXT
from tests.test_client_rules import LA, ReportBrowser, config, lead, when

SENDER = "Rivera, Alex Morgan"
CALL_OPTIONS = {"types": [{"id": "1074", "name": "Owner Follow Up"}], "results": [{"id": "1062", "name": "Left Message"}, {"id": "1052", "name": "Contacted"}]}


def phone_lead(name, email, cell="(310)555-0155", text_flags="preferred text off", call_flags="preferred call off good", **extra):
    item = lead(name, email, 100, extra.pop("history", None))
    item["phones"] = [{"kind": "cell", "phone": cell, "call": call_flags, "text": text_flags}] if cell else []
    item.update(extra)
    return item


class ChannelBrowser(ReportBrowser):
    """Adds Momentum's Send Text Message dialog and Log Call panel to the fake extension."""

    def __init__(self, *args):
        super().__init__(*args)
        self.texts, self.calls, self.panel = [], [], None

    def now_row(self, method, text):
        stamp = datetime.now(LA).strftime("%m/%d/%Y %I:%M %p")
        return {"when": stamp, "method": method, "by": SENDER, "text": f"{stamp} {method} {SENDER} {text}"}  # like the real row

    def handle(self, cmd):
        fn = cmd.get("fn")
        if fn == "readLead":
            result = super().handle(cmd)
            return {**result, "phones": self.leads[cmd["args"][0]].get("phones", [])}
        current = self.leads.get(self.composer_lead or "", {})
        cell = (current.get("phones") or [{}])[0].get("phone", "")
        if fn == "openSms":
            return {"templates": [{"id": "5430", "name": "Ace Text 2"}]}
        if fn in ("selectSmsTemplate", "smsState"):
            return {"from": SENDER, "to": cell, "toClass": "", "toOptions": [cell], "body": f"Hi {current['name']}, this is Alex at Sample Motors."}
        if fn == "sendSms":
            self.texts.append(self.composer_lead)
            current.setdefault("extra", []).append(self.now_row("Text Message (SMS) Outbound", f"Hi {current['name']}, this is Alex at Sample Motors."))
            return True
        if fn == "openLogCall":
            self.panel = None
            return {**CALL_OPTIONS, "phones": [{"id": cell, "name": cell + " - Cell"}]}
        if fn == "fillLogCall":
            self.panel = cmd["args"][0]
        if fn in ("fillLogCall", "logCallState"):
            result = next(r["name"] for r in CALL_OPTIONS["results"] if r["id"] == self.panel["result"])
            return {"incoming": "false", "video": False, "type": self.panel["type"], "phone": self.panel["phone"],
                    "result": self.panel["result"], "resultText": result, "note": self.panel["note"]}
        if fn == "saveLogCall":
            self.calls.append(self.composer_lead)
            result = next(r["name"] for r in CALL_OPTIONS["results"] if r["id"] == self.panel["result"])
            current.setdefault("extra", []).append(self.now_row(f"Outbound Call {cell}", f"[ {result} ] {self.panel['note']}"))
            return True
        if fn == "openLead":
            return True
        return super().handle(cmd)


def start(tmp_path, leads):
    bridge = __import__("momentum.bridge", fromlist=["get_bridge"]).get_bridge(tmp_path)
    bridge.open_pairing()
    token = bridge.hello(EXT)
    browser = ChannelBrowser(bridge, token, leads)
    browser.start()
    time.sleep(0.1)
    return browser


def run(app, owner, ids, channel, template=None, call=None, test=False, dry=False):
    preview = app.preview(owner, ids, template, channel, call)
    started = app.run(owner, {"preview_id": preview["id"], "dry_run": dry, "test": test, "authorization": "SEND APPROVED EMAILS"})
    app.worker.join(30)
    return preview, {row["name"]: row for row in app.ledger.results(started["batch"])}


def live_app(tmp_path):
    return Application(config(tmp_path, verified=False, max_batch=0, hourly_limit=0, daily_limit=0, delay_seconds=0))


def test_load_offers_text_templates_and_call_choices(tmp_path):
    browser = start(tmp_path, {"1": phone_lead("One", "a@example.test")})
    app = live_app(tmp_path)
    try:
        loaded = app.load("owner-load-000000000")
        assert loaded["sms_templates"] == [{"id": "5430", "name": "Ace Text 2"}]
        assert [r["name"] for r in loaded["call_options"]["results"]] == ["Left Message", "Contacted"]
    finally:
        browser.running = False
        app.ledger.close()


def test_a_text_batch_runs_and_each_is_confirmed(tmp_path):
    browser = start(tmp_path, {str(i): phone_lead(f"L{i}", f"l{i}@example.test", cell=f"(310)555-01{i:02d}") for i in range(1, 5)})
    app = live_app(tmp_path)
    owner = "owner-text-000000000"
    try:
        app.load(owner)
        preview, _ = run(app, owner, ["1", "2"], "text", "5430", dry=True)
        assert all(m["subject"] == "Text: Ace Text 2" and m["to"] for m in preview["messages"])
        assert browser.texts == []  # a dry run never sends
        _, results = run(app, owner, ["1"], "text", "5430")  # no single test is needed first any more
        assert results["L1"]["status"] == "sent" and "Text Message (SMS) Outbound" in results["L1"]["detail"]
        _, results = run(app, owner, ["1", "2", "3"], "text", "5430")
        assert results["L1"]["status"] == "skipped"  # already texted: duplicate block and the 72-hour text rule
        assert results["L2"]["status"] == results["L3"]["status"] == "sent" and browser.texts == ["1", "2", "3"]
    finally:
        browser.running = False
        app.ledger.close()


def test_text_respects_do_not_text_and_missing_cell(tmp_path):
    browser = start(tmp_path, {"1": phone_lead("Opted Out", "a@example.test", text_flags="preferred text dont"),
                               "2": phone_lead("No Phone", "b@example.test", cell=None)})
    app = live_app(tmp_path)
    try:
        app.load("owner-optout-0000000")
        preview = app.preview("owner-optout-0000000", ["1", "2"], "5430", "text")
        reasons = {m["name"]: m["exclusion"] for m in preview["messages"]}
        assert "do-not-text" in reasons["Opted Out"] and reasons["No Phone"] == "No cell number on file to text"
    finally:
        browser.running = False
        app.ledger.close()


def test_an_uncertain_test_confirmed_by_a_person_unlocks_batches(tmp_path, monkeypatch):
    browser = start(tmp_path, {"1": phone_lead("One", "a@example.test"), "2": phone_lead("Two", "b@example.test", cell="(310)555-0102")})
    app = live_app(tmp_path)
    owner = "owner-resolve-000000"

    def unconfirmed(self, message):
        raise RuntimeError("Send attempted but a unique new Contact History record could not be verified")

    monkeypatch.setattr("momentum.bridge_adapter.MomentumBridge.send", unconfirmed)
    try:
        app.load(owner)
        assert run(app, owner, ["1"], "text", "5430", test=True)[1]["One"]["status"] == "uncertain"
        assert not app.snapshot()["tests_done"]["text"]
        app.reconcile({"lead_id": "1:text", "resolution": "sent", "evidence": "Seen in Contact History just now", "confirmation": "I CHECKED CRM HISTORY"})
        assert app.snapshot()["tests_done"] == {"email": False, "text": True, "call": False}
    finally:
        browser.running = False
        app.ledger.close()


def test_call_log_is_saved_with_the_chosen_result_and_confirmed(tmp_path):
    browser = start(tmp_path, {"1": phone_lead("One", "a@example.test"), "2": phone_lead("Two", "b@example.test", cell="(310)555-0102"),
                               "3": phone_lead("No Calls", "c@example.test", call_flags="preferred call dont")})
    app = live_app(tmp_path)
    owner = "owner-call-000000000"
    call = {"type": "1074", "result": "1062", "note": "Left a voicemail about the Rogue"}
    try:
        app.load(owner)
        preview, results = run(app, owner, ["1"], "call", call=call, test=True)
        assert preview["messages"][0]["subject"] == "Call log: Left Message"
        assert results["One"]["status"] == "sent" and "[ Left Message ]" in results["One"]["detail"]
        _, results = run(app, owner, ["2", "3"], "call", call=call)
        assert results["Two"]["status"] == "sent" and results["No Calls"]["status"] == "skipped"
        assert browser.calls == ["1", "2"]
        try:
            app.preview(owner, ["2"], None, "call", {"type": "1074", "result": "9999"})
            raise AssertionError("an unknown call result must be refused")
        except ValueError as exc:
            assert "call result" in str(exc)
    finally:
        browser.running = False
        app.ledger.close()


def test_email_text_and_call_for_one_lead_do_not_block_each_other(tmp_path):
    browser = start(tmp_path, {"1": phone_lead("One", "a@example.test")})
    app = live_app(tmp_path)
    owner = "owner-mixed-00000000"
    try:
        app.load(owner)
        assert run(app, owner, ["1"], "call", call={"type": "1074", "result": "1052", "note": ""}, test=True)[1]["One"]["status"] == "sent"
        # The call itself is now contact in the last 72 hours, so the text is skipped for that reason, not as a duplicate.
        preview = app.preview(owner, ["1"], "5430", "text")
        assert "Contacted within the last 72 hours" in preview["messages"][0]["exclusion"]
    finally:
        browser.running = False
        app.ledger.close()


def test_a_text_row_from_another_message_does_not_confirm_the_send(tmp_path):
    browser = start(tmp_path, {"1": phone_lead("One", "a@example.test")})
    try:
        from momentum.bridge_adapter import MomentumBridge
        with MomentumBridge(config(tmp_path, channel="text", template_id="5430", template_name="Ace Text 2")) as crm:
            crm.leads()
            crm.send_started = datetime.now(LA)
            message = {"channel": "text", "body": "Hi One, this is Alex at Sample Motors."}
            row = {"when": when(0), "method": "Text Message (SMS) Outbound", "by": SENDER, "text": "Hi One, this is Alex at Sample Motors."}
            assert crm._is_this_send(row, SENDER, message)
            assert not crm._is_this_send({**row, "text": "Hola, un mensaje distinto"}, SENDER, message)
            # Exactly as the real Momentum logs a text sent from a saved template.
            assert crm._is_this_send({**row, "text": f"{when(0)} Text Message (SMS) Outbound {SENDER} Template: Ace Text 2"}, SENDER, message)
            assert not crm._is_this_send({**row, "text": "Template: Ace Text 22"}, SENDER, message)
            assert not crm._is_this_send({**row, "method": "Text Message (SMS) Inbound"}, SENDER, message)
            call = {"channel": "call", "call_result_name": "Left Message"}
            call_row = {"when": when(0), "method": "Outbound Call (310)555-0155", "by": SENDER, "text": "[ Left Message ] Lvm"}
            assert crm._is_this_send(call_row, SENDER, call)
            assert not crm._is_this_send({**call_row, "text": "[ Contacted ]"}, SENDER, call)
    finally:
        browser.running = False


def test_a_call_row_with_the_result_next_to_the_name_confirms_the_call(tmp_path):
    browser = start(tmp_path, {"1": phone_lead("One", "a@example.test")})
    try:
        from momentum.bridge_adapter import MomentumBridge
        with MomentumBridge(config(tmp_path, channel="call")) as crm:
            crm.leads()
            crm.send_started = datetime.now(LA)
            call = {"channel": "call", "call_result_name": "Left Message"}
            # Exactly as Momentum shows it: the result sits in the "performed by" column.
            row = {"when": when(0), "method": "Outbound Call (310)555-0198", "by": f"{SENDER} [ Left Message ]",
                   "text": f"{when(0)} Outbound Call (310)555-0198 {SENDER} [ Left Message ] Lvm"}
            assert crm._is_this_send(row, SENDER, call)
            assert not crm._is_this_send({**row, "by": "Lee, Taylor [ Left Message ]"}, SENDER, call)
    finally:
        browser.running = False


def test_a_new_build_keeps_the_tests_already_passed_by_this_account(tmp_path):
    old = "test:" + "a" * 64
    app = live_app(tmp_path)
    app.ledger.set_meta(old, f"11/24/2026 6:23 PM Email Outbound {SENDER} Template: BLANK Email")
    app.ledger.set_meta(old + ":text", "11/24/2026 6:55 PM Text Message (SMS) Outbound Lee, Taylor Template: CALL ME")
    app.ledger.close()
    app = live_app(tmp_path)
    try:  # the email test was by this account; the text test was somebody else's
        assert app.snapshot()["tests_done"] == {"email": True, "text": False, "call": False}
    finally:
        app.ledger.close()
    app = Application(config(tmp_path, verified=False, source_column="Lead Source", lead_scope="all"))
    try:
        assert app.snapshot()["tests_done"]["email"] is True  # settings that do not change who sends where
    finally:
        app.ledger.close()
    app = Application(config(tmp_path, verified=False, account_name="Taylor Lee"))
    try:
        assert app.snapshot()["tests_done"] == {"email": False, "text": True, "call": False}
    finally:
        app.ledger.close()

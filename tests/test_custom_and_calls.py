"""Batch call logs that skip do-not-call, custom messages with placeholders, saved own templates, and sending without a preview."""
import pytest

from momentum.bridge import BridgeError
from momentum.core import LeadExcluded, name_parts, personalize, placeholders
from tests.test_channels import CALL_OPTIONS, ChannelBrowser, live_app, phone_lead
from tests.test_bridge import EXT
from tests.test_client_rules import when

OWNER = "owner-custom-0000000"
CALL = {"type": "1074", "result": "1062", "note": "Left a voicemail"}


class CustomBrowser(ChannelBrowser):
    """Adds what a custom message needs: Momentum's name fields, and typing into the email and text windows."""

    def __init__(self, *args):
        super().__init__(*args)
        self.filled = {}
        self.refuse = set()

    def handle(self, cmd):
        fn, args = cmd.get("fn"), cmd.get("args") or []
        current = self.leads.get(self.composer_lead or "", {})
        if fn == "readLead":
            result = super().handle(cmd)
            return {**result, "firstName": self.leads[args[0]].get("first", ""), "lastName": self.leads[args[0]].get("last", "")}
        if fn == "openComposer":
            self.filled.pop(self.composer_lead, None)
        if fn == "fillComposer":
            self.filled[self.composer_lead] = {"subject": args[0]["subject"], "body": args[0]["html"].replace("<br>", "\n")}
        if fn in ("fillComposer", "composerState") and self.composer_lead in self.filled:
            f = self.filled[self.composer_lead]
            return {"toClass": "", "toText": f"{current['name']} <{current['email']}>", "cc": [], "subject": f["subject"], "body": f["body"]}
        if fn == "openSms":
            self.filled.pop(self.composer_lead, None)
        if fn == "fillSms":
            self.filled[self.composer_lead] = {"body": args[0]}
        if fn in ("fillSms", "smsState") and self.composer_lead in self.filled:
            cell = (current.get("phones") or [{}])[0].get("phone", "")
            return {"from": "Rivera, Alex Morgan", "to": cell, "toClass": "", "toOptions": [cell], "body": self.filled[self.composer_lead]["body"]}
        if fn == "sendSms" and self.composer_lead in self.filled:
            self.texts.append(self.composer_lead)
            current.setdefault("extra", []).append(self.now_row("Text Message (SMS) Outbound", self.filled[self.composer_lead]["body"]))
            return True
        if fn == "openLogCall":
            if current.get("log_call_refused"):
                raise BridgeError("Momentum does not allow Log Call on this lead: This customer does not want to be called")
            self.panel = None
            return {**CALL_OPTIONS, "phones": [{"id": p["phone"], "name": p["phone"]} for p in current.get("phones", [])]}
        if fn == "saveLogCall" and self.composer_lead in self.refuse:
            raise BridgeError("Momentum said: This number is on the do-not-call list")
        if fn == "saveLogCall":
            self.calls.append(self.composer_lead)
            result = next(r["name"] for r in CALL_OPTIONS["results"] if r["id"] == self.panel["result"])
            current.setdefault("extra", []).append(self.now_row(f"Outbound Call {self.panel['phone']}", f"[ {result} ] {self.panel['note']}"))
            return True
        return super().handle(cmd)


def start(tmp_path, leads):
    import time
    bridge = __import__("momentum.bridge", fromlist=["get_bridge"]).get_bridge(tmp_path)
    bridge.open_pairing()
    browser = CustomBrowser(bridge, bridge.hello(EXT), leads)
    browser.start()
    time.sleep(0.1)
    return browser


def go(app, data):
    batch = app.run(OWNER, {"dry_run": False, "authorization": "SEND APPROVED EMAILS", **data})["batch"]
    app.worker.join(60)
    return {row["name"]: row for row in app.ledger.results(batch)}


# --- placeholders ------------------------------------------------------------------------------
def test_names_come_from_momentum_and_unknown_placeholders_are_refused():
    assert name_parts({"first_name": "maria", "last_name": "LOPEZ"}) == ("Maria", "Lopez")
    assert name_parts({"name": "LOPEZ, MARIA M"}) == ("Maria", "Lopez")
    assert name_parts({"name": "Sam Carter"}) == ("Sam", "Carter")
    assert name_parts({"name": "McKenzie, De'Andre"}) == ("De'Andre", "McKenzie")
    assert name_parts({"name": "(310) 555-0100"}) == ("", "")
    assert personalize("Hi {customer_first_name}, it's {my_first_name}.", {"name": "Sam Carter"}, "Alex Morgan Rivera") == "Hi Sam, it's Alex."
    assert personalize("Hi { Customer_First_Name },", {"name": "Sam Carter"}, "A B") == "Hi Sam,"
    with pytest.raises(LeadExcluded, match="first name"):
        personalize("Hi {customer_first_name}", {"name": ""}, "A B")
    with pytest.raises(ValueError, match="Unknown placeholder"):
        placeholders("Hi {first_name}")
    assert placeholders("Hello there") == set()


# --- call logs ---------------------------------------------------------------------------------
def test_a_call_batch_skips_do_not_call_and_logs_everyone_else(tmp_path):
    browser = start(tmp_path, {
        "1": phone_lead("One", "a@example.test", cell="(310)555-0101"),
        "2": phone_lead("No Calls", "b@example.test", cell="(310)555-0102", call_flags="preferred call dont"),
        "3": phone_lead("Recently Texted", "c@example.test", cell="(310)555-0103", history=[{"when": when(3), "method": "Text Message (SMS) Outbound", "by": "Lee, Taylor", "text": "hi"}]),
        "4": phone_lead("Refused On Open", "d@example.test", cell="(310)555-0104", log_call_refused=True),
        "5": phone_lead("Refused On Save", "e@example.test", cell="(310)555-0105"),
        "6": phone_lead("Cell Do Not Call", "f@example.test", cell="(310)555-0106", call_flags="preferred call dont"),
        "7": phone_lead("Seven", "g@example.test", cell="(310)555-0107"),
    })
    browser.leads["6"]["phones"].append({"kind": "home", "phone": "(310)555-0206", "call": "preferred call off good", "text": ""})
    browser.refuse.add("5")
    app = live_app(tmp_path)
    try:
        app.load(OWNER)
        # No preview and no earlier single test: straight to the batch.
        rows = go(app, {"direct": True, "ids": [str(i) for i in range(1, 8)], "channel": "call", "call": CALL})
        status = {name: row["status"] for name, row in rows.items()}
        assert status == {"One": "sent", "No Calls": "skipped", "Recently Texted": "sent", "Refused On Open": "skipped",
                          "Refused On Save": "skipped", "Cell Do Not Call": "sent", "Seven": "sent"}
        assert "do-not-call" in rows["No Calls"]["detail"]
        assert "does not want to be called" in rows["Refused On Open"]["detail"]
        assert rows["Refused On Save"]["detail"] == "Momentum did not allow it: This number is on the do-not-call list"
        assert browser.calls == ["1", "3", "6", "7"]
        assert "(310)555-0206" in rows["Cell Do Not Call"]["detail"]  # logged to the number that may be called
        assert app.ledger.blocked({"id": "5", "channel": "call", "to": "(310)555-0105"}) is False  # a refusal holds nothing back
        assert app.snapshot()["error"] is None
    finally:
        browser.running = False
        app.ledger.close()


def test_log_call_choices_are_kept_when_a_later_sync_cannot_read_them(tmp_path):
    browser = start(tmp_path, {"1": phone_lead("One", "a@example.test")})
    app = live_app(tmp_path)
    try:
        assert app.load(OWNER)["call_options"]["results"]
        browser.leads["1"]["phones"] = []
        assert app.load(OWNER)["call_options"] == CALL_OPTIONS | {"phones": [{"id": "(310)555-0155", "name": "(310)555-0155"}]}
    finally:
        browser.running = False
        app.ledger.close()


# --- custom messages ---------------------------------------------------------------------------
def test_a_custom_text_is_personalized_in_the_preview_and_sent_as_shown(tmp_path):
    browser = start(tmp_path, {"1": phone_lead("CARTER, SAM", "a@example.test", first="SAM", last="CARTER"),
                               "2": phone_lead("Maria Lopez", "b@example.test", cell="(310)555-0102"),
                               "3": phone_lead("(310) 555-0103", "c@example.test", cell="(310)555-0103")})
    app = live_app(tmp_path)
    custom = {"name": "", "subject": "", "body": "Hi {customer_first_name}, it's {my_first_name} at the dealership."}
    try:
        app.load(OWNER)
        preview = app.preview(OWNER, ["1", "2", "3"], "custom", "text", None, False, custom)
        shown = {m["id"]: m for m in preview["messages"]}
        assert shown["1"]["body"] == "Hi Sam, it's Alex at the dealership."
        assert shown["2"]["body"] == "Hi Maria, it's Alex at the dealership."
        assert "first name" in shown["3"]["exclusion"]
        rows = go(app, {"preview_id": preview["id"]})
        assert [rows[n]["status"] for n in ("CARTER, SAM", "Maria Lopez", "(310) 555-0103")] == ["sent", "sent", "skipped"]
        assert browser.texts == ["1", "2"]
    finally:
        browser.running = False
        app.ledger.close()


def test_a_custom_email_goes_out_without_a_preview(tmp_path):
    browser = start(tmp_path, {"1": phone_lead("Sam Carter", "a@example.test"), "2": phone_lead("Opted Out", "b@example.test", optOut=True)})
    app = live_app(tmp_path)
    custom = {"subject": "Still looking, {customer_first_name}?", "body": "Hi {customer_first_name},\nThe car is here.\n{my_name}"}
    try:
        app.load(OWNER)
        rows = go(app, {"direct": True, "ids": ["1", "2"], "channel": "email", "template": "custom", "custom": custom})
        assert rows["Sam Carter"]["status"] == "sent" and rows["Opted Out"]["status"] == "skipped"
        assert browser.sent == ["1"] and browser.filled["1"] == {"subject": "Still looking, Sam?", "body": "Hi Sam,\nThe car is here.\nAlex Morgan Rivera"}
        assert rows["Sam Carter"]["subject"] == "Still looking, Sam?"  # what was sent is kept with the result
    finally:
        browser.running = False
        app.ledger.close()


def test_bad_custom_messages_are_refused_before_anything_is_opened(tmp_path):
    browser = start(tmp_path, {"1": phone_lead("One", "a@example.test")})
    app = live_app(tmp_path)
    try:
        app.load(OWNER)
        for custom, channel, error in [({"body": ""}, "text", "Write the message"), ({"subject": "", "body": "Hi"}, "email", "subject"),
                                       ({"body": "x" * 1351}, "text", "1350"), ({"body": "Hi {first}"}, "text", "Unknown placeholder")]:
            with pytest.raises(ValueError, match=error):
                app.preview(OWNER, ["1"], "custom", channel, None, False, custom)
            with pytest.raises(ValueError, match=error):
                app.run(OWNER, {"direct": True, "ids": ["1"], "channel": channel, "template": "custom", "custom": custom, "dry_run": True})
        assert browser.sent == browser.texts == []
    finally:
        browser.running = False
        app.ledger.close()


def test_my_templates_are_saved_updated_and_deleted(tmp_path):
    app = live_app(tmp_path)
    try:
        first = app.save_template({"name": "Day 1 text", "channel": "text", "body": "Hi {customer_first_name}"})["template"]
        app.save_template({"id": first["id"], "name": "Day 1 text", "channel": "text", "body": "Hello {customer_first_name}"})
        app.save_template({"name": "Intro", "channel": "email", "subject": "Hi {customer_first_name}", "body": "Welcome"})
        assert [(t["name"], t["body"]) for t in app.my_templates()] == [("Day 1 text", "Hello {customer_first_name}"), ("Intro", "Welcome")]
        with pytest.raises(ValueError, match="subject"):
            app.save_template({"name": "No subject", "channel": "email", "body": "Hi"})
        app.delete_template({"id": first["id"]})
        assert [t["name"] for t in app.my_templates()] == ["Intro"]
        assert live_app(tmp_path).my_templates()[0]["subject"] == "Hi {customer_first_name}"  # kept after a restart
    finally:
        app.ledger.close()


def test_a_dry_run_without_a_preview_sends_nothing(tmp_path):
    browser = start(tmp_path, {"1": phone_lead("One", "a@example.test"), "2": phone_lead("Two", "b@example.test", cell="(310)555-0102")})
    app = live_app(tmp_path)
    try:
        app.load(OWNER)
        batch = app.run(OWNER, {"direct": True, "ids": ["1", "2"], "channel": "call", "call": CALL, "dry_run": True})["batch"]
        app.worker.join(30)
        assert app.ledger.counts(batch) == {"dry-run": 2} and browser.calls == []
    finally:
        browser.running = False
        app.ledger.close()


def test_one_slow_lead_page_does_not_fail_the_sync(tmp_path):
    browser = start(tmp_path, {"1": phone_lead("Slow", "a@example.test"), "2": phone_lead("Fine", "b@example.test", cell="(310)555-0102")})
    original = browser.handle

    def slow(cmd):
        if cmd.get("fn") == "readLead" and cmd["args"][0] == "1":
            raise BridgeError("Timed out waiting for the lead page")
        return original(cmd)

    browser.handle = slow
    app = live_app(tmp_path)
    try:
        loaded = app.load(OWNER)
        assert len(loaded["leads"]) == 2 and loaded["templates"] and loaded["call_options"]["results"]
    finally:
        browser.running = False
        app.ledger.close()

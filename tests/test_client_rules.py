import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from momentum.bridge_adapter import MomentumBridge
from momentum.core import Ledger
from momentum.service import Application, short_error
from tests.test_bridge import ACCOUNT, EXT, FakeBrowser, config, paired, stamp


LA = ZoneInfo("America/Los_Angeles")  # the tool reads CRM times in the dealership's timezone


def when(hours_ago):
    return (datetime.now(LA) - timedelta(hours=hours_ago)).strftime("%m/%d/%Y %I:%M %p")


def lead(name, email, hours_ago_report, extra=None, opt_out=False):
    return {"name": name, "email": email, "optOut": opt_out, "extra": extra or [], "last_hours": hours_ago_report}


class ReportBrowser(FakeBrowser):
    """Fake browser whose report carries different last-contact times and whose dashboard returns counts."""

    def handle(self, cmd):
        if cmd["name"] == "report":
            rows = []
            for cid, item in self.leads.items():
                moment = datetime.now(LA) - timedelta(hours=item.get("last_hours", 120))
                rows.append({"id": cid, "name": item["name"], "last": moment.strftime("%Y-%m-%d %H:%M:%S.000"), "action": "Text Message (SMS)",
                             "created": (datetime.now(LA) - timedelta(hours=item.get("created_hours", 500))).strftime("%Y-%m-%d %H:%M:%S.000"), "status": "Active"})
            return rows
        if cmd.get("fn") == "dashboardCounts":
            return {"Outbound Calls": 1, "Outbound Emails / SMS": 7}
        result = super().handle(cmd)
        if cmd.get("fn") in ("selectTemplate", "composerState") and self.leads[self.composer_lead].get("badAddress"):
            result = {**result, "toClass": "sts-bad"}
        return result


def start(tmp_path, leads):
    bridge = __import__("momentum.bridge", fromlist=["get_bridge"]).get_bridge(tmp_path)
    bridge.open_pairing()
    token = bridge.hello(EXT)
    browser = ReportBrowser(bridge, token, leads)
    browser.start()
    time.sleep(0.1)
    return bridge, browser


def test_internal_notes_are_not_contact_but_real_contact_is(tmp_path):
    remark = {"when": when(1), "method": "Remark", "by": "Stone, Casey", "text": "sold with Alex"}
    status = {"when": when(2), "method": "ProspectStatus", "by": "Park, Jamie", "text": "assigned"}
    text = {"when": when(1), "method": "Text Message (SMS) Outbound", "by": "Lee, Taylor", "text": "hello"}
    bridge, browser = start(tmp_path, {
        "1": lead("Remark Only", "a@example.test", 100, [remark, status]),
        "2": lead("Recent Text", "b@example.test", 100, [text]),
    })
    try:
        with MomentumBridge(config(tmp_path)) as crm:
            crm.leads()
            assert crm.inspect("1")["subject"]  # a note and a status change do not reset the 72 hours
            skipped = crm.inspect("2")
            assert "subject" not in skipped and skipped["report_eligible"] is False  # a text yesterday does
    finally:
        browser.running = False


def test_lead_list_carries_ages_and_puts_the_newest_leads_first(tmp_path):
    fresh, middle, old = lead("Fresh", "a@example.test", 100), lead("Middle", "c@example.test", 400), lead("Old", "b@example.test", 90)
    fresh["created_hours"], middle["created_hours"], old["created_hours"] = 120, 600, 40000
    bridge, browser = start(tmp_path, {"1": old, "2": middle, "3": fresh})
    try:
        with MomentumBridge(config(tmp_path)) as crm:
            leads = crm.leads()
        assert [item["name"] for item in leads] == ["Fresh", "Middle", "Old"]  # most recently created first
        assert leads[0]["action"] == "Text Message (SMS)" and 99 < leads[0]["age_hours"] < 101
        assert 119 < leads[0]["created_hours"] < 121 and leads[0]["status"] == "Active"
    finally:
        browser.running = False


def test_ignore_pattern_is_configurable(tmp_path):
    remark = {"when": when(1), "method": "Remark", "by": "X", "text": "note"}
    bridge, browser = start(tmp_path, {"1": lead("Note", "a@example.test", 100, [remark])})
    try:
        with MomentumBridge(config(tmp_path, ignore_contact_methods="(?!)")) as crm:  # nothing is ignored
            crm.leads()
            assert "subject" not in crm.inspect("1")
    finally:
        browser.running = False


def test_hourly_allowance_wait_matches_the_rolling_window(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    cfg = {"hourly_limit": 2, "daily_limit": 50, "external_daily_reserve": 0, "delay_seconds": 0, "timezone": "America/Los_Angeles"}
    now = time.time()
    assert ledger.hourly_wait_seconds(cfg, now) == 0
    for number, age in enumerate((3000, 600)):
        ledger.db.execute("INSERT INTO attempts VALUES (?, ?, ?, ?, 'sent')", (f"a{number}", f"l{number}", f"e{number}", now - age))
    ledger.db.commit()
    # Two attempts are in the window; the older one (3000 s ago) frees a place in about 600 s.
    assert 595 < ledger.hourly_wait_seconds(cfg, now) < 605
    assert ledger.hourly_wait_seconds({**cfg, "hourly_limit": 3}, now) == 0
    ledger.close()


def test_a_batch_larger_than_the_hourly_allowance_is_accepted_and_paced(tmp_path):
    bridge, browser = start(tmp_path, {
        "1": lead("One", "a@example.test", 100), "2": lead("Two", "b@example.test", 100), "3": lead("Three", "c@example.test", 100)})
    app = Application(config(tmp_path, hourly_limit=1, daily_limit=10, delay_seconds=0, verified=True))
    owner = "pacing-owner-0000000"
    try:
        app.load(owner)
        # A single test send marks the configuration as verified (as in a real first live test).
        first = app.preview(owner, ["1"], "T1")
        app.run(owner, {"preview_id": first["id"], "dry_run": False, "test": True, "authorization": "SEND APPROVED EMAILS"})
        app.worker.join(30)
        assert app.ledger.get_meta(app.verification_key)
        # Two more leads exceed the hourly allowance (1) but fit today's (10): accepted, paced, stoppable.
        second = app.preview(owner, ["2", "3"], "T1")
        started = app.run(owner, {"preview_id": second["id"], "dry_run": False, "test": False, "authorization": "SEND APPROVED EMAILS"})
        time.sleep(1.5)
        assert "allowance" in app.snapshot()["job"].lower()
        app.stop.set()
        app.worker.join(30)
        outcomes = {row["status"] for row in app.ledger.results(started["batch"])}
        assert outcomes == {"skipped"} and browser.sent == ["1"]
    finally:
        app.stop.set()
        browser.running = False
        app.ledger.close()


def test_a_batch_beyond_today_allowance_is_refused(tmp_path):
    bridge, browser = start(tmp_path, {"1": lead("One", "a@example.test", 100), "2": lead("Two", "b@example.test", 100)})
    app = Application(config(tmp_path, hourly_limit=5, daily_limit=1, delay_seconds=0, verified=True))
    owner = "daily-owner-00000000"
    try:
        app.load(owner)
        app.ledger.set_meta(app.verification_key, "verified for the test")
        preview = app.preview(owner, ["1", "2"], "T1")
        with pytest.raises(ValueError, match="today's remaining allowance"):
            app.run(owner, {"preview_id": preview["id"], "dry_run": False, "test": False, "authorization": "SEND APPROVED EMAILS"})
    finally:
        browser.running = False
        app.ledger.close()


def test_preview_reports_progress_and_can_be_stopped(tmp_path):
    bridge, browser = start(tmp_path, {str(i): lead(f"L{i}", f"l{i}@example.test", 100) for i in range(1, 6)})
    app = Application(config(tmp_path))
    owner = "progress-owner-000000"
    try:
        app.load(owner)
        seen = []
        original = MomentumBridge.inspect

        def watching(self, lead_id):
            seen.append(app.job)
            if len(seen) == 2:
                app.stop.set()
            return original(self, lead_id)

        MomentumBridge.inspect = watching
        try:
            with pytest.raises(ValueError, match="Preview stopped"):
                app.preview(owner, [str(i) for i in range(1, 6)], "T1")
        finally:
            MomentumBridge.inspect = original
        assert seen[0] == "Checking lead 1 of 5" and seen[1] == "Checking lead 2 of 5"
        assert app.snapshot()["job"] == "Ready"
    finally:
        browser.running = False
        app.ledger.close()


def test_one_bad_address_skips_that_lead_and_the_batch_continues(tmp_path):
    bad = lead("Bad Address", "bad@example.test", 100)
    bad["badAddress"] = True
    bridge, browser = start(tmp_path, {"1": lead("Fine", "ok@example.test", 100), "2": bad, "3": lead("Also Fine", "ok2@example.test", 100)})
    app = Application(config(tmp_path))
    owner = "bad-address-owner-000"
    try:
        app.load(owner)
        preview = app.preview(owner, ["1", "2", "3"], "T1")
        reasons = {m["name"]: m["exclusion"] for m in preview["messages"]}
        assert reasons["Fine"] is None and reasons["Also Fine"] is None
        assert "opted out or bad" in reasons["Bad Address"]
        started = app.run(owner, {"preview_id": preview["id"], "dry_run": True, "test": False, "authorization": ""})
        app.worker.join(30)
        statuses = {row["name"]: row["status"] for row in app.ledger.results(started["batch"])}
        assert statuses == {"Fine": "dry-run", "Bad Address": "skipped", "Also Fine": "dry-run"}
    finally:
        browser.running = False
        app.ledger.close()


def test_extension_reload_can_be_requested(tmp_path):
    bridge, browser = start(tmp_path, {})
    app = Application(config(tmp_path))
    try:
        assert app.reload_extension() == {"ok": True}
    finally:
        browser.running = False
        app.ledger.close()


def test_crm_count_comes_from_the_dashboard(tmp_path):
    bridge, browser = start(tmp_path, {})
    app = Application(config(tmp_path))
    try:
        assert app.crm_stats()["counts"]["Outbound Emails / SMS"] == 7
    finally:
        browser.running = False
        app.ledger.close()


def test_errors_are_one_readable_line():
    long = RuntimeError("BrowserContext.storage_state: Target page, context or browser has been closed\nBrowser logs:\n<launching> C:\\x --flag --flag")
    assert "closed before the sign-in finished" in short_error(long)
    assert "\n" not in short_error(RuntimeError("line one\nline two"))
    assert len(short_error(RuntimeError("x" * 1000))) == 240


def test_unreadable_contact_time_blocks_the_lead(tmp_path):
    odd = {"when": "sometime last week", "method": "Text Message (SMS) Outbound", "by": "X", "text": "hi"}
    bridge, browser = start(tmp_path, {"1": lead("Odd Date", "a@example.test", 100, [odd])})
    try:
        with MomentumBridge(config(tmp_path)) as crm:
            crm.leads()
            skipped = crm.inspect("1")
            assert "subject" not in skipped and skipped["report_eligible"] is False
    finally:
        browser.running = False


def test_send_is_confirmed_only_by_this_outbound_email(tmp_path):
    bridge, browser = start(tmp_path, {"1": lead("One", "a@example.test", 100)})
    try:
        with MomentumBridge(config(tmp_path, template_name="BLANK Email")) as crm:
            crm.leads()
            crm.send_started = datetime.now(LA)
            sender = "Rivera, Alex Morgan"
            row = lambda **kw: {"when": when(0), "method": "Email Outbound", "by": sender, "text": "x", **kw}
            assert crm._is_this_send(row(), sender)
            assert not crm._is_this_send(row(method="Email Inbound"), sender)           # not outbound
            assert not crm._is_this_send(row(by="Someone, Else"), sender)               # another sender
            assert not crm._is_this_send(row(when=when(30)), sender)                    # an old email
            assert not crm._is_this_send(row(when="no date"), sender)                   # unreadable time
            assert not crm._is_this_send(row(text="Template: Other Template"), sender)  # a different template
            assert crm._is_this_send(row(text="Template: BLANK Email"), sender)
    finally:
        browser.running = False


def test_live_profile_needs_no_attestation_and_has_no_caps_of_its_own(tmp_path, monkeypatch):
    import json
    from momentum.settings import load_settings
    monkeypatch.setenv("MOMENTUM_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps({"mode": "live", "verified": False, "crm_profile": "udc", "connection": "both",
        "account_name": ACCOUNT, "hourly_limit": 20, "daily_limit": 100, "max_batch": 500, "delay_seconds": 30}), encoding="utf-8")
    loaded = load_settings()
    assert loaded["verified"] is True and loaded["max_batch"] == loaded["hourly_limit"] == loaded["daily_limit"] == 0
    assert loaded["delay_seconds"] == 5
    (tmp_path / "config.json").write_text(json.dumps({"mode": "live", "crm_profile": "udc", "connection": "both", "account_name": ACCOUNT,
        "enforce_limits": True, "hourly_limit": 20, "daily_limit": 100}), encoding="utf-8")
    assert load_settings()["hourly_limit"] == 20  # caps come back only when asked for


def test_a_limit_of_zero_means_no_limit(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    cfg = {"hourly_limit": 0, "daily_limit": 0, "external_daily_reserve": 0, "delay_seconds": 0, "timezone": "America/Los_Angeles"}
    now = time.time()
    for number in range(50):
        ledger.db.execute("INSERT INTO attempts VALUES (?, ?, ?, ?, 'sent')", (f"a{number}", f"l{number}", f"e{number}", now - number))
    ledger.db.commit()
    assert ledger.hourly_wait_seconds(cfg, now) == 0 and ledger.quota(cfg, now)["day_remaining"] > 10 ** 6
    ledger.close()


def test_first_single_send_works_without_the_verified_flag(tmp_path):
    bridge, browser = start(tmp_path, {"1": lead("One", "a@example.test", 100), "2": lead("Two", "b@example.test", 100)})
    app = Application(config(tmp_path, verified=False, max_batch=0, hourly_limit=0, daily_limit=0, delay_seconds=0))
    owner = "first-send-owner-0000"
    try:
        app.load(owner)
        preview = app.preview(owner, ["1"], "T1")
        app.run(owner, {"preview_id": preview["id"], "dry_run": False, "test": True, "authorization": "SEND APPROVED EMAILS"})
        app.worker.join(30)
        assert browser.sent == ["1"] and app.ledger.get_meta(app.verification_key)
        preview = app.preview(owner, ["2"], "T1")  # after the test, a batch of any size is accepted
        app.run(owner, {"preview_id": preview["id"], "dry_run": False, "test": False, "authorization": "SEND APPROVED EMAILS"})
        app.worker.join(30)
        assert browser.sent == ["1", "2"]
    finally:
        browser.running = False
        app.ledger.close()


def test_one_lead_that_will_not_load_does_not_stop_a_long_preview_or_run(tmp_path, monkeypatch):
    bridge, browser = start(tmp_path, {str(i): lead(f"L{i}", f"l{i}@example.test", 100) for i in range(1, 5)})
    app = Application(config(tmp_path, max_batch=0, hourly_limit=0, daily_limit=0, delay_seconds=0))
    owner = "flaky-owner-00000000"
    original = MomentumBridge.inspect
    broken = {"2"}

    def flaky(self, lead_id):
        if lead_id in broken:
            raise RuntimeError("Timed out waiting for the lead page")
        return original(self, lead_id)

    monkeypatch.setattr(MomentumBridge, "inspect", flaky)
    try:
        app.load(owner)
        preview = app.preview(owner, ["1", "2", "3", "4"], "T1")
        reasons = {m["name"]: m["exclusion"] for m in preview["messages"]}
        assert reasons["L2"].startswith("Could not be checked this time") and reasons["L1"] is None and reasons["L4"] is None
        broken.add("3")  # now fails during the run too: recorded as failed, the queue goes on
        started = app.run(owner, {"preview_id": preview["id"], "dry_run": True, "test": False, "authorization": ""})
        app.worker.join(30)
        statuses = {row["name"]: row["status"] for row in app.ledger.results(started["batch"])}
        assert statuses == {"L1": "dry-run", "L2": "skipped", "L3": "failed", "L4": "dry-run"}
        assert app.snapshot()["error"] is None
    finally:
        browser.running = False
        app.ledger.close()


def test_a_preview_stops_when_momentum_stops_answering(tmp_path, monkeypatch):
    bridge, browser = start(tmp_path, {str(i): lead(f"L{i}", f"l{i}@example.test", 100) for i in range(1, 6)})
    app = Application(config(tmp_path))
    owner = "dead-owner-000000000"
    try:
        app.load(owner)
        monkeypatch.setattr(MomentumBridge, "inspect", lambda self, lead_id: (_ for _ in ()).throw(RuntimeError("The browser is not connected")))
        with pytest.raises(ValueError, match="did not answer for 3 leads in a row"):
            app.preview(owner, [str(i) for i in range(1, 6)], "T1")
    finally:
        browser.running = False
        app.ledger.close()

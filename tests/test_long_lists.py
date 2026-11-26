"""Repeat follow-ups after 72 hours, and quick start for long lists."""
import json
import time

import pytest

from momentum.core import Ledger
from momentum.full_report import FullReport
from momentum.service import Application
from momentum.settings import load_settings
from tests.test_channels import ChannelBrowser, config, phone_lead
from tests.test_bridge import EXT

CFG = {"hourly_limit": 0, "daily_limit": 0, "external_daily_reserve": 0, "delay_seconds": 0, "timezone": "America/Los_Angeles"}
LEAD = {"id": "1", "email": "a@example.test"}
HOUR = 3600


def ledger(tmp_path, hours):
    return Ledger(tmp_path / "l.sqlite", hours)


def test_the_same_action_is_possible_again_after_72_hours(tmp_path):
    book, now = ledger(tmp_path, 72), time.time()
    assert book.reserve(LEAD, "b1", CFG, now)
    book.finish("1", "sent", "Email Outbound")
    done = time.time()
    assert book.blocked(LEAD, done + 71 * HOUR) and not book.reserve(LEAD, "b2", CFG, done + 71 * HOUR)
    assert not book.blocked(LEAD, done + 73 * HOUR) and book.reserve(LEAD, "b2", CFG, done + 73 * HOUR)
    assert book.blocked(LEAD, done + 200 * HOUR)  # the new one is uncertain until it is confirmed
    assert [r["batch"] for r in book.rows()] == ["b2"]
    assert "Email Outbound" in book.db.execute("SELECT evidence FROM audit WHERE action='earlier-follow-up'").fetchone()[0]
    book.close()


def test_uncertain_unreadable_and_switched_off_never_repeat(tmp_path):
    book, now = ledger(tmp_path, 72), time.time()
    assert book.reserve(LEAD, "b1", CFG, now)
    assert book.blocked(LEAD, now + 10000 * HOUR)  # uncertain
    book.finish("1", "sent", "ok")
    book.db.execute("UPDATE sends SET updated='not a date'")
    assert book.blocked(LEAD, now + 10000 * HOUR)
    book.close()
    off = ledger(tmp_path, 0)
    off.db.execute("UPDATE sends SET updated='2020-01-01T00:00:00+00:00'")
    assert off.blocked(LEAD)
    off.close()


def test_the_same_address_under_another_customer_is_also_held(tmp_path):
    book = ledger(tmp_path, 72)
    book.reserve(LEAD, "b1", CFG)
    book.finish("1", "sent", "ok")
    assert book.blocked({"id": "2", "email": "A@example.test"})
    book.close()


def test_settings_turn_it_on_for_the_dealership_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("MOMENTUM_HOME", str(tmp_path))
    base = {"mode": "live", "account_name": "A B", "crm_profile": "udc", "connection": "both", "allowed_origin": "https://x.udccrm.com"}
    (tmp_path / "config.json").write_text(json.dumps(base))
    assert load_settings()["repeat_after_hours"] == 72
    (tmp_path / "config.json").write_text(json.dumps({**base, "repeat_after_hours": 0}))
    assert load_settings()["repeat_after_hours"] == 0
    (tmp_path / "config.json").write_text(json.dumps({**base, "repeat_after_hours": 24}))
    with pytest.raises(ValueError, match="72 or more"):
        load_settings()


def start(tmp_path, leads):
    from momentum.bridge import get_bridge
    bridge = get_bridge(tmp_path)
    bridge.open_pairing()
    browser = ChannelBrowser(bridge, bridge.hello(EXT), leads)
    browser.start()
    time.sleep(0.1)
    return browser


def people(n):
    return {str(i): phone_lead(f"L{i}", f"l{i}@example.test", cell=f"(310)555-{i:04d}") for i in range(1, n + 1)}


OWNER = "owner-quick-000000000"


def test_quick_start_previews_a_sample_and_checks_the_rest_once(tmp_path):
    leads = people(60)
    leads["50"]["optOut"] = True
    browser = start(tmp_path, leads)
    reads = []
    original = browser.handle
    browser.handle = lambda cmd: (reads.append(cmd["args"][0]) if cmd.get("fn") == "readLead" else None, original(cmd))[1]
    app = Application(config(tmp_path, delay_seconds=0, max_batch=0, hourly_limit=0, daily_limit=0))
    try:
        ids = [x["id"] for x in app.load(OWNER)["leads"]]
        app.ledger.set_meta(app.verification("email"), "done")
        reads.clear()
        job = app.start_preview(OWNER, ids, "T1", "email", None, True)["job"]
        while not (state := app.preview_result(OWNER, job))["done"]:
            time.sleep(0.02)
        p = state["preview"]
        assert len(reads) == 10 and (len(p["messages"]), p["ready"], p["deferred"], p["total"]) == (10, 60, 50, 60)
        reads.clear()
        app.run(OWNER, {"preview_id": p["id"], "dry_run": True})
        app.worker.join(60)
        assert len(reads) == 60 and app.snapshot()["result_counts"] == {"dry-run": 59, "skipped": 1} and browser.sent == []
        preview = app.preview(OWNER, ids, "T1", "email", None, True)
        batch = app.run(OWNER, {"preview_id": preview["id"], "dry_run": False, "authorization": "SEND APPROVED EMAILS"})["batch"]
        app.worker.join(60)
        assert app.ledger.counts(batch) == {"sent": 59, "skipped": 1} and sorted(browser.sent, key=int) == [i for i in sorted(ids, key=int) if i != "50"]
        row = {r["id"]: r for r in app.ledger.results(batch)}
        assert row["50"]["detail"] == "The CRM marks this email address as opted out or bad" and row["40"]["email"] == "l40@example.test"
    finally:
        browser.running = False
        app.ledger.close()


def test_quick_start_stops_when_nobody_in_the_sample_can_be_contacted(tmp_path):
    leads = people(60)
    for lead in leads.values():
        lead["optOut"] = True
    browser = start(tmp_path, leads)
    app = Application(config(tmp_path, delay_seconds=0, max_batch=0, hourly_limit=0, daily_limit=0))
    try:
        ids = [x["id"] for x in app.load(OWNER)["leads"]] or list(leads)
        app.reports[OWNER] = {"ids": set(leads), "names": {}, "templates": [{"id": "T1", "name": "T"}]}
        with pytest.raises(ValueError, match="None of the first 40"):
            app.preview(OWNER, list(leads), "T1", "email", None, True)
        assert len(app.preview(OWNER, ids[:20], "T1", "email", None, True)["messages"]) == 20  # short lists are always previewed in full
    finally:
        browser.running = False
        app.ledger.close()


def test_a_long_run_waits_out_a_short_outage(tmp_path, monkeypatch):
    browser = start(tmp_path, people(30))
    app = Application(config(tmp_path, delay_seconds=0, max_batch=0, hourly_limit=0, daily_limit=0))
    monkeypatch.setattr("momentum.service.OUTAGE_WAIT", 0.05)
    from momentum.bridge_adapter import MomentumBridge
    original, down = MomentumBridge.inspect, {"left": 0}

    def flaky(self, lead_id):
        if down["left"]:
            down["left"] -= 1
            raise RuntimeError("Momentum could not be opened")
        return original(self, lead_id)

    monkeypatch.setattr(MomentumBridge, "inspect", flaky)
    try:
        ids = [x["id"] for x in app.load(OWNER)["leads"]]
        p = app.preview(OWNER, ids, "T1", "email", None, True)
        down["left"] = 5
        app.run(OWNER, {"preview_id": p["id"], "dry_run": True})
        app.worker.join(60)
        # The 5 leads that could not be opened during the outage are tried again at the end, and nothing is lost.
        assert app.snapshot()["error"] is None and app.snapshot()["result_counts"] == {"dry-run": 30}
    finally:
        browser.running = False
        app.ledger.close()


def test_reachability_comes_from_the_report_and_unknown_is_not_no():
    def row(i, reach):
        return [str(i), f"P{i}", "Me", "", "", "", "Active", [], reach]
    r = FullReport([row(1, "cp"), row(2, "p"), row(3, ""), row(4, "e")], "Me", "America/Los_Angeles", {"contacts": {"text": True, "call": True, "email": False}})
    ids = lambda reach: sorted(p["id"] for p in r.search(reach=reach)["rows"])
    assert ids("text") == ["1"] and ids("call") == ["1", "2"] and ids("email") == ["1", "2", "3", "4"]  # no email column: not filtered
    assert r.unreachable("text") == {"2", "3", "4"} and r.unreachable("email") == set() and r.search()["contacts"] == ["call", "text"]
    old = FullReport([row(1, "")[:8]], "Me", "America/Los_Angeles", {})
    assert old.search(reach="text")["matches"] == 1 and old.unreachable("call") == set()

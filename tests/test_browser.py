import threading
import os
import time
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect

from momentum.service import Application
from momentum.server import create_server
from momentum.settings import DEFAULTS
from tests.simulated_crm import CRM, SimulatedMomentum


def wait_batch(app, seconds=180):
    app.worker.join(seconds)
    assert not app.worker.is_alive(), "Batch did not finish"
    assert app.error is None, app.error


@pytest.fixture
def crm():
    fixture = CRM(120)
    try:
        yield fixture
    finally:
        fixture.close()


def test_actual_browser_pagination_templates_and_100_sends(crm, tmp_path):
    app = Application(crm.config(tmp_path), SimulatedMomentum)
    try:
        report = app.load("browser-test")
        assert len(report["leads"]) == 120
        assert len(report["templates"]) == 2
        ids = [x["id"] for x in report["leads"]]
        preview = app.preview("browser-test", ids[:1], "check-in")
        assert preview["messages"][0]["subject"] == "Checking in"
        app.run("browser-test", {"preview_id": preview["id"], "dry_run": False, "test": True, "authorization": "SEND APPROVED EMAILS"})
        wait_batch(app)
        assert app.snapshot()["test_verified"]
        assert len(crm.sends) == 1
        assert "Demo User" in crm.history[1][0]
        preview = app.preview("browser-test", ids[1:101], "follow-up")
        batch = app.run("browser-test", {"preview_id": preview["id"], "dry_run": False, "authorization": "SEND APPROVED EMAILS"})["batch"]
        wait_batch(app, 240)
        rows = app.ledger.results(batch)
        assert len(rows) == 100
        assert all(r["status"] == "sent" for r in rows)
        assert len(crm.sends) == len(set(crm.sends)) == 101
        repeat = app.preview("browser-test", ids[:101], "follow-up")
        assert all(m["exclusion"] for m in repeat["messages"])
    finally:
        app.ledger.close()


def test_browser_recheck_changed_eligibility_and_content(crm, tmp_path):
    app = Application(crm.config(tmp_path), SimulatedMomentum)
    try:
        app.load("test")
        p = app.preview("test", [crm.origin+"/lead/1", crm.origin+"/lead/2"], "follow-up")
        crm.opt_out.add(1)
        crm.changed.add(2)
        batch = app.run("test", {"preview_id": p["id"]})["batch"]
        wait_batch(app)
        assert [r["status"] for r in app.ledger.results(batch)] == ["skipped", "skipped"]
        assert crm.sends == []
    finally:
        app.ledger.close()


def test_login_persists_and_wrong_account_fails(crm, tmp_path):
    crm.require_login = True
    config = crm.config(tmp_path)
    with SimulatedMomentum(config) as browser:
        browser.page.goto(crm.origin+"/report")
        browser.page.get_by_role("button", name="Sign in", exact=True).click()
        browser.page.locator("#account").wait_for()
        browser.login(threading.Event())
    with SimulatedMomentum(config) as browser:
        assert len(browser.leads()) == 120
    crm.account = "Different User"
    with SimulatedMomentum(config) as browser:
        with pytest.raises(RuntimeError, match="account"):
            browser.leads()


def test_missing_confirmation_keeps_duplicate_block(crm, tmp_path):
    crm.fail_confirmation = True
    app = Application(crm.config(tmp_path), SimulatedMomentum)
    try:
        app.load("test")
        preview = app.preview("test", [crm.origin+"/lead/1"], "follow-up")
        batch = app.run("test", {"preview_id": preview["id"], "dry_run": False, "test": True, "authorization": "SEND APPROVED EMAILS"})["batch"]
        app.worker.join(30)
        assert not app.worker.is_alive()
        assert app.ledger.results(batch)[0]["status"] == "uncertain"
        assert crm.sends == [1]
        assert app.ledger.blocked(preview["messages"][0])
        assert not app.snapshot()["test_verified"]
    finally:
        app.ledger.close()


def test_ui_select_preview_dryrun_export_and_two_tabs(tmp_path):
    config = {**DEFAULTS, "data_dir": str(tmp_path), "demo_leads": 120}
    app = Application(config)
    server = create_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, channel=os.environ.get("MOMENTUM_TEST_CHANNEL"))
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url)
            page.get_by_role("button", name="Sync report", exact=True).click()
            expect(page.locator(".lead")).to_have_count(120)
            page.locator("#all").check()
            page.locator("#template").select_option("check-in")
            page.locator("#quick").uncheck()  # preview every one of them
            page.locator("#preview").click()
            expect(page.locator("#messages article")).to_have_count(120)
            expect(page.locator("#messages")).to_contain_text("A quick check-in")
            second = browser.new_page()
            second.goto(url)
            second.get_by_role("button", name="Sync report", exact=True).click()
            expect(second.locator(".lead")).to_have_count(120)
            page.locator("#run").click()
            expect(page.locator("#results")).to_contain_text("dry-run", timeout=15000)
            expect(page.locator("#results tr")).to_have_count(121)
            app.worker.join(30)
            rows = app.snapshot()["results"]
            assert len(rows) == 120
            assert sum(r["status"] == "dry-run" for r in rows) == 108
            assert sum(r["status"] == "skipped" for r in rows) == 12
            assert app.ledger.rows() == []
            with page.expect_download() as download:
                page.locator("#export").click()
            path = download.value.path()
            assert "dry-run" in Path(path).read_text()
            page.screenshot(path=str(tmp_path/"ui.png"), full_page=True)
            if os.environ.get("MOMENTUM_TEST_SCREENSHOT"):
                page.screenshot(path=os.environ["MOMENTUM_TEST_SCREENSHOT"], full_page=True)
            assert errors == []
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        if app.worker:
            app.worker.join(20)
        app.ledger.close()

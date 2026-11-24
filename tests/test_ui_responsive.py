import threading
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

from momentum.server import create_server
from momentum.service import Application
from momentum.settings import DEFAULTS


def test_responsive_layout_search_and_preview(tmp_path):
    app = Application({**DEFAULTS, "data_dir": str(tmp_path), "demo_leads": 30})
    server = create_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    artifacts = Path(__file__).resolve().parent.parent / "test-artifacts"
    artifacts.mkdir(exist_ok=True)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1050})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}")
            page.get_by_role("button", name="Sync report", exact=True).click()
            expect(page.locator(".lead")).to_have_count(30)
            page.locator(".lead").nth(0).check()
            page.locator(".lead").nth(1).check()
            page.get_by_role("button", name="Preview messages", exact=True).click()
            expect(page.locator("#metric-ready")).to_have_text("2")
            expect(page.locator("#messages article")).to_have_count(2)
            for width in [1440, 1024, 768, 390, 320]:
                page.set_viewport_size({"width": width, "height": 1050 if width > 680 else 844})
                wide = page.evaluate("[...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > window.innerWidth + 1).slice(0, 5).map(e => e.tagName + '#' + e.id + '.' + e.className)")
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), f"Overflow at {width}px: {wide}"
                expect(page.locator("#run")).to_be_enabled()
                page.screenshot(path=str(artifacts/f"ui-{width}.png"), full_page=True)
            page.locator("#search").fill("lead30@example.com")
            expect(page.locator(".lead-row:visible")).to_have_count(1)
            # Filtering does not silently change the selected recipients.
            expect(page.locator("#metric-selected")).to_have_text("2")
            page.locator("#search").fill("no-matching-lead")
            expect(page.locator("#search-empty")).to_be_visible()
            page.locator("#dry").uncheck()
            expect(page.locator("#approval-area")).to_be_visible()
            page.locator("#approval").fill("SEND APPROVED EMAILS")
            page.locator("#template").select_option("check-in")
            expect(page.locator("#run")).to_be_disabled()
            expect(page.locator("#approval")).to_have_value("")
            assert errors == []
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        app.ledger.close()


def test_direct_file_runs_the_sample_preview_without_sending():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        path = Path(__file__).resolve().parent.parent / "momentum" / "index.html"
        page.goto(path.as_uri())
        expect(page.locator("#file-notice")).to_be_visible()
        expect(page.locator("#mode")).to_have_text("Demo · no emails sent")
        expect(page.locator(".lead").first).to_be_attached()
        expect(page.locator("#login")).to_be_disabled()
        expect(page.locator("#connect")).to_be_disabled()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert errors == []
        browser.close()


def test_selects_recovery_accordion_navigation_and_guide(tmp_path):
    from momentum.adapters import Demo
    app = Application({**DEFAULTS, "data_dir": str(tmp_path)})
    server = create_server(app, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}")
            expect(page.locator("#template")).to_be_disabled()
            expect(page.locator("#batch")).to_be_disabled()
            page.get_by_role("button", name="Sync report", exact=True).click()
            expect(page.locator("#template")).to_be_enabled()
            page.locator("#template").select_option("check-in")
            expect(page.locator("#template")).to_have_value("check-in")
            page.locator("#template").select_option(label="Vehicle follow-up")
            expect(page.locator("#template")).to_have_value("follow-up")
            page.locator(".nav a[href='#activity']").click()
            expect(page.locator(".nav a[href='#activity']")).to_have_attribute("aria-current", "page")
            page.get_by_text("Resolve an uncertain send", exact=True).click()
            expect(page.locator("#resolution")).to_be_disabled()
            app.ledger.reserve(Demo({}).inspect("1"), "test-reservation")
            expect(page.locator("#resolution")).to_be_enabled(timeout=5000)
            page.locator("#resolution").select_option("not-sent")
            expect(page.locator("#resolution")).to_have_value("not-sent")
            # The guided tour opens from the button, shows each step, and can be skipped.
            page.locator("#start-guide").click()
            expect(page.locator(".driver-popover-title")).to_have_text("Sign in to CRM")
            page.locator(".driver-popover-next-btn").click()
            expect(page.locator(".driver-popover-title")).to_have_text("Connect automation browser")
            page.get_by_role("button", name="Skip").click()
            expect(page.locator(".driver-popover")).to_have_count(0)
            for width in [1280, 390]:
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        app.ledger.close()

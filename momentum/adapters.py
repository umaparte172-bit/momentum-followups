import time
import json
import os
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from .core import eligible, fingerprint


class Demo:
    def __init__(self, config):
        self.config = config

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def login(self, stop):
        return True

    def templates(self, leads):
        return [{"id": "follow-up", "name": "Vehicle follow-up"}, {"id": "check-in", "name": "Quick check-in"}]

    def leads(self):
        return [dict(id=str(i), name=f"Demo Lead {i}", email=f"lead{i}@example.com", assigned_to="Demo User", opt_out=i % 10 == 3, report_eligible=True, recent_email=False, age_hours=(i * 17) % 200 + 10, action="Text Message (SMS)") for i in range(1, self.config.get("demo_leads", 50)+1)]

    def inspect(self, lead_id):
        lead = next(x for x in self.leads() if x["id"] == lead_id)
        check = self.config.get("template_id") == "check-in"
        custom = self.config.get("custom")
        if custom:
            from .core import personalize
            return {**lead, "subject": personalize(custom["subject"], lead, "Demo User"), "body": personalize(custom["body"], lead, "Demo User")}
        return {**lead, "subject": "A quick check-in" if check else "Following up on your vehicle search", "body": f"Hi {lead['name']},\n\n" + ("How can I help today?" if check else "Are you still looking for your next vehicle?") + "\n\nDemo User"}

    def send(self, message):
        raise RuntimeError("Demo mode never sends email")


class Momentum:
    """Real CRM selectors and template readiness markers require dealership validation."""
    def __init__(self, config):
        self.config = config
        self.s = config["selectors"]

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self.check_url(self.config.get("login_url") or self.config["report_url"])
        self.pw = sync_playwright().start()
        try:
            self.context = self.pw.chromium.launch_persistent_context(str(Path(self.config["data_dir"]) / "browser"), headless=self.config.get("headless", False), channel=self.config.get("browser_channel"))
            self.auth_path = Path(self.config["data_dir"]) / "auth.json"
            if self.auth_path.exists():
                saved = json.loads(self.auth_path.read_text(encoding="utf-8"))
                self.context.add_cookies(saved.get("cookies", []))
            self.page = self.context.new_page()
            self.page.set_default_timeout(15000)
        except Exception:
            self.pw.stop()
            raise
        return self

    def __exit__(self, *args):
        try:
            # Preserve session-only cookies as well as the persistent browser profile.
            # CRM-side expiry/revocation still applies; no credentials or MFA are bypassed.
            try:
                saved = self.context.storage_state()
            except Exception:
                saved = None  # the user closed the automation browser; there is no session to save
            if saved is not None:
                temp = self.auth_path.with_suffix(".tmp")
                temp.write_text(json.dumps(saved), encoding="utf-8")
                temp.chmod(0o600)
                os.replace(temp, self.auth_path)
        finally:
            try:
                self.context.close()
            except Exception:
                pass
            finally:
                self.pw.stop()

    def require(self, *keys):
        if any(not self.s.get(key) for key in keys):
            raise ValueError("Missing verified CRM selectors: " + ", ".join(k for k in keys if not self.s.get(k)))

    def check_url(self, url):
        origin = urlparse(self.config["allowed_origin"])
        target = urlparse(url)
        if target.scheme != "https" or target.username or target.password or (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
            raise RuntimeError("CRM URL is outside the configured HTTPS origin")

    def login(self, stop, finished=None):
        login_url = self.config.get("login_url") or self.config["report_url"]
        self.check_url(login_url)
        self.page.goto(login_url)
        deadline = time.monotonic() + self.config.get("login_timeout_seconds", 300)
        while time.monotonic() < deadline and not stop.is_set():
            if self.page.is_closed():
                raise RuntimeError("Sign-in window closed")
            if self.s.get("account_name"):
                account = self.page.locator(self.s["account_name"])
                if account.count() and account.first.is_visible():
                    self.account()
                    return True
            if finished is not None and finished.is_set():
                if self.s.get("account_name"):
                    self.account()
                    return True
                # User completion saves the session, but is not proof of authentication.
                return False
            self.page.wait_for_timeout(250)
        raise RuntimeError("Sign-in canceled or timed out; click Sign in to retry")

    def account(self):
        self.require("account_name")
        self.check_url(self.page.url)
        if self.page.locator(self.s["account_name"]).inner_text().strip() != self.config["account_name"]:
            raise RuntimeError("Signed-in account does not match the approved account")

    def leads(self):
        self.require("report_row", "next_page", "lead_link", "name", "report_ready")
        self.page.goto(self.config["report_url"])
        self.account()
        result, seen, signatures = [], set(), set()
        while True:
            self.page.locator(self.s["report_ready"]).wait_for()
            rows = self.page.locator(self.s["report_row"])
            signature = tuple(rows.locator(self.s["lead_link"]).evaluate_all("els => els.map(el => el.href)"))
            if signature in signatures:
                raise RuntimeError("Report pagination repeated; refusing incomplete import")
            signatures.add(signature)
            for row in rows.all():
                href = row.locator(self.s["lead_link"]).evaluate("el => el.href")
                self.check_url(href)
                if href not in seen:
                    result.append({"id": href, "name": row.locator(self.s["name"]).inner_text().strip()})
                    seen.add(href)
            next_page = self.page.locator(self.s["next_page"])
            if not next_page.count() or not next_page.is_visible() or not next_page.is_enabled() or next_page.get_attribute("aria-disabled") == "true":
                return result
            next_page.click()
            self.page.wait_for_function("([row, link, old]) => JSON.stringify([...document.querySelectorAll(row)].flatMap(r => [...r.querySelectorAll(link)].map(a => a.href))) !== JSON.stringify(old)", arg=[self.s["report_row"], self.s["lead_link"], list(signature)])

    def _lead(self, lead_id):
        self.require("lead_ready", "name", "email", "assigned_to", "opt_out", "report_eligible", "recent_email")
        self.check_url(lead_id)
        self.page.goto(lead_id)
        self.page.locator(self.s["lead_ready"]).wait_for()
        self.account()
        def value(key):
            return self.page.locator(self.s[key]).inner_text().strip()
        def boolean(key):
            return {"true": True, "false": False}.get(value(key).lower())
        return dict(id=lead_id, name=value("name"), email=value("email"), assigned_to=value("assigned_to"), opt_out=boolean("opt_out"), report_eligible=boolean("report_eligible"), recent_email=boolean("recent_email"))

    def templates(self, leads):
        self.require("email_button", "template_select")
        for item in leads:
            lead = self._lead(item["id"])
            if eligible(lead, self.config["account_name"]):
                continue
            self.page.locator(self.s["email_button"]).click()
            options = self.page.locator(self.s["template_select"]).locator("option").evaluate_all("els => els.filter(e => e.value && !e.disabled).map(e => ({id:e.value,name:e.textContent.trim()}))")
            return options
        return []

    def inspect(self, lead_id):
        lead = self._lead(lead_id)
        if eligible(lead, self.config["account_name"]):
            return lead
        if self.config.get("custom"):
            raise ValueError("Custom messages need the Momentum Bridge connection")
        self.require("history_entry", "history_sender", "history_recipient", "history_subject", "email_button", "template_select", "template_ready", "recipient", "subject", "body")
        self.history_before = self.page.locator(self.s["history_entry"]).all_text_contents()
        self.page.locator(self.s["email_button"]).click()
        self.page.locator(self.s["template_select"]).select_option(self.config["template_id"])
        # Must indicate that personalization for this selected template has finished.
        self.page.locator(self.s["template_ready"]).wait_for()
        return self._composer(lead)

    def _composer(self, lead):
        recipient = self.page.locator(self.s["recipient"]).input_value().strip()
        if recipient.lower() != lead["email"].lower():
            raise RuntimeError("Composer recipient does not match lead")
        for key in ("cc", "bcc"):
            self.require(key)
            if self.page.locator(self.s[key]).input_value().strip():
                raise RuntimeError("Unexpected CC or BCC recipients")
        return {**lead, "subject": self.page.locator(self.s["subject"]).input_value(), "body": self.page.locator(self.s["body"]).input_value()}

    def send(self, message):
        if not self.config.get("verified"):
            raise RuntimeError("Live adapter has not been verified")
        self.require("send_button", "send_confirmation")
        self.account()
        if fingerprint(self._composer(message)) != fingerprint(message):
            raise RuntimeError("Composer changed immediately before send")
        self.page.locator(self.s["send_button"]).click()
        self.page.locator(self.s["send_confirmation"]).wait_for()
        self.page.goto(message["id"])
        self.page.locator(self.s["lead_ready"]).wait_for()
        self.account()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            records = self.page.locator(self.s["history_entry"])
            remaining = Counter(records.all_text_contents()) - Counter(self.history_before)
            matches = []
            for record in records.all():
                text = record.text_content()
                if remaining[text] <= 0:
                    continue
                remaining[text] -= 1
                sender = record.locator(self.s["history_sender"]).inner_text().strip()
                recipient = record.locator(self.s["history_recipient"]).inner_text().strip()
                subject = record.locator(self.s["history_subject"]).inner_text().strip()
                if sender == self.config["account_name"] and recipient.lower() == message["email"].lower() and subject == message["subject"]:
                    matches.append(text)
            if len(matches) == 1:
                return matches[0]
            self.page.wait_for_timeout(250)
        raise RuntimeError("Send attempted but a unique new Contact History record could not be verified")


def playwright_adapter(config):
    """The separate automation browser (needs its own sign-in)."""
    if config.get("crm_profile") == "udc":
        from .udc import MomentumUDC
        return MomentumUDC(config)
    return Momentum(config)


def live_adapter(config):
    """Chooses the connection per operation: the user's own browser (bridge) when it is connected, else the automation browser."""
    mode = config.get("connection", "playwright")
    if mode in ("bridge", "both"):
        from .bridge import get_bridge
        if mode == "bridge" or get_bridge(config["data_dir"]).connected():
            from .bridge_adapter import MomentumBridge
            return MomentumBridge(config)
    return playwright_adapter(config)


def adapter(config):
    return Demo(config) if config["mode"] == "demo" else live_adapter(config)

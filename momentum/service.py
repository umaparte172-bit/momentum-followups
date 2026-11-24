import copy
from importlib.resources import files
import hashlib
import json
import secrets
import threading
import time
from pathlib import Path

from .adapters import adapter, live_adapter, playwright_adapter, Momentum
from .bridge import get_bridge
from .core import CHANNELS, LeadExcluded, Ledger, eligible, fingerprint, send_key


def short_error(exc):
    """One readable line: browser libraries append long launch logs that do not belong in the interface."""
    text = str(exc).strip() or exc.__class__.__name__
    text = text.split("Browser logs:")[0].strip()
    line = text.splitlines()[0].strip() if text else exc.__class__.__name__
    if "has been closed" in line:
        line = "The automation browser was closed before the sign-in finished. Click Connect automation browser and try again."
    return line[:240]


MAX_FAILURES_IN_A_ROW = 3


class Application:
    def __init__(self, config, factory=adapter):
        self.config = copy.deepcopy(config)
        self.factory = factory
        self.ledger = Ledger(Path(config["data_dir"]) / "sends.sqlite")
        self.ledger.recover()
        self.operation = threading.Lock()
        self.guard = threading.RLock()
        self.stop = threading.Event()
        self.pause = threading.Event()
        self.login_finished = threading.Event()
        self.login_pending = False
        self.worker = None
        self.previews = {}
        self.reports = {}
        self.busy = False
        self.job = "Ready"
        self.error = None
        self.active_batch = None
        self.token = secrets.token_urlsafe(32)
        self.bridge = get_bridge(config["data_dir"])
        self.verification_key = "test:" + hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()

    def _enter_operation(self):
        if not self.operation.acquire(False):
            raise ValueError("Another CRM operation is running")

    def load(self, owner):
        self._enter_operation()
        try:
            self.previews.pop(owner, None)
            self.reports.pop(owner, None)
            with self.factory(self.config) as crm:
                leads = crm.leads()
                templates = crm.templates(leads)
                options = crm.channel_options(leads) if hasattr(crm, "channel_options") else {}
                counts = getattr(crm, "report_counts", None)
            if len(self.reports) >= 50:
                self.reports.clear()
                self.previews.clear()
            options = {"sms_templates": options.get("sms_templates", []), "call_options": options.get("call_options", {})}
            self.reports[owner] = {"ids": {x["id"] for x in leads}, "names": {x["id"]: x["name"] for x in leads}, "templates": templates, **options}
            return {"leads": leads, "templates": templates, **options, "counts": counts}
        finally:
            self.operation.release()

    def verification(self, channel):
        """The single authorized test is required once per channel (email, text, call log)."""
        return self.verification_key if channel == "email" else f"{self.verification_key}:{channel}"

    def preview(self, owner, ids, template, channel="email", call=None):
        self._enter_operation()
        try:
            # A failed request must invalidate the previous approval, too.
            self.previews.pop(owner, None)
            report = self.reports.get(owner)
            if not report:
                raise ValueError("Load the report in this tab first")
            if not isinstance(ids, list) or any(not isinstance(x, str) for x in ids):
                raise ValueError("Invalid lead selection")
            ids = list(dict.fromkeys(ids))
            if not ids or (self.config["max_batch"] and len(ids) > self.config["max_batch"]):
                raise ValueError(f"Select 1–{self.config['max_batch']} leads" if ids else "Select at least one lead")
            if not set(ids).issubset(report["ids"]):
                raise ValueError("Selection contains leads outside the loaded report")
            channel = channel or "email"
            if channel not in CHANNELS:
                raise ValueError("Choose email, text or call log")
            extra = {}
            if channel == "call":
                call = call if isinstance(call, dict) else {}
                options = report.get("call_options") or {}
                types = {t["id"]: t["name"] for t in options.get("types", [])}
                results = {r["id"]: r["name"] for r in options.get("results", [])}
                if call.get("type") not in types or call.get("result") not in results:
                    raise ValueError("Choose a call type and a call result")
                note = call.get("note") if isinstance(call.get("note"), str) else ""
                template, name = "call:" + call["result"], "Call log: " + results[call["result"]]
                extra = {"call_type_id": call["type"], "call_result_id": call["result"], "call_result_name": results[call["result"]], "call_note": note[:2000]}
            else:
                choices = report["templates"] if channel == "email" else report.get("sms_templates", [])
                if template not in {t["id"] for t in choices}:
                    raise ValueError("Select a saved template from the loaded report")
                name = next(t["name"] for t in choices if t["id"] == template)
            messages = []
            config = {**self.config, "template_id": template, "template_name": name, "channel": channel, **extra}
            self.stop.clear()
            self.job = f"Checking lead 1 of {len(ids)}"
            with self.factory(config) as crm:
                failures = 0
                for number, lead_id in enumerate(ids, 1):
                    if self.stop.is_set():
                        raise ValueError("Preview stopped")
                    self.job = f"Checking lead {number} of {len(ids)}"
                    try:
                        lead = crm.inspect(lead_id)
                        lead["exclusion"] = eligible(lead, config["account_name"])
                        failures = 0
                    except LeadExcluded as exc:
                        lead = {**exc.lead, "exclusion": exc.reason}
                        failures = 0
                    except Exception as exc:
                        # One lead that will not load must not throw away a long preview; several in a row means
                        # Momentum itself is not answering (signed out, closed tab), so stop and say so.
                        failures += 1
                        if failures >= MAX_FAILURES_IN_A_ROW:
                            raise ValueError(f"Momentum did not answer for {failures} leads in a row ({short_error(exc)}); check that Momentum is open and signed in")
                        lead = {"id": str(lead_id), "name": report.get("names", {}).get(lead_id) or str(lead_id), "email": "", "channel": channel,
                                "exclusion": "Could not be checked this time: " + short_error(exc)}
                    if not lead["exclusion"] and self.ledger.blocked(lead):
                        lead["exclusion"] = "Duplicate or unresolved send attempt"
                    if not lead["exclusion"] and channel == "email" and (not lead.get("subject", "").strip() or not lead.get("body", "").strip()):
                        raise ValueError("Saved template has empty subject or body")
                    if not lead["exclusion"] and channel == "text" and not lead.get("body", "").strip():
                        raise ValueError("Saved text template is empty")
                    lead["channel"] = channel
                    messages.append(lead)
            result = {"id": secrets.token_urlsafe(32), "expires": time.time()+config["preview_ttl_seconds"], "template": template, "template_name": name,
                      "channel": channel, "extra": extra, "messages": messages}
            self.previews[owner] = result
            return result
        finally:
            if not self.busy:
                self.job = "Ready"
            self.operation.release()

    def run(self, owner, data):
        self._enter_operation()
        handed_off = False
        try:
            preview = self.previews.get(owner)
            if not preview or preview["id"] != data.get("preview_id") or preview["expires"] < time.time():
                raise ValueError("Preview is missing, expired, or belongs to another tab; preview again")
            dry = data.get("dry_run") is not False
            test = data.get("test") is True
            messages = copy.deepcopy(preview["messages"])
            selected = [m for m in messages if not m["exclusion"]]
            if not selected:
                raise ValueError("No eligible leads in this preview")
            if not dry:
                if self.config["mode"] != "live":
                    raise ValueError("Live sending needs live mode; run setup-macos.sh to create the live configuration")
                if data.get("authorization") != "SEND APPROVED EMAILS":
                    raise ValueError("Explicit live-send approval is required")
                if test and len(selected) != 1:
                    raise ValueError("Select exactly one eligible lead for the authorized test")
                channel = preview.get("channel", "email")
                if not test and not self.ledger.get_meta(self.verification(channel)):
                    label = {"email": "email", "text": "text", "call": "call log"}[channel]
                    raise ValueError(f"Do one single authorized live test ({label}) before running a batch")
                if self.ledger.quota(self.config)["day_remaining"] < len(selected):
                    raise ValueError("Batch exceeds today's remaining allowance; select fewer leads or prepare the rest for tomorrow")
            batch = self.ledger.create_batch(messages, preview["template"], dry)
            del self.previews[owner]  # Consume approval before starting the worker.
            self.stop.clear()
            self.pause.clear()
            self.busy, self.error, self.active_batch = True, None, batch
            self.job = "Running batch"
            config = {**self.config, "template_id": preview["template"], "template_name": preview.get("template_name"),
                      "channel": preview.get("channel", "email"), **preview.get("extra", {})}
            self.worker = threading.Thread(target=self._run, args=(batch, messages, config, dry, test), daemon=True)
            self.worker.start()
            handed_off = True
            return {"batch": batch}
        finally:
            if not handed_off:
                if self.busy:
                    self.busy = False
                    self.worker = None
                    self.job = "Ready"
                    if self.active_batch:
                        self.ledger.recover()
                self.operation.release()

    def _run(self, batch, messages, config, dry, test):
        try:
            with self.factory(config) as crm:
                failures = 0
                for original in messages:
                    while self.pause.is_set() and not self.stop.wait(.1):
                        pass
                    if self.stop.is_set():
                        self.ledger.outcome(batch, original["id"], "skipped", "Stopped; eligible for a fresh preview")
                        continue
                    if original["exclusion"]:
                        self.ledger.outcome(batch, original["id"], "skipped", original["exclusion"])
                        continue
                    reserved = False
                    try:
                        if not dry:
                            if self.ledger.quota(config)["day_remaining"] <= 0:
                                self.ledger.outcome(batch, original["id"], "skipped", "Daily sending limit reached; prepare a retry for the next day")
                                continue
                            while not self.stop.is_set():
                                wait = max(self.ledger.hourly_wait_seconds(config), self.ledger.wait_seconds(config))
                                if wait <= 0:
                                    break
                                self.job = f"Waiting for the sending allowance ({int(wait // 60)} min {int(wait % 60)} s)" if wait > 5 else "Running batch"
                                self.stop.wait(min(wait, 5))
                            self.job = "Running batch"
                            if self.stop.is_set():
                                self.ledger.outcome(batch, original["id"], "skipped", "Stopped before sending")
                                continue
                        while self.pause.is_set() and not self.stop.wait(.1):
                            pass
                        if self.stop.is_set():
                            self.ledger.outcome(batch, original["id"], "skipped", "Stopped before recheck")
                            continue
                        while True:
                            try:
                                current = crm.inspect(original["id"])
                            except LeadExcluded as exc:
                                self.ledger.outcome(batch, original["id"], "skipped", exc.reason)
                                current = None
                                break
                            if not self.pause.is_set() or self.stop.is_set():
                                break
                            while self.pause.is_set() and not self.stop.wait(.1):
                                pass
                            if self.stop.is_set():
                                break
                        if current is None:
                            continue
                        reason = eligible(current, config["account_name"])
                        if not reason and self.ledger.blocked(current):
                            reason = "Duplicate or unresolved send attempt"
                        if not reason and fingerprint(current) != fingerprint(original):
                            reason = "Recipient or content changed after preview"
                        if self.stop.is_set():
                            reason = "Stopped during recheck; create a fresh preview for this lead"
                        if reason:
                            self.ledger.outcome(batch, original["id"], "skipped", reason)
                        elif dry:
                            self.ledger.outcome(batch, original["id"], "dry-run", "Dry run: lead rechecked and ready, nothing sent. Turn off Dry run to do it for real.")
                        elif self.ledger.reserve(current, batch, config):
                            reserved = True
                            detail = crm.send(current)
                            self.ledger.finish(send_key(current)[0], "sent", detail)
                            self.ledger.outcome(batch, original["id"], "sent", detail)
                            if test:
                                self.ledger.set_meta(self.verification(config["channel"]), detail)
                        else:
                            self.ledger.outcome(batch, original["id"], "skipped", "Duplicate reservation blocked")
                    except Exception as exc:
                        self.ledger.outcome(batch, original["id"], "uncertain" if reserved else "failed", str(exc))
                        failures += 1
                        # After a click the outcome is unknown, so the queue stops for a person to check. Before a click,
                        # one failed lead is recorded and the queue moves on, unless Momentum stops answering altogether.
                        if reserved or failures >= MAX_FAILURES_IN_A_ROW:
                            self.error = short_error(exc)
                            self.stop.set()
                    else:
                        failures = 0
        except Exception as exc:
            self.error = short_error(exc)
            for row in self.ledger.results(batch):
                if row["status"] == "pending":
                    self.ledger.outcome(batch, row["id"], "failed", str(exc))
        finally:
            self.ledger.complete(batch, "failed" if self.error else "stopped" if self.stop.is_set() else "complete")
            self.job, self.busy = "Ready", False
            self.operation.release()

    def crm_entry(self):
        # Let the CRM handle its own authenticated-session redirects in the user's tab.
        url = self.config.get("dashboard_url") or self.config.get("login_url")
        if not url:
            raise ValueError("Configure the CRM login URL before opening CRM")
        Momentum(self.config).check_url(url)
        use_bridge = self.config.get("connection") in ("bridge", "both")
        if use_bridge:
            # Allow the Momentum Bridge extension in this browser to pair while the CRM tab opens.
            self.bridge.open_pairing()
        return {"url": url, "bridge": use_bridge}

    def login(self):
        if self.config.get("connection") == "bridge":
            raise ValueError("This workspace connects through your own browser: click Sign in to CRM")
        if self.config["mode"] == "demo" and not self.config.get("login_url"):
            raise ValueError("Set login_url and allowed_origin in configuration to connect your CRM; demo mode does not authenticate")
        self._enter_operation()
        self.busy, self.job, self.error = True, "Waiting for CRM sign-in", None
        self.stop.clear()
        self.login_finished.clear()
        self.login_pending = True
        def work():
            try:
                login_config = {**self.config, "headless": False}
                login_factory = (playwright_adapter if self.config.get("crm_profile") == "udc" else Momentum) if self.config.get("login_url") else self.factory
                with login_factory(login_config) as crm:
                    verified = crm.login(self.stop, self.login_finished) if self.config.get("login_url") else crm.login(self.stop)
                self.job = ("Signed in as " + self.config["account_name"]) if verified else "Browser session saved; CRM account verification still required"
            except Exception as exc:
                self.error, self.job = short_error(exc), "Sign-in failed"
            finally:
                self.busy = False
                self.login_pending = False
                self.operation.release()
        self.worker = threading.Thread(target=work, daemon=True)
        try:
            self.worker.start()
        except Exception:
            self.busy = self.login_pending = False
            self.worker = None
            self.job = "Ready"
            self.operation.release()
            raise
        return {"ok": True}

    def finish_login(self):
        if not self.login_pending:
            raise ValueError("No CRM sign-in is in progress")
        self.login_finished.set()
        return {"ok": True}

    def reload_extension(self):
        """Ask the Momentum Bridge extension to reload itself so it picks up updated files."""
        self.bridge.reload_extension()
        return {"ok": True}

    def crm_stats(self):
        """Today's counts from the CRM dashboard (for example Outbound Emails / SMS), when connected through the browser."""
        self._enter_operation()
        try:
            with self.factory(self.config) as crm:
                if not hasattr(crm, "dashboard"):
                    raise ValueError("The CRM counts are available when connected through your own browser")
                return {"counts": crm.dashboard()}
        finally:
            self.operation.release()

    def reconcile(self, data):
        self._enter_operation()
        try:
            if data.get("confirmation") != "I CHECKED CRM HISTORY":
                raise ValueError("Confirm that you checked CRM history")
            self.ledger.reconcile(data["lead_id"], data["resolution"], data["evidence"])
            self.previews.clear()
            return {"ok": True}
        finally:
            self.operation.release()

    def snapshot(self):
        batches = self.ledger.batches()
        batch = self.active_batch or (batches[0]["id"] if batches else None)
        return {"busy": self.busy or self.operation.locked(), "login_pending": self.login_pending, "paused": self.pause.is_set(), "job": self.job, "error": self.error,
                "mode": self.config["mode"], "results": self.ledger.results(batch), "batches": batches,
                "ledger": self.ledger.rows(), "quota": self.ledger.quota(self.config), "batch": batch,
                "test_verified": bool(self.ledger.get_meta(self.verification_key)),
                "tests_done": {c: bool(self.ledger.get_meta(self.verification(c))) for c in CHANNELS},
                "pacing": {"delay_seconds": self.config["delay_seconds"], "hourly_limit": self.config["hourly_limit"], "daily_limit": self.config["daily_limit"]},
                "connection": self.config.get("connection", "playwright"),
                "active_connection": "bridge" if self.config.get("connection") in ("bridge", "both") and self.bridge.connected() else "automation",
                "bridge": {**self.bridge.status(), "extension_path": str(files("momentum").joinpath("extension"))}}

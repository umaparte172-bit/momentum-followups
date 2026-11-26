import copy
from importlib.resources import files
import hashlib
import json
import re
import secrets
import threading
import time
from pathlib import Path

from . import VERSION
from .adapters import adapter, live_adapter, playwright_adapter, Momentum
from .bridge import get_bridge
from .bridge_adapter import MomentumRefused
from .full_report import FullReport
from .core import CHANNELS, LeadExcluded, Ledger, eligible, required_owner, fingerprint, placeholders, send_key


def short_error(exc):
    """One readable line: browser libraries append long launch logs that do not belong in the interface."""
    text = str(exc).strip() or exc.__class__.__name__
    text = text.split("Browser logs:")[0].strip()
    line = text.splitlines()[0].strip() if text else exc.__class__.__name__
    if "has been closed" in line:
        line = "The automation browser was closed before the sign-in finished. Click Connect automation browser and try again."
    if "showing error page" in line:
        line = "Momentum could not be opened. Check your internet connection and that Momentum is signed in, then try again."
    return line[:240]


MAX_FAILURES_IN_A_ROW = 3
OUTAGE_WAIT = 60
QUICK_READY, QUICK_LOOKED, QUICK_MINIMUM = 10, 40, 25  # quick start: read this many ready messages, look at most at this many people
SHOWN_ROWS = 500  # the dashboard shows this many of a very long preview or run; the rest is kept and counted


def valid_id(lead_id):
    return bool(re.fullmatch(r"\d{1,12}", lead_id or ""))


class Application:
    def __init__(self, config, factory=adapter):
        self.config = copy.deepcopy(config)
        self.factory = factory
        self.ledger = Ledger(Path(config["data_dir"]) / "sends.sqlite", config.get("repeat_after_hours", 0))
        self.ledger.recover()
        self.operation = threading.Lock()
        self.guard = threading.RLock()
        self.stop = threading.Event()
        self.pause = threading.Event()
        self.login_finished = threading.Event()
        self.login_pending = False
        self.worker = None
        self.previews = {}
        self.preview_jobs = {}
        self.reports = {}
        self.full = None
        self.full_at, self.full_current = None, False
        self.busy = False
        self.job = "Ready"
        self.error = None
        self.active_batch = None
        self.token = secrets.token_urlsafe(32)
        self.bridge = get_bridge(config["data_dir"])
        # What the single test proves: this account, on this Momentum, through these screens. A new build keeps it.
        proven = {k: config.get(k) for k in ("account_name", "allowed_origin", "crm_profile", "report_id", "selectors")}
        self.verification_key = "test:v2:" + hashlib.sha256(json.dumps(proven, sort_keys=True).encode()).hexdigest()
        if config.get("crm_profile") == "udc" and config["mode"] == "live":
            self._keep_earlier_tests()

    def _keep_earlier_tests(self):
        """Tests passed under an earlier build count if their Contact History entry is by this account."""
        words = self.config["account_name"].split()
        sender = f"{words[-1]}, {' '.join(words[:-1])}" if len(words) > 1 else self.config["account_name"]
        for key, value in self.ledger.meta("test:"):
            found = re.fullmatch(r"test:[0-9a-f]{64}(:text|:call)?", key)
            if found and re.search(r"(?<!\w)" + re.escape(sender) + r"(?!\w)", value or ""):
                channel = (found.group(1) or ":email")[1:]
                if not self.ledger.get_meta(self.verification(channel)):
                    self.ledger.set_meta(self.verification(channel), value)

    def _enter_operation(self):
        if not self.operation.acquire(False):
            raise ValueError("Another CRM operation is running")

    def load(self, owner):
        self._enter_operation()
        try:
            self.previews.pop(owner, None)
            self.reports.pop(owner, None)
            self.full_current = False  # until this sync succeeds, the full report on screen is from an earlier one
            with self.factory(self.config) as crm:
                leads = crm.leads()
                templates = crm.templates(leads)
                everyone = getattr(crm, "full_rows", None)
                # People in the full report with a phone, for reading the text and Log Call choices.
                phoned = [r[0] for r in everyone or [] if len(r) > 8 and ("c" in r[8] or "p" in r[8]) and valid_id(r[0])][:12]
                options = (crm.channel_options(leads, phoned) if phoned else crm.channel_options(leads)) if hasattr(crm, "channel_options") else {}
                counts = getattr(crm, "report_counts", None)
                meta = getattr(crm, "full_meta", None)
            # An older extension sends only the account's own rows: no full view is better than an out-of-date one.
            self.full = FullReport(everyone, self.config["account_name"], self.config["timezone"], meta, self.config.get("source_column", "")) if everyone else None
            self.full_current, self.full_at = True, time.time()
            if self.full and counts is not None:
                counts = {**counts, "report_people": len(self.full.people)}
            if len(self.reports) >= 50:
                self.reports.clear()
                self.previews.clear()
            options = {"sms_templates": options.get("sms_templates", []), "call_options": options.get("call_options", {})}
            # The Log Call and text choices are the same for every lead: keep the last ones read, in case none could be read now.
            for key in ("sms_templates", "call_options"):
                if options[key]:
                    self.ledger.set_meta("choices:" + key, json.dumps(options[key]))
                elif self.ledger.get_meta("choices:" + key):
                    options[key] = json.loads(self.ledger.get_meta("choices:" + key))
            names = {x["id"]: x["name"] for x in leads}
            if self.full and required_owner(self.config) is None:
                names.update((p["id"], p["name"]) for p in self.full.people if valid_id(p["id"]))
            self.reports[owner] = {"ids": set(names), "names": names, "templates": templates, **options}
            return {"leads": leads, "templates": templates, **options, "counts": counts, "my_templates": self.my_templates()}
        finally:
            self.operation.release()

    def full_report(self, data):
        """View and search the whole report. Who may be selected from it follows lead_scope."""
        report = self.full
        if report is None:
            return {"ready": False}
        if data.get("ids_only") is True:
            found = report.search(data.get("query", ""), data.get("salesperson", ""), data.get("status", ""), data.get("min_days", 0),
                                  data.get("sort", "created"), 1, 1, data.get("sources") or [], ids_only=True, reach=data.get("reach", ""))
            return {"ready": True, "ids": [i for i in found if valid_id(i)] if required_owner(self.config) is None else []}
        return {"ready": True, "can_select": required_owner(self.config) is None, "synced_at": self.full_at, "current": self.full_current, **report.search(data.get("query", ""), data.get("salesperson", ""), data.get("status", ""), data.get("min_days", 0),
                                               data.get("sort", "created"), data.get("page", 1), data.get("size", 50), data.get("sources") or [], reach=data.get("reach", ""))}

    def _inspect(self, crm, lead_id):
        """Momentum sometimes takes too long to open a page: try once more. Nothing has been sent or saved at this point."""
        try:
            return crm.inspect(lead_id)
        except LeadExcluded:
            raise
        except Exception as exc:
            if self.stop.is_set() or "Timed out waiting" not in str(exc):
                raise
            return crm.inspect(lead_id)

    def verification(self, channel):
        """The single authorized test is required once per channel (email, text, call log)."""
        return self.verification_key if channel == "email" else f"{self.verification_key}:{channel}"

    def _prepare(self, owner, ids, template, channel, call, custom):
        """Checks the request itself (who, which action, which message) before any lead is opened."""
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
            if not types or not results:
                raise ValueError("Momentum's Log Call choices could not be read. Sync the report again")
            if call.get("type") not in types or call.get("result") not in results:
                raise ValueError("Choose a call type and a call result")
            note = (call.get("note") if isinstance(call.get("note"), str) else "").strip()
            if not note:
                # Momentum's Log Call panel requires Call Notes: without one it saves nothing ("Please fill out the form completely").
                raise ValueError("Type a call note first: Momentum requires Call notes and saves nothing without them")
            template, name = "call:" + call["result"], "Call log: " + results[call["result"]]
            extra = {"call_type_id": call["type"], "call_result_id": call["result"], "call_result_name": results[call["result"]], "call_note": note[:2000]}
        elif template == "custom" or custom:
            custom = custom if isinstance(custom, dict) else {}
            subject = custom.get("subject") if isinstance(custom.get("subject"), str) else ""
            body = custom.get("body") if isinstance(custom.get("body"), str) else ""
            body = body.replace("\r\n", "\n").strip()
            if not body:
                raise ValueError("Write the message first")
            if channel == "email" and not subject.strip():
                raise ValueError("Write a subject for the email")
            if channel == "text" and len(body) > 1350:
                raise ValueError("A text can be at most 1350 characters")
            placeholders(subject + " " + body)
            label = custom.get("name") if isinstance(custom.get("name"), str) and custom.get("name").strip() else "Custom message"
            template, name = "custom", label.strip()[:80]
            extra = {"custom": {"subject": " ".join(subject.split())[:300] if channel == "email" else "", "body": body[:20000]}, "template_name": None}
        else:
            choices = report["templates"] if channel == "email" else report.get("sms_templates", [])
            if template not in {t["id"] for t in choices}:
                raise ValueError("Select a saved template from the loaded report")
            name = next(t["name"] for t in choices if t["id"] == template)
        return report, ids, template, name, channel, extra

    def preview(self, owner, ids, template, channel="email", call=None, quick=False, custom=None):
        self._enter_operation()
        try:
            # A failed request must invalidate the previous approval, too.
            self.previews.pop(owner, None)
            report, ids, template, name, channel, extra = self._prepare(owner, ids, template, channel, call, custom)
            messages = []
            # Quick start: a sample is previewed, and everyone else is checked once, right before their own send.
            quick = quick is True and len(ids) > QUICK_MINIMUM
            missing = self.full.unreachable(channel) if self.full and len(ids) > QUICK_MINIMUM else set()
            looked = ready = 0
            config = {**self.config, "template_id": template, "template_name": name, "channel": channel, **extra}
            self.stop.clear()
            checked = 0
            self.job = f"Checking lead 1 of {len(ids)}"
            with self.factory(config) as crm:
                failures, ended = 0, ""
                for number, lead_id in enumerate(ids, 1):
                    if self.stop.is_set() or ended:
                        if not ended and (len(ids) <= 15 or not any(not m["exclusion"] for m in messages)):
                            raise ValueError("Preview stopped")
                        # A long preview keeps what it has checked; the rest is simply not part of this approval.
                        messages.append({"id": lead_id, "name": report["names"].get(lead_id, lead_id), "email": "", "channel": channel,
                                         "exclusion": "Not checked: " + (ended or "the preview was stopped")})
                        continue
                    if lead_id in missing:
                        what = {"text": "cell number", "call": "phone number", "email": "email"}[channel]
                        messages.append({"id": lead_id, "name": report["names"].get(lead_id, lead_id), "email": "", "channel": channel,
                                         "exclusion": f"The report shows no {what} for this person"})
                        continue
                    if quick and ready >= QUICK_READY:
                        messages.append({"id": lead_id, "name": report["names"].get(lead_id, lead_id), "email": "", "channel": channel,
                                         "exclusion": None, "deferred": True})
                        continue
                    if quick and looked >= QUICK_LOOKED and not ready:
                        raise ValueError(f"None of the first {looked} people can be contacted this way; narrow the list with the filters")
                    if quick and looked >= QUICK_LOOKED:
                        ready = QUICK_READY  # enough to read: the rest are checked as they are sent
                        messages.append({"id": lead_id, "name": report["names"].get(lead_id, lead_id), "email": "", "channel": channel,
                                         "exclusion": None, "deferred": True})
                        continue
                    looked += 1
                    self.job = f"Checking a sample: {looked} looked at, {ready} ready" if quick else f"Checking lead {number} of {len(ids)}"
                    try:
                        lead = self._inspect(crm, lead_id)
                        lead["exclusion"] = eligible(lead, required_owner(config))
                        failures, checked = 0, checked + 1
                    except LeadExcluded as exc:
                        lead = {**exc.lead, "exclusion": exc.reason}
                        failures, checked = 0, checked + 1
                    except Exception as exc:
                        # One lead that will not load must not throw away the preview; several in a row means
                        # Momentum itself is not answering (signed out, closed tab), so stop and say so.
                        failures += 1
                        if failures >= MAX_FAILURES_IN_A_ROW and checked and (len(ids) > 15 or failures >= 2 * MAX_FAILURES_IN_A_ROW):
                            ended = "Momentum stopped answering"
                        elif failures >= MAX_FAILURES_IN_A_ROW and not checked:
                            raise ValueError(f"Momentum did not answer for {failures} leads in a row ({short_error(exc)}); check that Momentum is open and signed in")
                        lead = {"id": str(lead_id), "name": report.get("names", {}).get(lead_id) or str(lead_id), "email": "", "channel": channel,
                                "exclusion": "Could not be checked this time: " + short_error(exc)}
                    if not lead["exclusion"] and self.ledger.blocked(lead):
                        lead["exclusion"] = "Duplicate or unresolved send attempt"
                    if not lead["exclusion"] and channel == "email" and (not lead.get("subject", "").strip() or not lead.get("body", "").strip()):
                        raise ValueError("The email has an empty subject or body")
                    if not lead["exclusion"] and channel == "text" and not lead.get("body", "").strip():
                        raise ValueError("The text message is empty")
                    lead["channel"] = channel
                    ready += not lead["exclusion"]
                    messages.append(lead)
            # A long list takes long to read through, and every lead is rechecked before its send anyway.
            ttl = max(config["preview_ttl_seconds"], 60 * len(ids)) if len(ids) > 15 else config["preview_ttl_seconds"]
            result = {"id": secrets.token_urlsafe(32), "expires": time.time()+ttl, "template": template, "template_name": name,
                      "channel": channel, "extra": extra, "messages": messages}
            self.previews[owner] = result
            return result
        finally:
            if not self.busy:
                self.job = "Ready"
            self.operation.release()

    def start_preview(self, owner, ids, template, channel="email", call=None, quick=False, custom=None):
        """Previews in the background: the full list takes over an hour, longer than a browser keeps one request open."""
        if self.operation.locked():
            raise ValueError("Another CRM operation is running")
        job = {"id": secrets.token_urlsafe(16), "done": False, "result": None, "error": None}
        if len(self.preview_jobs) >= 50:
            self.preview_jobs.clear()
        self.preview_jobs[owner] = job

        def work():
            try:
                job["result"] = self.preview(owner, ids, template, channel, call, quick, custom)
            except Exception as exc:
                job["error"] = short_error(exc)
            finally:
                job["done"] = True

        threading.Thread(target=work, daemon=True).start()
        return {"job": job["id"]}

    def preview_result(self, owner, job_id):
        job = self.preview_jobs.get(owner)
        if not job or job["id"] != job_id:
            raise ValueError("This preview is no longer available; preview again")
        if not job["done"]:
            return {"done": False, "job": self.job}
        del self.preview_jobs[owner]
        if job["error"]:
            raise ValueError(job["error"])
        whole = job["result"]
        # The first page of a very long preview: ready ones first, so there is something to read.
        shown = [m for m in whole["messages"] if not m.get("deferred")]
        shown = sorted(shown, key=lambda m: bool(m["exclusion"]))[:SHOWN_ROWS] if len(shown) > SHOWN_ROWS else shown
        deferred = sum(bool(m.get("deferred")) for m in whole["messages"])
        return {"done": True, "preview": {**whole, "messages": shown, "total": len(whole["messages"]),
                                          "ready": sum(not m["exclusion"] for m in whole["messages"]), "deferred": deferred}}

    def run(self, owner, data):
        self._enter_operation()
        handed_off = False
        try:
            if data.get("direct") is True:
                # Send without a preview: every person is checked once, right before their own action, and skipped if anything is wrong.
                preview = self._direct(owner, data)
            else:
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
                if " ".join(str(data.get("authorization") or "").split()).upper() != "SEND APPROVED EMAILS":
                    raise ValueError("Explicit live-send approval is required: type SEND APPROVED EMAILS in the approval box")
                if test and (len(selected) != 1 or selected[0].get("deferred")):
                    raise ValueError("Select exactly one eligible lead for the authorized test")
                if self.ledger.quota(self.config)["day_remaining"] < len(selected):
                    raise ValueError("Batch exceeds today's remaining allowance; select fewer leads or prepare the rest for tomorrow")
            batch = self.ledger.create_batch(messages, preview["template"], dry)
            self.previews.pop(owner, None)  # Consume approval before starting the worker.
            self.stop.clear()
            self.pause.clear()
            self.busy, self.error, self.active_batch = True, None, batch
            self.job = "Running batch"
            config = {**self.config, "template_id": preview["template"], "template_name": preview.get("template_name"),
                      "channel": preview.get("channel", "email"), **preview.get("extra", {})}
            if config["channel"] == "call":
                config["delay_seconds"] = 0  # a call log contacts nobody: no gap is needed between them
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

    def _direct(self, owner, data):
        report, ids, template, name, channel, extra = self._prepare(owner, data.get("ids"), data.get("template"), data.get("channel") or "email",
                                                                    data.get("call"), data.get("custom"))
        missing = self.full.unreachable(channel) if self.full else set()
        what = {"text": "cell number", "call": "phone number", "email": "email"}[channel]
        messages = [{"id": i, "name": report["names"].get(i, i), "email": "", "channel": channel,
                     "exclusion": f"The report shows no {what} for this person" if i in missing else None, "deferred": i not in missing} for i in ids]
        return {"id": "direct", "template": template, "template_name": name, "channel": channel, "extra": extra, "messages": messages}

    def _run(self, batch, messages, config, dry, test):
        try:
            with self.factory(config) as crm:
                failures = waits = 0
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
                                current = self._inspect(crm, original["id"])
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
                        reason = eligible(current, required_owner(config))
                        if not reason and self.ledger.blocked(current):
                            reason = "Duplicate or unresolved send attempt"
                        current["channel"] = config["channel"]
                        if original.get("deferred"):
                            # Not previewed on its own: the recipient and opt-outs were just checked; the message must not be empty.
                            if not reason and config["channel"] != "call" and not (current.get("body") or "").strip():
                                reason = "The message came out empty for this person"
                            if not reason and config["channel"] == "email" and not (current.get("subject") or "").strip():
                                reason = "The email came out without a subject for this person"
                            self.ledger.describe(batch, original["id"], current)
                        elif not reason and fingerprint(current) != fingerprint(original):
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
                    except MomentumRefused as exc:
                        # Momentum answered with a message and nothing appeared in Contact History: skip this one, go on.
                        self.ledger.release(send_key(current)[0])
                        self.ledger.outcome(batch, original["id"], "skipped", "Momentum did not allow it: " + str(exc))
                        failures = 0
                    except Exception as exc:
                        self.ledger.outcome(batch, original["id"], "uncertain" if reserved else "failed", str(exc))
                        if reserved and test:
                            # If a person confirms this one in Contact History, it counts as the authorized test.
                            self.ledger.set_meta("test-of:" + send_key(current)[0], self.verification(config["channel"]))
                        failures += 1
                        # After a click the outcome is unknown, so the queue stops for a person to check. Before a click,
                        # one failed lead is recorded and the queue moves on, unless Momentum stops answering altogether.
                        if not reserved and failures >= MAX_FAILURES_IN_A_ROW and len(messages) > 15 and waits < 10 and not self.stop.is_set():
                            # A long run survives a short outage: wait, then go on with the next lead.
                            waits, failures = waits + 1, 0
                            self.job = f"Momentum is not answering; trying again in {OUTAGE_WAIT} seconds ({waits} of 10)"
                            self.stop.wait(OUTAGE_WAIT)
                            self.job = "Running batch"
                        if reserved or failures >= MAX_FAILURES_IN_A_ROW:
                            self.error = short_error(exc)
                            self.stop.set()
                    else:
                        failures = waits = 0
        except Exception as exc:
            self.error = short_error(exc)
            for row in self.ledger.results(batch):
                if row["status"] == "pending":
                    self.ledger.outcome(batch, row["id"], "failed", str(exc))
        finally:
            self.ledger.complete(batch, "failed" if self.error else "stopped" if self.stop.is_set() else "complete")
            self.job, self.busy = "Ready", False
            self.operation.release()

    # --- my own templates ---------------------------------------------------------------------
    def _templates_file(self):
        return Path(self.config["data_dir"]) / "my-templates.json"

    def my_templates(self):
        try:
            items = json.loads(self._templates_file().read_text(encoding="utf-8"))
            return [t for t in items if isinstance(t, dict) and t.get("id") and t.get("channel") in ("email", "text")]
        except (OSError, ValueError):
            return []

    def save_template(self, data):
        name = " ".join(str(data.get("name") or "").split())[:80]
        channel, subject, body = data.get("channel"), str(data.get("subject") or ""), str(data.get("body") or "").replace("\r\n", "\n").strip()
        if not name:
            raise ValueError("Give the template a name")
        if channel not in ("email", "text"):
            raise ValueError("A template is for an email or a text")
        if not body or (channel == "email" and not subject.strip()):
            raise ValueError("Write the subject and the message first" if channel == "email" else "Write the message first")
        if channel == "text" and len(body) > 1350:
            raise ValueError("A text can be at most 1350 characters")
        placeholders(subject + " " + body)
        with self.guard:
            items = self.my_templates()
            found = next((t for t in items if t["id"] == data.get("id")), None) or next((t for t in items if t["name"].lower() == name.lower() and t["channel"] == channel), None)
            item = {"id": found["id"] if found else "my-" + secrets.token_hex(6), "name": name, "channel": channel,
                    "subject": " ".join(subject.split())[:300] if channel == "email" else "", "body": body[:20000]}
            items = [item if t is found else t for t in items] if found else items + [item]
            path = self._templates_file()
            path.with_suffix(".tmp").write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
            path.with_suffix(".tmp").replace(path)
        return {"template": item, "templates": items}

    def delete_template(self, data):
        with self.guard:
            items = [t for t in self.my_templates() if t["id"] != data.get("id")]
            path = self._templates_file()
            path.with_suffix(".tmp").write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
            path.with_suffix(".tmp").replace(path)
        return {"templates": items}

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
        self._enter_operation()  # never in the middle of a run
        try:
            self.bridge.reload_extension()
        finally:
            self.operation.release()
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
            test_of = self.ledger.get_meta("test-of:" + str(data["lead_id"]))
            if test_of and data["resolution"] == "sent":
                self.ledger.set_meta(test_of, data["evidence"])
            self.previews.clear()
            return {"ok": True}
        finally:
            self.operation.release()

    def snapshot(self):
        batches = self.ledger.batches()
        batch = self.active_batch or (batches[0]["id"] if batches else None)
        return {"version": VERSION, "busy": self.busy or self.operation.locked(), "login_pending": self.login_pending, "paused": self.pause.is_set(), "job": self.job, "error": self.error,
                "mode": self.config["mode"], "results": self.ledger.results(batch, SHOWN_ROWS), "result_counts": self.ledger.counts(batch), "batches": batches,
                "ledger": self.ledger.rows(SHOWN_ROWS), "quota": self.ledger.quota(self.config), "batch": batch,
                "test_verified": bool(self.ledger.get_meta(self.verification_key)),
                "tests_done": {c: bool(self.ledger.get_meta(self.verification(c))) for c in CHANNELS},
                "pacing": {"delay_seconds": self.config["delay_seconds"], "hourly_limit": self.config["hourly_limit"], "daily_limit": self.config["daily_limit"]},
                "connection": self.config.get("connection", "playwright"),
                "active_connection": "bridge" if self.config.get("connection") in ("bridge", "both") and self.bridge.connected() else "automation",
                "bridge": {**self.bridge.status(), "extension_path": str(files("momentum").joinpath("extension"))}}

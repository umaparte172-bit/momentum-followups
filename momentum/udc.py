import re
import time
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .adapters import Momentum
from .core import LeadExcluded, eligible, required_owner, fingerprint

EMAIL_METHOD = re.compile(r"e-?mail", re.I)
TEXT_METHOD = re.compile(r"text message|\bsms\b", re.I)
CALL_METHOD = re.compile(r"outbound call", re.I)


def digits(phone):
    return re.sub(r"\D", "", phone or "")[-10:]


def squash(text):
    return re.sub(r"\s+", " ", text or "").strip().lower()
CLOSED = "() => { const f = document.querySelector('iframe#modalDialog0'); return !f || !f.offsetParent; }"


def last_first_to_name(value):
    """'Rivera, Alex Morgan' -> 'Alex Morgan Rivera'; anything else is returned unchanged."""
    value = (value or "").strip()
    if value.count(",") == 1:
        last, first = (part.strip() for part in value.split(","))
        if last and first:
            return f"{first} {last}"
    return value


def name_to_last_first(value):
    parts = (value or "").split()
    return f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else (value or "")


class MomentumUDC(Momentum):
    """Momentum (UDC) layout: a shell page with nested same-origin iframes.

    A lead opens in the shell's #main_iframe, its action menu opens the composer in a jQuery-UI
    dialog iframe, and the report runs in a popup that renders a Tabulator grid. These selectors
    were observed read-only on the live site; config["selectors"] overrides any of them.
    """
    DEFAULTS = {
        "account_name": "#username",
        "shell": "iframe#main_iframe",
        "activities": "iframe[name=cap_activities_iframe]",
        "email_menu": "button#email",
        "tab_frame": "iframe#cap_tab_iframe",
        "history_frame": "iframe[src*=customer_history_followup]",
        "history_row": "table.rowtable tr",
        "history_when": "td.cap_hist_col_2",
        "history_method": "td.cap_hist_col_3",
        "history_by": "td.cap_hist_col_4",
        "lead_email": "#display_emailaddress",
        "email_pref": "button.preferred.email",
        "sales_person": "#select_salesperson",
        "composer": "iframe#modalDialog0",
        "to": "select[name=to]",
        "template_select": "select[name=templateId]",
        "subject": "input[name=subject]",
        "body": ".fr-element",
        "cc": "input[name=cc]",
        "bcc": "input[name=bcc]",
        "send_button": "button[type=submit]",
    }
    SHELL_PATH = "/acrm/servlet/general?cmd=home"

    def __init__(self, config):
        super().__init__(config)
        self.s = {**self.DEFAULTS, **config.get("selectors", {})}
        self.zone = ZoneInfo(config["timezone"])
        # Internal Contact History entries (notes, status changes, alerts) are not contact with the customer.
        self.ignore = re.compile(config.get("ignore_contact_methods") or r"(?!)", re.I)
        self.report = {}
        self.history_before = []

    # --- navigation -------------------------------------------------------------------------
    def shell(self):
        origin = self.config["allowed_origin"].rstrip("/")
        if "/servlet/general" not in self.page.url or "cmd=home" not in self.page.url:
            self.page.goto(origin + self.SHELL_PATH)
        self.page.locator(self.s["account_name"]).wait_for()
        self.check_url(self.page.url)

    def account(self):
        self.shell()
        text = self.page.locator(self.s["account_name"]).inner_text().split(":")[0].strip()
        if text != self.config["account_name"]:
            raise RuntimeError("Signed-in account does not match the approved account")

    def _close_composer(self):
        self.page.evaluate("""() => {
            const f = document.querySelector('iframe#modalDialog0');
            const dialog = f && f.closest('.ui-dialog');
            const x = dialog && dialog.querySelector('.ui-dialog-titlebar-close, .ui-dialog-titlebar button');
            if (x) x.click();
        }""")
        self.page.wait_for_function(CLOSED)

    def lead_frame(self):
        return self.page.frame_locator(self.s["shell"])

    def history_frame(self):
        return self.lead_frame().frame_locator(self.s["tab_frame"]).frame_locator(self.s["history_frame"])

    def composer(self):
        return self.page.frame_locator(self.s["composer"])

    def _open_lead(self, lead_id):
        if not re.fullmatch(r"\d{1,12}", str(lead_id)):
            raise RuntimeError("Invalid lead id")
        self.account()
        self._close_composer()
        self.page.evaluate("""(cid) => {
            document.querySelector('iframe#main_iframe').contentWindow.location.href =
                '/acrm/servlet/customer?cmd=viewcust&cid=' + encodeURIComponent(cid);
        }""", str(lead_id))
        self.page.wait_for_function("""(cid) => {
            const w = document.querySelector('iframe#main_iframe').contentWindow;
            return w.location.href.includes('cid=' + cid) && w.document.readyState === 'complete'
                && !!w.document.getElementById('display_emailaddress');
        }""", arg=str(lead_id))
        self.lead_frame().locator(self.s["email_pref"]).first.wait_for(state="attached")

    # --- report -----------------------------------------------------------------------------
    def _results(self):
        origin = self.config["allowed_origin"].rstrip("/")
        report_id = int(self.config.get("report_id", 1056))
        prompts = f"{origin}/acrm/servlet/general?cmd=home_report_list&action=displayprompts&reportid={report_id}"
        results = f"{origin}/acrm/servlet/report?cmd=customreportresults&action=display&reportid=prompt_{report_id}"
        page = self.context.new_page()
        try:
            page.goto(prompts)
            run = page.locator("input[type=button][value=Run]")
            run.wait_for()
            try:
                with self.context.expect_page(timeout=20000) as popup:
                    run.click()
                grid = popup.value
            except Exception:
                grid = page
                grid.goto(results)
            self.check_url(grid.url)
            grid.locator(".tabulator-row").first.wait_for(timeout=120000)
            return grid, page
        except Exception:
            page.close()
            raise

    def leads(self):
        self.account()
        grid, opener = self._results()
        try:
            t = "const t = v => v == null ? '' : String(v);"
            found = grid.evaluate(r"""(wanted) => { const c = new Set(); for (const r of Tabulator.findTable('.tabulator')[0].getData()) for (const k in r) c.add(k);
                return {columns: [...c], sourceColumns: [...c].filter(k => k === wanted || /source|\bsrc\b|vendor|provider/i.test(k)).slice(0, 8)}; }""", self.config.get("source_column", ""))
            self.full_meta = found
            self.full_rows = grid.evaluate("(keys) => { " + t + """ return Tabulator.findTable('.tabulator')[0].getData()
                .map(r => [t(r.personid), t(r['Customer Name']), t(r['Sales Person 1']), t(r['Last Contact Date']), t(r['Last Action']), t(r['Prospect Creation Date']), t(r['Sales Status']), keys.map(k => t(r[k]))]); }""", found["sourceColumns"])
            rows = grid.evaluate("""(account) => Tabulator.findTable('.tabulator')[0].getData()
                .filter(r => r['Sales Person 1'] === account)
                .map(r => ({id: String(r.personid), name: r['Customer Name'], last: r['Last Contact Date'], action: r['Last Action'], created: r['Prospect Creation Date'], status: r['Sales Status']}))""",
                                 self.config["account_name"])
        finally:
            for page in (grid, opener):
                if not page.is_closed():
                    page.close()
        return self._lead_list(rows, len(self.full_rows))

    def _age(self, last):
        """Hours since the report's last contact (dealership time), or None when unknown."""
        try:
            moment = datetime.strptime((last or "")[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=self.zone)
        except ValueError:
            return None
        return round((datetime.now(self.zone) - moment).total_seconds() / 3600, 1)

    def _lead_list(self, rows, total=None):
        """Unique leads with their contact and creation ages, most recently created first (the best follow-up candidates)."""
        seen, result = set(), []
        for row in rows:
            if row["id"] not in seen:
                seen.add(row["id"])
                self.report[row["id"]] = row
                result.append({"id": row["id"], "name": row["name"], "last": row.get("last"), "action": row.get("action"),
                               "age_hours": self._age(row.get("last")), "created_hours": self._age(row.get("created")), "status": row.get("status")})
        result.sort(key=lambda lead: lead["created_hours"] if lead["created_hours"] is not None else float("inf"))
        # Report rows for this account, the unique leads they hold, and the report's size for everyone.
        self.report_counts = {"report_total": total, "account_rows": len(rows), "leads": len(result)}
        return result

    # --- lead -------------------------------------------------------------------------------
    def _history(self):
        sel = {"when": self.s["history_when"], "method": self.s["history_method"], "by": self.s["history_by"]}
        return self.history_frame().locator(self.s["history_row"]).evaluate_all("""(rows, sel) => rows
            .filter(r => r.querySelector(sel.when) && !r.querySelector('img[src*="sched_followup"]'))
            .map(r => ({when: r.querySelector(sel.when).innerText.replace(/\\s+/g, ' ').trim(),
                        method: r.querySelector(sel.method).innerText.replace(/\\s+/g, ' ').trim(),
                        by: r.querySelector(sel.by).innerText.replace(/\\s+/g, ' ').trim(),
                        text: r.innerText.replace(/\\s+/g, ' ').trim()}))""", sel)

    def _when(self, text):
        match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})\s*(\d{1,2}:\d{2}\s*[AP]M)", text or "")
        if not match:
            return None
        return datetime.strptime(f"{match.group(1)} {match.group(2)}", "%m/%d/%Y %I:%M %p").replace(tzinfo=self.zone)

    def _lead(self, lead_id):
        return self._assemble(lead_id, self._read_lead(lead_id))

    def _read_lead(self, lead_id):
        self._open_lead(lead_id)
        frame = self.lead_frame()
        email = frame.locator(self.s["lead_email"]).input_value().strip()
        pref = frame.locator(self.s["email_pref"])
        # Only an unambiguous email-preference control is trusted; anything else is "unknown".
        opt_out = ("dont" in (pref.first.get_attribute("class") or "").split()) if pref.count() == 1 else None
        text = frame.locator("body").inner_text()
        person = frame.locator(self.s["sales_person"])
        assigned = last_first_to_name(person.locator("option:checked").inner_text()) if person.count() == 1 else None
        return dict(email=email, opt_out=opt_out, assigned=assigned, name=text.split("\n")[0].strip(), history=self._history())

    def _assemble(self, lead_id, raw):
        """Decide eligibility facts from what the browser read; identical for every connection type."""
        email, opt_out, assigned, history = raw["email"], raw["opt_out"], raw["assigned"], raw["history"]
        now = datetime.now(self.zone)
        row = self.report.get(str(lead_id))
        contact_rows = [(self._when(h["when"]), h["method"]) for h in history if not self.ignore.search(h["method"])]
        stamps = [t for t, _ in contact_rows]
        # A contact time that cannot be read might be recent: never treat it as "no contact".
        unreadable = any(t is None for t in stamps)
        if row and row.get("last"):
            try:
                stamps.append(datetime.strptime(row["last"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=self.zone))
            except ValueError:
                unreadable = True
        # Contact History also lists future-dated (scheduled) rows; only past contact counts here.
        stamps = [t for t in stamps if t and t <= now]
        # No contact in the last 72 hours, per both the report and Contact History.
        report_eligible = bool(stamps and not unreadable and now - max(stamps) >= timedelta(hours=72))
        newest = max(((t, m) for t, m in contact_rows if t and t <= now), default=None)
        contact_note = None
        if newest:
            hours = (now - newest[0]).total_seconds() / 3600
            contact_note = f"{newest[1]}, {round(hours)} h ago" if hours < 48 else f"{newest[1]}, {round(hours / 24)} days ago"
        emails = [self._when(h["when"]) for h in history if EMAIL_METHOD.search(h["method"])]
        recent_email = any(t is None or now - t < timedelta(hours=72) for t in emails)
        texts = [self._when(h["when"]) for h in history if TEXT_METHOD.search(h["method"])]
        recent_text = any(t is None or now - t < timedelta(hours=72) for t in texts)
        name = (row or {}).get("name") or raw.get("name", "")
        return dict(id=str(lead_id), name=name, email=email, assigned_to=assigned, opt_out=opt_out,
                    report_eligible=report_eligible, recent_email=recent_email, recent_text=recent_text, contact_note=contact_note,
                    phones=raw.get("phones", []), history_read=isinstance(history, list),
                    first_name=raw.get("first_name", ""), last_name=raw.get("last_name", ""))

    # --- composer ---------------------------------------------------------------------------
    def _open_composer(self):
        self.lead_frame().frame_locator(self.s["activities"]).locator(self.s["email_menu"]).click()
        comp = self.composer()
        comp.locator(self.s["template_select"]).wait_for()
        return comp

    def templates(self, leads):
        for item in leads:
            lead = self._lead(item["id"])
            if eligible(lead, required_owner(self.config)):
                continue
            comp = self._open_composer()
            options = comp.locator(self.s["template_select"]).locator("option").evaluate_all(
                "els => els.filter(e => e.value && !e.disabled).map(e => ({id: e.value, name: e.textContent.trim()}))")
            self._close_composer()
            return options
        return []

    def inspect(self, lead_id):
        lead = self._lead(lead_id)
        if eligible(lead, required_owner(self.config)):
            return lead
        if self.config.get("custom"):
            raise ValueError("Custom messages need the Momentum Bridge connection")
        self.history_before = [h["text"] for h in self._history()]
        comp = self._open_composer()
        comp.locator(self.s["template_select"]).select_option(value=str(self.config["template_id"]))
        # Personalization has finished once both the subject and the body are filled in.
        self.page.wait_for_function("""([frame, subject, body]) => {
            const d = document.querySelector(frame).contentDocument;
            return d.querySelector(subject).value.trim() && d.querySelector(body).innerText.trim();
        }""", arg=[self.s["composer"], self.s["subject"], self.s["body"]])
        return self._composer(lead)

    @staticmethod
    def _validate_composer(lead, to_class, to_text, cc, subject, body):
        classes = (to_class or "").split()
        if "sts-optout" in classes or "sts-bad" in classes:
            raise LeadExcluded("The CRM marks this email address as opted out or bad", lead)
        found = re.search(r"<([^<>]+)>", to_text or "")
        if not found or found.group(1).strip().lower() != lead["email"].lower():
            raise LeadExcluded("The email screen's recipient does not match the lead", lead)
        if any((value or "").strip() for value in cc):
            raise LeadExcluded("The email screen has unexpected CC or BCC recipients", lead)
        return {**lead, "subject": subject, "body": body}

    def _composer(self, lead):
        comp = self.composer()
        to = comp.locator(self.s["to"])
        cc = [field.input_value() for key in ("cc", "bcc") for field in comp.locator(self.s[key]).all()]
        return self._validate_composer(lead, to.get_attribute("class"), to.locator("option:checked").inner_text(), cc,
                                       comp.locator(self.s["subject"]).input_value(), comp.locator(self.s["body"]).inner_text())

    def send(self, message):
        if self.config.get("mode") != "live":
            raise RuntimeError("Live sending needs live mode")
        self.account()
        if fingerprint(self._composer(message)) != fingerprint(message):
            raise RuntimeError("Composer changed immediately before send")
        self.send_started = datetime.now(self.zone)
        self.composer().locator(self.s["send_button"]).filter(has_text="Send").click()
        self.page.wait_for_function(CLOSED, timeout=30000)
        self._open_lead(message["id"])
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            found = self._new_email_entry(self._history())
            if found:
                return found
            self.page.wait_for_timeout(500)
        raise RuntimeError("Send attempted but a unique new Contact History record could not be verified")

    def _is_this_send(self, h, sender, message=None):
        """A new row is accepted only if it is this outbound action by the approved account, dated at this send:
        an email from this template, a text starting with this message, or a call log with this result."""
        channel = (message or {}).get("channel", self.config.get("channel", "email"))
        # Call rows carry the result in the same column ("Rivera, Alex Morgan [ Left Message ]").
        if re.sub(r"\s*\[.*\]\s*$", "", h["by"]) != sender or re.search(r"inbound", h["method"], re.I):
            return False
        when = self._when(h["when"])
        started = getattr(self, "send_started", None)
        if when is None or started is None or abs((when - started).total_seconds()) > 300:
            return False
        if channel == "text":
            # Momentum logs a text from a saved template as "Template: <name>", and any other text as its message.
            template = re.search(r"Template:\s*(.+)$", h["text"])
            wanted = squash(self.config.get("template_name"))
            same = squash(message["body"])[:30] in squash(h["text"]) or bool(template and wanted and squash(template.group(1)) == wanted)
            return bool(TEXT_METHOD.search(h["method"]) and re.search(r"outbound", h["method"], re.I) and same)
        if channel == "call":
            brackets = lambda text: re.sub(r"\[\s*(.*?)\s*\]", r"[\1]", squash(text))
            return bool(CALL_METHOD.search(h["method"]) and brackets(f"[{message['call_result_name']}]") in brackets(h["text"]))
        if not EMAIL_METHOD.search(h["method"]):
            return False
        name = self.config.get("template_name")
        found = re.search(r"Template:\s*(.+)$", h["text"])
        return not (name and found and found.group(1).strip().lower() != name.strip().lower())

    def _new_email_entry(self, history, message=None):
        """The single new Contact History entry for this action under the approved account, or None while it is not (yet) unique."""
        sender = name_to_last_first(self.config["account_name"])
        new = Counter(h["text"] for h in history) - Counter(self.history_before)
        fresh = []
        for h in history:
            if new[h["text"]] > 0:
                new[h["text"]] -= 1
                if self._is_this_send(h, sender, message):
                    fresh.append(h["text"])
        return fresh[0] if len(fresh) == 1 else None

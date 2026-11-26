import html
import re
import time
from datetime import datetime

from .bridge import BridgeError, get_bridge
from .core import LeadExcluded, eligible, required_owner, fingerprint, personalize
from .udc import MomentumUDC, digits, last_first_to_name, name_to_last_first, squash


class MomentumRefused(RuntimeError):
    """Momentum answered the click with a message and no Contact History entry appeared: nothing was saved or sent."""


class MomentumBridge(MomentumUDC):
    """Same eligibility and safety logic as the Playwright adapter, but every browser action happens in the Momentum
    tab that is already signed in in the user's own browser, through the Momentum Bridge extension."""

    def __init__(self, config):
        super().__init__(config)
        self.bridge = get_bridge(config["data_dir"])
        self._last_history = []

    def __enter__(self):
        # Bring the Momentum tab forward (hidden tabs are throttled by the browser); best effort.
        try:
            if self.bridge.connected():
                self.bridge.call("focus", timeout=20, on=True)
        except BridgeError:
            pass
        return self

    def __exit__(self, *args):
        try:
            if self.bridge.connected():
                self.bridge.agent("closeComposer", timeout=20)
        except BridgeError:
            pass
        try:
            if self.bridge.connected():
                self.bridge.call("focus", timeout=20, on=False)
        except BridgeError:
            pass

    # --- connection -------------------------------------------------------------------------
    def login(self, stop, finished=None):
        self.account()
        return True

    def account(self):
        info = self.bridge.agent("ping", timeout=30)
        if not info or not info.get("shell"):
            raise RuntimeError("Open the Momentum home page in the connected browser, then try again.")
        if info.get("account") != self.config["account_name"]:
            raise RuntimeError("Signed-in account does not match the approved account")

    # --- report -----------------------------------------------------------------------------
    def leads(self):
        self.account()
        ask = lambda: self.bridge.call("report", timeout=300, reportId=int(self.config.get("report_id", 1056)), account=self.config["account_name"], sourceColumn=self.config.get("source_column", ""))
        try:
            data = ask()
        except BridgeError as exc:
            if "did not finish loading" not in str(exc):
                raise
            data = ask()  # Momentum's report page is sometimes slow; reading it again is harmless
        if isinstance(data, dict):
            self.full_rows = data.get("people")  # None from an extension older than 1.9.0
            self.full_meta = {k: data.get(k) for k in ("columns", "sourceColumns", "momentumTotal", "contacts")}
            return self._lead_list(data["rows"], data.get("total"))
        return self._lead_list(data)  # an extension older than 1.5.0

    def dashboard(self):
        """Today's activity counts from the CRM home page (for example Outbound Emails / SMS)."""
        return self.bridge.agent("dashboardCounts", timeout=60)

    # --- lead -------------------------------------------------------------------------------
    def _read_lead(self, lead_id):
        try:
            raw = self.bridge.agent("readLead", str(lead_id))
        except BridgeError as exc:
            if "Momentum does not allow" in str(exc):  # Momentum shows a message as soon as this customer opens
                raise LeadExcluded(str(exc), {"id": str(lead_id), "name": self.report.get(str(lead_id), {}).get("name", str(lead_id)), "email": ""})
            raise
        self._last_history = raw["history"]
        return dict(email=(raw["email"] or "").strip(), opt_out=raw["optOut"], assigned=last_first_to_name(raw["assigned"]) if raw["assigned"] else None,
                    name=raw.get("name", ""), history=raw["history"], phones=raw.get("phones", []),
                    first_name=raw.get("firstName") or "", last_name=raw.get("lastName") or "")

    # --- composer ---------------------------------------------------------------------------
    def templates(self, leads):
        """The saved email templates, read from the first lead whose email window opens. A slow or refused lead is passed over."""
        failures = 0
        for item in leads:
            try:
                lead = self._lead(item["id"])
                if eligible(lead, required_owner(self.config)):
                    continue
                options = self.bridge.agent("openComposer")["templates"]
                self.bridge.agent("closeComposer")
                return options
            except LeadExcluded:
                continue  # Momentum shows a message for this customer: try the next one
            except BridgeError:
                failures += 1
                if failures >= 4:
                    raise
        return []

    def channel_options(self, leads, more=()):
        """Saved text templates and the Log Call choices, read from the first leads that have a phone (nothing is saved).
        The account's own newest leads often have no phone yet, so people from the full report who do are tried next."""
        found = {"sms_templates": [], "call_options": {}}
        tried = list(dict.fromkeys([item["id"] for item in leads[:8]] + list(more)[:12]))
        for lead_id in tried:
            item = {"id": lead_id}
            try:
                lead = self._lead(item["id"])
            except (BridgeError, LeadExcluded):
                continue  # a slow lead page, or Momentum shows a message for this customer: try the next one
            if not lead.get("phones"):
                continue
            try:
                if not found["sms_templates"] and any(p["kind"] == "cell" for p in lead["phones"]):
                    found["sms_templates"] = self.bridge.agent("openSms")["templates"]
                    self.bridge.agent("closeComposer")
                if not found["call_options"]:
                    found["call_options"] = self.bridge.agent("openLogCall")
                    self.bridge.agent("openLead", str(item["id"]))  # reloading the lead discards the unsaved panel
            except (BridgeError, LeadExcluded):
                try:
                    self.bridge.agent("openLead", str(item["id"]))  # leave nothing half filled in
                except (BridgeError, LeadExcluded):
                    pass
                continue
            if found["sms_templates"] and found["call_options"]:
                break
        return found

    @staticmethod
    def _phone_fields(lead, channel, number=None):
        """The phone the action goes to and whether Momentum marks it do-not-text / do-not-call. Texts go to the cell.
        A call log uses the first number that may be called (cell, home, work): one do-not-call number does not skip the person."""
        phones = lead.get("phones") or []
        def opt_out(p):
            flags = (p["text"] if channel == "text" else p["call"]).split()
            # No preference control on the page means the permission is unknown, which is never treated as a yes.
            return ("dont" in flags or "bad" in flags) if flags else None
        if number is not None:
            chosen = next((p for p in phones if digits(p["phone"]) == digits(number)), None)
        else:
            order = {"cell": 0, "home": 1, "work": 2}
            usable = sorted((p for p in phones if channel != "text" or p["kind"] == "cell"), key=lambda p: order.get(p["kind"], 9))
            if channel == "call":
                usable = sorted(usable, key=lambda p: {False: 0, None: 1, True: 2}[opt_out(p)])
            chosen = usable[0] if usable else None
        if not chosen:
            return {"to": None, "phone_opt_out": None}
        return {"to": chosen["phone"], "phone_opt_out": opt_out(chosen)}

    def _open(self, fn, lead, *args):
        """Opens or fills an action screen. A refusal or message by Momentum itself is that lead's skip reason, not a failure."""
        try:
            return self.bridge.agent(fn, *args)
        except BridgeError as exc:
            if "Momentum does not allow" in str(exc):
                raise LeadExcluded(str(exc), lead)
            raise

    def _custom(self, lead):
        """A custom message for this person, placeholders filled in; None when a saved Momentum template is used."""
        custom = self.config.get("custom")
        if not custom:
            return None
        account = self.config["account_name"]
        return {"subject": personalize(custom.get("subject", ""), lead, account).strip(), "body": personalize(custom.get("body", ""), lead, account).strip()}

    def _inspect_phone(self, lead, channel):
        lead.update(channel=channel, **self._phone_fields(lead, channel))
        if eligible(lead, required_owner(self.config)):
            return lead
        self.history_before = [h["text"] for h in self._last_history]
        if channel == "text":
            custom = self._custom(lead)
            self._open("openSms", lead)
            if custom:
                state = self._open("fillSms", lead, custom["body"])
                if squash(state["body"]) != squash(custom["body"]):
                    raise LeadExcluded("The text window did not take the custom message", lead)
            else:
                state = self._open("selectSmsTemplate", lead, str(self.config["template_id"]))
            return self._validate_sms(lead, state)
        options = self._open("openLogCall", lead)
        if not any(digits(p["id"]) == digits(lead["to"]) for p in options["phones"]):
            raise LeadExcluded("Momentum's Log Call panel does not offer this lead's phone number", lead)
        number = next(p["id"] for p in options["phones"] if digits(p["id"]) == digits(lead["to"]))
        state = self._open("fillLogCall", lead, {"type": self.config["call_type_id"], "phone": number,
                                                 "result": self.config["call_result_id"], "note": self.config.get("call_note", "")})
        return self._validate_call(lead, state)

    def _validate_sms(self, lead, state):
        sender = name_to_last_first(self.config["account_name"])
        if state["from"] and state["from"] != sender:
            raise LeadExcluded("The text window's sender is not the approved account", lead)
        lead = {**lead, **self._phone_fields(lead, "text", state["to"])}
        if not lead["to"]:
            raise LeadExcluded("The text window's number is not one of this lead's phones", lead)
        if lead["phone_opt_out"] is not False:
            raise LeadExcluded("The CRM marks this phone number as do-not-text", lead)
        if not state["body"].strip() or len(state["body"]) > 1350:
            raise LeadExcluded("The text message is empty or longer than 1350 characters", lead)
        return {**lead, "subject": "Text: " + (self.config.get("template_name") or ""), "body": state["body"]}

    def _validate_call(self, lead, state):
        expected = (state["incoming"] == "false" and not state["video"] and state["type"] == str(self.config["call_type_id"])
                    and state["result"] == str(self.config["call_result_id"]) and digits(state["phone"]) == digits(lead["to"]))
        if not expected:
            raise LeadExcluded("The Log Call panel did not take the chosen values", lead)
        return {**lead, "subject": "Call log: " + state["resultText"], "body": state["note"],
                "call_result_name": state["resultText"]}

    def inspect(self, lead_id):
        lead = self._lead(lead_id)
        channel = self.config.get("channel", "email")
        if channel != "email":
            return self._inspect_phone(lead, channel)
        if eligible(lead, required_owner(self.config)):
            return lead
        self.history_before = [h["text"] for h in self._last_history]
        custom = self._custom(lead)
        if custom and not custom["subject"]:
            raise LeadExcluded("The custom email has no subject", lead)
        self._open("openComposer", lead)
        if custom:
            body = "<br>".join(html.escape(line) for line in custom["body"].split("\n"))
            state = self._open("fillComposer", lead, {"subject": custom["subject"], "html": body})
            if state["subject"].strip() != custom["subject"] or squash(state["body"]) != squash(custom["body"]):
                raise LeadExcluded("The email window did not take the custom message", lead)
        else:
            state = self._open("selectTemplate", lead, str(self.config["template_id"]))
        return self._validate_composer(lead, state["toClass"], state["toText"], state["cc"], state["subject"], state["body"])

    def _composer(self, lead):
        state = self.bridge.agent("composerState")
        return self._validate_composer(lead, state["toClass"], state["toText"], state["cc"], state["subject"], state["body"])

    def send(self, message):
        if self.config.get("mode") != "live":
            raise RuntimeError("Live sending needs live mode")
        self.account()
        channel = message.get("channel", "email")
        if channel == "text":
            current = self._validate_sms(message, self.bridge.agent("smsState"))
        elif channel == "call":
            current = self._validate_call(message, self.bridge.agent("logCallState"))
        else:
            current = self._composer(message)
        if fingerprint({**current, "channel": channel}) != fingerprint(message):
            raise RuntimeError("Composer changed immediately before send")
        self.send_started = datetime.now(self.zone)
        problem = None
        refused = None
        try:
            self.bridge.agent({"text": "sendSms", "call": "saveLogCall"}.get(channel, "sendComposer"))
        except BridgeError as exc:
            if "Momentum said" in str(exc):
                refused = exc  # Momentum answered with a message instead; Contact History says whether anything was saved
            else:
                problem = exc  # a slow window is not a failed send: Contact History decides
        deadline = time.monotonic() + (12 if refused else 25)
        while time.monotonic() < deadline:
            found = self._new_email_entry(self.bridge.agent("readLead", str(message["id"]))["history"], message)
            if found:
                return found
            time.sleep(1)
        if refused:
            raise MomentumRefused(re.sub(r"^.*?Momentum said:\s*", "", str(refused)) or str(refused))
        if problem:
            raise RuntimeError(f"{problem}. No matching new Contact History record was found; check the lead before retrying")
        raise RuntimeError("Send attempted but a unique new Contact History record could not be verified")

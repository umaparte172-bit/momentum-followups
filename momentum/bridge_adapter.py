import time
from datetime import datetime

from .bridge import BridgeError, get_bridge
from .core import LeadExcluded, eligible, fingerprint
from .udc import MomentumUDC, digits, last_first_to_name, name_to_last_first


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
        data = self.bridge.call("report", timeout=300, reportId=int(self.config.get("report_id", 1056)), account=self.config["account_name"])
        if isinstance(data, dict):
            return self._lead_list(data["rows"], data.get("total"))
        return self._lead_list(data)  # an extension older than 1.5.0

    def dashboard(self):
        """Today's activity counts from the CRM home page (for example Outbound Emails / SMS)."""
        return self.bridge.agent("dashboardCounts", timeout=60)

    # --- lead -------------------------------------------------------------------------------
    def _read_lead(self, lead_id):
        raw = self.bridge.agent("readLead", str(lead_id))
        self._last_history = raw["history"]
        return dict(email=(raw["email"] or "").strip(), opt_out=raw["optOut"], assigned=last_first_to_name(raw["assigned"]) if raw["assigned"] else None,
                    name=raw.get("name", ""), history=raw["history"], phones=raw.get("phones", []))

    # --- composer ---------------------------------------------------------------------------
    def templates(self, leads):
        for item in leads:
            lead = self._lead(item["id"])
            if eligible(lead, self.config["account_name"]):
                continue
            options = self.bridge.agent("openComposer")["templates"]
            self.bridge.agent("closeComposer")
            return options
        return []

    def channel_options(self, leads):
        """Saved text templates and the Log Call choices, read from the first leads that have a phone (nothing is saved)."""
        found = {"sms_templates": [], "call_options": {}}
        for item in leads[:8]:
            lead = self._lead(item["id"])
            if not lead.get("phones"):
                continue
            try:
                if not found["sms_templates"] and any(p["kind"] == "cell" for p in lead["phones"]):
                    found["sms_templates"] = self.bridge.agent("openSms")["templates"]
                    self.bridge.agent("closeComposer")
                if not found["call_options"]:
                    found["call_options"] = self.bridge.agent("openLogCall")
                    self.bridge.agent("openLead", str(item["id"]))  # reloading the lead discards the unsaved panel
            except BridgeError:
                continue
            if found["sms_templates"] and found["call_options"]:
                break
        return found

    @staticmethod
    def _phone_fields(lead, channel, number=None):
        """The phone the action goes to (the cell first) and whether Momentum marks it do-not-text / do-not-call."""
        phones = lead.get("phones") or []
        if number is not None:
            chosen = next((p for p in phones if digits(p["phone"]) == digits(number)), None)
        else:
            order = {"cell": 0, "home": 1, "work": 2}
            usable = [p for p in phones if channel != "text" or p["kind"] == "cell"]
            chosen = min(usable, key=lambda p: order.get(p["kind"], 9), default=None)
        if not chosen:
            return {"to": None, "phone_opt_out": None}
        flags = (chosen["text"] if channel == "text" else chosen["call"]).split()
        return {"to": chosen["phone"], "phone_opt_out": "dont" in flags or "bad" in flags}

    def _inspect_phone(self, lead, channel):
        lead.update(channel=channel, **self._phone_fields(lead, channel))
        if eligible(lead, self.config["account_name"]):
            return lead
        self.history_before = [h["text"] for h in self._last_history]
        if channel == "text":
            self.bridge.agent("openSms")
            state = self.bridge.agent("selectSmsTemplate", str(self.config["template_id"]))
            return self._validate_sms(lead, state)
        options = self.bridge.agent("openLogCall")
        if not any(digits(p["id"]) == digits(lead["to"]) for p in options["phones"]):
            raise LeadExcluded("Momentum's Log Call panel does not offer this lead's phone number", lead)
        number = next(p["id"] for p in options["phones"] if digits(p["id"]) == digits(lead["to"]))
        state = self.bridge.agent("fillLogCall", {"type": self.config["call_type_id"], "phone": number,
                                                  "result": self.config["call_result_id"], "note": self.config.get("call_note", "")})
        return self._validate_call(lead, state)

    def _validate_sms(self, lead, state):
        sender = name_to_last_first(self.config["account_name"])
        if state["from"] and state["from"] != sender:
            raise LeadExcluded("The text window's sender is not the approved account", lead)
        lead = {**lead, **self._phone_fields(lead, "text", state["to"])}
        if not lead["to"]:
            raise LeadExcluded("The text window's number is not one of this lead's phones", lead)
        if lead["phone_opt_out"]:
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
        if eligible(lead, self.config["account_name"]):
            return lead
        self.history_before = [h["text"] for h in self._last_history]
        self.bridge.agent("openComposer")
        state = self.bridge.agent("selectTemplate", str(self.config["template_id"]))
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
        self.bridge.agent({"text": "sendSms", "call": "saveLogCall"}.get(channel, "sendComposer"))
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            found = self._new_email_entry(self.bridge.agent("readLead", str(message["id"]))["history"], message)
            if found:
                return found
            time.sleep(1)
        raise RuntimeError("Send attempted but a unique new Contact History record could not be verified")

import copy
import threading

from .adapters import adapter
from .core import LeadExcluded, eligible, required_owner


def run_check(config, factory=adapter, out=print, sample=5):
    """Read-only self-check of the CRM connection. It never clicks Send.

    Signs in (live mode waits for the user), reads the report, lists saved templates, then opens the
    composer with a template for a few eligible leads to prove personalization works. Returns True
    only if every step passed.
    """
    config = copy.deepcopy(config)
    ok = True

    def step(name, fn):
        nonlocal ok
        try:
            value = fn()
            out(f"PASS  {name}")
            return value
        except Exception as exc:
            ok = False
            out(f"FAIL  {name}: {exc}")
            return None

    with factory(config) as crm:
        step("Sign in and approved account", lambda: crm.login(threading.Event()) or True)
        leads = step("Read report and keep this account's leads", crm.leads)
        if leads is not None:
            out(f"      {len(leads)} leads assigned to {config['account_name']}")
        counts = getattr(crm, "report_counts", None)
        if counts and counts.get("account_rows") is not None:
            out(f"      the list matches the report: {counts['leads']} leads from {counts['account_rows']} report rows for this account")
        templates = step("List saved email templates", lambda: crm.templates(leads or []))
        if hasattr(crm, "channel_options"):
            options = step("List saved text templates and Log Call choices", lambda: crm.channel_options(leads or []))
            if options is not None:
                out(f"      {len(options['sms_templates'])} text templates, {len((options['call_options'] or {}).get('results', []))} call results")
        if templates:
            out(f"      {len(templates)} templates, using '{templates[0]['name']}'")
            crm.config["template_id"] = templates[0]["id"]
            ready = skipped = 0

            def inspect(lead_id):
                try:
                    return crm.inspect(lead_id)
                except LeadExcluded as exc:  # the email screen excluded it: a skip, not a failure
                    return {**exc.lead, "__skip": exc.reason}

            for lead in (leads or [])[: sample * 4]:
                if ready >= sample:
                    break
                result = step(f"Inspect lead {lead['id']}", lambda lead=lead: inspect(lead["id"]))
                if result is None:
                    continue
                reason = result.get("__skip") or eligible(result, required_owner(config))
                if reason:
                    skipped += 1
                    out(f"      skipped: {reason}")
                elif result.get("subject") and result.get("body"):
                    ready += 1
                    out(f"      composer ready, subject: {result['subject'][:60]!r}")
                else:
                    ok = False
                    out("FAIL  composer returned an empty subject or body")
            if ready == 0:
                ok = False
                out(f"FAIL  no eligible lead produced a ready composer ({skipped} skipped)")
        elif templates is not None:
            ok = False
            out("FAIL  no templates available")
    out("CHECK PASSED: nothing was sent." if ok else "CHECK FAILED: nothing was sent; see the FAIL lines.")
    return ok

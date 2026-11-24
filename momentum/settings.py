import json
import os
import math
from pathlib import Path

LOGIN_PATH = "/acrm/servlet/login"


def crm_urls(address):
    """The dealership's Momentum origin and login page from what the user typed (the site or any page on it)."""
    from urllib.parse import urlparse
    text = (address or "").strip()
    target = urlparse(text if "://" in text else "https://" + text)
    if target.scheme != "https" or not target.hostname or "." not in target.hostname:
        raise ValueError("Enter your Momentum address, for example https://yourstore.udccrm.com")
    origin = f"https://{target.netloc}"
    return origin, origin + LOGIN_PATH

DEFAULTS = {
    "mode": "demo", "verified": False, "account_name": "Demo User",
    "max_batch": 0, "delay_seconds": 5, "hourly_limit": 0, "enforce_limits": False,
    "daily_limit": 0, "external_daily_reserve": 0, "timezone": "America/Los_Angeles",
    "report_url": "", "login_url": "", "dashboard_url": "", "allowed_origin": "", "selectors": {},
    "templates": [{"id": "follow-up", "name": "Vehicle follow-up"}, {"id": "check-in", "name": "Quick check-in"}],
    "preview_ttl_seconds": 900, "demo_leads": 50, "headless": False,
    "crm_profile": "", "report_id": 1056, "connection": "playwright",
    "ignore_contact_methods": "remark|prospect ?status|prospect ?alert|assigned|reminder",
}


def load_settings():
    directory = Path(os.environ.get("MOMENTUM_HOME", Path.home() / ".momentum-followups")).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    path = directory / "config.json"
    if not path.exists():
        path.write_text(json.dumps(DEFAULTS, indent=2), encoding="utf-8")
    config = {**DEFAULTS, **json.loads(path.read_text(encoding="utf-8-sig"))}
    # Either address is enough: the other one is derived from it.
    known = str(config.get("allowed_origin") or "").strip() or str(config.get("login_url") or "").strip()
    if known:
        origin, login = crm_urls(known)
        config["allowed_origin"] = str(config.get("allowed_origin") or "").strip() or origin
        config["login_url"] = str(config.get("login_url") or "").strip() or login
    validate(config)
    if config["crm_profile"] == "udc":
        config["verified"] = True  # the adapter is mapped and live-tested; the first send is still gated by the ledger
        if not config["enforce_limits"]:
            # The account owner asked for no caps of the tool's own: Momentum's own limits and refusals still apply.
            config["max_batch"] = config["hourly_limit"] = config["daily_limit"] = 0
            config["delay_seconds"] = min(config["delay_seconds"], 5)
    config["data_dir"] = str(directory)
    return config


def validate(config):
    from zoneinfo import ZoneInfo
    if config["mode"] not in ("demo", "live"):
        raise ValueError("Mode must be demo or live")
    for key in ("verified", "headless"):
        if type(config[key]) is not bool:
            raise ValueError(f"{key} must be true or false")
    if not isinstance(config["selectors"], dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in config["selectors"].items()):
        raise ValueError("selectors must map names to CSS selector strings")
    if not isinstance(config["crm_profile"], str) or config["crm_profile"] not in ("", "udc"):
        raise ValueError("crm_profile must be empty or udc")
    import re
    if not isinstance(config["ignore_contact_methods"], str):
        raise ValueError("ignore_contact_methods must be a regular expression string")
    try:
        re.compile(config["ignore_contact_methods"])
    except re.error as exc:
        raise ValueError(f"ignore_contact_methods is not a valid regular expression: {exc}")
    if config["connection"] not in ("playwright", "bridge", "both"):
        raise ValueError("connection must be playwright, bridge or both")
    if config["connection"] in ("bridge", "both") and config["crm_profile"] != "udc":
        raise ValueError("connection bridge or both requires crm_profile udc")
    if type(config["report_id"]) is not int or config["report_id"] <= 0:
        raise ValueError("report_id must be a positive integer")
    from urllib.parse import urlparse
    origin = urlparse(config.get("allowed_origin") or "")
    for key in ("login_url", "dashboard_url", "report_url"):
        value = config.get(key)
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a URL string")
        if value:
            target = urlparse(value)
            if target.scheme != "https" or not target.netloc or target.username or target.password or (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
                raise ValueError(f"{key} must use the configured HTTPS origin")
    for key in ("max_batch", "hourly_limit", "daily_limit"):
        if type(config[key]) is not int or config[key] < 0:
            raise ValueError(f"{key} must be a whole number (0 means no limit)")
    for key in ("preview_ttl_seconds", "demo_leads"):
        if type(config[key]) is not int or config[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    if type(config["external_daily_reserve"]) is not int or config["external_daily_reserve"] < 0:
        raise ValueError("external_daily_reserve must be a nonnegative integer")
    if type(config["delay_seconds"]) not in (int, float) or not math.isfinite(config["delay_seconds"]) or config["delay_seconds"] < 0:
        raise ValueError("delay_seconds must be nonnegative")
    if not isinstance(config["account_name"], str) or not config["account_name"].strip():
        raise ValueError("account_name is required")
    ZoneInfo(config["timezone"])

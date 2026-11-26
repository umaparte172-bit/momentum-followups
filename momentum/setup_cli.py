"""First-run setup: writes the live configuration for the dealership's Momentum site and account."""
import argparse
import json
import os
import time
from pathlib import Path

from .settings import DEFAULTS, crm_urls, load_settings, validate


def live_config(account, crm):
    origin, login = crm_urls(crm)
    config = {**DEFAULTS, "mode": "live", "verified": False, "account_name": account.strip(), "crm_profile": "udc",
              "connection": "both", "lead_scope": "all", "repeat_after_hours": 72, "login_url": login, "allowed_origin": origin}
    validate(config)
    return config


def write_live_config(account, home=None, force=False, crm=None):
    home = Path(home or os.environ.get("MOMENTUM_HOME", Path.home() / ".momentum-followups"))
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.json"
    if path.exists() and not force:
        try:
            existing = json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError:
            existing = {}
        if existing.get("mode") == "live" and existing.get("account_name") == account.strip():
            return path, False
    if not crm:
        raise ValueError("Give your Momentum address, for example https://yourstore.udccrm.com")
    text = json.dumps(live_config(account, crm), indent=2)
    if path.exists():
        path.replace(path.with_name(f"config.backup-{int(time.time())}.json"))
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path, True


def can_send(home=None):
    """None when the existing configuration is able to send, otherwise the reason it is not."""
    if home:
        os.environ["MOMENTUM_HOME"] = str(home)
    path = Path(os.environ.get("MOMENTUM_HOME", Path.home() / ".momentum-followups")) / "config.json"
    if not path.exists():
        return "there is no configuration yet"
    try:
        config = load_settings()
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return f"the configuration cannot be read ({exc})"
    if config["mode"] != "live":
        return "the configuration is in demo mode, which cannot send"
    if config["crm_profile"] != "udc":
        return "the configuration does not use the Momentum profile"
    return None


def main():
    parser = argparse.ArgumentParser(description="Write the live Momentum configuration")
    parser.add_argument("--check", action="store_true", help="say whether the existing configuration can send, and stop")
    parser.add_argument("--account", help="the name Momentum shows at the top right before the colon")
    parser.add_argument("--crm", help="your Momentum address, for example https://yourstore.udccrm.com")
    parser.add_argument("--home", help="data directory (default ~/.momentum-followups)")
    parser.add_argument("--force", action="store_true", help="overwrite an existing configuration")
    args = parser.parse_args()
    if args.check:
        reason = can_send(args.home)
        print("Configuration is ready for live sending." if not reason else "Not ready for live sending: " + reason)
        raise SystemExit(1 if reason else 0)
    if not args.account:
        parser.error("--account is required")
    path, written = write_live_config(args.account, args.home, args.force, args.crm)
    print(("Configuration written to " if written else "Configuration already set up at ") + str(path))


if __name__ == "__main__":
    main()

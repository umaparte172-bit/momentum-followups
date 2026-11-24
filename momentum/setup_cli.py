"""First-run setup: writes the live configuration for the dealership's Momentum site and account."""
import argparse
import json
import os
from pathlib import Path

from .settings import DEFAULTS, crm_urls, validate


def live_config(account, crm):
    origin, login = crm_urls(crm)
    config = {**DEFAULTS, "mode": "live", "verified": False, "account_name": account.strip(), "crm_profile": "udc",
              "connection": "both", "login_url": login, "allowed_origin": origin}
    validate(config)
    return config


def write_live_config(account, home=None, force=False, crm=None):
    home = Path(home or os.environ.get("MOMENTUM_HOME", Path.home() / ".momentum-followups"))
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.json"
    if path.exists() and not force:
        existing = json.loads(path.read_text(encoding="utf-8-sig"))
        if existing.get("mode") == "live" and existing.get("account_name") == account.strip():
            return path, False
    if not crm:
        raise ValueError("Give your Momentum address, for example https://yourstore.udccrm.com")
    path.write_text(json.dumps(live_config(account, crm), indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path, True


def main():
    parser = argparse.ArgumentParser(description="Write the live Momentum configuration")
    parser.add_argument("--account", required=True, help="the name Momentum shows at the top right before the colon")
    parser.add_argument("--crm", help="your Momentum address, for example https://yourstore.udccrm.com")
    parser.add_argument("--home", help="data directory (default ~/.momentum-followups)")
    parser.add_argument("--force", action="store_true", help="overwrite an existing configuration")
    args = parser.parse_args()
    path, written = write_live_config(args.account, args.home, args.force, args.crm)
    print(("Configuration written to " if written else "Configuration already set up at ") + str(path))


if __name__ == "__main__":
    main()

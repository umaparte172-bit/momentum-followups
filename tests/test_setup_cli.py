import json

import pytest

from momentum.settings import load_settings
from momentum.settings import crm_urls
from momentum.setup_cli import live_config, write_live_config

CRM = "https://yourstore.udccrm.com"


def test_writes_a_locked_live_configuration(tmp_path, monkeypatch):
    path, written = write_live_config("Alex Morgan Rivera", tmp_path, crm=CRM)
    assert written
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["mode"] == "live" and saved["verified"] is False  # sending stays locked
    assert saved["allowed_origin"] == CRM and saved["login_url"] == CRM + "/acrm/servlet/login"
    assert saved["connection"] == "both" and saved["crm_profile"] == "udc" and saved["account_name"] == "Alex Morgan Rivera"
    monkeypatch.setenv("MOMENTUM_HOME", str(tmp_path))
    assert load_settings()["account_name"] == "Alex Morgan Rivera"  # the application accepts it as written


def test_is_idempotent_and_does_not_clobber_a_verified_setup(tmp_path):
    path, _ = write_live_config("Alex Morgan Rivera", tmp_path, crm=CRM)
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["verified"] = True
    path.write_text(json.dumps(saved), encoding="utf-8")
    again, written = write_live_config("Alex Morgan Rivera", tmp_path, crm=CRM)
    assert not written and json.loads(again.read_text(encoding="utf-8"))["verified"] is True
    other, written = write_live_config("Someone Else", tmp_path, crm=CRM)
    assert written and json.loads(other.read_text(encoding="utf-8"))["account_name"] == "Someone Else"


def test_blank_account_is_rejected():
    with pytest.raises(ValueError):
        live_config("   ", CRM)


def test_the_momentum_address_can_be_typed_in_several_ways():
    for typed in ("yourstore.udccrm.com", "https://yourstore.udccrm.com/", "https://yourstore.udccrm.com/acrm/servlet/general?cmd=home"):
        assert crm_urls(typed) == (CRM, CRM + "/acrm/servlet/login")
    for bad in ("", "http://yourstore.udccrm.com", "not an address"):
        with pytest.raises(ValueError):
            crm_urls(bad)


def test_a_new_setup_needs_the_momentum_address(tmp_path):
    with pytest.raises(ValueError, match="Momentum address"):
        write_live_config("Alex Morgan Rivera", tmp_path)

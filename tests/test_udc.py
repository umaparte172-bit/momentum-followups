import pytest

from momentum.adapters import Momentum, adapter, live_adapter
from momentum.settings import DEFAULTS, validate
from momentum.udc import MomentumUDC, last_first_to_name, name_to_last_first


def test_name_conversion_round_trip():
    assert last_first_to_name("Rivera, Alex Morgan") == "Alex Morgan Rivera"
    assert name_to_last_first("Alex Morgan Rivera") == "Rivera, Alex Morgan"
    assert last_first_to_name("--") == "--"
    assert last_first_to_name("") == ""


def test_profile_selects_adapter(tmp_path):
    live = {**DEFAULTS, "mode": "live", "data_dir": str(tmp_path), "allowed_origin": "https://crm.example.test"}
    assert type(live_adapter(live)) is Momentum
    assert type(adapter({**live, "crm_profile": "udc"})) is MomentumUDC


def test_profile_and_report_id_validation():
    config = {**DEFAULTS, "allowed_origin": "https://crm.example.test"}
    validate(config)
    with pytest.raises(ValueError, match="crm_profile"):
        validate({**config, "crm_profile": "other"})
    with pytest.raises(ValueError, match="report_id"):
        validate({**config, "report_id": 0})

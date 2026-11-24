from unittest.mock import patch

import pytest

from momentum.service import Application
from momentum.settings import DEFAULTS, validate


@pytest.mark.parametrize("changes", [
    {"verified": "false"}, {"headless": 1}, {"selectors": []},
    {"account_name": None}, {"dashboard_url": "http://crm.test/home"},
    {"allowed_origin": "https://crm.test", "login_url": "https://other.test/login"},
])
def test_bad_configuration_fails_early(changes):
    with pytest.raises(ValueError):
        validate({**DEFAULTS, **changes})


@pytest.mark.parametrize("operation", ["login", "batch"])
def test_worker_start_failure_releases_operation(tmp_path, operation):
    app = Application({**DEFAULTS, "data_dir": str(tmp_path), "login_url": "https://crm.test/login"})
    try:
        if operation == "batch":
            app.load("owner")
            preview = app.preview("owner", ["1"], "follow-up")
        with patch("threading.Thread.start", side_effect=RuntimeError("Thread unavailable")):
            with pytest.raises(RuntimeError, match="Thread unavailable"):
                if operation == "login":
                    app.login()
                else:
                    app.run("owner", {"preview_id": preview["id"]})
        assert not app.busy
        assert not app.login_pending
        assert not app.operation.locked()
        assert app.job == "Ready"
        if operation == "batch":
            assert app.ledger.results(app.active_batch)[0]["status"] == "interrupted"
        app.load("owner")
    finally:
        app.ledger.close()

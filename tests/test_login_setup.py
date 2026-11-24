import threading
from unittest.mock import patch

import pytest

from momentum.service import Application
from momentum.settings import DEFAULTS
from tests.simulated_crm import CRM, SimulatedMomentum


def test_demo_without_login_url_does_not_claim_authentication(tmp_path):
    app = Application({**DEFAULTS, "data_dir": str(tmp_path)})
    try:
        with pytest.raises(ValueError, match="login_url"):
            app.login()
        assert not app.busy
        assert not app.login_pending
        assert app.job == "Ready"
    finally:
        app.ledger.close()


def test_login_setup_saves_without_claiming_verified_account(tmp_path):
    crm = CRM(1)
    entered = threading.Event()
    class LoginBrowser(SimulatedMomentum):
        def __enter__(self):
            self.config["headless"] = True
            return super().__enter__()

        def login(self, stop, finished=None):
            entered.set()
            return super().login(stop, finished)

    config = {**crm.config(tmp_path), "mode": "demo", "verified": False,
              "login_url": crm.origin+"/login", "report_url": "", "selectors": {}}
    app = Application(config)
    try:
        with patch("momentum.service.Momentum", LoginBrowser):
            app.login()
            assert entered.wait(10)
            assert app.snapshot()["login_pending"]
            app.finish_login()
            app.worker.join(20)
        assert not app.worker.is_alive()
        assert app.error is None
        assert app.job == "Browser session saved; CRM account verification still required"
        assert (tmp_path/"auth.json").exists()
        assert app.config["mode"] == "demo"
        assert not app.config["verified"]
        assert not app.snapshot()["test_verified"]
        with pytest.raises(ValueError, match="No CRM sign-in"):
            app.finish_login()
    finally:
        app.stop.set()
        if app.worker:
            app.worker.join(20)
        app.ledger.close()
        crm.close()

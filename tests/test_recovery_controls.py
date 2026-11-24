import threading
import pytest

from momentum.adapters import Demo
from momentum.service import Application
from momentum.settings import DEFAULTS


def test_pause_rechecks_before_resume_and_stop_records_remaining(tmp_path):
    reached = threading.Event()
    release = threading.Event()
    calls = []
    class Controlled(Demo):
        def inspect(self, lead_id):
            calls.append(lead_id)
            reached.set()
            assert release.wait(10)
            return super().inspect(lead_id)
    app = Application({**DEFAULTS, "data_dir": str(tmp_path)})
    try:
        app.load("tab")
        p = app.preview("tab", ["1", "2"], "follow-up")
        app.factory = Controlled
        batch = app.run("tab", {"preview_id": p["id"]})["batch"]
        assert reached.wait(10)
        app.pause.set()
        release.set()
        # Pause must not classify the in-progress lead as sent or dry-run.
        assert app.ledger.results(batch)[0]["status"] == "pending"
        app.stop.set()
        app.worker.join(10)
        assert not app.worker.is_alive()
        assert [r["status"] for r in app.ledger.results(batch)] == ["skipped", "skipped"]
        assert app.ledger.rows() == []
    finally:
        release.set()
        app.stop.set()
        if app.worker:
            app.worker.join(10)
        app.ledger.close()


def test_completed_send_before_crash_recovers_as_sent(tmp_path):
    app = Application({**DEFAULTS, "data_dir": str(tmp_path)})
    msg = Demo({}).inspect("1")
    batch = app.ledger.create_batch([msg], "follow-up", False)
    app.ledger.reserve(msg, batch)
    app.ledger.finish("1", "sent", "Verified CRM history entry 123")
    app.ledger.close()
    recovered = Application({**DEFAULTS, "data_dir": str(tmp_path)})
    try:
        assert recovered.ledger.results(batch)[0]["status"] == "sent"
        assert recovered.ledger.blocked(msg)
    finally:
        recovered.ledger.close()


def test_two_connections_cannot_exceed_quota(tmp_path):
    from momentum.core import Ledger
    path = tmp_path / "quota.sqlite"
    left, right = Ledger(path), Ledger(path)
    cfg = {**DEFAULTS, "hourly_limit": 1, "delay_seconds": 0}
    try:
        assert left.reserve(Demo({}).inspect("1"), "one", cfg)
        with pytest.raises(ValueError, match="quota"):
            right.reserve(Demo({}).inspect("2"), "two", cfg)
    finally:
        left.close()
        right.close()


def test_pause_resume_reinspects_current_lead(tmp_path):
    inspected = threading.Event()
    release = threading.Event()
    paused = threading.Event()
    calls = []
    class ObservablePause(threading.Event):
        def is_set(self):
            value = super().is_set()
            if value:
                paused.set()
            return value
    class Controlled(Demo):
        def inspect(self, lead_id):
            calls.append(lead_id)
            if len(calls) == 1:
                inspected.set()
                assert release.wait(10)
            return super().inspect(lead_id)
    app = Application({**DEFAULTS, "data_dir": str(tmp_path)})
    try:
        app.load("tab")
        p = app.preview("tab", ["1"], "follow-up")
        app.factory = Controlled
        app.pause = ObservablePause()
        batch = app.run("tab", {"preview_id": p["id"]})["batch"]
        assert inspected.wait(10)
        app.pause.set()
        release.set()
        assert paused.wait(10)
        app.pause.clear()
        app.worker.join(10)
        assert not app.worker.is_alive()
        assert calls == ["1", "1"]
        assert app.ledger.results(batch)[0]["status"] == "dry-run"
    finally:
        release.set()
        app.stop.set()
        app.pause.clear()
        if app.worker:
            app.worker.join(10)
        app.ledger.close()


def test_abrupt_process_exit_retains_reservation(tmp_path):
    import subprocess
    import sys
    script = '''
import os, sys
from momentum.core import Ledger
from momentum.adapters import Demo
ledger = Ledger(sys.argv[1])
message = Demo({}).inspect('1')
batch = ledger.create_batch([message], 'follow-up', False)
ledger.reserve(message, batch)
os._exit(9)
'''
    completed = subprocess.run([sys.executable, "-c", script, str(tmp_path/"sends.sqlite")], timeout=20)
    assert completed.returncode == 9
    app = Application({**DEFAULTS, "data_dir": str(tmp_path)})
    try:
        assert app.snapshot()["results"][0]["status"] == "uncertain"
        assert app.ledger.blocked(Demo({}).inspect("1"))
    finally:
        app.ledger.close()

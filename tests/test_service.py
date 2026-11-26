import tempfile
import time
import unittest
from pathlib import Path

from momentum.adapters import Demo
from momentum.service import Application
from momentum.settings import DEFAULTS
from momentum.core import Ledger


class FakeSender(Demo):
    def send(self, message):
        return "Verified history for " + message["email"]


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = {**DEFAULTS, "data_dir": self.temp.name, "delay_seconds": 0}
        self.app = Application(self.config)
        self.app.load("tab-one")

    def tearDown(self):
        if self.app.worker:
            self.app.worker.join(5)
        self.app.ledger.close()
        self.temp.cleanup()

    def preview(self, ids=None):
        return self.app.preview("tab-one", ids or ["1"], "follow-up")

    def run_preview(self, p, **data):
        result = self.app.run("tab-one", {"preview_id": p["id"], **data})
        self.app.worker.join(10)
        return self.app.ledger.results(result["batch"])

    def test_cross_tab_and_single_use_approval(self):
        p = self.preview()
        with self.assertRaises(ValueError):
            self.app.run("tab-two", {"preview_id": p["id"]})
        self.assertEqual(self.run_preview(p)[0]["status"], "dry-run")
        with self.assertRaises(ValueError):
            self.app.run("tab-one", {"preview_id": p["id"]})

    def test_failed_preview_invalidates_old_approval(self):
        p = self.preview()
        with self.assertRaises(ValueError):
            self.app.preview("tab-one", ["not-in-report"], "follow-up")
        with self.assertRaises(ValueError):
            self.run_preview(p)

    def test_expiry(self):
        p = self.preview()
        p["expires"] = time.time()-1
        with self.assertRaises(ValueError):
            self.run_preview(p)

    def test_templates_change_content(self):
        a = self.preview()["messages"][0]
        b = self.app.preview("tab-one", ["1"], "check-in")["messages"][0]
        self.assertNotEqual(a["subject"], b["subject"])

    def test_all_results_persist_after_restart(self):
        self.run_preview(self.preview(["1", "3"]))
        self.app.ledger.close()
        self.app = Application(self.config)
        self.assertEqual([r["status"] for r in self.app.snapshot()["results"]], ["dry-run", "skipped"])

    def test_recovery_distinguishes_reserved_and_pending(self):
        messages = [Demo({}).inspect("1"), Demo({}).inspect("2")]
        batch = self.app.ledger.create_batch(messages, "follow-up", False)
        self.app.ledger.reserve(messages[0], batch)
        self.app.ledger.close()
        self.app = Application(self.config)
        self.assertEqual([r["status"] for r in self.app.ledger.results(batch)], ["uncertain", "interrupted"])
        self.assertTrue(self.app.ledger.blocked(messages[0]))
        self.assertFalse(self.app.ledger.blocked(messages[1]))

    def test_quota_and_reconciliation_preserve_attempt_count(self):
        message = Demo({}).inspect("1")
        cfg = {**self.config, "hourly_limit": 1}
        self.assertTrue(self.app.ledger.reserve(message, "batch", cfg))
        with self.assertRaises(ValueError):
            self.app.ledger.reserve(Demo({}).inspect("2"), "batch", cfg)
        self.app.ledger.reconcile("1", "not-sent", "CRM history checked; no email sent at this time")
        self.assertFalse(self.app.ledger.blocked(message))
        self.assertEqual(self.app.ledger.quota(cfg)["hour_remaining"], 0)

    def test_live_test_gate_and_persistence(self):
        self.app.config.update(mode="live", verified=True)
        self.app.factory = FakeSender
        p = self.preview()
        # No single test is needed before a batch any more; the typed approval still is.
        with self.assertRaises(ValueError):
            self.app.run("tab-one", {"preview_id": p["id"], "dry_run": False, "authorization": ""})
        rows = self.run_preview(p, dry_run=False, authorization="SEND APPROVED EMAILS")
        self.assertEqual(rows[0]["status"], "sent")

    def test_one_uncertain_send_does_not_stop_the_batch(self):
        # Each unconfirmed send stays blocked (never sent twice) and the batch goes on with the next lead.
        self.app.config.update(mode="live", verified=True)
        self.app.factory = Demo
        self.app.ledger.set_meta(self.app.verification_key, "test verified")
        rows = self.run_preview(self.preview(["1", "2"]), dry_run=False, authorization="SEND APPROVED EMAILS")
        self.assertEqual([r["status"] for r in rows], ["uncertain", "uncertain"])
        self.assertTrue(all(self.app.ledger.blocked({"id": r["id"], "email": r.get("email", "")}) for r in rows))

    def test_three_unconfirmed_sends_in_a_row_stop_the_batch(self):
        # Several in a row means Momentum itself is not saving: then it stops rather than keep clicking.
        self.app.config.update(mode="live", verified=True)
        self.app.factory = Demo
        self.app.ledger.set_meta(self.app.verification_key, "test verified")
        rows = self.run_preview(self.preview(["1", "2", "4", "5", "6"]), dry_run=False, authorization="SEND APPROVED EMAILS")
        self.assertEqual([r["status"] for r in rows], ["uncertain", "uncertain", "uncertain", "skipped", "skipped"])
        self.assertIn("several actions in a row", self.app.snapshot()["error"])

    def test_an_old_extension_blocks_a_live_batch(self):
        self.app.config.update(mode="live", verified=True, connection="bridge")
        self.app.bridge.status = lambda: {"outdated": True, "version": "1.13.2", "expected_version": "1.16.0"}
        p = self.preview(["1"])
        with self.assertRaisesRegex(ValueError, r"old Momentum Bridge \(1\.13\.2\).*needs 1\.16\.0.*Nothing was sent"):
            self.app.run("tab-one", {"preview_id": p["id"], "dry_run": False, "authorization": "SEND APPROVED EMAILS"})

    def test_daily_boundary_in_dealership_timezone(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        cfg = {**self.config, "daily_limit": 1, "hourly_limit": 10}
        at = datetime(2026, 11, 22, 23, 59, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()
        self.app.ledger.reserve(Demo({}).inspect("1"), "batch", cfg, now=at)
        self.assertEqual(self.app.ledger.quota(cfg, at)["day_remaining"], 0)
        self.assertEqual(self.app.ledger.quota(cfg, at+120)["day_remaining"], 1)

    def test_storage_lock_rejects_second_instance(self):
        from momentum.storage_lock import StorageLock
        with StorageLock(self.temp.name):
            with self.assertRaises(RuntimeError):
                with StorageLock(self.temp.name):
                    pass

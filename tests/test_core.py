import tempfile
import unittest
from pathlib import Path
from momentum.core import Ledger, eligible, fingerprint
from momentum.adapters import Demo


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.temp.name) / "test.sqlite")
        self.lead = Demo({}).inspect("1")

    def tearDown(self):
        self.ledger.db.close()
        self.temp.cleanup()

    def test_fail_closed(self):
        self.assertIsNone(eligible(self.lead, "Demo User"))
        for field, value in [("opt_out", None), ("report_eligible", None), ("recent_email", None), ("assigned_to", "Other")]:
            self.assertIsNotNone(eligible({**self.lead, field: value}, "Demo User"))

    def test_reservation_survives_restart_and_blocks_same_address(self):
        self.assertTrue(self.ledger.reserve(self.lead, "batch"))
        self.ledger.db.close()
        self.ledger = Ledger(Path(self.temp.name) / "test.sqlite")
        self.assertTrue(self.ledger.blocked(self.lead))
        self.assertFalse(self.ledger.reserve({**self.lead, "id": "another"}, "batch2"))
        self.assertEqual(self.ledger.rows()[0]["status"], "uncertain")

    def test_changed_message_has_different_fingerprint(self):
        self.assertNotEqual(fingerprint(self.lead), fingerprint({**self.lead, "body": "Changed"}))

    def test_demo_cannot_send(self):
        with self.assertRaises(RuntimeError):
            Demo({}).send(self.lead)


if __name__ == "__main__":
    unittest.main()

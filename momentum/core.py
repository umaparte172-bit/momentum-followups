import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


class LeadExcluded(RuntimeError):
    """One lead cannot be emailed (for example the composer marks its address as bad). The batch continues without it."""

    def __init__(self, reason, lead):
        super().__init__(reason)
        self.reason, self.lead = reason, lead


CHANNELS = ("email", "text", "call")


def send_key(message):
    """Ledger identity of one action: emails keep the original lead/address key; texts and call logs get their own."""
    channel = message.get("channel", "email")
    if channel == "email":
        return message["id"], message["email"].strip().lower()
    return f"{message['id']}:{channel}", f"{channel}:{message.get('to') or message['id']}"


def eligible(lead, account):
    if lead.get("assigned_to") != account:
        return "Lead is not assigned to the current user"
    channel = lead.get("channel", "email")
    if channel != "email":
        if lead.get("report_eligible") is not True:
            note = lead.get("contact_note")
            return f"Contacted within the last 72 hours ({note})" if note else "No contact history could be read for this lead"
        if not lead.get("to"):
            return "No phone number on file"
        if lead.get("phone_opt_out") is True:
            return "The CRM marks this phone number as do-not-" + ("text" if channel == "text" else "call")
        if channel == "text" and lead.get("recent_text") is not False:
            return "A text was already sent in the last 72 hours"
        return None
    if lead.get("opt_out") is True:
        return "The CRM marks this email address as opted out or bad"
    if lead.get("opt_out") is not False:
        return "The email opt-out status could not be read"
    if lead.get("report_eligible") is not True:
        note = lead.get("contact_note")
        return f"Contacted within the last 72 hours ({note})" if note else "No contact history could be read for this lead"
    if not re.fullmatch(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+", lead.get("email", "")):
        return "Missing or invalid email"
    if lead.get("recent_email") is not False:
        return "Recent email activity or unknown contact history"
    return None


def fingerprint(message):
    parts = [message[k] for k in ("id", "email", "subject", "body")] + [message.get("channel", "email"), message.get("to")]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


class Ledger:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS sends (lead_id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, batch TEXT, status TEXT, detail TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, lead_id TEXT, email TEXT, at REAL, status TEXT);
        CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, template TEXT, dry INTEGER, status TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS items (batch TEXT, lead_id TEXT, message TEXT, status TEXT, detail TEXT, PRIMARY KEY(batch, lead_id));
        CREATE TABLE IF NOT EXISTS audit (at REAL, lead_id TEXT, action TEXT, evidence TEXT);
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
        ''')
        self.db.commit()

    def close(self):
        self.db.close()

    def recover(self):
        with self.lock, self.db:
            self.db.execute("UPDATE items SET status='sent', detail=(SELECT detail FROM sends WHERE (sends.lead_id=items.lead_id OR sends.lead_id LIKE items.lead_id||':%') AND sends.batch=items.batch) WHERE status='sending' AND EXISTS (SELECT 1 FROM sends WHERE (sends.lead_id=items.lead_id OR sends.lead_id LIKE items.lead_id||':%') AND sends.batch=items.batch AND sends.status='sent')")
            self.db.execute("UPDATE items SET status='uncertain', detail='Interrupted after reservation; reconcile CRM history' WHERE status='sending'")
            self.db.execute("UPDATE items SET status='interrupted', detail='Interrupted before sending; create a fresh preview' WHERE status='pending'")
            self.db.execute("UPDATE batches SET status='interrupted' WHERE status='running'")

    def blocked(self, lead):
        with self.lock:
            return self.db.execute("SELECT 1 FROM sends WHERE lead_id=? OR email=?", send_key(lead)).fetchone() is not None

    def reserve(self, lead, batch, config=None, now=None):
        now = time.time() if now is None else now
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if self.blocked(lead):
                return False
            if config:
                counts = self.quota(config, now)
                if counts["hour_remaining"] <= 0 or counts["day_remaining"] <= 0:
                    raise ValueError("Configured sending quota reached")
                latest = self.db.execute("SELECT MAX(at) FROM attempts").fetchone()[0]
                if latest is not None and now - latest < config["delay_seconds"]:
                    raise ValueError("Minimum send interval has not elapsed")
            key, address = send_key(lead)
            self.db.execute("INSERT INTO sends VALUES (?, ?, ?, 'uncertain', 'Reserved; verify CRM before retry', ?)", (key, address, batch, datetime.fromtimestamp(now, timezone.utc).isoformat()))
            self.db.execute("INSERT INTO attempts VALUES (?, ?, ?, ?, 'uncertain')", (str(uuid.uuid4()), key, address, now))
            self.db.execute("UPDATE items SET status='sending' WHERE batch=? AND lead_id=?", (batch, lead["id"]))
            return True

    def quota(self, config, now=None):
        now = time.time() if now is None else now
        local = datetime.fromtimestamp(now, ZoneInfo(config["timezone"]))
        start = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        with self.lock:
            hour = self.db.execute("SELECT COUNT(*) FROM attempts WHERE at>?", (now-3600,)).fetchone()[0]
            day = self.db.execute("SELECT COUNT(*) FROM attempts WHERE at>=?", (start,)).fetchone()[0]
        free = 10 ** 9  # a limit of 0 means no limit
        return {"hour_remaining": max(0, config["hourly_limit"]-hour) if config["hourly_limit"] else free,
                "day_remaining": max(0, config["daily_limit"]-config["external_daily_reserve"]-day) if config["daily_limit"] else free}

    def hourly_wait_seconds(self, config, now=None):
        """Seconds until the rolling hourly allowance has room again (0 when it already has)."""
        now = time.time() if now is None else now
        with self.lock:
            rows = [r[0] for r in self.db.execute("SELECT at FROM attempts WHERE at>? ORDER BY at", (now - 3600,))]
        if not config["hourly_limit"] or len(rows) < config["hourly_limit"]:
            return 0
        # The allowance frees up when the attempt that is `hourly_limit` places back leaves the window.
        return max(0, rows[len(rows) - config["hourly_limit"]] + 3600 - now)

    def wait_seconds(self, config):
        with self.lock:
            latest = self.db.execute("SELECT MAX(at) FROM attempts").fetchone()[0]
        return max(0, config["delay_seconds"] - (time.time()-latest)) if latest else 0

    def finish(self, lead_id, status, detail):
        with self.lock, self.db:
            self.db.execute("UPDATE sends SET status=?, detail=?, updated=? WHERE lead_id=?", (status, detail, datetime.now(timezone.utc).isoformat(), lead_id))
            self.db.execute("UPDATE attempts SET status=? WHERE lead_id=? AND status='uncertain'", (status, lead_id))

    def rows(self):
        with self.lock:
            return [dict(row) for row in self.db.execute("SELECT * FROM sends ORDER BY updated DESC")]

    def create_batch(self, messages, template, dry):
        batch = str(uuid.uuid4())
        with self.lock, self.db:
            self.db.execute("INSERT INTO batches VALUES (?, ?, ?, 'running', ?)", (batch, template, dry, time.time()))
            self.db.executemany("INSERT INTO items VALUES (?, ?, ?, 'pending', '')", [(batch, m["id"], json.dumps(m)) for m in messages])
        return batch

    def outcome(self, batch, lead_id, status, detail):
        with self.lock, self.db:
            self.db.execute("UPDATE items SET status=?, detail=? WHERE batch=? AND lead_id=?", (status, detail, batch, lead_id))

    def complete(self, batch, status="complete"):
        with self.lock, self.db:
            self.db.execute("UPDATE batches SET status=? WHERE id=?", (status, batch))

    def results(self, batch):
        with self.lock:
            rows = self.db.execute("SELECT * FROM items WHERE batch=? ORDER BY rowid", (batch,)).fetchall()
        return [{**json.loads(r["message"]), "status": r["status"], "detail": r["detail"]} for r in rows]

    def batches(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM batches ORDER BY created DESC")]

    def reconcile(self, lead_id, resolution, evidence):
        if resolution not in ("sent", "not-sent") or len(evidence.strip()) < 15:
            raise ValueError("Provide a resolution and CRM evidence of at least 15 characters")
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM sends WHERE lead_id=? AND status='uncertain'", (lead_id,)).fetchone()
            if row is None:
                raise ValueError("Only uncertain attempts can be reconciled")
            self.db.execute("INSERT INTO audit VALUES (?, ?, ?, ?)", (time.time(), lead_id, resolution, evidence))
            self.db.execute("UPDATE items SET status=?, detail=? WHERE batch=? AND lead_id=?", ("sent" if resolution == "sent" else "not-sent", evidence, row["batch"], lead_id.split(":")[0]))
            if resolution == "sent":
                self.db.execute("UPDATE sends SET status='sent', detail=? WHERE lead_id=?", (evidence, lead_id))
                self.db.execute("UPDATE attempts SET status='sent' WHERE lead_id=? AND status='uncertain'", (lead_id,))
            else:
                self.db.execute("UPDATE attempts SET status='not-sent' WHERE lead_id=? AND status='uncertain'", (lead_id,))
                self.db.execute("DELETE FROM sends WHERE lead_id=?", (lead_id,))

    def set_meta(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", (key, value))

    def get_meta(self, key):
        with self.lock:
            row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

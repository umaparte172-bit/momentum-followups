"""Local bridge between the follow-up tool and the Momentum Bridge browser extension.

The extension runs in the user's own browser, next to the Momentum tab that is already signed in. It asks this server
for work (long polling) and returns results, so the tool never needs its own login or two-step code.

Pairing: the extension may only receive a token after the user clicks "Sign in to CRM" (which opens a short pairing
window). The first extension that asks during that window is bound by its origin; later requests need that token.
"""
import collections
import functools
import json
import os
import secrets
import threading
import time
import uuid
from importlib.resources import files
from pathlib import Path

PAIRING_SECONDS = 180
ALIVE_SECONDS = 45


@functools.lru_cache(maxsize=1)
def expected_version():
    """The Momentum Bridge version that belongs to this build."""
    return json.loads(files("momentum").joinpath("extension/manifest.json").read_text(encoding="utf-8"))["version"]


class BridgeError(RuntimeError):
    pass


class Bridge:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "bridge.json"
        self.cond = threading.Condition()
        self.pairing_until = 0.0
        self.origin = None
        self.token = None
        self.last_seen = 0.0
        self.account = ""
        self.version = ""
        self.polls = 0
        self.queue = []
        self.results = {}
        self.timings = collections.deque(maxlen=30)
        self.stopped = False
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                self.origin, self.token = saved.get("origin"), saved.get("token")
            except (OSError, ValueError):
                pass

    # --- pairing ----------------------------------------------------------------------------
    def open_pairing(self, seconds=PAIRING_SECONDS):
        with self.cond:
            self.pairing_until = time.monotonic() + seconds

    def pairing_open(self):
        return time.monotonic() < self.pairing_until

    def hello(self, origin):
        if not origin or not origin.startswith("chrome-extension://"):
            raise PermissionError("Only the Momentum Bridge extension may connect")
        with self.cond:
            if self.token and self.origin == origin:
                return self.token
            if not self.pairing_open():
                raise PermissionError("Click Sign in to CRM in the tool to pair this browser")
            self.origin, self.token = origin, secrets.token_urlsafe(32)
            self._save()
            return self.token

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"origin": self.origin, "token": self.token}), encoding="utf-8")
        try:
            temp.chmod(0o600)
        except OSError:
            pass
        os.replace(temp, self.path)

    def _check(self, origin, token):
        # The token is a 256-bit secret handed only to the paired extension; if the browser also sends an origin it must match.
        if not token or not self.token or not secrets.compare_digest(token, self.token) or (origin and origin != self.origin):
            raise PermissionError("Not paired")

    # --- extension side ---------------------------------------------------------------------
    def poll(self, origin, token, account="", timeout=20, version=None):
        with self.cond:
            self._check(origin, token)
            self.last_seen = time.monotonic()
            # Only the newest poll may take work: one left over from a reloaded extension would lose the command.
            self.polls += 1
            mine = self.polls
            self.cond.notify_all()
            if account:
                self.account = account
            if version is not None:
                self.version = version
            deadline = time.monotonic() + timeout
            while not self.queue and not self.stopped:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self.cond.wait(left)
                if mine != self.polls:
                    return None
            return self.queue.pop(0) if self.queue and mine == self.polls else None

    def result(self, origin, token, cid, ok, value=None, error=None):
        with self.cond:
            self._check(origin, token)
            self.last_seen = time.monotonic()
            self.results[cid] = {"ok": bool(ok), "value": value, "error": error}
            self.cond.notify_all()

    # --- tool side --------------------------------------------------------------------------
    def connected(self):
        return bool(self.token) and time.monotonic() - self.last_seen < ALIVE_SECONDS

    def status(self):
        return {"paired": bool(self.token), "connected": self.connected(), "account": self.account if self.connected() else "",
                "version": self.version, "expected_version": expected_version(), "outdated": self.connected() and self.version != expected_version(),
                "pairing": self.pairing_open(), "recent_calls": list(self.timings)[-8:]}

    def call(self, name, timeout=150, **args):
        with self.cond:
            if not self.connected():
                raise BridgeError("The browser is not connected. Open Momentum in Chrome with the Momentum Bridge extension installed, then click Sign in to CRM.")
            cid = uuid.uuid4().hex
            started = time.monotonic()
            self.queue.append({"id": cid, "name": name, **args})
            self.cond.notify_all()
            deadline = time.monotonic() + timeout
            while cid not in self.results:
                left = deadline - time.monotonic()
                if left <= 0:
                    self.queue = [c for c in self.queue if c["id"] != cid]
                    raise BridgeError("The browser did not answer in time. Check the Momentum tab and try again.")
                self.cond.wait(left)
            answer = self.results.pop(cid)
            self.timings.append({"command": args.get("fn") or name, "seconds": round(time.monotonic() - started, 1), "ok": answer["ok"]})
        if not answer["ok"]:
            raise BridgeError(answer["error"] or "The browser reported an error")
        return answer["value"]

    def reload_extension(self):
        """Ask the extension to reload itself from disk (used after the tool is updated)."""
        return self.call("reload", timeout=20)

    def agent(self, fn, *args, timeout=150):
        return self.call("agent", timeout=timeout, fn=fn, args=list(args))

    def stop(self):
        with self.cond:
            self.stopped = True
            self.cond.notify_all()


_instances = {}


def get_bridge(data_dir):
    key = str(Path(data_dir).resolve())
    if key not in _instances:
        _instances[key] = Bridge(data_dir)
    return _instances[key]

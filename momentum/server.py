import argparse
import csv
import io
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlparse, parse_qs

from . import VERSION
from .service import Application, short_error
from .settings import load_settings


def create_server(app, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value, status=200, content_type="application/json"):
            data = value if isinstance(value, bytes) else value.encode("utf-8") if isinstance(value, str) else json.dumps(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def host(self):
            return f"127.0.0.1:{self.server.server_port}"

        def bridge_headers(self):
            origin = self.headers.get("Origin", "")
            return origin if origin.startswith("chrome-extension://") else ""

        def do_OPTIONS(self):
            # Preflight for the Momentum Bridge extension only.
            origin = self.bridge_headers()
            if self.headers.get("Host") != self.host() or not origin or not urlparse(self.path).path.startswith("/bridge/"):
                return self.reply({"error": "Not allowed"}, 403)
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Vary", "Origin")
            self.end_headers()

        def bridge_reply(self, value, status=200):
            origin = self.bridge_headers()
            data = json.dumps(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(data)

        def do_bridge(self, parsed):
            origin = self.headers.get("Origin", "")
            try:
                if parsed.path == "/bridge/hello" and self.command == "POST":
                    return self.bridge_reply({"token": app.bridge.hello(origin)})
                if parsed.path == "/bridge/poll" and self.command == "GET":
                    query = parse_qs(parsed.query)
                    cmd = app.bridge.poll(origin, query.get("token", [""])[0], query.get("account", [""])[0], version=query.get("version", [""])[0])
                    return self.bridge_reply({"cmd": cmd} if cmd else {})
                if parsed.path == "/bridge/result" and self.command == "POST":
                    size = int(self.headers.get("Content-Length", "0"))
                    # The whole report (about 65,000 rows) comes back in one answer.
                    if not 0 < size <= 100000000:
                        raise ValueError("Invalid request size")
                    data = json.loads(self.rfile.read(size))
                    app.bridge.result(origin, data.get("token", ""), data.get("id", ""), data.get("ok"), data.get("value"), data.get("error"))
                    return self.bridge_reply({"ok": True})
            except PermissionError as exc:
                return self.bridge_reply({"error": str(exc)}, 403)
            except (ValueError, TypeError, AttributeError) as exc:
                return self.bridge_reply({"error": str(exc)}, 400)
            return self.bridge_reply({"error": "Not found"}, 404)

        def do_GET(self):
            if self.headers.get("Host") != self.host():
                return self.reply({"error": "Invalid host"}, 403)
            parsed = urlparse(self.path)
            if parsed.path.startswith("/bridge/"):
                return self.do_bridge(parsed)
            if parsed.path == "/":
                return self.reply(files("momentum").joinpath("index.html").read_text(encoding="utf-8").replace("__TOKEN__", app.token), content_type="text/html; charset=utf-8")
            if parsed.path == "/api/state":
                return self.reply(app.snapshot())
            assets = {
                "/assets/bootstrap-icons.min.css": ("assets/bootstrap-icons.min.css", "text/css; charset=utf-8"),
                "/assets/fonts/bootstrap-icons.woff2": ("assets/fonts/bootstrap-icons.woff2", "font/woff2"),
                "/assets/fonts/bootstrap-icons.woff": ("assets/fonts/bootstrap-icons.woff", "font/woff"),
                "/assets/bootstrap.min.css": ("assets/bootstrap.min.css", "text/css; charset=utf-8"),
                "/assets/bootstrap.bundle.min.js": ("assets/bootstrap.bundle.min.js", "text/javascript; charset=utf-8"),
                "/assets/driver.css": ("assets/driver.css", "text/css; charset=utf-8"),
                "/assets/driver.js.iife.js": ("assets/driver.js.iife.js", "text/javascript; charset=utf-8"),
            }
            if parsed.path in assets:
                path, kind = assets[parsed.path]
                return self.reply(files("momentum").joinpath(path).read_bytes(), content_type=kind)
            if parsed.path in ("/api/results", "/api/export"):
                rows = app.ledger.results(parse_qs(parsed.query).get("batch", [""])[0])
                if parsed.path == "/api/results":
                    rows = [{k: r.get(k) for k in ("id", "name", "email", "status", "detail")} for r in rows]
                if parsed.path == "/api/results":
                    return self.reply(rows)
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(["Lead", "Email", "Status", "Detail"])
                def safe(value):
                    text = str(value or "")
                    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text
                for row in rows:
                    writer.writerow([safe(row.get(k)) for k in ("name", "email", "status", "detail")])
                return self.reply(output.getvalue(), content_type="text/csv; charset=utf-8")
            self.reply({"error": "Not found"}, 404)

        def do_POST(self):
            if urlparse(self.path).path.startswith("/bridge/") and self.headers.get("Host") == self.host():
                return self.do_bridge(urlparse(self.path))
            if self.headers.get("Host") != self.host() or self.headers.get("Origin") != "http://"+self.host() or self.headers.get("X-Session-Token") != app.token:
                return self.reply({"error": "Invalid local session: the tool was restarted or updated. Reload this page, then try again."}, 403)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 <= size <= 4000000:  # the whole report can be selected
                    raise ValueError("Invalid request size")
                self.connection.settimeout(30)
                data = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("Request must be an object")
                owner = self.headers.get("X-Tab-Id", "")
                if not re.fullmatch(r"[A-Za-z0-9-]{16,100}", owner):
                    raise ValueError("Invalid browser tab identity")
                path = self.path
                if path == "/api/leads":
                    result = app.load(owner)
                elif path == "/api/full-report":
                    result = app.full_report(data)
                elif path == "/api/preview":
                    result = app.preview(owner, data.get("ids"), data.get("template"), data.get("channel") or "email", data.get("call"), False, data.get("custom"))
                elif path == "/api/preview-start":
                    result = app.start_preview(owner, data.get("ids"), data.get("template"), data.get("channel") or "email", data.get("call"), data.get("quick") is True, data.get("custom"))
                elif path == "/api/my-templates":
                    result = {"templates": app.my_templates()}
                elif path == "/api/save-template":
                    result = app.save_template(data)
                elif path == "/api/delete-template":
                    result = app.delete_template(data)
                elif path == "/api/preview-result":
                    result = app.preview_result(owner, data.get("job"))
                elif path == "/api/run":
                    result = app.run(owner, data)
                elif path == "/api/bridge-reload":
                    result = app.reload_extension()
                elif path == "/api/describe-screen":
                    result = app.bridge.agent("describeScreen", timeout=20)  # read only: which Momentum windows are open
                elif path == "/api/crm-stats":
                    result = app.crm_stats()
                elif path == "/api/crm-entry":
                    result = app.crm_entry()
                elif path == "/api/login":
                    result = app.login()
                elif path == "/api/finish-login":
                    result = app.finish_login()
                elif path == "/api/stop":
                    app.stop.set()
                    result = {"ok": True}
                elif path == "/api/pause":
                    app.pause.set()
                    result = {"ok": True}
                elif path == "/api/resume":
                    app.pause.clear()
                    result = {"ok": True}
                elif path == "/api/reconcile":
                    result = app.reconcile(data)
                else:
                    return self.reply({"error": "Not found"}, 404)
                self.reply(result)
            except (ValueError, KeyError, TypeError) as exc:
                self.reply({"error": str(exc)}, 400)
            except Exception as exc:
                self.reply({"error": short_error(exc)}, 500)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--check", action="store_true", help="read-only CRM self-check; never sends email")
    args = parser.parse_args()
    config = load_settings()
    if args.check:
        from .check import run_check
        from .storage_lock import StorageLock
        with StorageLock(config["data_dir"]):
            if config["mode"] == "live" and config.get("connection") in ("bridge", "both"):
                # The extension talks to this port: serve it while checking, so the signed-in Chrome tab is used.
                import threading
                import time
                from .bridge import get_bridge
                server = create_server(Application(config), args.port)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                print("Waiting for Chrome with Momentum open and the Momentum Bridge extension (up to 90 seconds)...", flush=True)
                deadline = time.monotonic() + 90
                while not get_bridge(config["data_dir"]).connected() and time.monotonic() < deadline:
                    time.sleep(1)
                if not get_bridge(config["data_dir"]).connected():
                    print("FAIL  Chrome is not connected: open Momentum in Chrome (signed in), make sure Momentum Bridge is on,", flush=True)
                    print("      and click Sign in to CRM once in the tool if it was never paired. Nothing was sent.", flush=True)
                    raise SystemExit(1)
            raise SystemExit(0 if run_check(config) else 1)
    # Lock before recovery, including when another instance uses a different port.
    from .storage_lock import StorageLock
    with StorageLock(config["data_dir"]):
        app = Application(config)
        server = create_server(app, args.port)
        print(f"Momentum follow-ups {VERSION}: http://127.0.0.1:{server.server_port}", flush=True)
        print(f"Configuration and private data: {config['data_dir']}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            app.stop.set()
        finally:
            server.server_close()
            if app.worker:
                app.worker.join()
            if not app.worker or not app.worker.is_alive():
                app.ledger.close()


if __name__ == "__main__":
    main()

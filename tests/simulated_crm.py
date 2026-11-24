"""Local test-only CRM. Never included in the distributed application package."""
import html
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from momentum.adapters import Momentum


class SimulatedMomentum(Momentum):
    def check_url(self, url):
        parsed = urlparse(url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or f"http://{parsed.netloc}" != self.config["allowed_origin"]:
            raise ValueError("Simulation must remain on its exact loopback origin")


class CRM:
    def __init__(self, count=120):
        self.count = count
        self.history = {}
        self.sends = []
        self.changed = set()
        self.opt_out = set()
        self.fail_confirmation = False
        self.account = "Demo User"
        self.require_login = False
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def render(self, content, headers=None):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(content.encode())

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path == "/login":
                    return self.render('<button onclick="location.href=\'/authenticated\'">Sign in</button>')
                if parsed.path == "/authenticated":
                    return self.render('<a href="/report">Continue</a><script>location.href="/report"</script>', {"Set-Cookie": "session=yes; Path=/; SameSite=Strict"})
                if parent.require_login and "session=yes" not in self.headers.get("Cookie", ""):
                    self.send_response(302)
                    self.send_header("Location", "/login")
                    self.end_headers()
                    return
                account = f'<div id="account">{html.escape(parent.account)}</div>'
                if parsed.path == "/report":
                    page = int(parse_qs(parsed.query).get("page", ["0"])[0])
                    start = page*25+1
                    rows = ''.join(f'<tr class="lead"><td class="name">Lead {i}</td><td><a class="lead-link" href="/lead/{i}">Open</a></td></tr>' for i in range(start, min(parent.count+1, start+25)))
                    nxt = f'<button id="next" onclick="location.href=\'/report?page={page+1}\'">Next</button>' if start+25 <= parent.count else '<button id="next" disabled>Next</button>'
                    return self.render(account+f'<table id="report-ready">{rows}</table>'+nxt)
                if parsed.path.startswith("/lead/"):
                    i = int(parsed.path.rsplit("/", 1)[1])
                    history = ''
                    for entry in parent.history.get(i, []):
                        number, sender, email, subject = entry.split(' | ', 3)
                        history += f'<div class="history">{html.escape(number)} | <span class="sender">{html.escape(sender)}</span> | <span class="to">{html.escape(email)}</span> | <span class="subject">{html.escape(subject)}</span></div>'
                    optout = str(i in parent.opt_out).lower()
                    changed = " changed" if i in parent.changed else ""
                    content = account+f'''<main id="lead-ready"><div class="name">Lead {i}</div>
                    <div id="email">lead{i}@example.com</div><div id="assigned">Demo User</div>
                    <div id="optout">{optout}</div><div id="eligible">true</div><div id="recent">false</div>
                    {history}<button id="email-button" onclick="document.getElementById('composer').hidden=false">Email</button>
                    <div id="composer" hidden><select id="template" onchange="fill()"><option value="">Choose</option><option value="follow-up">Vehicle follow-up</option><option value="check-in">Quick check-in</option></select>
                    <input id="recipient" value="lead{i}@example.com"><input id="cc"><input id="bcc"><input id="subject"><textarea id="body"></textarea><span id="template-ready" hidden>Ready</span><button id="send" onclick="send()">Send</button></div><div id="confirmation" hidden>Sent</div></main>
                    <script>function fill(){{document.getElementById('subject').value=document.getElementById('template').value==='check-in'?'Checking in':'Vehicle follow-up';document.getElementById('body').value='Hello lead {i}{changed}';document.getElementById('template-ready').hidden=false}}
                    async function send(){{const r=await fetch('/send/{i}',{{method:'POST',body:JSON.stringify({{subject:document.getElementById('subject').value,email:document.getElementById('recipient').value}})}});const d=await r.json();if(d.confirm)document.getElementById('confirmation').hidden=false}}
                    </script>'''
                    return self.render(content)
                self.send_error(404)

            def do_POST(self):
                i = int(self.path.rsplit("/", 1)[1])
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                parent.sends.append(i)
                parent.history.setdefault(i, []).append(f"Email {len(parent.sends)} | {parent.account} | {data['email']} | {data['subject']}")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"confirm": not parent.fail_confirmation}).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def config(self, directory):
        from momentum.settings import DEFAULTS
        return {**DEFAULTS, "mode": "live", "verified": True, "data_dir": str(directory), "headless": True, "browser_channel": os.environ.get("MOMENTUM_TEST_CHANNEL"),
                "report_url": self.origin+"/report", "allowed_origin": self.origin, "template_id": "follow-up",
                "delay_seconds": 0, "hourly_limit": 1000, "daily_limit": 1000,
                "selectors": {"account_name": "#account", "report_row": "tr.lead", "next_page": "#next", "lead_link": "a.lead-link", "report_ready": "#report-ready", "lead_ready": "#lead-ready", "name": ".name", "email": "#email", "assigned_to": "#assigned", "opt_out": "#optout", "report_eligible": "#eligible", "recent_email": "#recent", "email_button": "#email-button", "template_select": "#template", "template_ready": "#template-ready", "recipient": "#recipient", "cc": "#cc", "bcc": "#bcc", "subject": "#subject", "body": "#body", "send_button": "#send", "send_confirmation": "#confirmation", "history_entry": ".history", "history_sender": ".sender", "history_recipient": ".to", "history_subject": ".subject"}}

    def close(self):
        self.server.shutdown()
        self.server.server_close()

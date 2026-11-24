import json
import threading
import urllib.error
import urllib.request
import pytest

from momentum.server import create_server
from momentum.service import Application
from momentum.settings import DEFAULTS


@pytest.fixture
def running(tmp_path):
    app = Application({**DEFAULTS, "data_dir": str(tmp_path)})
    server = create_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    yield app, origin
    server.shutdown()
    server.server_close()
    if app.worker:
        app.worker.join(10)
    app.ledger.close()


def post(app, origin, route, data=None, **headers):
    request = urllib.request.Request(origin+"/api/"+route, data=json.dumps(data or {}).encode(), headers={"Origin": origin, "Content-Type": "application/json", "X-Session-Token": app.token, "X-Tab-Id": "tab-1234567890123456", **headers})
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def test_http_rejects_wrong_origin_and_token(running):
    app, origin = running
    for headers in ({"Origin": "https://untrusted.example"}, {"X-Session-Token": "wrong"}):
        with pytest.raises(urllib.error.HTTPError) as error:
            post(app, origin, "leads", **headers)
        assert error.value.code == 403


def test_http_selection_preview_single_use_and_results(running):
    app, origin = running
    report = post(app, origin, "leads")
    assert len(report["templates"]) == 2
    p = post(app, origin, "preview", {"ids": ["1", "3"], "template": "follow-up"})
    run = post(app, origin, "run", {"preview_id": p["id"]})
    app.worker.join(10)
    with urllib.request.urlopen(origin+"/api/results?batch="+run["batch"]) as response:
        rows = json.load(response)
    assert [r["status"] for r in rows] == ["dry-run", "skipped"]
    with pytest.raises(urllib.error.HTTPError):
        post(app, origin, "run", {"preview_id": p["id"]})


def test_live_send_is_rejected_in_demo(running):
    app, origin = running
    post(app, origin, "leads")
    p = post(app, origin, "preview", {"ids": ["1"], "template": "follow-up"})
    with pytest.raises(urllib.error.HTTPError) as error:
        post(app, origin, "run", {"preview_id": p["id"], "dry_run": False, "test": True, "authorization": "SEND APPROVED EMAILS"})
    assert error.value.code == 400
    assert not app.ledger.rows()

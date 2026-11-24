"""Run with a clean wheel installation, outside the source working directory."""
import json
import os
import tempfile
import threading
import urllib.request
from pathlib import Path

import momentum
from momentum.settings import load_settings
from momentum.service import Application
from momentum.server import create_server

assert "site-packages" in str(Path(momentum.__file__)), momentum.__file__
with tempfile.TemporaryDirectory() as directory:
    os.environ["MOMENTUM_HOME"] = directory
    config = load_settings()
    assert (Path(directory)/"config.json").exists()
    app = Application(config)
    server = create_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(origin) as response:
            assert "Follow-up campaigns" in response.read().decode()
        for asset in ("assets/bootstrap-icons.min.css", "assets/fonts/bootstrap-icons.woff2"):
            with urllib.request.urlopen(origin + "/" + asset) as response:
                assert response.status == 200 and len(response.read()) > 100
        report = app.load("package-smoke")
        p = app.preview("package-smoke", [report["leads"][0]["id"]], report["templates"][0]["id"])
        app.run("package-smoke", {"preview_id": p["id"]})
        app.worker.join(10)
        assert app.snapshot()["results"][0]["status"] == "dry-run"
        print("Installed wheel: resource loading, config creation, HTTP UI and dry-run workflow passed")
    finally:
        server.shutdown()
        server.server_close()
        app.ledger.close()

"""NetPulse-Kopplung: Pause vor dem Stoppen, Ende nach dem Neustart, Ergebnis-Meldung - und nie ein
abgebrochenes Backup, nur weil NetPulse nicht erreichbar ist."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import app.backup_engine as backup_engine
from app import netpulse
from app.database import init_db


class FakeNetPulse:
    def __init__(self):
        self.calls = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                outer.calls.append((self.command, self.path, self.headers.get("Authorization"), body))
                if self.headers.get("Authorization") != "Bearer npi_test":
                    self.send_response(401)
                    self.end_headers()
                    return
                if self.path.endswith("/pause"):
                    reply = {"id": 42, "matches": [{"kind": "check", "id": 1, "label": "Postgres", "why": "Name"}]}
                elif self.path.endswith("/hello"):
                    reply = {"ok": True, "app": "NetPulse", "integration": "DBM"}
                elif self.path.endswith("/inventory"):
                    reply = {"subjects": [{"name": s["name"], "matches": []} for s in body["subjects"]]}
                else:
                    reply = {"ok": True}
                data = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = _answer

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def paths(self):
        return [(m, p) for m, p, _, _ in self.calls]


@pytest.fixture()
def fake():
    init_db()
    server = FakeNetPulse()
    netpulse.save_config({**netpulse.DEFAULTS, "enabled": True, "url": server.url, "token": "npi_test", "grace_s": 60})
    yield server
    server.server.shutdown()
    netpulse.save_config(dict(netpulse.DEFAULTS))


def _wait_for(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _running_container():
    container = MagicMock()
    container.name = "postgres"
    container.attrs = {"Mounts": [], "NetworkSettings": {"Networks": {}}, "Config": {"Image": "postgres:16"},
                       "State": {"Status": "running"}}
    container.image.save.return_value = iter([b"image-bytes"])
    return container


def test_pause_resume_report_requests(fake):
    pause_id = netpulse.pause(["nextcloud"], "Backup")
    assert pause_id == 42
    netpulse.resume(pause_id)
    netpulse.report("nextcloud", "ok", size_bytes=10, duration_s=1.5)
    assert fake.paths() == [("POST", "/api/integration/v1/pause"), ("POST", "/api/integration/v1/pause/42/end"),
                            ("POST", "/api/integration/v1/report")]
    _, _, auth, body = fake.calls[0]
    assert auth == "Bearer npi_test"
    assert body["subjects"] == ["nextcloud"] and body["minutes"] == 240
    assert fake.calls[1][3] == {"grace_s": 60}
    assert fake.calls[2][3]["status"] == "ok"


def test_nothing_sent_when_disabled(fake):
    netpulse.save_config({**netpulse.get_config(), "enabled": False})
    assert netpulse.pause(["x"]) is None
    netpulse.report("x", "ok")
    assert fake.calls == []


def test_backup_pauses_before_stop_and_resumes_after_start(fake, tmp_path: Path, monkeypatch):
    container = _running_container()
    order = []
    container.stop.side_effect = lambda: order.append(("stop", list(fake.paths())))
    container.start.side_effect = lambda: order.append(("start", list(fake.paths())))
    client = MagicMock()
    client.containers.get.return_value = container
    client.version.return_value = {"ApiVersion": "1.45"}
    monkeypatch.setattr(backup_engine, "get_client", lambda: client)

    result = backup_engine.backup_container("postgres", dest_root=tmp_path, stop_container=True)

    assert result.ok
    # Pause stand schon, als gestoppt wurde; beim Start noch nicht beendet
    assert order[0] == ("stop", [("POST", "/api/integration/v1/pause")])
    assert order[1][1] == [("POST", "/api/integration/v1/pause")]
    assert ("POST", "/api/integration/v1/pause/42/end") in fake.paths()
    assert _wait_for(lambda: ("POST", "/api/integration/v1/report") in fake.paths())
    report = [c for c in fake.calls if c[1].endswith("/report")][0][3]
    assert report["subject"] == "postgres" and report["status"] == "ok" and report["kind"] == "backup"


def test_failed_backup_still_ends_pause_and_reports_failure(fake, tmp_path: Path, monkeypatch):
    container = _running_container()
    container.attrs["Mounts"] = [{"Type": "volume", "Name": "pg-data"}]
    client = MagicMock()
    client.containers.get.return_value = container
    client.version.return_value = {"ApiVersion": "1.45"}
    monkeypatch.setattr(backup_engine, "get_client", lambda: client)
    monkeypatch.setattr(backup_engine, "backup_volume_to_file",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))

    result = backup_engine.backup_container("postgres", dest_root=tmp_path, stop_container=True)

    assert not result.ok
    container.start.assert_called_once()
    assert ("POST", "/api/integration/v1/pause/42/end") in fake.paths()
    assert _wait_for(lambda: any(c[1].endswith("/report") for c in fake.calls))
    report = [c for c in fake.calls if c[1].endswith("/report")][0][3]
    assert report["status"] == "failed" and "disk full" in report["message"]


def test_unreachable_netpulse_never_blocks_backup(tmp_path: Path, monkeypatch):
    init_db()
    # Port 9 (discard) auf localhost: Verbindung wird sofort abgelehnt
    netpulse.save_config({**netpulse.DEFAULTS, "enabled": True, "url": "http://127.0.0.1:9", "token": "npi_x"})
    try:
        container = _running_container()
        client = MagicMock()
        client.containers.get.return_value = container
        client.version.return_value = {"ApiVersion": "1.45"}
        monkeypatch.setattr(backup_engine, "get_client", lambda: client)
        result = backup_engine.backup_container("postgres", dest_root=tmp_path, stop_container=True)
        assert result.ok
        container.stop.assert_called_once()
        container.start.assert_called_once()
        assert netpulse.public_config()["status"]["last_error"]
    finally:
        netpulse.save_config(dict(netpulse.DEFAULTS))


def test_public_config_hides_token(fake):
    cfg = netpulse.public_config()
    assert "token" not in cfg and cfg["token_set"] is True


def test_wrong_token_is_reported(fake):
    netpulse.save_config({**netpulse.get_config(), "token": "npi_wrong"})
    with pytest.raises(netpulse.NetPulseError, match="Schlüssel"):
        netpulse.test(netpulse.get_config())

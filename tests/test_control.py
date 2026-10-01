"""The Control Panel's side: the state file, the local stop, sign-in start."""

from __future__ import annotations

import json
import os
import sys

import pytest

from ninaivu_lite import control, db


def test_state_file_round_trip(tmp_path):
    token = control.write_state(tmp_path, 8123)
    c = control.Controller(str(tmp_path))
    assert c.state()["token"] == token and c.port == 8123 and c.can_stop()
    assert c.url() == "http://localhost:8123/" and c.url(True).endswith("/admin")
    control.clear_state(tmp_path)
    assert c.state() == {} and c.port == 8080 and not c.can_stop()


def test_clear_state_leaves_another_servers_file(tmp_path):
    (tmp_path / control.STATE_FILE).write_text(json.dumps({"pid": -5, "port": 1}))
    control.clear_state(tmp_path)
    assert (tmp_path / control.STATE_FILE).exists()


def test_stop_needs_the_token_and_this_computer(app):
    stopped = []
    app.config["STOP"] = lambda: stopped.append(True)
    app.config["STOP_TOKEN"] = "secret-token"
    client = app.test_client()
    assert client.post("/api/local/stop", json={"token": "wrong"}).status_code == 403
    far = {"REMOTE_ADDR": "192.168.1.30"}
    assert client.post("/api/local/stop", json={"token": "secret-token"},
                       environ_base=far).status_code == 403
    near = {"REMOTE_ADDR": "127.0.0.1"}
    assert client.post("/api/local/stop", json={"token": "secret-token"},
                       environ_base=near).json == {"ok": True}
    for _ in range(50):
        if stopped:
            break
        import time
        time.sleep(0.02)
    assert stopped == [True]


def test_stop_is_refused_when_not_started_by_main(app):
    client = app.test_client()
    assert client.post("/api/local/stop", json={"token": ""},
                       environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 403


def test_library_summary_reads_without_the_server(app):
    cfg = app.config["LITE"]
    cfg.save()
    c = control.Controller(cfg.data_dir)
    summary = c.library_summary()
    count = db.connect(cfg.data_dir).execute(
        "SELECT COUNT(*) FROM assets WHERE missing = 0").fetchone()[0]
    assert summary == {"folders": cfg.folders, "items": count}


def test_nothing_running(tmp_path):
    c = control.Controller(str(tmp_path))
    c.state = lambda: {"port": 1}            # nothing listens on port 1
    assert c.health(timeout=0.3) is None and not c.running()
    assert c.stop() == "Ninaivu Lite is not running."


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux autostart file")
def test_start_at_sign_in_on_linux(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    c = control.Controller(str(tmp_path / "data"))
    assert c.autostart_supported() and not c.autostart_enabled()
    c.set_autostart(True)
    entry = (tmp_path / "config" / "autostart" / control.LINUX_AUTOSTART).read_text()
    assert "-m ninaivu_lite --no-browser" in entry and str(tmp_path / "data") in entry
    c.set_autostart(False)
    assert not c.autostart_enabled()


def test_package_import_does_not_load_the_web_app():
    """The panel imports the package; Flask and Pillow should wait for the server."""
    import subprocess
    code = ("import sys, ninaivu_lite.panel, ninaivu_lite.control; "
            "print('flask' in sys.modules, 'PIL' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=os.path.dirname(os.path.dirname(__file__)), check=True)
    assert out.stdout.split() == ["False", "False"]


def test_command_line_for_installers(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    data = str(tmp_path / "data")
    assert control.main(["--data", data, "--stop", "--status"]) == 0
    out = capsys.readouterr().out
    assert "not running" in out and "stopped" in out
    if control.Controller.autostart_supported():
        assert control.main(["--data", data, "--autostart", "on"]) == 0
        assert control.Controller(data).autostart_enabled()
        assert control.main(["--data", data, "--autostart", "off"]) == 0
        assert not control.Controller(data).autostart_enabled()

import json
import socket

import pytest

from ninaivu_lite import net
from ninaivu_lite.__main__ import parse_args
from ninaivu_lite.config import DEFAULT_PORT, Config


def test_defaults():
    cfg = Config()
    assert cfg.port == DEFAULT_PORT == 8080
    assert parse_args([]).port == 8080


def test_save_and_load_round_trip(tmp_path):
    cfg = Config(data_dir=str(tmp_path), folders=["D:\\Photos", "/home/amma/படங்கள்"], language="ta")
    cfg.port = 9999
    cfg.save()
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["folders"] == ["D:\\Photos", "/home/amma/படங்கள்"] and saved["language"] == "ta"
    assert "port" not in saved and "data_dir" not in saved
    loaded = Config.load(tmp_path)
    assert loaded.folders == cfg.folders and loaded.language == "ta"
    assert loaded.port == 8080  # runtime value, not saved
    assert not list(tmp_path.glob(".settings-*"))  # no temp files left


def test_damaged_settings_start_on_defaults(tmp_path):
    (tmp_path / "settings.json").write_text("{ not json", encoding="utf-8")
    cfg = Config.load(tmp_path)
    assert cfg.folders == [] and cfg.language == "en"
    assert (tmp_path / "settings.json.damaged").exists()


def test_bad_values_are_cleaned(tmp_path):
    (tmp_path / "settings.json").write_text(
        json.dumps({"folders": ["", 3, "/ok"], "language": "fr"}), encoding="utf-8")
    cfg = Config.load(tmp_path)
    assert cfg.folders == ["/ok"] and cfg.language == "en"


def test_pick_port_steps_past_a_busy_port():
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        busy = holder.getsockname()[1]
        chosen = net.pick_port("127.0.0.1", busy)
        assert chosen != busy
        assert net.port_is_free("127.0.0.1", chosen)


def test_rank_prefers_home_networks():
    ordered = sorted(["172.17.0.1", "100.100.1.1", "10.0.0.5", "192.168.1.9"], key=net._rank)
    assert ordered == ["192.168.1.9", "10.0.0.5", "172.17.0.1", "100.100.1.1"]


def test_lan_addresses_never_include_loopback():
    assert all(not a.startswith("127.") for a in net.lan_addresses())


def test_already_running_detects_only_ninaivu_lite():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        body = b'{"app": "Ninaivu Lite", "ok": true}'

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(self.body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        assert net.already_running(port) is True
        Handler.body = b'{"app": "something else"}'
        assert net.already_running(port) is False
    finally:
        server.shutdown()
    assert net.already_running(port) is False


def test_old_viewing_copies_are_cleared(tmp_path):
    import os
    import time

    from ninaivu_lite import backups
    shelf = tmp_path / "views" / "0a"
    shelf.mkdir(parents=True)
    old, fresh = shelf / "10-1-1.jpg", shelf / "11-1-1.jpg"
    old.write_bytes(b"x")
    fresh.write_bytes(b"x")
    month_ago = time.time() - 40 * 86400
    os.utime(old, (month_ago, month_ago))
    assert backups.prune_views(tmp_path) == 1
    assert not old.exists() and fresh.exists()
    assert backups.prune_views(tmp_path / "nowhere") == 0


def test_reset_password_from_the_command_line(app, monkeypatch, capsys):
    import getpass

    from ninaivu_lite import __main__ as cli
    from ninaivu_lite import auth, db
    cfg = app.config["LITE"]
    conn = db.connect(cfg.data_dir)
    appa = auth.get_user_by_name(conn, "appa")
    auth.update_profile(conn, appa.id, active=0)
    answers = iter(["a brand new phrase", "a brand new phrase"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    assert cli.reset_password(cfg, "Appa") == 0
    assert auth.authenticate(conn, "appa", "a brand new phrase") is not None
    assert cli.reset_password(cfg, "nobody") == 2
    assert "appa" in capsys.readouterr().err


def test_a_slow_name_lookup_does_not_hold_up_the_start(monkeypatch):
    import threading
    import time

    gate = threading.Event()

    def slow(*args, **kwargs):
        # Stalls like a lookup going out to the network, then fails as a real
        # one would, so the abandoned thread ends cleanly once the test is done.
        gate.wait(30)
        raise OSError("gave up")

    monkeypatch.setattr(net, "_probe", lambda target: None)          # no network found
    monkeypatch.setattr(net.socket, "getaddrinfo", slow)
    try:
        started = time.time()
        assert net._addresses_by_name(timeout=0.2) == []
        assert time.time() - started < 2
    finally:
        gate.set()


def test_a_name_the_idna_codec_refuses_gives_no_addresses(monkeypatch):
    def refused(*args, **kwargs):
        raise UnicodeError("encoding with 'idna' codec failed")

    monkeypatch.setattr(net.socket, "getaddrinfo", refused)
    assert net._addresses_by_name(timeout=2.0) == []


def test_no_name_lookup_when_the_address_is_already_known(monkeypatch):
    monkeypatch.setattr(net, "_probe", lambda target: "192.168.1.20")
    monkeypatch.setattr(net, "_addresses_by_name", lambda timeout: pytest.fail("looked up the name"))
    assert net.lan_addresses() == ["192.168.1.20"]

"""Security audit of 2026-10-11 (after Live Photos, the Duplicates list, On
this day, Photos taken nearby and the thumbnail preload): sign-in limits,
the Control Panel's own requests, ZIP names, and copies made for guests."""

from __future__ import annotations

import http.server
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from io import BytesIO

import pytest

from ninaivu_lite import api_auth, auth, db, media, net
from ninaivu_lite.app import create_app
from ninaivu_lite.config import Config

from conftest import ids, make_jpeg


def rescan(app) -> None:
    app.config["SCANNER"].scan_once(db.connect(app.config["LITE"].data_dir))


# --- sign-in limits keep what was typed, not all of it ------------------------------


def test_a_huge_username_is_not_kept_whole_by_the_sign_in_limits(app, caplog):
    client = app.test_client()
    typed = "x" * 500_000
    with caplog.at_level(logging.WARNING):
        for n in range(3):
            r = client.post("/api/auth/login", json={"username": f"{n}{typed}", "password": "y"})
            assert r.status_code == 401
    for throttle in (api_auth.throttle_short, api_auth.throttle_long, api_auth.throttle_day):
        assert all(len(key) <= auth.THROTTLE_KEY_CHARS for key in throttle._fails)
    # The log says who, briefly: one such attempt used to be half a megabyte
    # of log, which the rotation then kept instead of everything before it.
    assert sum(len(r.getMessage()) for r in caplog.records) < 2_000


def test_long_keys_still_count_apart_and_together():
    limit = auth.Throttle(limit=2, window=60)
    one, other = "a" * 1000 + "1", "a" * 1000 + "2"
    limit.fail(one)
    limit.fail(one)
    assert limit.blocked(one) and not limit.blocked(other)
    limit.forget(one)
    assert not limit.blocked(one)


def test_asking_whether_a_key_is_blocked_stores_nothing():
    limit = auth.Throttle(limit=2, window=60)
    for n in range(100):
        assert not limit.blocked(f"never-failed-{n}")
    assert limit._fails == {}


# --- codes compared as bytes ----------------------------------------------------------


def test_a_setup_code_in_tamil_is_refused_not_a_server_error(library):
    root, data = library
    app = create_app(Config(data_dir=str(data), folders=[str(root)], active=str(root)),
                     addresses=["192.168.1.20"])
    r = app.test_client().post(
        "/api/auth/setup", json={"setup_code": "அஆ", "username": "appa",
                                 "password": "a long passphrase"},
        environ_base={"REMOTE_ADDR": "192.168.1.50"}, headers={"Origin": "http://localhost"})
    assert r.status_code == 403 and r.json.get("setup_code_required")


def test_a_stop_token_in_tamil_is_refused_not_a_server_error(app):
    app.config["STOP"] = lambda: None
    app.config["STOP_TOKEN"] = "the-real-token"
    r = app.test_client().post("/api/local/stop", json={"token": "அஆ"},
                               headers={"Origin": "http://localhost"})
    assert r.status_code == 403


def test_an_undo_of_a_batch_too_large_for_the_index_is_a_400(admin):
    r = admin.post("/api/visibility/undo", json={"batch_id": 2 ** 70})
    assert r.status_code == 400


# --- the Control Panel talks to this computer, never through a proxy -----------------


class _Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"ok": True, "app": "Ninaivu Lite", "instance": "mine"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_the_panel_finds_its_server_with_a_proxy_set(monkeypatch):
    server = http.server.HTTPServer(("127.0.0.1", 0), _Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # A proxy that is not there: urllib used to send 127.0.0.1 to it (and the
    # stop token with it), and the panel then said Ninaivu Lite was stopped.
    for name in list(os.environ):
        if name.lower() in ("no_proxy", "http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    try:
        assert net.already_running(server.server_address[1], "mine")
    finally:
        server.shutdown()


# --- ZIP downloads --------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="such a name cannot be made on Windows")
def test_a_name_that_is_a_path_on_windows_is_one_file_in_a_zip(app, library, family):
    root, _ = library
    climbing = "x\\..\\..\\..\\Startup\\x.jpg"
    make_jpeg(root / climbing, "2020:01:01 10:00:00")
    make_jpeg(root / "C:x.jpg", "2020:01:02 10:00:00")
    rescan(app)
    known = ids(app)
    wanted = f"{known[climbing]},{known['C:x.jpg']}"
    r = family.get(f"/api/download/zip?ids={wanted}")
    assert r.status_code == 200
    with zipfile.ZipFile(BytesIO(r.data)) as z:
        names = z.namelist()
    assert len(names) == 2
    for name in names:
        assert not any(c in name for c in '\\/:'), name


# --- a guest's copy of a video ---------------------------------------------------------


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_a_guests_copy_of_a_video_has_no_subtitle_track(tmp_path, monkeypatch):
    # A drone or a dashcam writes where it was, second by second, as subtitles.
    srt = tmp_path / "where.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nGPS(78.9012,12.3456,20) HOME\n")
    source = tmp_path / "flight.mkv"
    made = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi", "-i", "testsrc=d=1:s=64x64", "-i", str(srt),
         "-c:v", "mpeg4", "-c:s", "srt", str(source)], check=False)
    if made.returncode != 0:
        pytest.skip("this ffmpeg cannot make the sample")
    assert b"HOME" in source.read_bytes()
    monkeypatch.setattr(media, "FFMPEG", shutil.which("ffmpeg"))
    out = tmp_path / "copy.mkv"
    assert media.strip_video(str(source), str(out))
    assert b"HOME" not in out.read_bytes()


def test_a_huge_host_name_is_not_kept_or_logged_whole(app, caplog):
    from ninaivu_lite import app as app_module
    app_module._hosts_refused.clear()
    with caplog.at_level(logging.WARNING):
        r = app.test_client().get("/", headers={"Host": "h" * 100_000 + ".example"})
    assert r.status_code == 400
    assert all(len(name) <= app_module.LOGGED_CHARS for name in app_module._hosts_refused)
    assert sum(len(r.getMessage()) for r in caplog.records) < 2_000

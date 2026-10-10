"""A pendrive or an external hard drive plugged in: found, asked about once,
and the library copied onto it without touching what is already there."""

from __future__ import annotations

import os
import queue
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import make_jpeg
from test_import import noisy_jpeg

from ninaivu_lite import drives, panel


def fake_drive(path, label="USB STICK", drive_id="usb1") -> drives.Drive:
    return drives.Drive(drive_id, str(path), label, 16 * 1024 ** 3, 8 * 1024 ** 3)


@pytest.fixture()
def plugged(app, tmp_path, monkeypatch):
    """One drive, plugged in or taken out by the test."""
    monkeypatch.setattr(drives, "CACHE_SECONDS", 0)
    usb = tmp_path / "USB"
    usb.mkdir()
    present = [fake_drive(usb)]
    app.config["DRIVES"] = drives.Watcher(lister=lambda: list(present))
    return SimpleNamespace(path=usb, present=present)


def wait_export(admin, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = admin.get("/api/admin/drives/export").get_json()
        if not state["running"]:
            return state
        time.sleep(0.05)
    raise AssertionError("the export did not finish")


def test_listing_the_drives_never_fails_here():
    """Whatever this computer has plugged in; on Windows, the kernel32 path
    itself, so a mistake there fails here rather than hiding behind []."""
    found = drives._windows_drives() if sys.platform == "win32" else drives.connected()
    assert isinstance(found, list)
    for d in found:
        assert d.id and d.path and d.label


@pytest.mark.skipif(sys.platform == "win32", reason="macOS and Linux mount points")
def test_mounted_folders_are_drives_and_the_own_disk_is_not(tmp_path, monkeypatch):
    volumes = tmp_path / "Volumes"
    (volumes / "PENDRIVE").mkdir(parents=True)
    (volumes / ".hidden").mkdir()
    (volumes / "Macintosh HD").symlink_to("/")
    (volumes / "plain-folder").mkdir()
    monkeypatch.setattr(drives.os.path, "ismount",
                        lambda p: p == "/" or p.endswith("PENDRIVE"))
    found = drives._mount_drives([str(volumes)])
    assert [d.label for d in found] == ["PENDRIVE"]


def test_a_drive_is_asked_about_once_each_time_it_is_plugged_in(monkeypatch):
    monkeypatch.setattr(drives, "CACHE_SECONDS", 0)
    present = [fake_drive("E:\\")]
    watcher = drives.Watcher(lister=lambda: list(present))
    assert [d.id for d in watcher.pending()] == ["usb1"]
    watcher.answer("usb1")
    assert watcher.pending() == []
    present.clear()                      # taken out
    assert watcher.pending() == []
    present.append(fake_drive("E:\\"))   # and put back: asked again
    assert [d.id for d in watcher.pending()] == ["usb1"]


def test_the_console_asks_and_remembers_the_answer(app, admin, plugged):
    listed = admin.get("/api/admin/drives").get_json()
    assert [(d["label"], d["pending"]) for d in listed["drives"]] == [("USB STICK", True)]
    assert listed["export"]["running"] is False
    assert admin.post("/api/admin/drives/answer", json={"id": "usb1"}).status_code == 200
    assert admin.get("/api/admin/drives").get_json()["drives"][0]["pending"] is False
    gone = admin.post("/api/admin/drives/answer", json={"id": "nope"})
    assert gone.status_code == 404
    assert gone.get_json()["error"] == "That drive is no longer plugged in."


def test_only_an_administrator_is_asked(app, family, plugged):
    assert family.get("/api/admin/drives").status_code == 403
    assert app.test_client().get("/api/admin/drives").status_code == 401
    assert family.post("/api/admin/drives/export", json={"id": "usb1"}).status_code == 403


def test_the_drive_the_library_lives_on_is_not_asked_about(app, admin, library, monkeypatch):
    monkeypatch.setattr(drives, "CACHE_SECONDS", 0)
    root, _data = library
    app.config["DRIVES"] = drives.Watcher(lister=lambda: [fake_drive(root.parent)])
    entry = admin.get("/api/admin/drives").get_json()["drives"][0]
    assert entry["holds_library"] is True and entry["pending"] is False
    refused = admin.post("/api/admin/drives/export", json={"id": "usb1"})
    assert refused.status_code == 409


def test_export_copies_the_library_and_adds_only_what_is_new(app, admin, plugged, library):
    root, _data = library
    started = admin.post("/api/admin/drives/export", json={"id": "usb1"})
    assert started.status_code == 200, started.get_json()
    state = wait_export(admin)
    assert state["phase"] == "done", state
    out = plugged.path / "Ninaivu Lite" / root.name
    assert (out / "2019" / "beach.jpg").read_bytes() == (root / "2019" / "beach.jpg").read_bytes()
    assert (out / "family" / "clip.mp4").is_file()
    assert not (out / "notes.txt").exists()                 # photos and videos only
    assert not (out / ".hidden").exists()
    assert not list(out.rglob("*.partial"))
    copied = state["copied"]
    assert copied >= 5 and state["errors"] == 0
    assert state["message"]["key"].startswith("Copied {copied}")
    # Answering export is answering the question.
    assert admin.get("/api/admin/drives").get_json()["drives"][0]["pending"] is False

    # Next time: only the new photograph is copied.
    make_jpeg(root / "2024" / "new.jpg", "2024:01:01 10:00:00")
    assert admin.post("/api/admin/drives/export", json={"id": "usb1"}).status_code == 200
    again = wait_export(admin)
    assert again["copied"] == 1 and again["skipped"] == copied
    assert (out / "2024" / "new.jpg").is_file()


def test_a_different_file_with_the_same_name_is_kept_beside_it(app, admin, plugged, library):
    root, _data = library
    theirs = plugged.path / "Ninaivu Lite" / root.name / "2019" / "beach.jpg"
    theirs.parent.mkdir(parents=True)
    theirs.write_bytes(b"someone else's file")
    assert admin.post("/api/admin/drives/export", json={"id": "usb1"}).status_code == 200
    assert wait_export(admin)["phase"] == "done"
    assert theirs.read_bytes() == b"someone else's file"     # never overwritten
    assert (theirs.parent / "beach (2).jpg").read_bytes() == (root / "2019" / "beach.jpg").read_bytes()


def test_the_control_panel_mentions_a_new_drive_once(tmp_path, monkeypatch):
    monkeypatch.setattr(drives, "CACHE_SECONDS", 0)
    library_disk = tmp_path / "Disk"
    quiet = fake_drive(tmp_path / "Quiet", "QUIET", "quiet1")
    present = [fake_drive(tmp_path / "USB"), fake_drive(library_disk, "DISK", "disk1"), quiet]
    view = SimpleNamespace(drive_watcher=drives.Watcher(lister=lambda: list(present)),
                           events=queue.Queue(),
                           controller=SimpleNamespace(data_dir=tmp_path / "data",
                                                      drives_never_ask=lambda: ["quiet1"]))
    panel.Panel.look_for_drives(view, [str(library_disk / "Photos")])
    panel.Panel.look_for_drives(view, [str(library_disk / "Photos")])
    offered = []
    while not view.events.empty():
        offered.append(view.events.get_nowait())
    assert [(kind, d.label) for kind, d in offered] == [("drive", "USB STICK")]

    # No window and no browser opened: a line in the panel points at the console.
    opened = []
    monkeypatch.setattr(panel.webbrowser, "open", lambda url: opened.append(url))
    said = []
    view.notice = SimpleNamespace(set=said.append)
    panel.Panel.offer_drive(view, offered[0][1])
    assert not opened
    assert "A drive was connected: USB STICK" in said[0] and "Open the console" in said[0]


def test_the_panel_reads_dont_ask_again_from_the_settings(tmp_path):
    from ninaivu_lite.control import Controller
    controller = Controller(str(tmp_path))
    assert controller.drives_never_ask() == []
    (tmp_path / "settings.json").write_text('{"folders": [], "drives_never_ask": ["usb1", 3]}',
                                            encoding="utf-8")
    assert controller.drives_never_ask() == ["usb1"]


def test_dont_ask_again_is_kept_until_ask_again(app, admin, plugged):
    assert admin.post("/api/admin/drives/never", json={"id": "usb1"}).status_code == 200
    assert admin.get("/api/admin/drives").get_json()["drives"][0]["pending"] is False
    overview = admin.get("/api/admin/overview").get_json()
    assert overview["app"]["quiet_drives"] == 1
    # Taken out and plugged back in: still not mentioned.
    watcher = app.config["DRIVES"]
    watcher.answered.clear()
    assert admin.get("/api/admin/drives").get_json()["drives"][0]["pending"] is False
    # Kept in the settings file, so a restart does not forget it.
    from ninaivu_lite.config import Config
    saved = Config.load(app.config["LITE"].data_dir)
    assert saved.drives_never_ask == ["usb1"]
    assert admin.post("/api/admin/drives/ask-again").status_code == 200
    assert admin.get("/api/admin/overview").get_json()["app"]["quiet_drives"] == 0
    assert admin.get("/api/admin/drives").get_json()["drives"][0]["pending"] is True
    assert admin.post("/api/admin/drives/never", json={"id": "nope"}).status_code == 404


# --- phones on a cable --------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="gvfs names hold a colon, which Windows refuses")
def test_a_phone_opened_by_the_linux_desktop_is_a_phone(tmp_path):
    gvfs = tmp_path / "gvfs"
    (gvfs / "mtp:host=Google_Pixel_7_29081JEGR").mkdir(parents=True)
    (gvfs / "smb-share:server=nas").mkdir()
    found = drives._gvfs_phones(str(gvfs))
    assert [(d.label, d.kind, d.shell) for d in found] == [("Google Pixel 7 29081JEGR", "phone", False)]


def test_nothing_is_copied_onto_a_phone(app, admin, tmp_path, monkeypatch):
    monkeypatch.setattr(drives, "CACHE_SECONDS", 0)
    phone = drives.Drive("ph1", str(tmp_path), "Pixel", 0, 0, kind="phone")
    app.config["DRIVES"] = drives.Watcher(lister=lambda: [phone])
    entry = admin.get("/api/admin/drives").get_json()["drives"][0]
    assert entry["kind"] == "phone" and entry["pending"] is True
    refused = admin.post("/api/admin/drives/export", json={"id": "ph1"})
    assert refused.status_code == 409 and "phone" in refused.get_json()["error"]


class FakeShell:
    """PowerShell copying a phone's DCIM, as phones.FETCH_SCRIPT would:
    files that are on the skip list are not copied again."""

    def __init__(self, camera, env):
        self.stdout = []
        dest = Path(env["NL_DEST"])
        skip = set()
        if Path(env["NL_SKIP"]).exists():
            skip = set(Path(env["NL_SKIP"]).read_text(encoding="utf-8").split("\n"))
        files = sorted(camera.iterdir())
        self.stdout.append(f'{{"total":{len(files)}}}\n'.encode())
        for n, f in enumerate(files, 1):
            rel = os.path.join("Internal shared storage", "DCIM", "Camera", f.name)
            if rel not in skip:
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest / rel)
            self.stdout.append(f'{{"done":{n}}}\n'.encode())
        self.stdout.append(b'{"finished":true}\n')

    def wait(self):
        return 0

    def poll(self):
        return 0


def test_a_phone_windows_shows_only_in_explorer_is_fetched_imported_and_tidied(
        app, admin, tmp_path, monkeypatch):
    from ninaivu_lite import phones
    monkeypatch.setattr(drives, "CACHE_SECONDS", 0)
    camera = tmp_path / "phone-camera"
    # Big enough for the Import, which passes over icons and thumbnails.
    noisy_jpeg(camera / "IMG_20240101_101010.jpg", "2024:01:01 10:10:10", seed=1)
    noisy_jpeg(camera / "IMG_20240102_101010.jpg", "2024:01:02 10:10:10", seed=2)
    monkeypatch.setattr(phones, "_powershell", lambda script, env=None: FakeShell(camera, env))
    phone = drives.Drive("ph1", "::{20D04FE0}\\\\?\\usb#vid_18d1", "Pixel 7", 0, 0,
                         kind="phone", shell=True)
    app.config["DRIVES"] = drives.Watcher(lister=lambda: [phone])

    def run():
        assert admin.post("/api/admin/drives/phone-import", json={"id": "ph1"}).status_code == 200
        deadline = time.time() + 30
        while time.time() < deadline:
            state = admin.get("/api/admin/drives/phone-import").get_json()
            if not state["running"]:
                return state
            time.sleep(0.05)
        raise AssertionError("the phone import did not finish")

    state = run()
    assert state["phase"] == "done", state
    assert state["message"]["vars"] == {"added": "2", "already": "0", "folder": state["destination"]}
    archive = Path(state["destination"])
    assert len(list(archive.rglob("IMG_*.jpg"))) == 2
    mirror = Path(phones.mirror_for(app.config["LITE"].data_dir, phone))
    assert not list(mirror.rglob("*.jpg"))                   # the copies were tidied away
    listing = Path(phones.imported_list(app.config["LITE"].data_dir, phone))
    assert len(listing.read_text(encoding="utf-8").splitlines()) == 2

    # Next time: only the new photograph crosses the cable.
    noisy_jpeg(camera / "IMG_20240103_101010.jpg", "2024:01:03 10:10:10", seed=3)
    state = run()
    assert state["message"]["vars"] == {"added": "1", "already": "0", "folder": state["destination"]}
    assert len(list(archive.rglob("IMG_*.jpg"))) == 3


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' shell and PowerShell")
def test_the_windows_phone_scripts_run_here():
    """No phone on the test machine: the listing runs and finds none, and the
    fetch script is valid PowerShell."""
    from ninaivu_lite import phones
    assert isinstance(phones.wpd_devices(), tuple)
    assert phones._ask_shell() == []
    check = phones._powershell("[void][scriptblock]::Create($env:NL_SCRIPT); 'parsed'",
                               {"NL_SCRIPT": phones.FETCH_SCRIPT})
    out, _ = check.communicate(timeout=60)
    assert b"parsed" in out
    assert isinstance(phones.listed(), list)

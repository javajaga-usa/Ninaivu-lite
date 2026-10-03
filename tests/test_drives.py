"""A pendrive or an external hard drive plugged in: found, asked about once,
and the library copied onto it without touching what is already there."""

from __future__ import annotations

import queue
import sys
import time
from types import SimpleNamespace

import pytest
from conftest import make_jpeg

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


def test_the_control_panel_offers_a_new_drive_once(tmp_path, monkeypatch):
    monkeypatch.setattr(drives, "CACHE_SECONDS", 0)
    library_disk = tmp_path / "Disk"
    present = [fake_drive(tmp_path / "USB"), fake_drive(library_disk, "DISK", "disk1")]
    view = SimpleNamespace(drive_watcher=drives.Watcher(lister=lambda: list(present)),
                           events=queue.Queue(),
                           controller=SimpleNamespace(data_dir=tmp_path / "data"))
    panel.Panel.look_for_drives(view, [str(library_disk / "Photos")])
    panel.Panel.look_for_drives(view, [str(library_disk / "Photos")])
    offered = []
    while not view.events.empty():
        offered.append(view.events.get_nowait())
    assert [(kind, d.label) for kind, d in offered] == [("drive", "USB STICK")]

    # The answer opens the console, which carries it out.
    opened = []
    monkeypatch.setattr(panel.webbrowser, "open", lambda url: opened.append(url))
    view.ask_drive = lambda drive: "import"
    view.notice = SimpleNamespace(set=lambda text: None)
    view.controller.url = lambda admin=False: "http://localhost:8080/admin"
    panel.Panel.offer_drive(view, offered[0][1])
    assert opened and opened[0].startswith("http://localhost:8080/admin?drive=")
    assert opened[0].endswith("&do=import")

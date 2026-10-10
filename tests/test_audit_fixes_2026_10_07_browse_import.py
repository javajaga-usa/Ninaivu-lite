"""Regression tests for the browse/share findings (A78-A86) and the
import/wizard/drives findings (A87-A102) of the 2026-10-07 audit. Each test
checks the fixed behaviour; a fix that lives only in a page's script is
checked on the script the server sends, as tests/test_audit_fixes_p4_ui.py
does."""

from __future__ import annotations

import errno
import json
import os
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import ids, sign_in

from ninaivu_lite import auth, db, drives, importer, phones
from ninaivu_lite.importer import Importer
from test_import import noisy_jpeg, rows, run, sha, wait_for

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "ninaivu_lite" / "static"
JS = STATIC / "js"
TEMPLATES = ROOT / "ninaivu_lite" / "templates"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def locale(lang: str) -> dict[str, str]:
    return json.loads(read(STATIC / "i18n" / f"{lang}.json"))


def conn_of(app):
    return db.connect(app.config["LITE"].data_dir)


def share(client, scope, target):
    r = client.post("/api/shares", json={"scope": scope, "target_id": target,
                                         "expires_in_days": 0, "password": None})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["token"]


def hide(client, *asset_ids: int) -> int:
    r = client.post("/api/visibility", json={"ids": list(asset_ids), "visibility": "hidden"})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["batch_id"]


# === Browse and share ========================================================================


def test_a78_an_admins_photo_link_stops_when_the_photo_is_hidden(app, admin):
    beach = ids(app)["beach.jpg"]
    token = share(admin, "asset", beach)
    stranger = app.test_client()
    assert stranger.get(f"/api/share/{token}").status_code == 200
    assert stranger.get(f"/api/share/{token}/thumb/{beach}").status_code != 404
    hide(admin, beach)
    # Hidden means administrators only: a link the administrator made is no exception.
    assert stranger.get(f"/api/share/{token}").status_code == 404
    assert stranger.get(f"/api/share/{token}/file/{beach}").status_code == 404
    assert stranger.get(f"/api/share/{token}/thumb/{beach}").status_code == 404


def test_a78_a_hidden_photo_cannot_be_shared_even_by_an_admin(app, admin):
    sunset = ids(app)["sunset.jpg"]
    hide(admin, sunset)
    r = admin.post("/api/shares", json={"scope": "asset", "target_id": sunset})
    assert r.status_code == 400
    assert "Hidden photograph cannot be shared" in r.get_json()["error"]
    assert conn_of(app).execute("SELECT COUNT(*) FROM shares").fetchone()[0] == 0


def test_a79_a_hidden_photo_renamed_on_disk_keeps_hidden_favourites_and_albums(
        app, admin, library):
    root, _data = library
    i = ids(app)
    beach, sunset = i["beach.jpg"], i["sunset.jpg"]
    hide(admin, beach)
    album = admin.post("/api/albums", json={"name": "Holiday", "ids": [beach, sunset]})
    assert album.status_code == 200, album.get_json()
    admin_id = auth.get_user_by_name(conn_of(app), "appa").id
    c = conn_of(app)
    with c:
        c.executemany("INSERT INTO user_assets (user_id, asset_id, favorite) VALUES (?, ?, 1)",
                      [(admin_id, beach), (admin_id, sunset)])
    # Renamed in Explorer, and the other moved to another folder.
    os.replace(root / "2019" / "beach.jpg", root / "2019" / "beach at Marina.jpg")
    os.replace(root / "2019" / "sunset.jpg", root / "family" / "sunset.jpg")
    app.config["SCANNER"].scan_once(conn_of(app))

    c = conn_of(app)
    renamed = c.execute("SELECT * FROM assets WHERE name = 'beach at Marina.jpg' "
                        "AND missing = 0").fetchone()
    moved = c.execute("SELECT * FROM assets WHERE name = 'sunset.jpg' AND dir = 'family' "
                      "AND missing = 0").fetchone()
    assert renamed is not None and moved is not None
    assert renamed["id"] != beach and moved["id"] != sunset
    assert renamed["visibility"] == db.VIS_HIDDEN and renamed["vis_source"] == "item"
    assert moved["visibility"] == db.VIS_FAMILY
    favourites = {r[0] for r in c.execute(
        "SELECT asset_id FROM user_assets WHERE user_id = ? AND favorite = 1", (admin_id,))}
    assert favourites == {renamed["id"], moved["id"]}
    in_album = {r[0] for r in c.execute("SELECT asset_id FROM album_items")}
    assert in_album == {renamed["id"], moved["id"]}
    # And the family does not see the renamed photograph.
    family = app.test_client()
    sign_in(app, family, "family", username="amma")
    assert family.get(f"/api/asset/{renamed['id']}").status_code == 404


def test_a80_a_device_whose_session_ended_is_told_and_its_cookie_dropped(app):
    device = app.test_client()
    who = sign_in(app, device, "family", username="amma")
    live = device.get("/api/segments")
    assert live.status_code == 200 and "X-Ninaivu-Session" not in live.headers
    auth.end_all_sessions(conn_of(app), who.id)          # Sign out everywhere, a PIN changed
    r = device.get("/api/segments")
    assert r.headers.get("X-Ninaivu-Session") == "ended"
    assert r.headers.get("Clear-Site-Data") == '"cache"'
    dropped = [h for h in r.headers.getlist("Set-Cookie")
               if h.startswith(f"{auth.SESSION_COOKIE}=")]
    assert dropped and ("Max-Age=0" in dropped[0] or "1970" in dropped[0])
    # Someone who never signed in is not told anything.
    assert "X-Ninaivu-Session" not in app.test_client().get("/api/segments").headers
    # The page hears it on every fetch, whichever helper made it.
    api_js = read(JS / "api.js")
    assert "X-Ninaivu-Session" in api_js and "sessionEnded()" in api_js


def test_a81_avatars_are_open_when_open_browsing_is_off(app, family):
    me = family.post("/api/me/avatar", json={"asset_id": ids(app)["beach.jpg"]}).get_json()
    app.config["LITE"].open_browsing = False
    anyone = app.test_client()
    assert anyone.get("/api/segments").status_code == 401        # still private
    picture = anyone.get(me["avatar"])
    assert picture.status_code == 200 and picture.mimetype == "image/jpeg"
    # A picture that cannot be fetched falls back to the initials.
    accounts = read(JS / "accounts.js")
    node = accounts[accounts.index("export function avatarNode"):]
    assert "img.onerror" in node[:node.index("\n}\n")]


def test_a82_a_hidden_photograph_cannot_be_a_profile_picture(app, admin):
    sunset = ids(app)["sunset.jpg"]
    hide(admin, sunset)
    r = admin.post("/api/me/avatar", json={"asset_id": sunset})
    assert r.status_code == 400 and "Hidden" in r.get_json()["error"]
    assert admin.get("/api/me").get_json().get("avatar") is None


def test_a83_profile_link_list_names_the_photo_and_delete_dialog_mentions_links(app, family):
    beach = ids(app)["beach.jpg"]
    token = share(family, "asset", beach)
    listed = family.get("/api/shares").get_json()
    links = listed["shares"] if isinstance(listed, dict) else listed
    assert [s["name"] for s in links if s["token"] == token] == ["beach.jpg"]
    accounts = read(JS / "accounts.js")
    assert "share.name ? `${i18n.t('Photo')}: ${share.name}`" in accounts
    admin_html = read(TEMPLATES / "admin.html")
    for key in ("Share links they made stop working for good, and albums they made pass to you.",
                "To keep their favourites and let them back in later, disable the profile "
                "instead. A disabled profile's share links pause, and work again when it is "
                "enabled."):
        assert f'data-i18n="{key}"' in admin_html
        assert key in locale("en") and key in locale("ta")


def test_a83_a_disabled_persons_links_pause_and_come_back(app, admin, family):
    """What the delete dialog now says about disabling is true."""
    beach = ids(app)["beach.jpg"]
    token = share(family, "asset", beach)
    amma = auth.get_user_by_name(conn_of(app), "amma")
    stranger = app.test_client()
    assert admin.post(f"/api/people/{amma.id}", json={"active": False}).status_code == 200
    assert stranger.get(f"/api/share/{token}").status_code == 404
    assert admin.post(f"/api/people/{amma.id}", json={"active": True}).status_code == 200
    assert stranger.get(f"/api/share/{token}").status_code == 200


def test_a84_vis_reasons_use_the_servers_rule_name(app, admin):
    r = admin.post("/api/visibility/folder", json={"folder": "2019", "visibility": "family",
                                                   "confirm": True})
    assert r.status_code == 200, r.get_json()
    item = admin.get(f"/api/asset/{ids(app)['beach.jpg']}").get_json()
    assert item["visibility_source"] == "rule"
    app_js = read(JS / "app.js")
    reasons = app_js[app_js.index("const VIS_REASONS = {"):]
    reasons = reasons[:reasons.index("};")]
    assert "rule: i18n.key('Follows the rule set on its folder')" in reasons
    assert "folder:" not in reasons


def test_a84_the_undo_strip_undoes_the_change_it_names(app, admin):
    i = ids(app)
    first = hide(admin, i["beach.jpg"])
    hide(admin, i["sunset.jpg"])
    r = admin.post("/api/visibility/undo", json={"batch_id": first})
    assert r.status_code == 200 and r.get_json()["batch_id"] == first
    c = conn_of(app)
    vis = dict(c.execute("SELECT name, visibility FROM assets WHERE name IN "
                         "('beach.jpg', 'sunset.jpg')").fetchall())
    assert vis == {"beach.jpg": db.VIS_FAMILY, "sunset.jpg": db.VIS_HIDDEN}
    admin_js = read(JS / "admin.js")
    assert "strip.dataset.batch = String(last.id)" in admin_js
    undo = admin_js[admin_js.index("async function undoLastVisibility()"):]
    assert "adminApi.undoVisibility(named)" in undo[:undo.index("\n}\n")]
    assert "undoVisibility(null)" not in admin_js


def test_a85_a_private_librarys_401_says_so_and_the_page_shows_sign_in(app):
    app.config["LITE"].open_browsing = False
    r = app.test_client().get("/api/segments")
    assert r.status_code == 401 and r.get_json().get("private") is True
    api_js = read(JS / "api.js")
    assert "reportUnauthorized(url, !!data?.private)" in api_js
    assert "if (viewerIsAnonymous && !closed) return false;" in api_js


def test_a86_a_shared_photo_page_does_not_show_its_file_name(app, family):
    beach = ids(app)["beach.jpg"]
    token = share(family, "asset", beach)
    item = app.test_client().get(f"/api/share/{token}").get_json()["item"]
    assert "name" not in item and "filename" not in item
    assert "beach" not in json.dumps(item)
    album_id = family.post("/api/albums", json={"name": "Ours", "ids": [beach]}).get_json()["id"]
    album_token = share(family, "album", album_id)
    items = app.test_client().get(f"/api/share/{album_token}").get_json()["items"]
    assert items and all("name" not in it for it in items)
    share_js = read(JS / "share.js")
    one = share_js[share_js.index("function renderOne(item)"):]
    assert "textContent = i18n.t('Shared photograph');" in one[:400]
    assert "item.filename" not in share_js and "item.name" not in share_js


# === Import =================================================================================


def photos(folder: Path, n: int, seed: int = 1) -> list[Path]:
    return [noisy_jpeg(folder / f"IMG_{k:04}.jpg", f"2020:01:{k + 1:02} 10:00:00", seed=seed + k)
            for k in range(n)]


def test_a87_a_source_gone_mid_run_is_incomplete_not_finished(tmp_path, monkeypatch):
    src = tmp_path / "Card"
    photos(src / "a", 2)
    photos(src / "b", 2, seed=10)
    photos(src / "c", 2, seed=20)
    real = Importer._one
    pulled = []

    def unplugged(self, conn, path, st, kind):
        if not pulled:
            pulled.append(path)
            shutil.rmtree(src)                 # the card comes out
        return real(self, conn, path, st, kind)

    monkeypatch.setattr(Importer, "_one", unplugged)
    engine = run(tmp_path / "data", [src], tmp_path / "Archive")
    assert engine.job["phase"] == "incomplete"
    assert engine.job["message"]["key"].startswith("Not finished: {missed} of {total} files")
    assert engine.job["message"]["vars"]["total"] == "6"
    state = db.connect(tmp_path / "data").execute(
        "SELECT state, phase FROM import_jobs ORDER BY id DESC LIMIT 1").fetchone()
    assert (state["state"], state["phase"]) == ("stopped", "incomplete")


def test_a88_an_import_into_a_library_folder_shows_in_the_gallery(app, admin, library,
                                                                  tmp_path, monkeypatch):
    root, _data = library
    looks = []
    monkeypatch.setattr(app.config["SCANNER"], "rescan", lambda within=None: looks.append(time.time()))
    src = tmp_path / "OldDrive"
    photos(src, 2)
    job = {"source_dirs": [{"path": str(src)}], "destination_dir": str(root / "Archive"),
           "media_types": ["image"], "mode": "copy"}
    r = admin.post("/api/archive/start", json=job)
    assert r.status_code == 200, r.get_json()
    assert wait_for(admin)["phase"] == "done"
    assert looks, "the gallery was not told about the imported photographs"
    # Pressing "Add to library" for an archive already inside it looks again.
    looks.clear()
    r = admin.post("/api/archive/adopt", json={"path": str(root / "Archive")})
    assert r.get_json()["already"] is True and looks


def test_a88_an_import_outside_the_library_does_not_rescan(app, admin, tmp_path, monkeypatch):
    looks = []
    monkeypatch.setattr(app.config["SCANNER"], "rescan", lambda within=None: looks.append(1))
    src = tmp_path / "OldDrive"
    photos(src, 1)
    job = {"source_dirs": [{"path": str(src)}], "destination_dir": str(tmp_path / "Elsewhere"),
           "media_types": ["image"], "mode": "copy"}
    assert admin.post("/api/archive/start", json=job).status_code == 200
    assert wait_for(admin)["phase"] == "done"
    assert looks == []


def test_a89_start_makes_the_destination_so_the_wizard_can_add_it(app, admin, tmp_path,
                                                                   monkeypatch):
    started = []
    monkeypatch.setattr(app.config["IMPORTER"], "start",
                        lambda *a, **k: started.append((a, k)))
    src = tmp_path / "OldDrive"
    photos(src, 1)
    dest = tmp_path / "New drive" / "Archive"
    r = admin.post("/api/archive/start", json={
        "source_dirs": [{"path": str(src)}], "destination_dir": str(dest), "mode": "copy"})
    assert r.status_code == 200, r.get_json()
    assert dest.is_dir() and started
    assert started[0][1]["library"] == list(app.config["LITE"].folders)
    # So the wizard's next call, adding it to the library, is not refused.
    adopted = admin.post("/api/archive/adopt", json={"path": str(dest)})
    assert adopted.status_code == 200, adopted.get_json()
    fd = read(JS / "first-day.js")
    assert "could not be added to the library" in fd


def test_a90_free_space_counts_only_what_this_archive_already_holds(tmp_path, monkeypatch):
    data = tmp_path / "data"
    first, second = tmp_path / "DriveA", tmp_path / "DriveB"
    a = photos(first, 3)
    photos(second, 1, seed=50)
    run(data, [first], tmp_path / "Archive1")
    engine = Importer(data)
    archived = sum(p.stat().st_size for p in a)
    assert engine._done_bytes([str(first)], str(tmp_path / "Archive1")) == archived
    assert engine._done_bytes([str(first)], str(tmp_path / "Archive2")) == 0
    assert engine._done_bytes([str(second)], str(tmp_path / "Archive1")) == 0
    # Another archive with almost no room: earlier imports elsewhere do not make room.
    monkeypatch.setattr(importer, "free_space", lambda _d: 1000)
    other = run(data, [second], tmp_path / "Archive2")
    assert other.job["phase"] == "failed"
    said = other.job["message"]
    assert said["key"].startswith("Not enough room")
    assert said["vars"]["free"] == "0.0 MB"          # not "0.0 GB"
    assert importer.human_size(int(3.6 * 2 ** 20)) == "3.6 MB"
    assert importer.human_size(3 * 2 ** 30) == "3.0 GB"


def test_a91_a_source_holding_the_library_does_not_copy_the_library(tmp_path):
    drive = tmp_path / "Drive"
    library = drive / "Photos"
    photos(library, 2)
    noisy_jpeg(drive / "Other" / "new.jpg", "2021:02:03 04:05:06", seed=77)
    dest = tmp_path / "Archive"
    engine = Importer(tmp_path / "data")
    engine.start([str(drive)], str(dest), ["image"], "copy", library=[str(library)])
    engine.wait(120)
    assert engine.job["phase"] == "done"
    assert set(rows(tmp_path / "data")) == {"new.jpg"}
    assert [p.name for p in dest.rglob("*.jpg")] == ["new.jpg"]


def test_a92_a_retried_import_never_writes_over_a_different_archived_photo(tmp_path):
    data, card, dest = tmp_path / "data", tmp_path / "Card", tmp_path / "Archive"
    source = noisy_jpeg(card / "DCIM" / "IMG_0001.jpg", "2020:01:01 10:00:00", seed=1)
    run(data, [card], dest)
    archived = dest / "2020" / "01" / "01" / "IMG_0001.jpg"
    assert archived.read_bytes() == source.read_bytes()
    # Edited in an editor afterwards: the audit marks it as not matching.
    noisy_jpeg(archived, "2020:01:01 10:00:00", seed=40)
    edited = sha(archived)
    run(data, [card], dest, "verify")
    assert rows(data)["IMG_0001.jpg"]["status"] == "error"
    # The card is reused: a new photograph at the same path.
    noisy_jpeg(source, "2021:05:05 10:00:00", seed=2)
    again = run(data, [card], dest)
    assert again.job["phase"] == "done"
    assert sha(archived) == edited                           # never written over
    fresh = dest / "2021" / "05" / "05" / "IMG_0001.jpg"
    assert fresh.read_bytes() == source.read_bytes()


def test_a92_the_same_photo_still_replaces_its_damaged_copy(tmp_path):
    data, card, dest = tmp_path / "data", tmp_path / "Card", tmp_path / "Archive"
    source = noisy_jpeg(card / "IMG_0001.jpg", "2020:01:01 10:00:00", seed=1)
    run(data, [card], dest)
    archived = dest / "2020" / "01" / "01" / "IMG_0001.jpg"
    archived.write_bytes(archived.read_bytes()[:-10] + b"\0" * 10)
    run(data, [card], dest, "verify")
    run(data, [card], dest)
    assert archived.read_bytes() == source.read_bytes()
    assert not (archived.parent / "IMG_0001_1.jpg").exists()


def test_a93_the_wizard_asks_to_start_the_import_on_next():
    fd = read(JS / "first-day.js")
    nxt = fd[fd.index("if (STEPS[this.step] === 'import'"):]
    nxt = nxt[:nxt.index("this.step += 1;")]
    key = ("Start the import of the folders you added now? Cancel goes on without importing; "
           "Import, under Library, can do it later.")
    assert f"confirm(i18n.t('{key}'))" in nxt
    assert "this.startImport(start)" in nxt and "!this.importStarted" in nxt
    assert "start.id = 'fd-start-import';" in fd
    assert key in locale("en") and key in locale("ta")


class FakeEngine:
    """The Import as the phone job sees it."""

    def __init__(self, phase: str = "done", said=None, refuse: bool = False) -> None:
        self.phase, self.said, self.refuse = phase, said, refuse
        self.running = False

    def start(self, *_args, **_kwargs) -> None:
        if self.refuse:
            raise ValueError("A run is already going. Stop it first.")

    def wait(self, _timeout: float) -> None:
        pass

    def progress(self) -> dict:
        return {"phase": self.phase, "job_said": self.said, "processed": 0, "total_files": 0}


def phone_run(tmp_path, monkeypatch, engine: FakeEngine) -> dict:
    monkeypatch.setattr(phones.PhoneImport, "_fetch", lambda self, drive, mirror: True)
    monkeypatch.setattr(phones.PhoneImport, "_tidy", lambda self, *a: (2, 1))
    phone = drives.Drive("ph1", "::{20D04FE0}\\usb#phone", "Pixel 7", 0, 0, kind="phone",
                         shell=True)
    job = phones.PhoneImport()
    mirror = tmp_path / "mirror"
    mirror.mkdir(exist_ok=True)
    job.start(phone, str(mirror), str(tmp_path / "Archive"), engine, str(tmp_path / "data"))
    job.wait(30)
    return job.progress()


def test_a94_a_phone_import_whose_archive_step_failed_is_not_done(tmp_path, monkeypatch):
    full = importer._say("The destination is full. Make room on it, then press Start to "
                         "carry on.")
    state = phone_run(tmp_path, monkeypatch, FakeEngine("failed", full))
    assert state["phase"] == "failed" and state["message"] == full
    state = phone_run(tmp_path, monkeypatch, FakeEngine("stopped", None))
    assert state["phase"] == "stopped"
    assert state["message"]["key"].startswith("The archive step stopped before the end.")
    state = phone_run(tmp_path, monkeypatch, FakeEngine(refuse=True))
    assert state["phase"] == "failed"
    assert "another import is running" in state["message"]["text"]


def test_a94_a_finished_phone_import_names_its_folder(tmp_path, monkeypatch):
    state = phone_run(tmp_path, monkeypatch, FakeEngine("done"))
    assert state["phase"] == "done"
    assert state["message"]["key"].endswith(" They are in {folder}.")
    assert state["message"]["vars"]["folder"] == str(tmp_path / "Archive")


def test_a94_the_import_page_waits_while_a_phone_is_imported(app, admin, tmp_path):
    app.config["PHONE_IMPORT"] = SimpleNamespace(running=True,
                                                 progress=lambda engine=None: {"running": True})
    src = tmp_path / "OldDrive"
    photos(src, 1)
    r = admin.post("/api/archive/start", json={
        "source_dirs": [{"path": str(src)}], "destination_dir": str(tmp_path / "Archive"),
        "mode": "copy"})
    assert r.status_code == 409 and "phone is being imported" in r.get_json()["error"]


def test_a95_the_drives_listing_carries_the_phone_job_and_the_console_reopens_it(app, admin):
    data = admin.get("/api/admin/drives").get_json()
    assert "export" in data and "phone" in data
    assert data["phone"]["running"] is False
    js = read(JS / "drives.js")
    assert "['export', 'phone'].find((job) => data[job]?.running)" in js
    reopen = js[js.index("const going ="):]
    reopen = reopen[:reopen.index("return;")]
    assert "this.showJob(data[going])" in reopen and "this.follow(going)" in reopen


@pytest.fixture()
def usb(tmp_path):
    path = tmp_path / "USB"
    path.mkdir()
    return drives.Drive("usb1", str(path), "USB STICK", 16 * 1024 ** 3, 8 * 1024 ** 3)


def export(library, usb) -> dict:
    root, data = library
    job = drives.Exporter()
    job.start(usb, [str(root)], str(data))
    job.wait(60)
    assert not job.running
    return job.progress()


def test_a96_an_export_is_on_the_drive_itself_and_says_eject(library, usb, tmp_path,
                                                             monkeypatch):
    synced = []
    real_fsync = os.fsync

    def fsync(fd):
        synced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(drives.os, "fsync", fsync)
    src = noisy_jpeg(tmp_path / "one.jpg", seed=3)
    dest = tmp_path / "USB" / "one.jpg"
    drives._copy(str(src), str(dest), threading.Event())
    assert synced and dest.read_bytes() == src.read_bytes()
    monkeypatch.setattr(drives.os, "fsync", real_fsync)
    state = export(library, usb)
    assert state["phase"] == "done"
    assert state["message"]["key"].endswith(" Eject the drive before you unplug it.")
    assert state["message"]["key"] in locale("en") and state["message"]["key"] in locale("ta")


def test_a96_a_full_drive_stops_the_export_and_says_so(library, usb, monkeypatch):
    def full(src, dest, cancel):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(drives, "_copy", full)
    monkeypatch.setattr(importer, "too_big", lambda dest, size: False)   # really full
    state = export(library, usb)
    assert state["phase"] == "failed"
    assert state["message"]["key"].startswith("The drive is full.")
    assert state["copied"] == 0 and state["done"] == 0


def test_a97_an_interrupted_import_is_reported(app, admin, tmp_path):
    c = conn_of(app)
    with c:
        c.execute("INSERT INTO import_jobs (sources, destination, mode, state, phase, "
                  "started_at) VALUES (?, ?, 'copy', 'running', 'copying', ?)",
                  (json.dumps([str(tmp_path / "Drive")]), str(tmp_path / "Archive"),
                   time.time() - 60))
    status = admin.get("/api/archive/status").get_json()
    assert status["phase"] == "interrupted"
    assert status["job_said"]["key"].startswith("The last import was interrupted")
    assert "Press Start to finish it" in status["job_message"]
    archive_js = read(JS / "archive.js")
    assert "data.phase === 'interrupted'" in archive_js


def test_a98_import_summaries_are_sentences_the_console_translates(app, admin, tmp_path):
    src = tmp_path / "Drive"
    a, _b = photos(src, 2)
    (src / "copy of first.jpg").write_bytes(a.read_bytes())
    job = {"source_dirs": [{"path": str(src)}], "destination_dir": str(tmp_path / "Archive"),
           "media_types": ["image"], "mode": "copy"}
    assert admin.post("/api/archive/start", json=job).status_code == 200
    status = wait_for(admin)
    said = status["job_said"]
    assert set(said) >= {"key", "vars", "text"}
    assert said["key"] == ("Finished: {verified} files archived and verified, {duplicates} "
                           "duplicates left in place.")
    assert said["vars"] == {"verified": "2", "duplicates": "1"}
    assert status["job_message"] == said["text"] == (
        "Finished: 2 files archived and verified, 1 duplicates left in place.")
    assert said["key"] in locale("ta")
    # Stored as the sentence, and read back the same after a restart.
    stored = conn_of(app).execute(
        "SELECT message FROM import_jobs ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert importer.stored_said(stored)["key"] == said["key"]
    assert importer.stored_said("Finished: 3 files.") == "Finished: 3 files."


def test_a98_an_error_is_said_without_its_exception_class(tmp_path, monkeypatch):
    def broken(self, conn):
        raise RuntimeError("the index is locked")

    monkeypatch.setattr(Importer, "_copy_all", broken)
    src = tmp_path / "Drive"
    photos(src, 1)
    engine = run(tmp_path / "data", [src], tmp_path / "Archive")
    said = engine.job["message"]
    assert engine.job["phase"] == "failed"
    assert said == {"key": "The run stopped with an error: {why}",
                    "vars": {"why": "the index is locked"},
                    "text": "The run stopped with an error: the index is locked"}


def test_a99_a_cancelled_estimate_stops_at_the_next_folder(tmp_path, monkeypatch):
    src = tmp_path / "Drive"
    for n in range(40):
        folder = src / f"d{n:02}"
        folder.mkdir(parents=True)
        (folder / "tiny.jpg").write_bytes(b"\xff\xd8\xff" + b"\0" * 10)
    engine = Importer(tmp_path / "data")
    real = Importer._wanted
    looked = []

    def wanted(path, name, size, kinds, counters):
        looked.append(path)
        engine.cancel_estimate("t1")            # Cancel pressed after the first file
        return real(path, name, size, kinds, counters)

    monkeypatch.setattr(Importer, "_wanted", staticmethod(wanted))
    result = engine.estimate([str(src)], str(tmp_path / "Archive"), ["image"], "t1")
    assert result == {"ok": False, "cancelled": True}
    assert len(looked) == 1


def test_a100_a_sidecar_is_written_whole_or_not_at_all(tmp_path, monkeypatch):
    src_dir, out = tmp_path / "src", tmp_path / "Archive" / "2020"
    out.mkdir(parents=True)
    photo = noisy_jpeg(src_dir / "IMG_1.jpg", seed=1)
    sidecar = src_dir / "IMG_1.xmp"
    sidecar.write_text("<x:xmpmeta>" + "x" * 2000 + "</x:xmpmeta>")
    final = out / "IMG_1.jpg"
    shutil.copy2(photo, final)
    engine = Importer(tmp_path / "data")
    # A sidecar already there is never written over (A137): since this fix
    # one under the real name is always whole, and it may hold later edits.
    (out / "IMG_1.xmp").write_text("<x:xmp edited/>")
    engine._copy_sidecars(str(photo), str(final))
    assert (out / "IMG_1.xmp").read_text() == "<x:xmp edited/>"

    # A copy that fails part-way leaves nothing under the real name.
    (out / "IMG_1.xmp").unlink()

    def short(src, dst, *a, **k):
        with open(dst, "w") as f:
            f.write("<x:xm")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(importer.shutil, "copy2", short)
    engine._copy_sidecars(str(photo), str(final))
    assert not (out / "IMG_1.xmp").exists()
    assert not (out / "IMG_1.xmp.partial").exists()


def test_a101_an_export_to_a_drive_with_nothing_free_is_refused(library, usb, monkeypatch):
    monkeypatch.setattr(drives.shutil, "disk_usage",
                        lambda _p: SimpleNamespace(total=16 * 1024 ** 3, used=16 * 1024 ** 3,
                                                   free=0))
    state = export(library, usb)
    assert state["phase"] == "failed"
    assert state["message"]["key"] == "Not enough room on the drive: {need} needed, {free} free."
    assert state["copied"] == 0
    assert not list((Path(usb.path) / drives.EXPORT_FOLDER).rglob("*.jpg"))


def test_a102_packed_takeout_zips_are_counted_and_mentioned(tmp_path):
    src = tmp_path / "Downloads"
    photos(src, 1)
    (src / "takeout-20240101T000000Z-001.zip").write_bytes(b"PK\x03\x04" + b"\0" * 40000)
    (src / ".hidden.zip").write_bytes(b"PK\x03\x04")
    engine = Importer(tmp_path / "data")
    est = engine.estimate([str(src)], str(tmp_path / "Archive"), ["image"], "t")
    assert est["files"] == 1 and est["packed"] == 1
    done = run(tmp_path / "data", [src], tmp_path / "Archive")
    said = done.job["message"]
    keys = [part["key"] for part in said["more"]]
    packed = ("{packed} zip or other packed files were left alone: unpack them (a Google "
              "Takeout export, say) and import the folder.")
    assert packed in keys and packed in locale("ta")
    assert "1 zip or other packed files" in said["text"]


# --- the full disk (D4's "stop on ENOSPC") -------------------------------------------------


def test_a90_a_full_destination_stops_the_run_at_once(tmp_path, monkeypatch):
    def full(self, source, tmp):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Importer, "_copy_and_hash", full)
    monkeypatch.setattr(importer, "too_big", lambda dest, size: False)   # really full
    src = tmp_path / "Drive"
    photos(src, 4)
    engine = run(tmp_path / "data", [src], tmp_path / "Archive")
    assert engine.job["phase"] == "failed"
    assert engine.job["message"]["key"].startswith("The destination is full.")
    assert engine.job["processed"] == 0
    statuses = [r["status"] for r in rows(tmp_path / "data").values()]
    assert statuses == ["error"]               # the first, not every file after it

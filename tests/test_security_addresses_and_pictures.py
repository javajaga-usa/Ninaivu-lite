"""Security review of 2026-10-10: A144-A147."""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from PIL import Image

from ninaivu_lite import api_auth, db, media, phones
from ninaivu_lite import app as app_module
from ninaivu_lite.drives import Drive

from conftest import ids, sign_in

CAPTION = b"Amma, hospital, 12 Gandhi Street, Chennai"


def from_address(address):
    return {"environ_base": {"REMOTE_ADDR": address}}


@pytest.fixture()
def nobody_on_this_network(monkeypatch):
    monkeypatch.setattr(app_module, "_same_network", lambda ip: False)
    monkeypatch.delenv("NINAIVU_ALLOW_INTERNET", raising=False)
    app_module._internet_refused.clear()


# --- A144: IPv6 tunnels from the internet are not the home network ------------------------


@pytest.mark.parametrize("address", [
    "2001:0:4136:e378:8000:63bf:3fff:fdd2",     # Teredo
    "2002:0101:0101::1",                        # 6to4 for 1.1.1.1
    "2002:0808:0808::1",                        # 6to4 for 8.8.8.8
    "2001:db8::1",                              # documentation
])
def test_a144_teredo_and_6to4_are_refused(app, nobody_on_this_network, address):
    client = app.test_client()
    r = client.get("/api/auth/state", **from_address(address))
    assert r.status_code == 403
    assert r.json["error"] == "Ninaivu Lite answers only the home network."
    # Nor through a proxy or tunnel on this computer that names one.
    r = client.get("/api/auth/state", environ_base={"REMOTE_ADDR": "127.0.0.1",
                                                    "HTTP_X_FORWARDED_FOR": address})
    assert r.status_code == 403


def test_a144_6to4_of_a_home_address_is_judged_by_it(nobody_on_this_network):
    assert app_module.home_address("2002:c0a8:0114::1") is True      # 192.168.1.20
    assert app_module.home_address("2002:0101:0101::1") is False     # 1.1.1.1
    assert app_module.home_address("fd00::5") is True
    assert app_module.home_address("fe80::1") is True


# --- A145: no JPEG comment in what leaves the family -----------------------------------


def with_caption(path: Path) -> None:
    with Image.open(path) as img:
        exif = img.info.get("exif", b"")
        img.load()
        img.save(path, "JPEG", exif=exif, comment=CAPTION)


def test_a145_viewing_copy_and_profile_picture_drop_the_comment(tmp_path):
    source = tmp_path / "captioned.jpg"
    Image.new("RGB", (800, 600), (10, 120, 200)).save(source, "JPEG", comment=CAPTION)
    assert Image.open(source).info.get("comment") == CAPTION
    for data in (media.viewing_copy(str(source)), media.viewing_copy(str(source), rotation=90),
                 media.profile_picture(str(source))):
        info = Image.open(io.BytesIO(data)).info
        assert "comment" not in info and "exif" not in info


def test_a145_share_links_guests_and_avatars_carry_no_comment(app, library, family, guest):
    root, _ = library
    with_caption(root / "2019" / "beach.jpg")
    asset = ids(app)["beach.jpg"]
    conn = db.connect(app.config["LITE"].data_dir)
    with conn:
        conn.execute("UPDATE assets SET visibility = ? WHERE id = ?", (db.VIS_PUBLIC, asset))
    token = family.post("/api/shares", json={"scope": "asset", "target_id": asset}).json["token"]
    stranger = app.test_client()
    for client, url in ((stranger, f"/api/share/{token}/file/{asset}"),
                        (stranger, f"/api/share/{token}/preview/{asset}"),
                        (guest, f"/api/file/{asset}"),
                        (app.test_client(), f"/api/preview/{asset}")):
        r = client.get(url)
        assert r.status_code == 200, url
        info = Image.open(io.BytesIO(r.data)).info
        assert "comment" not in info and "exif" not in info, url
    picture = family.post("/api/me/avatar", json={"asset_id": asset}).json["avatar"]
    r = app.test_client().get(picture)
    assert r.status_code == 200 and "comment" not in Image.open(io.BytesIO(r.data)).info


def test_a145_viewing_copies_made_before_are_not_served(app, library, guest):
    """A copy made before the fix still has the comment in it: it is made anew."""
    root, data = library
    path = root / "2019" / "beach.jpg"
    with_caption(path)
    asset = ids(app)["beach.jpg"]
    conn = db.connect(data)
    with conn:
        conn.execute("UPDATE assets SET visibility = ? WHERE id = ?", (db.VIS_PUBLIC, asset))
    st = os.stat(path)
    old = Path(data) / "views" / f"{asset % 256:02x}" / f"{asset}-{st.st_size}-{int(st.st_mtime)}.jpg"
    old.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64)).save(old, "JPEG", comment=CAPTION)
    r = guest.get(f"/api/file/{asset}")
    info = Image.open(io.BytesIO(r.data))
    assert r.status_code == 200 and info.size != (64, 64) and "comment" not in info.info
    assert not old.exists()                     # replaced by the new copy


# --- A146: a picture whose photograph is Hidden now, or of a profile switched off -----------


def avatar_url(client, name):
    return next(p["avatar"] for p in client.get("/api/auth/profiles").json["profiles"]
                if p["name"] == name)


def test_a146_picture_goes_when_its_photograph_is_hidden(app, admin, family):
    asset = ids(app)["sunset.jpg"]
    picture = family.post("/api/me/avatar", json={"asset_id": asset}).json["avatar"]
    anyone = app.test_client()
    assert anyone.get(picture).status_code == 200
    assert anyone.get(picture).headers["Cache-Control"] == "no-cache"
    assert avatar_url(anyone, "Family Person") == picture
    assert admin.post("/api/visibility", json={"ids": [asset], "visibility": "hidden"}
                      ).status_code == 200
    assert anyone.get(picture).status_code == 404
    assert avatar_url(anyone, "Family Person") is None
    assert family.get("/api/me").json["avatar"] is None
    assert admin.get("/api/people").json["people"][1]["avatar"] is None
    # Family again: back on the sign-in screen.
    assert admin.post("/api/visibility", json={"ids": [asset], "visibility": "family"}
                      ).status_code == 200
    assert anyone.get(picture).status_code == 200
    assert avatar_url(anyone, "Family Person") == picture


def test_a146_picture_goes_when_its_photograph_is_gone(app, family):
    asset = ids(app)["sunset.jpg"]
    picture = family.post("/api/me/avatar", json={"asset_id": asset}).json["avatar"]
    conn = db.connect(app.config["LITE"].data_dir)
    with conn:
        conn.execute("UPDATE assets SET missing = 1 WHERE id = ?", (asset,))
    assert app.test_client().get(picture).status_code == 404


def test_a146_a_switched_off_profile_shows_no_picture(app, admin, family):
    asset = ids(app)["sunset.jpg"]
    me = family.get("/api/me").json
    picture = family.post("/api/me/avatar", json={"asset_id": asset}).json["avatar"]
    assert admin.post(f"/api/people/{me['id']}", json={"active": False}).status_code == 200
    assert app.test_client().get(picture).status_code == 404


def test_a146_older_pictures_without_a_record_are_still_shown(app, family):
    asset = ids(app)["sunset.jpg"]
    picture = family.post("/api/me/avatar", json={"asset_id": asset}).json["avatar"]
    records = list((Path(app.config["LITE"].data_dir) / "avatars").glob("*.jpg.source"))
    assert len(records) == 1 and records[0].read_text() == str(asset)
    records[0].unlink()                          # as a picture from before A146
    assert app.test_client().get(picture).status_code == 200


def test_a146_records_follow_the_pictures(app, admin, family):
    folder = Path(app.config["LITE"].data_dir) / "avatars"
    i = ids(app)
    family.post("/api/me/avatar", json={"asset_id": i["sunset.jpg"]})
    family.post("/api/me/avatar", json={"asset_id": i["beach.jpg"]})
    records = list(folder.glob("*.jpg.source"))
    assert len(records) == 1 and records[0].read_text() == str(i["beach.jpg"])
    assert Path(str(records[0]).removesuffix(".source")).is_file()
    family.delete("/api/me/avatar")
    assert not list(folder.glob("*.jpg.source"))
    other = app.test_client()
    person = sign_in(app, other, "family", username="thambi")
    other.post("/api/me/avatar", json={"asset_id": i["beach.jpg"]})
    assert admin.delete(f"/api/people/{person.id}/avatar").status_code == 200
    assert not list(folder.glob("*.jpg.source"))
    assert api_auth.avatar_shown(None) is False


# --- A147: a phone's names never reach outside its own copy ----------------------------------


def test_a147_fetch_script_refuses_dot_names():
    assert "Trim(' .')" in phones.FETCH_SCRIPT


def test_a147_failed_list_never_removes_outside_the_mirror(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    phone = Drive("ph1", "::{20D04FE0}\\\\?\\usb#vid_18d1", "Pixel 7", 0, 0,
                  kind="phone", shell=True)
    mirror = Path(phones.mirror_for(str(data), phone))
    (mirror / "DCIM").mkdir(parents=True)
    cut_short = mirror / "DCIM" / "cut.jpg"
    cut_short.write_bytes(b"half")
    outside = tmp_path / "Photos" / "precious.jpg"
    outside.parent.mkdir()
    outside.write_bytes(b"the family's photograph")
    escape = os.path.relpath(outside, mirror)
    listing = Path(phones.imported_list(str(data), phone, "failed"))
    listing.parent.mkdir(parents=True, exist_ok=True)
    listing.write_text(f"{os.path.join('DCIM', 'cut.jpg')}\n{escape}\n{outside}\n.\n",
                       encoding="utf-8")

    class Busy:
        running = False

        def start(self, *_args, **_kwargs):
            raise ValueError("busy")

    job = phones.PhoneImport()
    monkeypatch.setattr(job, "_fetch", lambda drive, mirror: True)
    job._run(phone, str(mirror), str(tmp_path / "archive"), Busy(), str(data))
    assert not cut_short.exists()                       # inside the copy: removed
    assert outside.read_bytes() == b"the family's photograph"
    assert mirror.is_dir()

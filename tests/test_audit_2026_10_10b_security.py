"""Security audit of 2026-10-10 (after the rotation pull request): links in
the photo folders, request bodies and search parameters."""

from __future__ import annotations

import json
import os
import zipfile
from io import BytesIO

import pytest

from ninaivu_lite import db
from ninaivu_lite.common import JSON_MAX_BYTES

from conftest import ids, make_jpeg


def link(target, where) -> None:
    try:
        os.symlink(target, where, target_is_directory=os.path.isdir(target))
    except (OSError, NotImplementedError) as exc:     # Windows without the right to
        pytest.skip(f"symbolic links cannot be made here: {exc}")


def rescan(app) -> None:
    app.config["SCANNER"].scan_once(db.connect(app.config["LITE"].data_dir))


def test_a_link_named_like_a_photo_to_any_other_file_is_not_indexed(app, library, family, tmp_path):
    root, _ = library
    secret = tmp_path / "id_rsa"
    secret.write_text("PRIVATE KEY")
    link(str(secret), str(root / "key.jpg"))
    rescan(app)
    assert "key.jpg" not in ids(app)


def test_a_photo_made_a_link_after_indexing_is_not_served(app, library, family, tmp_path):
    root, _ = library
    secret = tmp_path / "id_rsa"
    secret.write_text("PRIVATE KEY")
    asset = ids(app)["sunset.jpg"]
    photo = root / "2019" / "sunset.jpg"
    photo.unlink()
    link(str(secret), str(photo))
    for url in (f"/api/download/{asset}", f"/api/file/{asset}"):
        r = family.get(url)
        assert r.status_code == 404, url
        assert b"PRIVATE KEY" not in r.get_data()
    r = family.get(f"/api/download/zip?ids={asset},{ids(app)['beach.jpg']}")
    assert r.status_code == 200
    archive = zipfile.ZipFile(BytesIO(r.get_data()))
    assert b"PRIVATE KEY" not in b"".join(archive.read(n) for n in archive.namelist())
    assert "NOT INCLUDED.txt" in archive.namelist()


def test_a_linked_folder_into_the_data_folder_is_not_walked(app, library, admin, family):
    root, data = library
    hidden = ids(app)["beach.jpg"]
    assert admin.post("/api/visibility", json={"ids": [hidden], "visibility": "hidden"}).status_code == 200
    assert family.get(f"/api/thumb/{hidden}").status_code == 404
    assert admin.get(f"/api/thumb/{hidden}?s=640").status_code == 200    # made now
    link(str(data / "thumbs"), str(root / "pictures"))
    rescan(app)
    names = ids(app)
    assert not any(n.endswith(".webp") for n in names), names
    # The index itself, linked under a photograph's name, is not indexed either.
    link(str(data / db.DB_FILE), str(root / "index.jpg"))
    rescan(app)
    assert "index.jpg" not in ids(app)


def test_a_linked_folder_of_photographs_still_counts(app, library, family, tmp_path):
    root, _ = library
    elsewhere = tmp_path / "Elsewhere"
    make_jpeg(elsewhere / "linked.jpg", "2018:01:01 10:00:00")
    link(str(elsewhere), str(root / "linked"))
    make_jpeg(tmp_path / "single.jpg", "2018:01:02 10:00:00")
    link(str(tmp_path / "single.jpg"), str(root / "single.jpg"))
    rescan(app)
    names = ids(app)
    assert "linked.jpg" in names and "single.jpg" in names
    assert family.get(f"/api/download/{names['linked.jpg']}").status_code == 200
    assert family.get(f"/api/download/{names['single.jpg']}").status_code == 200


def big_json() -> str:
    return json.dumps({"password": "x", "pad": [{}] * (JSON_MAX_BYTES // 3)})


def test_a_share_password_body_over_the_limit_is_refused_unread(app, family):
    target = ids(app)["beach.jpg"]
    token = family.post("/api/shares", json={"scope": "asset", "target_id": target,
                                             "password": "open sesame"}).get_json()["token"]
    stranger = app.test_client()
    r = stranger.post(f"/api/share/{token}/unlock", data=big_json(),
                      content_type="application/json")
    assert r.status_code == 413
    assert stranger.post(f"/api/share/{token}/unlock",
                         json={"password": "open sesame"}).status_code == 200


def test_album_bodies_over_the_limit_are_refused(app, family):
    r = family.post("/api/albums", data=big_json(), content_type="application/json")
    assert r.status_code == 413
    album = family.post("/api/albums", json={"name": "Trip"}).get_json()["id"]
    r = family.patch(f"/api/albums/{album}", data=big_json(), content_type="application/json")
    assert r.status_code == 413


def test_deeply_nested_json_is_a_400_not_a_server_error(app, family):
    nested = "[" * 100_000 + "]" * 100_000
    stranger = app.test_client()
    r = stranger.post("/api/auth/login", data=nested, content_type="application/json")
    assert r.status_code == 400
    r = family.post("/api/albums", data=nested, content_type="application/json")
    assert r.status_code == 400


def test_very_long_searches_are_answered(app, caplog):
    looker = app.test_client()          # Just looking: anyone on the network
    for q in (" ".join(["a"] * 1000), "a" * 60_000):
        for url in ("/api/segments", "/api/months"):
            r = looker.get(url, query_string={"q": q})
            assert r.status_code == 200, (url, len(q))
    assert "unexpected error" not in caplog.text


def test_huge_parameters_are_answered_but_not_kept(app):
    looker = app.test_client()
    remembered = app.config["REMEMBERED"]
    pages = app.config["REMEMBERED_PAGES"]
    assert looker.get("/api/months", query_string={"folder": "2019"}).status_code == 200
    assert looker.get("/api/segments", query_string={"folder": "2019"}).status_code == 200
    kept, kept_pages = len(remembered._answers), len(pages._answers)
    assert kept and kept_pages        # small questions are still remembered
    for n in range(5):
        big = f"{n}" + "x" * 250_000
        assert looker.get("/api/months", query_string={"folder": big}).status_code == 200
        assert looker.get("/api/segments", query_string={"folder": big}).status_code == 200
    assert len(remembered._answers) == kept
    assert len(pages._answers) == kept_pages


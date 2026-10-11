"""Gallery and console findings of the 2026-10-10 audit (A143, A148-A153)."""
import re
from pathlib import Path

from ninaivu_lite import auth, create_app, db, media
from ninaivu_lite.config import Config
from ninaivu_lite.scanner import Scanner

from conftest import ids, make_jpeg, sign_in

STATIC = Path(__file__).resolve().parent.parent / "ninaivu_lite" / "static" / "js"
BIG = "99999999999999999999"


def _setup(tmp_path, folders):
    data = tmp_path / "data"
    cfg = Config(data_dir=str(data), folders=[str(f) for f in folders], active=str(folders[0]))
    scanner = Scanner(cfg.data_dir, cfg.folders)
    scanner.scan_once(db.connect(data))
    app = create_app(cfg, scanner=scanner, addresses=["192.168.1.20"])
    auth.create_user(db.connect(data), "appa", password="admin passphrase", name="Appa",
                     role="admin")
    admin = app.test_client()
    sign_in(app, admin, "admin")
    return data, cfg, scanner, app, admin


def test_a148_a_folder_removed_during_its_first_scan_stays_out(tmp_path, monkeypatch):
    a, b = tmp_path / "A", tmp_path / "Private"
    make_jpeg(a / "a1.jpg", "2020:01:01 10:00:00")
    for k in range(5):
        make_jpeg(b / f"b{k}.jpg", "2021:01:01 10:00:00")
    data, cfg, scanner, app, admin = _setup(tmp_path, [a])
    assert admin.post("/api/library/root", json={"path": str(b)}).status_code == 200
    real = Scanner._files

    def files(self, root, failed=None, under=None):
        for n, item in enumerate(real(self, root, failed, under)):
            if root == str(b) and n == 1:     # "wrong folder": removed mid-scan
                assert admin.delete(f"/api/admin/libraries?path={b}").status_code == 200
            yield item

    monkeypatch.setattr(Scanner, "_files", files)
    scanner.scan_once(db.connect(data))
    monkeypatch.setattr(Scanner, "_files", real)
    scanner.scan_once(db.connect(data))
    assert admin.get("/api/segments").json["total"] == 1


def test_a149_a_folder_hidden_while_the_scan_adds_to_it_hides_them_all(tmp_path, monkeypatch):
    a = tmp_path / "A"
    make_jpeg(a / "private" / "p0.jpg", "2020:01:01 10:00:00")
    data, cfg, scanner, app, admin = _setup(tmp_path, [a])
    for k in range(1, 6):
        make_jpeg(a / "private" / f"p{k}.jpg", "2020:01:02 10:00:00")
    real, seen = media.describe, []

    def describe(path, name, kind, st):
        seen.append(name)
        if len(seen) == 2:
            r = admin.post("/api/visibility/folder",
                           json={"folder": "private", "visibility": "hidden"})
            assert r.status_code == 200, r.json
        return real(path, name, kind, st)

    monkeypatch.setattr(media, "describe", describe)
    scanner.scan_once(db.connect(data))
    levels = [r[0] for r in db.connect(data).execute("SELECT visibility FROM assets")]
    assert levels and all(v == db.VIS_HIDDEN for v in levels)


def test_a150_undo_puts_later_photographs_back_under_the_rules_as_they_are(app, admin, library):
    root, data = library
    r = admin.post("/api/visibility/folder",
                   json={"folder": "2019", "visibility": "public", "confirm": True})
    assert r.status_code == 200, r.json
    make_jpeg(root / "2019" / "later.jpg", "2019:05:13 10:00:00")
    app.config["SCANNER"].scan_once(db.connect(data))
    assert admin.post("/api/visibility/undo", json={}).status_code == 200
    row = db.connect(data).execute(
        "SELECT visibility, vis_source FROM assets WHERE name = 'later.jpg'").fetchone()
    assert tuple(row) == (db.VIS_FAMILY, "default")


def test_a151_skipped_counts_what_was_sent(app, family):
    i = ids(app)
    sent = [i["beach.jpg"], *range(900000, 906000)]
    r = family.post("/api/assets/bulk", json={"ids": sent, "favorite": True})
    assert r.json["updated"] == 1 and r.json["skipped"] == len(sent) - 1


def test_a151_large_selections_go_in_pieces():
    api = (STATIC / "api.js").read_text(encoding="utf-8")
    accounts = (STATIC / "accounts.js").read_text(encoding="utf-8")
    assert "IDS_PER_REQUEST = 5000" in api
    for call in ("bulk:", "albumAdd:", "albumRemove:"):
        line = next(x for x in api.splitlines() if x.strip().startswith(call))
        assert "inPieces(" in line, line
    assert "setVisibility: (ids, visibility) => inPieces(" in accounts


def test_a152_ids_too_large_for_the_index_are_not_server_errors(app, admin):
    bad = []
    for rule in app.url_map.iter_rules():
        if "<int:" not in rule.rule:
            continue
        url = re.sub(r"<(path:)?\w+>", "abc", re.sub(r"<int:\w+>", BIG, rule.rule))
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            if admin.open(url, method=method, json={} if method != "GET" else None) \
                    .status_code >= 500:
                bad.append((method, rule.rule))
    anon = app.test_client()
    assert anon.get(f"/api/avatar/{BIG}").status_code == 404
    assert anon.post("/api/auth/enter", json={"id": 2 ** 70}).status_code == 400
    assert admin.get(f"/api/segments?offset={BIG}").status_code == 200
    asset = ids(app)["beach.jpg"]
    assert admin.post(f"/api/asset/{asset}/rotate", json={"rotation": 1e999}).status_code == 400
    assert not bad


def test_a153_a_full_cache_lets_only_the_oldest_answer_go(app, library):
    _, data = library
    cache = db.Remembered(data, limit=3)
    for key in "abc":
        cache.get(key, lambda k=key: k)
    cache.get("a", lambda: "recomputed")          # a is now the newest
    cache.get("d", lambda: "d")                   # b, the oldest, gives way
    assert cache.get("a", lambda: "again") == "a"
    assert cache.get("b", lambda: "fresh") == "fresh"
    cache.close()


def test_a143_an_unreadable_folder_is_counted_once_however_often_it_is_rescanned(
        tmp_path, monkeypatch):
    a = tmp_path / "A"
    make_jpeg(a / "in" / "x.jpg", "2020:01:01 10:00:00")
    data = tmp_path / "data"
    scanner = Scanner(str(data), [str(a)])
    real = Scanner._files

    def files(self, root, failed=None, under=None):
        if failed is not None:
            failed.add("in/locked") if isinstance(failed, set) else failed.append("in/locked")
        yield from real(self, root, failed, under)

    monkeypatch.setattr(Scanner, "_files", files)
    scanner.scan_once(db.connect(data))
    assert scanner.status["unreadable"] == 1
    for _ in range(3):
        scanner.scan_once(db.connect(data), within=[str(a / "in")])
    assert scanner.status["unreadable"] == 1

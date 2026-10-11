"""Speed and memory on large libraries and slow computers (audit of 2026-10-11):
the copies queries start from the sizes more than one file has, a walk pairs
Live Photos only where files changed and reads only the rows of the folders it
walks, a HEIC (or any photograph read whole) is read once for both thumbnails,
and the memory a large picture took is given back."""

from __future__ import annotations

import random
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from conftest import ids, make_jpeg
from PIL import Image
from test_gallery_api import rescan
from test_live_photos_duplicates import conn_of, make_clip

from ninaivu_lite import copies, media, scanner

# --- copies: only files whose size another file has are looked at -----------------------

#: The queries as they were, to check the new ones give the same answers.
OLD_GROUPS = f"""SELECT size, content_hash, COUNT(*) AS n FROM assets
                 WHERE content_hash IS NOT NULL AND content_hash != '' AND {copies.SHOWN}
                 GROUP BY size, content_hash HAVING n > 1"""
OLD_TO_CONTENT = f"""SELECT a.id FROM assets a JOIN folders f ON f.id = a.folder_id
    WHERE {copies.SHOWN_A} AND a.content_hash IS NULL
      AND a.quick_hash IS NOT NULL AND a.quick_hash != ''
      AND (a.size, a.quick_hash) IN (
          SELECT size, quick_hash FROM assets
          WHERE {copies.SHOWN} AND quick_hash IS NOT NULL AND quick_hash != ''
          GROUP BY size, quick_hash HAVING COUNT(*) > 1)
    ORDER BY a.size DESC, a.id"""
OLD_PENDING = f"""SELECT COUNT(*) FROM assets WHERE {copies.SHOWN} AND (
        (quick_hash IS NULL AND size IN (
            SELECT size FROM assets WHERE {copies.SHOWN} AND size > 0
            GROUP BY size HAVING COUNT(*) > 1))
        OR (content_hash IS NULL AND quick_hash != '' AND (size, quick_hash) IN (
            SELECT size, quick_hash FROM assets
            WHERE {copies.SHOWN} AND quick_hash IS NOT NULL AND quick_hash != ''
            GROUP BY size, quick_hash HAVING COUNT(*) > 1)))"""


def _library_of(app, count: int, seed: int = 7) -> sqlite3.Connection:
    """*count* rows, most of a size of their own (as photographs are), some
    sharing a size, some of those a quick hash, some of those every byte;
    a few missing, a few a Live Photo's clip, a few unreadable."""
    conn = conn_of(app)
    folder = conn.execute("SELECT id FROM folders").fetchone()[0]
    rnd = random.Random(seed)
    rows = []
    for n in range(count):
        size = rnd.randrange(1_000_000, 50_000_000)
        quick = content = None
        if n % 40 == 0:
            size = 4242 + n % 7              # a size shared by many
            quick = rnd.choice(["qa", "qb", "", None])
            if quick and n % 80 == 0:
                content = rnd.choice(["ha", "hb", None])
        rows.append((folder, f"d{n % 30}", f"f{n}.jpg", "jpg", "picture", size, 1.0, 1.0,
                     "2020-01-01", "mtime", quick, content, int(n % 97 == 0),
                     5 if n % 89 == 0 else None))
    with conn:
        conn.executemany(
            "INSERT INTO assets (folder_id, dir, name, ext, kind, size, mtime, captured_at, "
            "date_key, date_source, quick_hash, content_hash, missing, live_of) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return conn


def _plans(conn: sqlite3.Connection, work) -> list[str]:
    """The query plan of every statement *work* runs, one line per step."""
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    try:
        work()
    finally:
        conn.set_trace_callback(None)
    lines = []
    for sql in statements:
        if sql.lstrip().upper().startswith("SELECT"):
            lines += [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql)]
    return lines


def test_the_copies_queries_answer_as_before(app):
    conn = _library_of(app, 4000)
    read = lambda sql, params: [tuple(r) for r in conn.execute(sql, params)]  # noqa: E731
    groups = sorted(tuple(r) for r in conn.execute(copies.GROUPS_SQL))
    assert groups == sorted(tuple(r) for r in conn.execute(OLD_GROUPS))
    assert groups
    assert [r["id"] for r in copies.to_content_hash(conn, limit=10_000)] == \
        [r[0] for r in conn.execute(OLD_TO_CONTENT)]
    assert copies.totals(read)["pending"] == conn.execute(OLD_PENDING).fetchone()[0] > 0


def test_the_copies_queries_do_not_read_every_row(app):
    """Opening Library → Duplicates, and every scan, read every row of the
    index to find the few files whose size another has (100,000 rows: 0.6 s
    for the page here, several seconds on a Raspberry Pi)."""
    conn = _library_of(app, 2000)
    read = lambda sql, params: [tuple(r) for r in conn.execute(sql, params)]  # noqa: E731
    lines = _plans(conn, lambda: (copies.groups(conn, 0, 20), copies.totals(read),
                                  copies.to_content_hash(conn), copies.to_quick_hash(conn)))
    reads = [line for line in lines
             if re.match(r"(SCAN|SEARCH) (assets|a)\b", line) and "COVERING INDEX" not in line]
    assert reads
    # Every step that reads rows of the table reads only those of one size or
    # one hash: the walks of every row (by missing=? and live_of alone) are gone.
    assert all("size=?" in line or "content_hash=?" in line for line in reads), reads


# --- Live Photos: paired where files changed ------------------------------------------------


def test_a_walk_pairs_live_photos_only_where_files_changed(app, library, monkeypatch):
    root, _ = library
    make_jpeg(root / "iphone" / "IMG_1.JPG", "2024:01:15 10:00:00")
    make_clip(root / "iphone" / "IMG_1.MOV", datetime(2024, 1, 15, 4, 30), 2.5)
    rescan(app)                       # the first walk after start: the whole library
    s = app.config["SCANNER"]
    asked = []
    pair = s._pair_live
    monkeypatch.setattr(s, "_pair_live", lambda conn, folders=None: asked.append(folders)
                        or pair(conn, folders))
    rescan(app)
    assert asked == []                # nothing changed: nothing paired
    make_jpeg(root / "trip" / "IMG_2.JPG", "2024:02:01 10:00:00")
    make_clip(root / "trip" / "IMG_2.MOV", datetime(2024, 2, 1, 4, 30), 2.0)
    rescan(app)
    folder = conn_of(app).execute("SELECT id FROM folders").fetchone()[0]
    assert asked == [{(folder, "trip")}]
    found = ids(app)
    conn = conn_of(app)
    for still, clip in (("IMG_1.JPG", "IMG_1.MOV"), ("IMG_2.JPG", "IMG_2.MOV")):
        assert conn.execute("SELECT live_id FROM assets WHERE id = ?",
                            (found[still],)).fetchone()[0] == found[clip]
    # The still taken away: its folder is looked at again and the clip is a video again.
    (root / "trip" / "IMG_2.JPG").unlink()
    rescan(app)
    assert asked[-1] == {(folder, "trip")}
    assert conn.execute("SELECT live_of FROM assets WHERE id = ?",
                        (found["IMG_2.MOV"],)).fetchone()[0] is None
    assert conn.execute("SELECT live_id FROM assets WHERE id = ?",
                        (found["IMG_1.JPG"],)).fetchone()[0] == found["IMG_1.MOV"]


def test_many_folders_changed_pairs_the_whole_library(app, library, monkeypatch):
    root, _ = library
    rescan(app)
    monkeypatch.setattr(scanner, "PAIR_FOLDERS_AT_MOST", 2)
    for n in range(3):
        make_jpeg(root / f"f{n}" / "IMG_9.JPG", f"2024:03:0{n + 1} 10:00:00")
    s = app.config["SCANNER"]
    asked = []
    pair = s._pair_live
    monkeypatch.setattr(s, "_pair_live", lambda conn, folders=None: asked.append(folders)
                        or pair(conn, folders))
    rescan(app)
    assert asked == [None]


def test_an_import_walk_of_one_folder_leaves_its_neighbours_alone(app, library):
    """Only that folder's rows are read; folders whose names merely start the
    same way ("a b", "a-b", "ab") are not inside it."""
    root, _ = library
    for folder in ("a", "a/b", "a b", "a-b", "ab", "a0"):
        make_jpeg(root / folder / "x.jpg", "2024:01:01 10:00:00")
    rescan(app)
    (root / "a" / "b" / "x.jpg").unlink()
    s = app.config["SCANNER"]
    s.scan_once(conn_of(app), within=[str(root / "a")])
    rows = {r[0]: r[1] for r in conn_of(app).execute(
        "SELECT dir, missing FROM assets WHERE name = 'x.jpg'")}
    assert rows == {"a": 0, "a/b": 1, "a b": 0, "a-b": 0, "ab": 0, "a0": 0}


# --- photographs read whole: both thumbnails from one reading -------------------------------


def test_a_photo_read_whole_is_read_once_for_both_thumbnails(app, library, monkeypatch):
    """A HEIC (here a PNG, read whole the same way) was read in the small
    pass and again in the large one: 1.3 s for an iPhone photograph here
    where 0.7 s does both. A JPEG is read at a reduced size for each."""
    root, _ = library
    Image.new("RGB", (900, 600), (10, 120, 200)).save(root / "screen.png")
    make_jpeg(root / "cam.jpg", "2024:05:01 10:00:00", size=(900, 600))
    made = []
    real = media.make_thumbnails

    def spy(path, kind, thumbs_dir, asset_id, sizes=("s", "l"), *args, **kwargs):
        made.append((Path(path).name, tuple(sizes)))
        return real(path, kind, thumbs_dir, asset_id, sizes, *args, **kwargs)

    monkeypatch.setattr(media, "make_thumbnails", spy)
    rescan(app)
    assert [m for m in made if m[0] == "screen.png"] == [("screen.png", ("s", "l"))]
    assert sorted(m for m in made if m[0] == "cam.jpg") == [("cam.jpg", ("l",)), ("cam.jpg", ("s",))]
    found = ids(app)
    row = conn_of(app).execute("SELECT thumb, large FROM assets WHERE id = ?",
                               (found["screen.png"],)).fetchone()
    assert tuple(row) == (1, 1)
    for size in ("s", "l"):
        assert media.thumb_path(app.config["SCANNER"].thumbs_dir, found["screen.png"],
                                size).is_file()


def test_asking_for_the_big_thumbnail_does_not_make_the_small_one_again(app, library,
                                                                        monkeypatch):
    """The small one is current and cached by browsers for a year under its
    version: only the big one is made, and the version stays."""
    root, _ = library
    Image.new("RGB", (900, 600), (10, 120, 200)).save(root / "screen.png")
    rescan(app)
    conn = conn_of(app)
    asset = ids(app)["screen.png"]
    s = app.config["SCANNER"]
    media.thumb_path(s.thumbs_dir, asset, "l").unlink()
    with conn:
        conn.execute("UPDATE assets SET large = 0 WHERE id = ?", (asset,))
    before = conn.execute("SELECT thumb_v FROM assets WHERE id = ?", (asset,)).fetchone()[0]
    made = []
    real = media.make_thumbnails
    monkeypatch.setattr(media, "make_thumbnails", lambda path, kind, d, i, sizes=("s", "l"),
                        *a, **k: made.append(tuple(sizes)) or real(path, kind, d, i, sizes, *a, **k))
    assert s.thumbnail_now(conn, asset, "l")
    assert made == [("l",)]
    assert conn.execute("SELECT thumb_v FROM assets WHERE id = ?", (asset,)).fetchone()[0] == before


def test_memory_a_large_picture_took_is_given_back(tmp_path, monkeypatch):
    trimmed = []

    class Libc:
        def malloc_trim(self, pad):
            trimmed.append(pad)
            return 1

    monkeypatch.setattr(media, "_LIBC", Libc())
    picture = tmp_path / "big.png"
    Image.new("RGB", (400, 300), (1, 2, 3)).save(picture)
    out = tmp_path / "thumbs"
    assert media.make_thumbnails(str(picture), "picture", out, 1, ("s",))[0]
    assert trimmed == []                  # an ordinary one: nothing to give back
    monkeypatch.setattr(media, "HEAVY_PIXELS", 100_000)
    assert media.make_thumbnails(str(picture), "picture", out, 2, ("s",))[0]
    assert trimmed == [0]


def test_giving_memory_back_never_fails(monkeypatch):
    class Broken:
        def malloc_trim(self, pad):
            raise OSError("no")

    monkeypatch.setattr(media, "_LIBC", Broken())
    media._give_back_memory()
    monkeypatch.setattr(media, "_LIBC", None)
    media._give_back_memory()


def test_a_photo_read_again_counts_as_changed_for_pairing(app, library, monkeypatch):
    """A HEIC indexed before this computer could read it is read again after a
    start; its folder is paired again with the rest of what changed."""
    root, _ = library
    path = root / "iphone" / "IMG_5.HEIC"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 300), (10, 200, 30)).save(path, "HEIF")
    make_clip(root / "iphone" / "IMG_5.MOV", datetime(2024, 1, 15, 4, 30), 2.5)
    real = Image.open

    def no_heif(fp, *args, **kwargs):
        if str(fp).lower().endswith(".heic"):
            raise OSError("cannot identify image file")
        return real(fp, *args, **kwargs)

    monkeypatch.setattr(media.Image, "open", no_heif)
    rescan(app)
    monkeypatch.setattr(media.Image, "open", real)
    s = app.config["SCANNER"]
    s._reread = set()                 # as after a start, with the library paired since
    asked = []
    pair = s._pair_live
    monkeypatch.setattr(s, "_pair_live", lambda conn, folders=None: asked.append(folders)
                        or pair(conn, folders))
    rescan(app)
    folder = conn_of(app).execute("SELECT id FROM folders").fetchone()[0]
    assert asked == [{(folder, "iphone")}]
    found = ids(app)
    row = conn_of(app).execute("SELECT error, live_id FROM assets WHERE id = ?",
                               (found["IMG_5.HEIC"],)).fetchone()
    assert row["error"] is None and row["live_id"] == found["IMG_5.MOV"]

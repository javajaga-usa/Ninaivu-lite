"""Data safety (2026-10-11-b audit): a damaged index says how to get back."""

from __future__ import annotations

import os
from pathlib import Path

from test_install_backup_restore import clean_logging  # noqa: F401 — a fixture

from ninaivu_lite import __main__ as cli
from ninaivu_lite import auth, backups, db
from ninaivu_lite.config import Config


def damage(path) -> None:
    """Pages past the first overwritten, as a failing disk leaves them."""
    data = bytearray(path.read_bytes())
    for at in range(4096 * 2, len(data) - 1024, 4096):
        data[at:at + 512] = b"\xa5" * 512
    path.write_bytes(bytes(data))


def test_a_damaged_index_names_the_backup_and_restore_brings_it_back(
        tmp_path, capsys, clean_logging):  # noqa: F811
    data, photos = tmp_path / "data", tmp_path / "Photos"
    photos.mkdir()
    Config(data_dir=str(data), folders=[str(photos)], active=str(photos),
           first_day_done=True).save()
    conn = db.connect(data)
    auth.create_user(conn, "appa", password="admin passphrase", name="Appa", role="admin")
    with conn:
        conn.executemany("INSERT INTO folders (path) VALUES (?)",
                         [(f"/media/old-drive-{n}/" + "x" * 300,) for n in range(400)])
    conn.close()
    zipped = backups.daily(data)
    conn = db.connect(data)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    damage(data / db.DB_FILE)

    assert cli.main(["--data", str(data), "--no-browser"]) == 2
    said = capsys.readouterr().err
    assert "Traceback" not in said
    assert "is damaged" in said and "your photos are not in it" in said
    assert f'--restore "{zipped}"' in said
    log = (data / "logs" / "ninaivu-lite.log").read_text(encoding="utf-8")
    assert "not starting" in log and "is damaged" in log
    # Nothing holds the damaged file open: on Windows a handle left open kept
    # --restore from setting it aside.
    fds = Path("/proc/self/fd")
    if fds.is_dir():
        held = [os.readlink(fd) for fd in fds.iterdir() if fd.is_symlink()]
        assert str(data / db.DB_FILE) not in held, held

    # The command it gives puts the index back.
    assert cli.main(["--data", str(data), "--restore", str(zipped)]) == 0
    conn = db.connect(data)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert auth.get_user_by_name(conn, "appa") is not None
    finally:
        conn.close()

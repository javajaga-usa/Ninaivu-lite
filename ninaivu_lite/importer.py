"""Bringing old drives, cards and backup folders into one archive.

Taken from Ninaivu's archive engine and cut to the part a household uses:

* **Many sources, one archive.** Every file is filed as ``YYYY/MM/DD`` by the
  date it was taken (EXIF, the video's own header, a Google Takeout sidecar,
  the file name, a dated folder, then the file's timestamp); a file with no
  believable date goes to ``Unknown-Date``.
* **Sources are only ever read.** Nothing in them is written, renamed or
  deleted, and the bytes are copied unchanged.
* **Every copy is checked.** The file is hashed (SHA-256) as it is read,
  written to a temporary name, renamed into place, then read back from the
  archive and hashed again. A copy that does not match is removed and
  reported.
* **Duplicates are found by content** and left where they are, logged.
  The same name with different bytes gets ``_1``, ``_2``.
* **Start is also Resume.** Progress lives in the index, so an interrupted
  run carries on where it stopped. A dry run decides everything and writes
  nothing; an audit re-reads every archived file and checks its hash.

One background thread, the standard library and Pillow. No pacing, no
drive-health sampling, no classifiers: those stay in Ninaivu.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any

from . import db, parallel
from .import_plan import (  # noqa: F401 — the importer's names, where they always were
    ARCHIVE_NAME, CHUNK, IGNORE_DIRS, KINDS, LIVE_CLIP_EXTS, LIVE_STILL_EXTS,
    MARKER, MIN_BYTES, MODES, NEVER_MEDIA_EXTS, PACKED_EXTS, PARTIAL_DIR, PLAN, SHOW_EVERY,
    SIDECAR_EXTS, TERMINAL, UNDATED, _say, Cancelled, capture_date, clean_kinds, clean_sources,
    default_destination, free_space, Gate, hash_file, human_size, Incomplete, is_sidecar,
    is_within, kind_by_extension, norm, notices, Problem, publish, resolve_destination,
    said_text, sniff_media, still_done, stored_said, target_folder, too_big, validate,
)
from .import_copy import Copying
from .import_walk import Walking

log = logging.getLogger(__name__)


# --- the engine -----------------------------------------------------------------------


class Importer(Walking, Copying):
    """One run at a time, in one background thread."""

    def __init__(self, data_dir: str | os.PathLike) -> None:
        self.data_dir = str(data_dir)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.gate = Gate()
        self.job: dict[str, Any] = {}
        self._estimates: dict[str, dict[str, Any]] = {}
        #: Called with the destination as files land and when a run ends, so
        #: the library can look at them (set by the app).
        self.on_files = None

    # -- lifecycle --------------------------------------------------------------

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, sources: list[str], destination: str, kinds: list[str], mode: str,
              library: list[str] = ()) -> None:
        """Begin a run. Raises ValueError with a sentence when it cannot.
        *library* is the library's folders: never walked as a source (their
        photographs are in the gallery already)."""
        with self._lock:
            if self.running:
                raise ValueError("A run is already going. Stop it first.")
        self.forget_estimates()
        with self._lock:
            if self.running:
                raise ValueError("A run is already going. Stop it first.")
            self.gate = Gate()
            self.job = {
                "mode": mode, "sources": list(sources), "destination": destination,
                "kinds": list(kinds), "phase": "counting", "message": "",
                "processed": 0, "stepped_over": 0, "total_files": 0, "total_bytes": 0,
                "bytes_copied": 0, "started_at": time.time(), "ended_at": None,
                "fresh_started": None, "unreadable": 0, "job_id": None, "plan_aside": {},
                "library": [f for f in library if f], "packed": 0, "too_small": 0,
            }
            self._thread = threading.Thread(target=self._run, name="importer", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self.gate.cancel()

    def pause(self) -> None:
        if self.running:
            self.gate.pause()

    def resume(self) -> None:
        self.gate.resume()

    def wait(self, timeout: float = 60) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _set(self, **values: Any) -> None:
        with self._lock:
            self.job.update(values)

    def progress(self) -> dict[str, Any]:
        with self._lock:
            j = dict(self.job)
        running = self.running
        eta = None
        if running and j.get("phase") in ("copying", "verifying") and j.get("fresh_started"):
            fresh = j["processed"] - j["stepped_over"]
            left = j["total_files"] - j["processed"]
            elapsed = time.time() - j["fresh_started"]
            if fresh >= 10 and elapsed > 2:
                eta = int(left * elapsed / fresh)
        return {
            "is_scanning": running,
            "is_paused": running and self.gate.paused,
            "job_mode": j.get("mode"),
            "phase": j.get("phase"),
            "job_message": said_text(j.get("message")),
            "job_said": j.get("message") or None,
            "destination": j.get("destination", ""),
            "processed": j.get("processed", 0),
            "stepped_over": j.get("stepped_over", 0),
            "total_files": j.get("total_files", 0),
            "total_bytes": j.get("total_bytes", 0),
            "bytes_copied": j.get("bytes_copied", 0),
            "eta_seconds": eta,
            "is_resume": bool(j.get("stepped_over")),
        }


    # -- one run --------------------------------------------------------------------

    def _run(self) -> None:
        parallel.background()      # the gallery first; the import gets the rest
        try:
            conn = db.connect(self.data_dir)
        except Exception as exc:  # noqa: BLE001 — said on the console, not left "counting"
            log.exception("import could not open the index")
            self._set(phase="failed", ended_at=time.time(),
                      message=_say("The run stopped with an error: {why}", why=str(exc)))
            return
        job_id = None
        try:
            job_id = self._begin(conn)
            if self.job["mode"] == "verify":
                self._verify(conn)
            else:
                self._copy_all(conn)
            self._finish(conn, job_id, "completed")
        except Cancelled:
            self._finish(conn, job_id, "stopped")
        except Incomplete as exc:
            self._set(message=exc.said)
            self._finish(conn, job_id, "incomplete")
        except Problem as exc:
            self._set(message=exc.said)
            self._finish(conn, job_id, "failed")
        except Exception as exc:  # noqa: BLE001 — the thread must end tidily
            log.exception("import failed")
            self._set(message=_say("The run stopped with an error: {why}", why=str(exc)))
            self._finish(conn, job_id, "failed")
        finally:
            conn.close()

    def _begin(self, conn: sqlite3.Connection) -> int:
        j = self.job
        with conn:
            cur = conn.execute(
                "INSERT INTO import_jobs (sources, destination, mode, state, phase, started_at) "
                "VALUES (?, ?, ?, 'running', 'counting', ?)",
                (json.dumps(j["sources"]), j["destination"], j["mode"], j["started_at"]))
            job_id = int(cur.lastrowid)
            conn.execute("UPDATE import_jobs SET state = 'stopped', ended_at = ? "
                         "WHERE state = 'running' AND id != ?", (time.time(), job_id))
            if j["mode"] == "copy":
                conn.execute("DELETE FROM import_files WHERE status IN ('planned', "
                             "'plan-duplicate', 'plan-skip')")
        self._set(job_id=job_id)
        return job_id

    def _finish(self, conn: sqlite3.Connection, job_id: int | None, state: str) -> None:
        j = self.job
        phase = {"completed": "done", "stopped": "stopped", "failed": "failed",
                 "incomplete": "incomplete"}[state]
        if state == "completed":
            message = self._summary(conn)
        elif state == "stopped":
            message = _say("Stopped. Press Start to carry on where it left off.")
        else:
            message = j.get("message") or _say("The run failed.")
        self._set(phase=phase, message=message, ended_at=time.time())
        self._sweep_partials()
        if job_id is not None:
            with conn:
                conn.execute(
                    "UPDATE import_jobs SET state = ?, phase = ?, ended_at = ?, message = ?, "
                    "total_files = ?, total_bytes = ?, bytes_copied = ? WHERE id = ?",
                    ("stopped" if state == "incomplete" else state, phase, time.time(),
                     json.dumps(message, ensure_ascii=False), j["total_files"],
                     j["total_bytes"], j["bytes_copied"], job_id))
        log.info("import %s: %s", state, message["text"])
        if j["mode"] != "verify" and self.on_files is not None:
            self.on_files(j["destination"])

    def _summary(self, conn: sqlite3.Connection) -> dict[str, Any]:
        """How the run went, as a sentence the console can translate: a key
        made of the parts that apply, and their numbers."""
        j = self.job
        if j["mode"] == "verify":
            errors = conn.execute("SELECT COUNT(*) FROM import_files WHERE status = 'error' "
                                  "AND job_id = ?", (j["job_id"],)).fetchone()[0]
            return (_say("Audit finished: every archived file matches its hash.") if not errors
                    else _say("Audit finished: {errors} archived files did not match or are "
                              "missing.", errors=f"{errors:,}"))
        counts = {r[0]: r[1] for r in conn.execute(
            "SELECT status, COUNT(*) FROM import_files WHERE job_id = ? GROUP BY status",
            (j["job_id"],))}
        parts: list[str] = []
        values: dict[str, Any] = {}
        if j["mode"] == "dry-run":
            for status, n in j.get("plan_aside", {}).items():
                counts[status] = counts.get(status, 0) + n
            parts.append("Dry run finished: {planned} files would be archived, {duplicates} are "
                         "duplicates. Nothing was written.")
            values.update(planned=f"{counts.get('planned', 0):,}",
                          duplicates=f"{counts.get('plan-duplicate', 0):,}")
        else:
            parts.append("Finished: {verified} files archived and verified, {duplicates} "
                         "duplicates left in place." if not j["stepped_over"] else
                         "Finished: {verified} files archived and verified, {duplicates} "
                         "duplicates left in place, {done} already done.")
            values.update(verified=f"{counts.get('verified', 0):,}",
                          duplicates=f"{counts.get('duplicate', 0):,}",
                          done=f"{j['stepped_over']:,}")
        if counts.get("error"):
            parts.append("{errors} could not be archived.")
            values["errors"] = f"{counts['error']:,}"
        if j.get("unreadable"):
            parts.append("{unreadable} folders could not be read.")
            values["unreadable"] = f"{j['unreadable']:,}"
        if j.get("packed"):
            parts.append("{packed} zip or other packed files were left alone: unpack them (a "
                         "Google Takeout export, say) and import the folder.")
            values["packed"] = f"{j['packed']:,}"
        if j.get("too_small"):
            # Left out without a word, an import of an old phone's tiny photos
            # said "0 files archived", and the card could be wiped believing
            # everything was safe.
            parts.append("{small} files under 24 KB were left out as icons or thumbnails.")
            values["small"] = f"{j['too_small']:,}"
        said = [_say(part, **{k: v for k, v in values.items() if "{" + k + "}" in part})
                for part in parts]
        return {"key": said[0]["key"], "vars": said[0]["vars"],
                "text": " ".join(x["text"] for x in said), "more": said[1:]}


# --- reports for the console ------------------------------------------------------------


def stats(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT status, COUNT(*) FROM import_files GROUP BY status")}
    return {
        "verified": counts.get("verified", 0), "duplicates": counts.get("duplicate", 0),
        "skipped": counts.get("skipped", 0), "errors": counts.get("error", 0),
        "planned": counts.get("planned", 0), "plan_duplicates": counts.get("plan-duplicate", 0),
        "plan_skipped": counts.get("plan-skip", 0), "total_scanned": sum(counts.values()),
    }


def years(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [{"year": r[0] or UNDATED, "count": r[1]} for r in conn.execute(
        "SELECT substr(taken, 1, 4), COUNT(*) FROM import_files "
        "WHERE status IN ('verified', 'planned') GROUP BY 1 ORDER BY 1")]


def recent(conn: sqlite3.Connection, status: str | None, limit: int = 60) -> list[dict[str, Any]]:
    where, params = "", []
    if status == "planned":
        where, params = "WHERE status IN ('planned', 'plan-duplicate', 'plan-skip')", []
    elif status:
        where, params = "WHERE status = ?", [status]
    return [{"filename": r["name"], "status": r["status"], "destination_path": r["destination"],
             "source_path": r["source"], "duplicate_of": r["duplicate_of"], "error": r["error"],
             "size": r["size"], "date": r["taken"], "date_source": r["date_source"]}
            for r in conn.execute(
                f"SELECT * FROM import_files {where} ORDER BY updated_at DESC, id DESC LIMIT ?",
                [*params, limit])]


def retry_errors(conn: sqlite3.Connection) -> int:
    with conn:
        return conn.execute("UPDATE import_files SET status = 'pending', error = NULL "
                            "WHERE status = 'error'").rowcount


def clear_report(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("DELETE FROM import_files")
        conn.execute("DELETE FROM import_jobs")


def last_job(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM import_jobs ORDER BY id DESC LIMIT 1").fetchone()


def manifest_rows(conn: sqlite3.Connection):
    """(source, destination, size, sha256, status, date) for every file, for a CSV."""
    for r in conn.execute("SELECT source, destination, size, dest_hash, status, taken "
                          "FROM import_files ORDER BY destination, source"):
        yield r["source"], r["destination"] or "", r["size"], r["dest_hash"] or "", \
            r["status"], r["taken"] or ""

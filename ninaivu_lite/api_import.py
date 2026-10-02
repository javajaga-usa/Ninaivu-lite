"""The console's Import page: sweeping old drives into one archive.

Every route is for an administrator. The shapes are Ninaivu's archive API's,
trimmed, so the page taken from Ninaivu reads them unchanged.
"""

from __future__ import annotations

import csv
import io
import os
import re
import time
from typing import Any

from flask import Blueprint, Response, jsonify, request

from . import folders, importer, takeout
from .common import body, cfg, conn, fail, importer as engine, require_admin, scanner

bp = Blueprint("api_import", __name__)

_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _job(data: dict[str, Any]) -> tuple[list[str], str, list[str]]:
    sources = importer.clean_sources(data.get("source_dirs"))
    destination = str(data.get("destination_dir") or "").strip()
    if destination:
        destination = os.path.normpath(os.path.expanduser(destination))
    kinds = importer.clean_kinds(data.get("media_types"))
    return sources, destination, kinds


def _known_roots() -> list[str]:
    roots = [r[0] for r in conn().execute("SELECT DISTINCT destination FROM import_jobs")]
    if cfg().import_destination:
        roots.append(cfg().import_destination)
    return roots


def _resolve(destination: str) -> dict[str, Any]:
    return importer.resolve_destination(destination, _known_roots())


def _status_payload() -> dict[str, Any]:
    c = conn()
    out = {**importer.stats(c), **engine().progress()}
    last = importer.last_job(c)
    destination = out["destination"] or (last["destination"] if last else "") \
        or cfg().import_destination
    if not out["is_scanning"] and last is not None and not out["phase"]:
        out["phase"] = last["phase"]
        out["job_message"] = last["message"] or ""
        out["job_mode"] = last["mode"]
    verified = c.execute("SELECT COUNT(*) FROM import_files WHERE status = 'verified'").fetchone()[0]
    in_library = any(importer.is_within(destination, root) for root in cfg().folders) \
        if destination else False
    out["handoff"] = {"destination": destination, "verified": verified,
                      "available": bool(destination) and verified > 0
                      and os.path.isdir(destination) and not out["is_scanning"],
                      "in_library": in_library}
    return out


@bp.get("/api/archive/status")
def status():
    require_admin()
    return jsonify(_status_payload())


@bp.get("/api/archive/settings")
def settings():
    require_admin()
    c = cfg()
    # Never empty: the archive goes inside the default library folder unless
    # the administrator browses to somewhere else.
    destination = c.import_destination or importer.default_destination(c.active_folder)
    return jsonify({"source_dirs": [{"path": p} for p in c.import_sources],
                    "destination_dir": destination, "media_types": c.import_kinds,
                    "destination_is_default": not c.import_destination})


@bp.post("/api/archive/validate")
def validate():
    require_admin()
    sources, destination, kinds = _job(body())
    resolution = _resolve(destination)
    destination = resolution["destination"]
    problems = importer.validate(sources, destination, cfg().data_dir, cfg().folders)
    if not kinds:
        problems.append(importer._say("Pick at least one kind of file: photos or video."))
    return jsonify({"ok": not problems, "problems": problems,
                    "notices": importer.notices(sources, destination), "resolution": resolution})


@bp.post("/api/archive/capacity")
def capacity():
    """Count what the job would take, in the request: the page polls
    ``capacity/progress`` with the same token while it runs."""
    require_admin()
    data = body()
    sources, destination, kinds = _job(data)
    destination = _resolve(destination)["destination"]
    if importer.validate(sources, destination, cfg().data_dir, cfg().folders) or not kinds:
        return jsonify({"ok": False})
    token = str(data.get("progress_token") or "")
    if not _TOKEN.match(token):
        token = f"est-{int(time.time() * 1000)}"
    return jsonify(engine().estimate(sources, destination, kinds, token))


@bp.get("/api/archive/capacity/progress")
def capacity_progress():
    require_admin()
    return jsonify(engine().estimate_progress(str(request.args.get("token") or "")))


@bp.post("/api/archive/capacity/cancel")
def capacity_cancel():
    require_admin()
    engine().cancel_estimate(str(body().get("token") or ""))
    return jsonify({"ok": True})


@bp.post("/api/archive/start")
def start():
    require_admin()
    data = body()
    sources, destination, kinds = _job(data)
    mode = str(data.get("mode") or "copy")
    if mode not in importer.MODES:
        fail(400, "Mode must be copy, dry-run or verify.")
    resolution = _resolve(destination)
    destination = resolution["destination"]
    if mode == "verify":
        problems = [] if destination and os.path.isdir(destination) \
            else [importer._say("Choose the archive folder to audit.")]
    else:
        problems = importer.validate(sources, destination, cfg().data_dir, cfg().folders)
        if not kinds:
            problems.append(importer._say("Pick at least one kind of file: photos or video."))
    if problems:
        fail(409, problems[0]["text"], problems=problems, resolution=resolution)
    try:
        engine().start(sources, destination, kinds, mode)
    except ValueError as exc:
        fail(409, str(exc))
    c = cfg()
    c.import_sources, c.import_destination, c.import_kinds = sources, destination, kinds
    c.save()
    message = {"copy": "Import started.", "dry-run": "Dry run started.",
               "verify": "Audit started."}[mode]
    return jsonify({"ok": True, "message": message, "resolution": resolution,
                    "destination": destination})


@bp.post("/api/archive/stop")
def stop():
    require_admin()
    engine().stop()
    return jsonify({"ok": True, "message": "Stopping after the current file."})


@bp.post("/api/archive/pause")
def pause():
    require_admin()
    engine().pause()
    return jsonify({"ok": True, "message": "Paused."})


@bp.post("/api/archive/resume")
def resume():
    require_admin()
    engine().resume()
    return jsonify({"ok": True, "message": "Resumed."})


@bp.get("/api/archive/recent")
def recent():
    require_admin()
    status_filter = request.args.get("status") or None
    if status_filter not in (None, "verified", "duplicate", "skipped", "error", "planned"):
        status_filter = None
    try:
        limit = max(1, min(500, int(request.args.get("limit", 60))))
    except ValueError:
        limit = 60
    return jsonify(importer.recent(conn(), status_filter, limit))


@bp.get("/api/archive/years")
def years():
    require_admin()
    return jsonify(importer.years(conn()))


@bp.get("/api/archive/manifest.csv")
def manifest():
    require_admin()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["source", "destination", "size", "sha256", "status", "date"])
    for row in importer.manifest_rows(conn()):
        writer.writerow(row)
    stamp = time.strftime("%Y%m%d-%H%M")
    return Response(out.getvalue().encode("utf-8-sig"), mimetype="text/csv",
                    headers={"Content-Disposition":
                             f'attachment; filename="ninaivu-lite-archive-{stamp}.csv"'})


@bp.post("/api/archive/retry-errors")
def retry_errors():
    require_admin()
    if engine().running:
        fail(409, "Wait for the run to finish first.")
    count = importer.retry_errors(conn())
    return jsonify({"ok": True, "count": count,
                    "message": f"{count:,} files will be tried again on the next run."})


@bp.post("/api/archive/reset")
def reset():
    """Forget the report. The archive's files are not touched."""
    require_admin()
    if engine().running:
        fail(409, "Wait for the run to finish first.")
    importer.clear_report(conn())
    return jsonify({"ok": True, "message": "The report was cleared. The archive itself is untouched."})


@bp.post("/api/archive/adopt")
def adopt():
    """Add the archive to the library, so the household can browse it."""
    require_admin()
    from .api_admin import _folders_changed
    c = cfg()
    path = str(body().get("path") or "").strip() or c.import_destination
    if not path:
        fail(400, "Which folder?")
    path = os.path.normpath(os.path.expanduser(path))
    already = any(importer.is_within(path, root) for root in c.folders)
    if not already:
        problem = folders.problem(path, c.data_dir, c.folders)
        if problem:
            fail(400, problem)
        c.folders.append(path)
        if not c.active:
            c.active = path
        _folders_changed()
    return jsonify({"ok": True, "already": already, "path": path, "folders": list(c.folders),
                    "message": "That folder is already in the library." if already
                    else "Added to the library. Indexing it now."})


@bp.get("/api/archive/takeout-albums")
def takeout_albums():
    require_admin()
    found = takeout.albums_in(cfg().import_sources)
    return jsonify({"albums": [{"title": a["title"], "files": len(a["files"])} for a in found]})


@bp.post("/api/archive/takeout-albums")
def make_takeout_albums():
    who = require_admin()
    if not cfg().folders:
        fail(409, "Add the archive to the library first.")
    result = takeout.recreate(conn(), cfg().import_sources, who.id)
    if result["albums"]:
        scanner().generation += 1
    return jsonify(result)




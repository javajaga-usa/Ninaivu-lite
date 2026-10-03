"""A pendrive or an external hard drive was plugged in: the console asks
whether to bring its photos in or to copy the library out to it.

Every route is for an administrator. Bringing photos in is the Import page,
with the drive as its source; copying out is :class:`drives.Exporter`.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from . import drives
from .common import body, cfg, fail, require_admin

bp = Blueprint("api_drives", __name__)


def watcher() -> drives.Watcher:
    return current_app.config["DRIVES"]


def exporter() -> drives.Exporter:
    return current_app.config["EXPORTER"]


def _holds_library(drive: drives.Drive) -> bool:
    """The library (or Ninaivu Lite's own data) lives on it: nothing to ask."""
    c = cfg()
    return any(drives.is_within(path, drive.path) for path in [*c.folders, c.data_dir] if path)


def _drive() -> drives.Drive:
    drive_id = str(body().get("id") or "")
    drive = watcher().find(drive_id) if drive_id else None
    if drive is None:
        fail(404, "That drive is no longer plugged in.")
    return drive


@bp.get("/api/admin/drives")
def listed():
    require_admin()
    pending = {d.id for d in watcher().pending()}
    out = []
    for d in watcher().drives():
        home = _holds_library(d)
        out.append({**d.to_json(), "holds_library": home, "pending": d.id in pending and not home})
    return jsonify({"drives": out,
                    "export": exporter().progress()})


@bp.post("/api/admin/drives/answer")
def answer():
    """Import chosen (the Import page takes it from here) or Not now."""
    require_admin()
    watcher().answer(_drive().id)
    return jsonify({"ok": True})


@bp.post("/api/admin/drives/export")
def export():
    require_admin()
    drive = _drive()
    watcher().answer(drive.id)
    c = cfg()
    if not c.folders:
        fail(409, "There is no library to copy yet. Choose a photo folder first.")
    if exporter().running:
        fail(409, "A copy to a drive is already running.")
    if _holds_library(drive):
        fail(409, "This drive holds the library itself, so it cannot be copied onto it.")
    exporter().start(drive, c.folders, c.data_dir)
    return jsonify({"ok": True, "destination": drives.export_root(drive.path),
                    "message": "Copying the library to the drive."})


@bp.get("/api/admin/drives/export")
def export_status():
    require_admin()
    return jsonify(exporter().progress())


@bp.post("/api/admin/drives/export/stop")
def export_stop():
    require_admin()
    exporter().stop()
    return jsonify({"ok": True, "message": "Stopping after the current file."})

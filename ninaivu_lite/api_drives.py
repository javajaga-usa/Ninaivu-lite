"""A pendrive, an external hard drive or a phone was plugged in: the console
asks whether to bring its photos in or to copy the library out to it.

Every route is for an administrator. Bringing photos in is the Import page,
with the drive as its source, or for a phone Windows reaches only through
Explorer, :class:`phones.PhoneImport`; copying out is :class:`drives.Exporter`.
"""

from __future__ import annotations

import os

from flask import Blueprint, current_app, jsonify

from . import drives, importer, phones
from .common import body, cfg, fail, require_admin
from .common import importer as engine

bp = Blueprint("api_drives", __name__)


def watcher() -> drives.Watcher:
    return current_app.config["DRIVES"]


def exporter() -> drives.Exporter:
    return current_app.config["EXPORTER"]


def _holds_library(drive: drives.Drive) -> bool:
    """The library (or Ninaivu Lite's own data) lives on it: nothing to ask."""
    if drive.kind == "phone":
        return False
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
                    "export": exporter().progress(),
                    "phone": phone_import_job().progress(engine())})


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
    if drive.kind == "phone":
        fail(409, "Copying the library onto a phone isn't offered. Use a pendrive or "
                  "an external drive.")
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


# --- a phone Windows shows only in Explorer --------------------------------------------------


def phone_import_job() -> phones.PhoneImport:
    return current_app.config["PHONE_IMPORT"]


@bp.post("/api/admin/drives/phone-import")
def phone_import():
    require_admin()
    drive = _drive()
    watcher().answer(drive.id)
    if not drive.shell:
        fail(409, "Open Import and add the phone as a source.")
    if phone_import_job().running or engine().running:
        fail(409, "A run is already going. Stop it first.")
    c = cfg()
    chosen = c.import_destination or importer.default_destination(c.active_folder)
    known = [c.import_destination] if c.import_destination else []
    destination = importer.resolve_destination(chosen, known)["destination"]
    mirror = phones.mirror_for(c.data_dir, drive)
    os.makedirs(mirror, exist_ok=True)
    problems = importer.validate([mirror], destination, c.data_dir, c.folders)
    if problems:
        fail(409, problems[0]["text"], problem=problems[0])
    phone_import_job().start(drive, mirror, destination, engine(), c.data_dir)
    return jsonify({"ok": True, "destination": destination})


@bp.get("/api/admin/drives/phone-import")
def phone_import_status():
    require_admin()
    return jsonify(phone_import_job().progress(engine()))


@bp.post("/api/admin/drives/phone-import/stop")
def phone_import_stop():
    require_admin()
    phone_import_job().stop(engine())
    return jsonify({"ok": True, "message": "Stopping after the current file."})

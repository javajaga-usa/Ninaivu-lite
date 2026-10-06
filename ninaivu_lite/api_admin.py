"""The admin console's calls: overview, library folders, people, who sees
what, settings, and the two downloads (backup and export).

Every route checks for an administrator itself (401 for nobody, 403 for a
family member): on one port there is no console-wide gate. Messages are
Ninaivu's own English sentences, so its Tamil translations apply unchanged.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any

from flask import Blueprint, Response, jsonify, request

from . import auth, backups, db, export, folders, media
from . import importer as importer_rules
from .common import (body, cfg, conn, drop_avatar, fail, folder_ids, importer,
                     library_exists, require_admin, scanner, split_library, subtree, visible)
from .config import clean_house_name
from .version import COPYRIGHT, LICENCE, __version__

bp = Blueprint("api_admin", __name__)

#: How many bulk visibility changes stay undoable (Ninaivu's number).
UNDO_HISTORY = 20

#: The check "is this the last administrator?" and the change that could make
#: it so happen with nothing in between.
_ADMIN_COUNT_LOCK = threading.Lock()

_CANNOT_OPEN = ("“{}” could not be opened. Check the path exists and that "
                "Ninaivu has permission to read it.")


# --- helpers ---------------------------------------------------------------------------


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _inside(assignment: str | None, root: str) -> bool:
    """*assignment* is *root* or a folder inside it, compared on path parts
    (``/data/kids`` does not contain ``/data/kids-private``)."""
    if not assignment:
        return False
    a, r = _norm(assignment), _norm(root)
    return a == r or a.startswith(r.rstrip(os.sep) + os.sep)


def _find_library(raw: str) -> str | None:
    """The configured library folder *raw* names, or None."""
    if not raw:
        return None
    return next((f for f in cfg().folders if f == raw or _norm(f) == _norm(raw)), None)


def _clean_dir(value: Any) -> str:
    """A folder inside a library folder, '/'-separated, no leading or trailing '/'."""
    text = str(value or "").replace("\\", "/")
    parts = [p for p in text.split("/") if p and p != "."]
    if ".." in parts:
        fail(400, "That folder is not in the library.")
    return "/".join(parts)


def _library_summaries() -> list[dict[str, Any]]:
    c = cfg()
    ids = folder_ids()
    libraries = [r["library"] for r in conn().execute(
        "SELECT library FROM users WHERE active = 1 AND library IS NOT NULL")]
    out = []
    for root in c.folders:
        count, size = 0, 0
        if root in ids:
            row = conn().execute(
                "SELECT COUNT(*) n, COALESCE(SUM(size), 0) b FROM assets "
                "WHERE folder_id = ? AND missing = 0", (ids[root],)).fetchone()
            count, size = row["n"], row["b"]
        out.append({
            "path": root,
            "name": os.path.basename(root.rstrip("\\/")) or root,
            "exists": library_exists(root),
            "count": count,
            "bytes": size,
            "assigned": sum(1 for lib in libraries if _inside(lib, root)),
            "active": root == c.active_folder,
        })
    return out


def _stats(who: auth.User) -> dict[str, Any]:
    where, params = visible(who)
    row = conn().execute(
        f"""SELECT COUNT(*) count,
                   COALESCE(SUM(a.kind = 'picture'), 0) pictures,
                   COALESCE(SUM(a.kind = 'video'), 0) videos,
                   COALESCE(SUM(a.visibility = 0), 0) public,
                   COALESCE(SUM(a.visibility = 1), 0) family,
                   COALESCE(SUM(a.visibility = 2), 0) hidden,
                   COALESCE(SUM(a.size), 0) bytes,
                   MIN(a.date_key) first_date, MAX(a.date_key) last_date
            FROM assets a WHERE {where}""", params).fetchone()
    favourites = conn().execute(
        f"SELECT COUNT(*) FROM user_assets u JOIN assets a ON a.id = u.asset_id "
        f"WHERE u.user_id = ? AND u.favorite = 1 AND {where}", [who.id, *params]).fetchone()[0]
    return {
        "count": row["count"], "pictures": row["pictures"], "videos": row["videos"],
        "audio": 0, "public": row["public"], "family": row["family"], "hidden": row["hidden"],
        "bytes": row["bytes"], "live": 0, "favorites": favourites, "nsfw": 0,
        "duplicate_groups": 0, "embedded": 0,
        "first_date": row["first_date"] or "", "last_date": row["last_date"] or "",
    }


def _active_folder_id() -> int | None:
    root = cfg().active_folder
    return folder_ids().get(root) if root else None


def _rules(folder_id: int | None) -> dict[str, int]:
    if folder_id is None:
        return {}
    return {r["dir"]: int(r["visibility"]) for r in conn().execute(
        "SELECT dir, visibility FROM folder_rules WHERE folder_id = ? ORDER BY dir",
        (folder_id,))}


def _rule_list(folder_id: int | None) -> list[dict[str, Any]]:
    if folder_id is None:
        return []
    return [{"folder": r["dir"], "visibility": int(r["visibility"]),
             "created_at": r["created_at"],
             "visibility_name": db.VIS_NAMES[int(r["visibility"])]}
            for r in conn().execute(
                "SELECT dir, visibility, created_at FROM folder_rules WHERE folder_id = ? "
                "ORDER BY dir", (folder_id,))]


def _effective(rules: dict[str, int], path: str) -> int | None:
    parts = path.split("/") if path else []
    for i in range(len(parts), -1, -1):
        candidate = "/".join(parts[:i])
        if candidate in rules:
            return rules[candidate]
    return None


def _below(rel: str) -> tuple[str, list[Any]]:
    """SQL for folders strictly below *rel* (not *rel* itself)."""
    if not rel:
        return "dir <> ''", []
    return "(dir > ? AND dir < ?)", [rel + "/", rel + "/\U0010ffff"]


def _download(data: bytes, mimetype: str, filename: str) -> Response:
    response = Response(data, mimetype=mimetype)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Cache-Control"] = "no-store"
    return response


# --- overview --------------------------------------------------------------------------


@bp.get("/api/admin/overview")
def overview():
    who = require_admin()
    c = cfg()
    people = auth.list_users(conn())
    active = [p for p in people if p.active]
    signed_in = conn().execute(
        "SELECT COUNT(DISTINCT user_id) n, COUNT(*) s FROM sessions WHERE expires_at > ?",
        (time.time(),)).fetchone()
    return jsonify({
        "app": {
            "version": __version__,
            "copyright": COPYRIGHT,
            "licence": LICENCE,
            "home_url": "/",
            "open_browsing": c.open_browsing,
            "watch": c.watch,
            "video_originals": c.video_originals,
            "house_name": c.house_name,
            "house_name_effective": c.house_name_effective,
            "default_language": c.language,
        },
        "library": {
            "root": c.active_folder,
            "roots": list(c.folders),
            "folders": _library_summaries(),
            "locked": False,
        },
        "capabilities": {"ffmpeg": bool(media.FFMPEG), "heif": bool(media.HEIF),
                         "opencv": bool(media.FACES)},
        "people": {
            "total": len(active),
            "by_role": {role: sum(1 for p in active if p.role == role) for role in auth.ROLES},
            "disabled": len(people) - len(active),
            # People, not sessions: every phone and browser keeps its own.
            "signed_in": signed_in["n"],
            "sessions": signed_in["s"],
        },
        "stats": _stats(who),
        "rules": _rule_list(_active_folder_id()),
    })


@bp.get("/api/admin/preview")
def preview():
    """What a role or a person would actually see: the check an admin wants
    before handing out a login."""
    require_admin()
    c = cfg()
    if not c.folders:
        return jsonify({"total": 0, "items": []})
    person_id = request.args.get("person")
    if person_id:
        try:
            viewer = auth.get_user(conn(), int(person_id))
        except ValueError:
            viewer = None
        if viewer is None:
            fail(404, "No such profile.")
        label = viewer.display_name
    else:
        role = request.args.get("role", auth.ROLE_GUEST)
        if role not in auth.ROLES:
            fail(400, "Unknown role.")
        label = auth.ROLE_LABELS[role]
        viewer = auth.User(id=0, username="preview", display_name=label, role=role,
                           library=request.args.get("library") or None)
    where, params = visible(viewer)
    total = conn().execute(f"SELECT COUNT(*) FROM assets a WHERE {where}", params).fetchone()[0]
    rows = conn().execute(
        f"SELECT a.id, a.name, a.kind, a.dir, a.thumb, a.thumb_v, a.visibility FROM assets a "
        f"WHERE {where} ORDER BY a.captured_at DESC, a.id DESC LIMIT 12", params).fetchall()
    return jsonify({
        "as": label,
        "role": viewer.role,
        "role_label": auth.ROLE_LABELS[viewer.role],
        "scope": None,
        "library": viewer.assigned_library,
        "folders": list(c.folders),
        "total": total,
        "items": [{
            "id": r["id"], "name": r["name"], "kind": r["kind"], "folder": r["dir"],
            "has_thumb": r["thumb"] != db.THUMB_NONE, "thumb_v": r["thumb_v"] or "",
            "visibility": db.VIS_NAMES.get(r["visibility"], "family"),
        } for r in rows],
    })


# --- library folders -----------------------------------------------------------------


def _shortcuts() -> list[dict[str, str]]:
    c = cfg()
    home = _norm(os.path.expanduser("~"))
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for root in c.folders:
        if _norm(root) in seen or not library_exists(root):
            continue
        seen.add(_norm(root))
        name = os.path.basename(root.rstrip("\\/")) or root
        out.append({"name": f"{name} (current)" if root == c.active_folder else name,
                    "path": root})
    for point in folders.starting_points():
        path = point["path"]
        if _norm(path) in seen:
            continue
        seen.add(_norm(path))
        if _norm(path) == home:
            label = "Home"
        elif len(path) <= 3 and path[1:2] == ":":
            label = f"{path[0].upper()}: drive"
        else:
            label = point["name"]
        out.append({"name": label, "path": path})
    return out[:14]


@bp.get("/api/library/browse")
def browse():
    """The folder picker: the folders inside one folder, and where to jump to."""
    require_admin()
    c = cfg()
    raw = (request.args.get("path") or "").strip()
    shortcuts = _shortcuts()
    if raw:
        target = os.path.expanduser(raw)
        if not os.path.isabs(target):
            fail(400, "Give the full path of the folder, for example D:\\Photos.")
    elif c.active_folder:
        target = c.active_folder
    elif shortcuts:
        target = shortcuts[0]["path"]
    else:
        target = os.path.expanduser("~")
    target = os.path.normpath(target)
    try:
        listing = folders.list_dirs(target)
    except (FileNotFoundError, NotADirectoryError):
        fail(404, "Not a directory")
    except OSError:                  # PermissionError included
        fail(403, _CANNOT_OPEN.format(target))
    path = listing["path"]
    selectable = _find_library(path) is not None \
        or folders.problem(path, c.data_dir, c.folders) is None
    return jsonify({
        "path": path,
        "parent": listing["parent"],
        "dirs": [{"name": name, "path": os.path.join(path, name)}
                 for name in listing["dirs"][:500]],
        "shortcuts": shortcuts,
        "locked": False,
        "selectable": selectable,
    })


def _folders_changed() -> None:
    """Save, bring the index's folder list in line now (so the screens update
    straight away), and let the scanner look."""
    c = cfg()
    c.save()
    s = scanner()
    s.folders = list(c.folders)          # before the sync: a scan starting now reads this
    db.sync_folders(conn(), list(c.folders))
    s.generation += 1
    s.rescan()


@bp.post("/api/library/root")
def add_root():
    require_admin()
    c = cfg()
    raw = str(body().get("path") or "").strip()
    if not raw:
        fail(400, "Path required")
    path = os.path.normpath(os.path.expanduser(raw))
    existing = _find_library(path)
    added = existing is None
    if existing is None:
        problem = folders.problem(path, c.data_dir, c.folders)
        if problem:
            refused = problem.startswith(("That is a system folder", "That is Ninaivu Lite's own"))
            fail(403 if refused else 400, problem)
        c.folders.append(path)
    else:
        path = existing
    c.active = path
    _folders_changed()
    return jsonify({"ok": True, "root": c.active_folder, "added": added,
                    "folders": list(c.folders)})


@bp.delete("/api/admin/libraries")
def remove_library():
    """Stop indexing a library folder. The files themselves are never touched."""
    require_admin()
    c = cfg()
    raw = str(request.args.get("path") or "").strip()
    if not raw:
        fail(400, "Which folder?")
    path = _find_library(raw)
    if path is None:
        fail(404, "That folder is not in the library.")
    assigned = [r["display_name"] for r in conn().execute(
        "SELECT display_name, library FROM users WHERE active = 1 AND library IS NOT NULL "
        "ORDER BY display_name COLLATE NOCASE") if _inside(r["library"], path)]
    if assigned and request.args.get("force") not in ("1", "true", "yes"):
        names = ", ".join(assigned[:5])
        fail(409, f"{names} {'is' if len(assigned) == 1 else 'are'} assigned to that "
                  f"folder. Reassign them first, or confirm to remove it anyway: they "
                  f"will see nothing until they are reassigned. Removing a folder also "
                  f"forgets its photographs' favourites, album places and per-photo "
                  f"visibility.", assigned=assigned)
    # Assignments are left as they are: one that matches no library folder
    # sees nothing, where clearing it would mean "everything".
    c.folders.remove(path)
    if c.active == path:
        c.active = c.folders[0] if c.folders else ""
    _folders_changed()
    return jsonify({"ok": True, "folders": _library_summaries()})


@bp.post("/api/admin/libraries/active")
def set_active_library():
    require_admin()
    c = cfg()
    path = _find_library(str(body().get("path") or "").strip())
    if path is None:
        fail(404, "That folder is not in the library.")
    c.active = path
    c.save()
    return jsonify({"ok": True, "root": c.active_folder})


@bp.post("/api/scan")
def rescan():
    require_admin()
    full = bool(body().get("full"))
    s = scanner()
    s.folders = list(cfg().folders)
    s.rescan()
    return jsonify({"ok": True, "full": full, "folders": list(cfg().folders)})


@bp.get("/api/admin/assignable")
def assignable():
    """Folders a person can be given: each library folder, then every folder
    inside it that holds photos, as absolute paths."""
    require_admin()
    ids = folder_ids()
    out: list[dict[str, Any]] = []
    for root in cfg().folders:
        rows: list[sqlite3.Row] = []
        if root in ids:
            rows = conn().execute(
                "SELECT dir, COUNT(*) n FROM assets WHERE folder_id = ? AND missing = 0 "
                "GROUP BY dir", (ids[root],)).fetchall()
        out.append({"path": root, "label": os.path.basename(root.rstrip("\\/")) or root,
                    "depth": 0, "count": sum(r["n"] for r in rows), "is_root": True})
        totals: dict[str, int] = {}
        for r in rows:
            parts = [p for p in r["dir"].split("/") if p]
            for depth in range(1, len(parts) + 1):
                prefix = "/".join(parts[:depth])
                totals[prefix] = totals.get(prefix, 0) + r["n"]
        for prefix, count in sorted(totals.items()):
            out.append({"path": os.path.join(root, *prefix.split("/")),
                        "label": prefix.split("/")[-1], "depth": prefix.count("/") + 1,
                        "count": count, "is_root": False})
    return jsonify({"folders": out})


# --- settings and the first day ------------------------------------------------------


def _settings_payload() -> dict[str, Any]:
    c = cfg()
    return {"house_name": c.house_name, "house_name_effective": c.house_name_effective,
            "open_browsing": c.open_browsing, "watch": c.watch,
            "video_originals": c.video_originals,
            "language": c.language, "default_language": c.language}


@bp.post("/api/admin/settings")
def settings():
    require_admin()
    c = cfg()
    data = body()
    # Everything is checked before anything is written.
    updates: dict[str, Any] = {}
    if "house_name" in data:
        if not isinstance(data["house_name"], str):
            fail(400, "house_name must be text.")
        updates["house_name"] = clean_house_name(data["house_name"])
    for key in ("open_browsing", "watch", "video_originals"):
        if key in data:
            if not isinstance(data[key], bool):
                fail(400, f"{key} must be true or false.")
            updates[key] = data[key]
    for key in ("language", "default_language"):
        if key in data:
            if data[key] not in ("en", "ta"):
                fail(400, "The language must be en or ta.")
            updates["language"] = data[key]
    for key, value in updates.items():
        setattr(c, key, value)
    changed = list(updates)
    if changed:
        c.save()
    if "watch" in updates:
        scanner().auto = c.watch
        if c.watch:
            scanner().rescan()
    return jsonify({"ok": True, "changed": changed, "settings": _settings_payload()})


@bp.get("/api/admin/first-day")
def first_day():
    require_admin()
    c = cfg()
    people = conn().execute(
        "SELECT COUNT(*) FROM users WHERE role != 'admin' AND active = 1").fetchone()[0]
    # The import step suggests building the archive inside the library folder,
    # so what it brings in is indexed and shown to the family straight away.
    return jsonify({"done": bool(c.first_day_done),
                    "library": {"chosen": bool(c.folders), "root": c.active_folder},
                    "import": {"sources": list(c.import_sources),
                               "destination": c.import_destination
                               or importer_rules.default_destination(c.active_folder),
                               "running": importer().running},
                    "people": people})


@bp.post("/api/admin/first-day")
def first_day_done():
    require_admin()
    c = cfg()
    c.first_day_done = True
    c.save()
    return jsonify({"done": True})


# --- people ----------------------------------------------------------------------------


def _person(person: auth.User, sessions: dict[int, int] | None = None) -> dict[str, Any]:
    payload = person.public()
    if sessions is None:
        sessions = auth.session_counts(conn())
    where, params = visible(person)
    payload.update({
        "last_login": person.last_login,
        "created_at": person.created_at,
        "library": person.library or None,
        "has_pin": person.has_pin,
        "has_password": person.has_password,
        "entry": person.entry,
        "sessions": sessions.get(person.id, 0),
        "favorites": conn().execute(
            "SELECT COUNT(*) FROM user_assets WHERE user_id = ? AND favorite = 1",
            (person.id,)).fetchone()[0],
        # Counted the way their own requests are answered.
        "visible_count": conn().execute(
            f"SELECT COUNT(*) FROM assets a WHERE {where}", params).fetchone()[0],
    })
    return payload


def _library_assignment(value: Any) -> str | None:
    """An absolute folder inside a library folder, or None for all of them."""
    text = str(value or "").strip()
    if not text:
        return None
    path = os.path.normpath(text)
    if not os.path.isabs(path) or split_library(path) is None:
        fail(400, f"“{text}” is not inside any library folder. Add it on the Library tab first.")
    return path


@bp.get("/api/people")
def people():
    require_admin()
    sessions = auth.session_counts(conn())
    return jsonify({
        "people": [_person(p, sessions) for p in auth.list_users(conn())],
        "roles": [{"value": r, "label": auth.ROLE_LABELS[r]} for r in auth.ROLES],
    })


@bp.post("/api/people")
def create_person():
    admin = require_admin()
    data = body()
    role = str(data.get("role") or auth.ROLE_FAMILY)
    if role not in auth.ROLES:
        fail(400, "Unknown role.")
    library = _library_assignment(data.get("library"))
    password = str(data.get("password") or "")
    try:
        person = auth.create_user(
            conn(), str(data.get("username") or ""), password=password,
            pin=str(data.get("pin") or ""), name=str(data.get("name") or ""), role=role,
            library=library, created_by=admin.id,
            must_change=bool(data.get("must_change", True)) and bool(password))
    except auth.AccountError as exc:
        fail(400, str(exc))
    return jsonify({"ok": True, "person": _person(person)})


def _target(user_id: int) -> auth.User:
    person = auth.get_user(conn(), user_id)
    if person is None:
        fail(404, "No such profile.")
    return person


@bp.post("/api/people/<int:user_id>")
def update_person(user_id: int):
    admin = require_admin()
    data = body()
    c = conn()
    target = _target(user_id)

    # Every check before any write: a refused PIN must not leave the role changed.
    fields: dict[str, Any] = {}
    last_admin = target.is_admin and target.active and auth.count_admins(c) <= 1
    password = str(data.get("password") or "")

    if "role" in data:
        role = str(data["role"])
        if role not in auth.ROLES:
            fail(400, "Unknown role.")
        if role != auth.ROLE_ADMIN and last_admin:
            fail(409, "This is the only administrator — promote someone else first.")
        if role == auth.ROLE_ADMIN and not target.is_admin \
                and not target.has_password and not password:
            fail(400, f"{target.display_name} has no password, and an administrator signs "
                      f"in with one. Give them a password when making them an administrator.")
        if role != target.role:
            fields["role"] = role

    if "name" in data:
        name = str(data["name"] or "").strip()[:60]
        if name:
            fields["display_name"] = name

    if "library" in data:
        fields["library"] = _library_assignment(data["library"])

    active: bool | None = None
    if "active" in data:
        active = bool(data["active"])
        if not active and target.is_admin and last_admin:
            fail(409, "You can't disable the only administrator.")
        if not active and target.id == admin.id:
            fail(409, "You can't disable your own profile.")

    pin: str | None = None
    if "pin" in data:
        pin = str(data["pin"] or "") or None
        if pin and (problem := auth.pin_problem(pin)):
            fail(400, problem)

    if password and (problem := auth.password_problem(password)):
        fail(400, problem)

    loses_an_admin = target.is_admin and target.active and (
        fields.get("role", auth.ROLE_ADMIN) != auth.ROLE_ADMIN or active is False)
    with _ADMIN_COUNT_LOCK:
        if loses_an_admin and auth.count_admins(c) <= 1:
            fail(409, "This is the only administrator — promote someone else first.")
        if active is not None:
            fields["active"] = int(active)
        if fields:
            auth.update_profile(c, user_id, **fields)
    if fields.get("role") == auth.ROLE_ADMIN:
        # A tap or a PIN is not how an administrator signs in.
        auth.end_all_sessions(c, user_id)
    if "pin" in data:
        auth.set_pin(c, user_id, pin)
    if password:
        # A password set by an administrator is temporary; their devices sign out.
        auth.set_password(c, user_id, password, must_change=True)
        auth.end_all_sessions(c, user_id)
    return jsonify({"ok": True, "person": _person(_target(user_id))})


@bp.post("/api/people/<int:user_id>/signout")
def signout_person(user_id: int):
    require_admin()
    _target(user_id)
    return jsonify({"ok": True, "sessions_ended": auth.end_all_sessions(conn(), user_id)})


@bp.delete("/api/people/<int:user_id>/avatar")
def remove_person_avatar(user_id: int):
    """Take a person's picture off the sign-in screen. Only they can put one up."""
    require_admin()
    _target(user_id)
    drop_avatar(user_id)
    return jsonify(_person(auth.get_user(conn(), user_id)))


@bp.delete("/api/people/<int:user_id>")
def delete_person(user_id: int):
    """Remove a profile for good. Photos are never touched."""
    admin = require_admin()
    c = conn()
    target = _target(user_id)
    if target.id == admin.id:
        fail(409, "You can't delete the profile you're signed in with. "
                  "Ask another administrator to remove it.")
    with _ADMIN_COUNT_LOCK:
        if target.is_admin and target.active and auth.count_admins(c) <= 1:
            fail(409, "This is the only administrator — make someone else an admin first.")
        sessions = c.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?",
                             (user_id,)).fetchone()[0]
        personal = c.execute("SELECT COUNT(*) FROM user_assets WHERE user_id = ?",
                             (user_id,)).fetchone()[0]
        favourites = c.execute(
            "SELECT COUNT(*) FROM user_assets WHERE user_id = ? AND favorite = 1",
            (user_id,)).fetchone()[0]
        with c:
            # Albums are the household's photographs, arranged: they pass to
            # the administrator rather than becoming nobody's.
            c.execute("UPDATE albums SET created_by = ? WHERE created_by = ?",
                      (admin.id, user_id))
            c.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            c.execute("DELETE FROM user_assets WHERE user_id = ?", (user_id,))
            # Their share links stop working with them.
            c.execute("DELETE FROM shares WHERE created_by = ?", (user_id,))
            c.execute("UPDATE users SET created_by = NULL WHERE created_by = ?", (user_id,))
            c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    if target.avatar_at:
        drop_avatar(user_id)
    return jsonify({"ok": True, "removed": {
        "username": target.username, "name": target.display_name, "role": target.role,
        "sessions": sessions, "favorites": favourites, "personal_rows": personal,
    }})


# --- who sees what ---------------------------------------------------------------------


@bp.get("/api/admin/folders")
def folder_tree():
    """The default library folder's tree: every folder holding photos and the
    folders above it, with counts by visibility and the rules set."""
    require_admin()
    folder_id = _active_folder_id()
    if folder_id is None:
        return jsonify({"folders": [], "rules": []})
    rows = conn().execute(
        "SELECT dir, COUNT(*) n, SUM(visibility = 0) pub, SUM(visibility = 1) fam, "
        "SUM(visibility = 2) hid FROM assets WHERE folder_id = ? AND missing = 0 "
        "GROUP BY dir", (folder_id,)).fetchall()
    totals: dict[str, dict[str, int]] = {}
    for row in rows:
        parts = [p for p in row["dir"].split("/") if p]
        for depth in range(1, len(parts) + 1):
            bucket = totals.setdefault("/".join(parts[:depth]),
                                       {"count": 0, "public": 0, "family": 0, "hidden": 0})
            bucket["count"] += row["n"]
            bucket["public"] += row["pub"] or 0
            bucket["family"] += row["fam"] or 0
            bucket["hidden"] += row["hid"] or 0
    rules = _rules(folder_id)
    return jsonify({
        "folders": [{
            "path": path,
            "name": path.split("/")[-1],
            "depth": path.count("/"),
            **counts,
            "rule": db.VIS_NAMES[rules[path]] if path in rules else None,
            "effective": db.VIS_NAMES.get(_effective(rules, path)),
        } for path, counts in sorted(totals.items())],
        "root_rule": db.VIS_NAMES[rules[""]] if "" in rules else None,
        "rules": [{"folder": f, "visibility": db.VIS_NAMES[v]} for f, v in rules.items()],
    })


def _impact(folder_id: int, folder: str, wanted: int) -> dict[str, Any]:
    inside, params = subtree("", folder)
    row = conn().execute(
        f"SELECT COUNT(*) total, COALESCE(SUM(visibility > ?), 0) exposed, "
        f"COALESCE(SUM(visibility < ?), 0) restricted, "
        f"COALESCE(SUM(vis_source = 'item'), 0) decided "
        f"FROM assets WHERE folder_id = ? AND missing = 0 AND {inside}",
        [wanted, wanted, folder_id, *params]).fetchone()
    below, below_params = _below(folder)
    child_rules = conn().execute(
        f"SELECT COUNT(*) FROM folder_rules WHERE folder_id = ? AND {below}",
        [folder_id, *below_params]).fetchone()[0]
    return {"folder": folder, "visibility": wanted, "total": row["total"],
            # Less visible now than the new level: this would show them to more people.
            "exposed": row["exposed"], "restricted": row["restricted"],
            "hidden_by_the_filesystem": 0, "decided_individually": row["decided"],
            "child_rules": child_rules}


def _trim_history(c: sqlite3.Connection) -> None:
    old = [r[0] for r in c.execute(
        "SELECT id FROM visibility_batches ORDER BY id DESC LIMIT -1 OFFSET ?",
        (UNDO_HISTORY,))]
    for batch_id in old:
        c.execute("DELETE FROM visibility_undo WHERE batch_id = ?", (batch_id,))
        c.execute("DELETE FROM visibility_undo_rules WHERE batch_id = ?", (batch_id,))
        c.execute("DELETE FROM visibility_batches WHERE id = ?", (batch_id,))


@bp.post("/api/visibility/folder")
def set_folder_visibility():
    """A rule for a folder and everything in it, files added later included."""
    admin = require_admin()
    data = body()
    level = str(data.get("visibility") or "").lower()
    if level not in db.VIS_VALUES:
        fail(400, "Visibility must be public, family or hidden.")
    folder = _clean_dir(data.get("folder"))
    folder_id = _active_folder_id()
    if folder_id is None:
        fail(409, "No library folder has been set up yet.")
    if not folder:
        fail(403, "The whole library cannot be changed in one go. Set the visibility on a "
                  "folder, or on the photographs themselves.", folder="", visibility=level)
    wanted = db.VIS_VALUES[level]
    impact = _impact(folder_id, folder, wanted)
    # Showing files to more people is asked for twice.
    if impact["exposed"] and data.get("confirm") is not True:
        fail(409, "This would show files to more people. Confirm to go ahead.",
             needs_confirmation=True, impact=impact, folder=folder, visibility=level)

    c = conn()
    inside, params = subtree("", folder)
    below, below_params = _below(folder)
    with c:
        prior = c.execute("SELECT visibility FROM folder_rules WHERE folder_id = ? AND dir = ?",
                          (folder_id, folder)).fetchone()
        cur = c.execute(
            "INSERT INTO visibility_batches (created_at, created_by, scope, folder_id, dir, "
            "visibility, affected, exposed, had_rule, prior_rule) "
            "VALUES (?,?,'folder',?,?,?,?,?,?,?)",
            (time.time(), admin.id, folder_id, folder, wanted, impact["total"],
             impact["exposed"], 1 if prior else 0, int(prior["visibility"]) if prior else None))
        batch_id = int(cur.lastrowid)
        # Only rows that actually change are remembered.
        c.execute(
            f"INSERT INTO visibility_undo (batch_id, asset_id, visibility, vis_source) "
            f"SELECT ?, id, visibility, vis_source FROM assets WHERE folder_id = ? AND {inside} "
            f"AND (visibility <> ? OR vis_source <> 'rule')",
            [batch_id, folder_id, *params, wanted])
        # The rules below this folder give way to it; they are remembered for undo.
        c.execute(
            f"INSERT INTO visibility_undo_rules (batch_id, dir, visibility, created_at) "
            f"SELECT ?, dir, visibility, created_at FROM folder_rules "
            f"WHERE folder_id = ? AND {below}", [batch_id, folder_id, *below_params])
        c.execute(f"DELETE FROM folder_rules WHERE folder_id = ? AND {below}",
                  [folder_id, *below_params])
        c.execute(
            "INSERT INTO folder_rules (folder_id, dir, visibility, created_at) VALUES (?,?,?,?) "
            "ON CONFLICT (folder_id, dir) DO UPDATE SET visibility = excluded.visibility, "
            "created_at = excluded.created_at", (folder_id, folder, wanted, time.time()))
        c.execute(f"UPDATE assets SET visibility = ?, vis_source = 'rule' "
                  f"WHERE folder_id = ? AND {inside}", [wanted, folder_id, *params])
        _trim_history(c)
    scanner().generation += 1
    return jsonify({
        "updated": impact["total"], "folder": folder, "visibility": level, "impact": impact,
        "undo": _history(1), "rules": _rule_list(folder_id),
    })


def _history(limit: int) -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT b.*, f.path AS root, (SELECT COUNT(*) FROM visibility_undo u "
        "WHERE u.batch_id = b.id) AS restorable FROM visibility_batches b "
        "LEFT JOIN folders f ON f.id = b.folder_id ORDER BY b.id DESC LIMIT ?",
        (limit,)).fetchall()
    return [{
        "id": r["id"], "created_at": r["created_at"], "created_by": r["created_by"],
        "scope": r["scope"], "root": r["root"] or "", "folder": r["dir"] or "",
        "visibility": r["visibility"], "affected": r["affected"], "exposed": r["exposed"],
        "had_rule": r["had_rule"], "prior_rule": r["prior_rule"],
        "undone_at": r["undone_at"], "restorable": r["restorable"],
    } for r in rows]


@bp.get("/api/visibility/history")
def visibility_history():
    require_admin()
    return jsonify({"changes": _history(10),
                    "names": {str(k): v for k, v in db.VIS_NAMES.items()}})


@bp.post("/api/visibility/undo")
def undo_visibility():
    """Put a visibility change back exactly as it was: each file's level and
    where it came from, and the folder rules it replaced."""
    require_admin()
    raw = body().get("batch_id")
    batch_id: int | None = None
    if raw not in (None, "", 0, False):
        try:
            batch_id = int(raw)
        except (TypeError, ValueError, OverflowError):
            fail(400, "batch_id must be a number.")
    c = conn()
    if batch_id is None:
        row = c.execute("SELECT id FROM visibility_batches WHERE undone_at IS NULL "
                        "ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            fail(409, "There is nothing to undo.", ok=False)
        batch_id = int(row["id"])
    batch = c.execute("SELECT * FROM visibility_batches WHERE id = ?", (batch_id,)).fetchone()
    if batch is None:
        fail(409, "That change is no longer in the history.", ok=False)
    if batch["undone_at"]:
        fail(409, "That change has already been undone.", ok=False)

    with c:
        cur = c.execute(
            "UPDATE assets SET "
            "visibility = (SELECT u.visibility FROM visibility_undo u "
            "  WHERE u.batch_id = ? AND u.asset_id = assets.id), "
            "vis_source = (SELECT u.vis_source FROM visibility_undo u "
            "  WHERE u.batch_id = ? AND u.asset_id = assets.id) "
            "WHERE id IN (SELECT asset_id FROM visibility_undo WHERE batch_id = ?)",
            (batch_id, batch_id, batch_id))
        restored = cur.rowcount
        folder_id = batch["folder_id"]
        alive = folder_id is not None and c.execute(
            "SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone() is not None
        if batch["scope"] == "folder" and alive:
            folder = batch["dir"] or ""
            if batch["had_rule"]:
                c.execute(
                    "INSERT INTO folder_rules (folder_id, dir, visibility, created_at) "
                    "VALUES (?,?,?,?) ON CONFLICT (folder_id, dir) DO UPDATE SET "
                    "visibility = excluded.visibility, created_at = excluded.created_at",
                    (folder_id, folder, batch["prior_rule"], time.time()))
            else:
                c.execute("DELETE FROM folder_rules WHERE folder_id = ? AND dir = ?",
                          (folder_id, folder))
            c.execute(
                "INSERT OR REPLACE INTO folder_rules (folder_id, dir, visibility, created_at) "
                "SELECT ?, dir, visibility, COALESCE(created_at, ?) FROM visibility_undo_rules "
                "WHERE batch_id = ?", (folder_id, time.time(), batch_id))
        c.execute("UPDATE visibility_batches SET undone_at = ? WHERE id = ?",
                  (time.time(), batch_id))
    scanner().generation += 1
    return jsonify({"ok": True, "batch_id": batch_id, "restored": restored,
                    "folder": batch["dir"] or "", "visibility": int(batch["visibility"]),
                    "rules": _rule_list(_active_folder_id())})


# --- the two downloads -----------------------------------------------------------------


@bp.get("/admin/backup")
def backup_download():
    """The index and settings as a zip, to keep somewhere else."""
    require_admin()
    return _download(backups.download(cfg().data_dir), "application/zip",
                     f"ninaivu-lite-backup-{datetime.now():%Y-%m-%d}.zip")


@bp.get("/admin/export")
def export_download():
    """Everything the household made, for moving up to Ninaivu."""
    require_admin()
    return _download(export.dumps(conn(), cfg()), "application/json",
                     f"ninaivu-lite-export-{datetime.now():%Y-%m-%d}.json")

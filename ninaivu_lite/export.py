"""Everything a household made in Ninaivu Lite, in one file, for moving up to
Ninaivu when they need more (faces, search by description, cloud backup).

The file holds no photos. A photo is named by its folder and its path inside
that folder, which is exactly how Ninaivu finds the same file once it has
scanned the same folders. The format is described in docs/UPGRADE.md and
versioned by ``format``; a field is only ever added, never changed in meaning.

What carries across unchanged:

* roles: ``admin`` / ``family`` / ``guest`` mean the same in both;
* visibility: 0 public, 1 family, 2 hidden, the same numbers in both;
* passwords: ``scrypt$n$r$p$salt$hash``, the format Ninaivu verifies, so
  nobody has to choose a new one (a PBKDF2 hash from a Python without
  scrypt is marked, and that person sets a new password after moving);
  PINs are hashed the same way;
* folder rules and each person's assigned folder, by library folder and the
  path inside it, so who sees what is the same after the move;
* turns and flips set by hand, which exist only in the index;
* share links: the token is kept, so a link already sent keeps working if
  Ninaivu answers on the same address.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from .db import VIS_FAMILY
from .version import __version__

FORMAT = "ninaivu-lite-export"
FORMAT_VERSION = 1


def _ref(row: sqlite3.Row) -> dict[str, str]:
    rel = f"{row['dir']}/{row['name']}" if row["dir"] else row["name"]
    return {"folder": row["root"], "path": rel}


def _scheme(stored: str | None) -> str | None:
    return stored.split("$", 1)[0] if stored else None


def build(conn: sqlite3.Connection, cfg) -> dict[str, Any]:
    users = {r["id"]: r for r in conn.execute("SELECT * FROM users ORDER BY id")}
    name_of = {uid: r["username"] for uid, r in users.items()}
    assets = {r["id"]: r for r in conn.execute(
        "SELECT a.id, a.dir, a.name, a.visibility, a.vis_source, a.rotation, a.mirror, "
        "a.rot_source, f.path AS root FROM assets a "
        "JOIN folders f ON f.id = a.folder_id")}

    out: dict[str, Any] = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "app_version": __version__,
        "exported_at": time.time(),
        "folders": list(cfg.folders),
        "default_folder": cfg.active_folder,
        "default_language": cfg.language,
        "house_name": cfg.house_name,
        "open_browsing": cfg.open_browsing,
        "people": [{
            "username": r["username"], "name": r["display_name"], "role": r["role"],
            "password": r["password"], "password_scheme": _scheme(r["password"]),
            # A PIN is kept as a hash in the same format as a password.
            "pin": r["pin"], "pin_scheme": _scheme(r["pin"]),
            # The folder this person sees (absolute), or null for everything.
            "library": r["library"],
            "color": r["color"], "language": r["language"], "home_label": r["home_label"],
            "active": bool(r["active"]), "must_change": bool(r["must_change"]),
            "created_at": r["created_at"], "last_login": r["last_login"],
            "created_by": name_of.get(r["created_by"]),
        } for r in users.values()],
        # Who sees each folder: a rule covers the folder and everything below it,
        # files added later included. "path" is inside "folder"; "" is the whole folder.
        "folder_rules": [{"folder": r["root"], "path": r["dir"], "level": r["visibility"],
                          "created_at": r["created_at"]}
                         for r in conn.execute(
                             "SELECT f.path AS root, r.dir, r.visibility, r.created_at "
                             "FROM folder_rules r JOIN folders f ON f.id = r.folder_id "
                             "ORDER BY f.path, r.dir")],
        # Only what differs from the default, which is what an admin decided.
        # "source" is "rule" (from a folder rule) or "item" (set on that file).
        "visibility": [{**_ref(a), "level": a["visibility"], "source": a["vis_source"]}
                       for a in assets.values()
                       if a["visibility"] != VIS_FAMILY or a["vis_source"] != "default"],
        "favourites": [{**_ref(assets[r["asset_id"]]), "user": name_of.get(r["user_id"]),
                        "added_at": r["added_at"]}
                       for r in conn.execute(
                           "SELECT * FROM user_assets WHERE favorite = 1 ORDER BY added_at")
                       if r["asset_id"] in assets],
        # A turn or flip somebody set by hand (Rotate and Flip in the viewer).
        # It lives only in the index, never in the file, so this is the one
        # place it can move from: mirrored left to right first, then turned
        # "rotation" degrees clockwise.
        "turns": [{**_ref(a), "rotation": a["rotation"], "mirror": bool(a["mirror"])}
                  for a in assets.values() if a["rot_source"] == "manual"
                  and (a["rotation"] or a["mirror"])],
        "albums": [],
        "shares": [],
    }
    for album in conn.execute("SELECT * FROM albums ORDER BY id"):
        items = conn.execute("SELECT asset_id, added_at FROM album_items WHERE album_id = ? "
                             "ORDER BY added_at, asset_id", (album["id"],)).fetchall()
        cover = assets.get(album["cover_id"]) if album["cover_id"] else None
        out["albums"].append({
            "id": album["id"], "name": album["name"],
            "owner": name_of.get(album["created_by"]),
            "created_at": album["created_at"],
            "cover": _ref(cover) if cover is not None else None,
            "items": [{**_ref(assets[i["asset_id"]]), "added_at": i["added_at"]}
                      for i in items if i["asset_id"] in assets],
        })
    for share in conn.execute("SELECT * FROM shares ORDER BY id"):
        entry = {"token": share["token"], "kind": share["scope"],
                 "owner": name_of.get(share["created_by"]), "password": share["password"],
                 "expires_at": share["expires_at"], "created_at": share["created_at"],
                 "views": share["view_count"]}
        if share["scope"] == "album":
            entry["album_id"] = share["target_id"]
        elif share["target_id"] in assets:
            entry.update(_ref(assets[share["target_id"]]))
        else:
            continue
        out["shares"].append(entry)
    return out


def dumps(conn: sqlite3.Connection, cfg) -> bytes:
    return json.dumps(build(conn, cfg), ensure_ascii=False, indent=1).encode("utf-8")

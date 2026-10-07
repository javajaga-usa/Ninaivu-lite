"""The three screens taken from Ninaivu, and the small files around them."""

from __future__ import annotations

import hmac
import threading
import time

from flask import Blueprint, current_app, jsonify, render_template, request

from . import auth
from .common import body, cfg, conn, fail
from .version import __version__

bp = Blueprint("pages", __name__)


@bp.get("/")
def gallery():
    return render_template("index.html", app_name="Ninaivu")


@bp.get("/admin")
@bp.get("/admin/")
def console():
    # The page itself is open: it shows its own sign-in card, and every call it
    # makes checks for an administrator.
    return render_template("admin.html", app_name="Ninaivu")


@bp.get("/share/<token>")
def share_page(token: str):
    row = conn().execute("SELECT expires_at FROM shares WHERE token = ?", (token,)).fetchone()
    if row is None:
        return render_template("share.html", token=token, app_name="Ninaivu"), 404
    if row["expires_at"] and row["expires_at"] < time.time():
        return render_template("share.html", token=token, app_name="Ninaivu"), 410
    response = current_app.make_response(
        render_template("share.html", token=token, app_name="Ninaivu"))
    response.headers["X-Robots-Tag"] = "noindex"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


_shell_version: dict[str, str] = {}


def shell_version(static: str) -> str:
    """A name for this exact set of the app's files: the version plus a hash
    of every script, stylesheet and translation, so a worker sent after an
    upgrade (or any change) is a new one and drops the old caches."""
    if static not in _shell_version:
        import hashlib
        from pathlib import Path
        digest = hashlib.sha256(__version__.encode())
        root = Path(static)
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in (".js", ".mjs", ".css", ".json", ".html"):
                digest.update(path.relative_to(root).as_posix().encode())
                digest.update(path.read_bytes())
        _shell_version[static] = f"{__version__}-{digest.hexdigest()[:12]}"
    return _shell_version[static]


@bp.get("/sw.js")
def service_worker():
    from pathlib import Path
    static = current_app.static_folder or ""
    text = (Path(static) / "sw.js").read_text(encoding="utf-8")
    text = text.replace("const CACHE_VERSION = 'lite-1';",
                        f"const CACHE_VERSION = 'lite-{shell_version(static)}';", 1)
    response = current_app.response_class(text, mimetype="text/javascript")
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.get("/manifest.webmanifest")
def manifest():
    name = cfg().house_name_effective
    return current_app.response_class(
        _manifest(name, "/", "/static/icons"), mimetype="application/manifest+json")


@bp.get("/admin/manifest.webmanifest")
def admin_manifest():
    return current_app.response_class(
        _manifest(f"{cfg().house_name_effective} · Admin", "/admin", "/static/icons/admin"),
        mimetype="application/manifest+json")


def _manifest(name: str, start: str, icons: str) -> str:
    import json
    return json.dumps({
        "name": name, "short_name": name[:12], "start_url": start, "scope": start,
        "display": "standalone", "background_color": "#101114", "theme_color": "#101114",
        "icons": [
            {"src": f"{icons}/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": f"{icons}/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": f"{icons}/icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
    }, ensure_ascii=False)


@bp.get("/api/health")
@bp.get("/healthz")
def health():
    # Which data folder: two copies of Ninaivu Lite on one computer (an
    # installed one and a portable one) are told apart by this.
    return jsonify(ok=True, app="Ninaivu Lite", version=__version__,
                   instance=current_app.config.get("INSTANCE"), busy=busy())


def busy() -> str | None:
    """What a Stop would cut short: a copy into the archive, to a drive, or
    from a phone. The Control Panel asks before stopping it."""
    config = current_app.config
    for name, key in (("phone", "PHONE_IMPORT"), ("import", "IMPORTER"),
                      ("export", "EXPORTER")):
        job = config.get(key)
        if job is not None and getattr(job, "running", False):
            return name
    return None


@bp.post("/api/local/stop")
def local_stop():
    """The Control Panel's Stop: only from this computer, and only with the
    token the server wrote in its data folder when it started."""
    stop = current_app.config.get("STOP")
    token = current_app.config.get("STOP_TOKEN")
    given = str(body().get("token") or "")
    if not (stop and token and auth.is_local_request(request.remote_addr, request.headers)
            and hmac.compare_digest(given, token)):
        fail(403, "That isn't allowed.")
    threading.Thread(target=stop, name="stopping", daemon=True).start()
    return jsonify(ok=True)


@bp.get("/readyz")
def ready():
    try:
        conn().execute("SELECT 1").fetchone()
        auth.needs_setup(conn())
        ok = True
    except Exception:  # noqa: BLE001
        ok = False
    return jsonify(ok=ok, checks={"database": ok}, time=time.time()), (200 if ok else 503)

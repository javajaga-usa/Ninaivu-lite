"""The three screens taken from Ninaivu, and the small files around them."""

from __future__ import annotations

import hmac
import threading
import time

from flask import Blueprint, current_app, jsonify, render_template, request, send_from_directory

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


@bp.get("/sw.js")
def service_worker():
    response = send_from_directory(current_app.static_folder, "sw.js",
                                   mimetype="text/javascript", max_age=0)
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
    return jsonify(ok=True, app="Ninaivu Lite", version=__version__)


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

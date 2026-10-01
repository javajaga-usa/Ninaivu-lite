"""Ninaivu Lite — your family's photographs, at home. The small, steady edition.

Ninaivu's own screens (the family gallery, the admin console at /admin and the
share page) on one port, served by a lighter engine: no AI, no cloud, nothing
that changes or deletes a photograph. :func:`create_app` builds it;
``python -m ninaivu_lite`` runs it.
"""

from __future__ import annotations

from .version import APP_NAME, __version__  # noqa: F401, I001 — first; other modules read it

import hashlib
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, g, request

from . import common
from .common import ApiError
from .config import Config
from .scanner import Scanner

log = logging.getLogger(__name__)

CSP = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
       "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
       "font-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": CSP,
}

#: Answered for someone not signed in even when guest browsing is off.
OPEN_PREFIXES = ("/static/", "/api/auth/", "/api/share/", "/share/")
OPEN_PATHS = {"/", "/admin", "/admin/", "/sw.js", "/healthz", "/readyz", "/api/health",
              "/manifest.webmanifest", "/admin/manifest.webmanifest", "/favicon.ico"}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def create_app(cfg: Config | None = None, *, addresses: list[str] | None = None,
               scanner: Scanner | None = None) -> Flask:
    """Build the application. *addresses* are the home-network addresses this
    computer answers on, printed for the family's phones."""
    cfg = cfg or Config()
    app = Flask(__name__)
    app.config["LITE"] = cfg
    app.config["ADDRESSES"] = list(addresses or [])
    app.config["SCANNER"] = scanner or Scanner(cfg.data_dir, cfg.folders)
    app.config["SCANNER"].auto = cfg.watch
    app.json.ensure_ascii = False
    app.json.sort_keys = False

    from . import api_admin, api_auth, api_gallery, api_share, pages
    for module in (pages, api_auth, api_gallery, api_share, api_admin):
        app.register_blueprint(module.bp)

    asset_cache: dict[str, str] = {}

    @app.context_processor
    def template_helpers() -> dict[str, Any]:
        def asset(path: str) -> str:
            """A static URL that changes when the file does, so it can be cached."""
            if path not in asset_cache:
                whole = Path(app.static_folder or "") / path.removeprefix("/static/")
                try:
                    asset_cache[path] = hashlib.sha256(whole.read_bytes()).hexdigest()[:12]
                except OSError:
                    asset_cache[path] = __version__
            return f"{path}?v={asset_cache[path]}"

        tamil_font = (Path(app.static_folder or "") / "fonts" / "NotoSansTamil.ttf").is_file()
        return {"asset": asset, "tamil_font": tamil_font, "map_tiles": False,
                "version": __version__}

    @app.before_request
    def guard():
        # Writes only from this site's own pages.
        if request.method in UNSAFE:
            site = request.headers.get("Sec-Fetch-Site")
            origin = request.headers.get("Origin")
            if site is not None:
                if site not in ("same-origin", "none"):
                    raise ApiError(403, "Cross-origin request refused.")
            elif origin and origin != "null" and urlsplit(origin).netloc != request.host:
                raise ApiError(403, "Cross-origin request refused.")
        # A closed library answers nobody who has not signed in.
        path = request.path
        if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
            return None
        if not cfg.open_browsing and common.user().anonymous:
            raise ApiError(401, "This library is private. Please sign in.")
        return None

    @app.after_request
    def headers(response):
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.path.startswith("/api/"):
            response.headers.add("Vary", "Cookie")
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.teardown_appcontext
    def close_db(_exc):
        handle = g.pop("db", None)
        if handle is not None:
            handle.close()

    @app.errorhandler(Exception)
    def any_error(exc: Exception):
        from werkzeug.exceptions import HTTPException
        if isinstance(exc, ApiError):
            return exc.get_response()
        code = exc.code if isinstance(exc, HTTPException) and exc.code else 500
        if code == 500:
            log.exception("unexpected error on %s", request.path)
        message = {400: "That request wasn't understood.", 403: "That isn't allowed.",
                   404: "Not found.", 405: "Not allowed.", 413: "That is too large.",
                   500: "Something went wrong on the server."}.get(code, "Something went wrong.")
        if request.path.startswith("/api/"):
            return ApiError(code, message).get_response()
        if isinstance(exc, HTTPException) and code != 500:
            return exc
        return app.response_class(
            "<!doctype html><meta charset=utf-8><title>Ninaivu Lite</title>"
            "<p style='font:16px system-ui;margin:3rem auto;max-width:30rem'>"
            "Something went wrong. Please go back and try again. "
            "/ ஏதோ தவறு நடந்துவிட்டது. பின்சென்று மீண்டும் முயலவும்.</p>",
            500, mimetype="text/html")

    return app

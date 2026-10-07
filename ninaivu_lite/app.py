"""The web application: Ninaivu's three screens and the JSON API behind them."""

from __future__ import annotations

import hashlib
import html
import ipaddress
import logging
import os
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, g, request

from . import auth, common, compress, drives, phones
from .common import ApiError
from .config import Config
from .importer import Importer
from .scanner import Scanner
from .version import __version__

log = logging.getLogger(__name__)

CSP = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
       "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
       "font-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": CSP,
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}

#: The largest request body any route takes: an edited photograph for the
#: library (api_sudar.MAX_BYTES). Anything bigger is refused before it is read.
MAX_REQUEST_BYTES = 100 * 1024 * 1024

#: Answered for someone not signed in even when guest browsing is off.
OPEN_PREFIXES = ("/static/", "/api/auth/", "/api/share/", "/share/",
                 # The sign-in screen shows people's pictures before anyone signs in.
                 "/api/avatar/")
OPEN_PATHS = {"/", "/admin", "/admin/", "/sw.js", "/healthz", "/readyz", "/api/health",
              "/manifest.webmanifest", "/admin/manifest.webmanifest", "/favicon.ico",
              "/api/local/stop"}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
#: What someone on a temporary password may still change (sign-in is open).
MUST_CHANGE_PATHS = {"/api/me", "/api/me/password"}

#: Endings a home network's own names use for this computer (its name plus one of these).
LOCAL_SUFFIXES = ("", ".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain")


_hosts_refused: set[str] = set()


def refuse_host(host: str):
    """A name this computer does not know (a bookmark with a router's or a
    NAS's name, after 1.6.0 started checking): said once in the log, with the
    name to add, and as a page a person can read, not raw JSON."""
    name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
    if name not in _hosts_refused and len(_hosts_refused) < 100:
        _hosts_refused.add(name)
        log.warning("refused a request for %r: add it to allowed_hosts in settings.json "
                    "(or NINAIVU_ALLOWED_HOSTS) if it is a name of this computer", name)
    if request.path.startswith("/api/"):
        raise ApiError(400, "This address is not one Ninaivu Lite answers to. Open it "
                            "with this computer's address instead.")
    safe = html.escape(name)
    page = (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        "<title>Ninaivu Lite</title><div style='font:16px/1.5 system-ui;margin:3rem auto;"
        "max-width:34rem;padding:0 1rem'>"
        f"<h1 style='font-size:1.3rem'>Ninaivu Lite does not answer to “{safe}”</h1>"
        "<p>Open it with this computer's address instead (shown in the Control Panel, for "
        "example http://192.168.1.20:8080). If this name is meant to work, the person who looks "
        f"after Ninaivu Lite adds <code>{safe}</code> to <code>allowed_hosts</code> in "
        "settings.json in its data folder (or <code>NINAIVU_ALLOWED_HOSTS</code>, in Docker) "
        "and restarts it.</p><hr>"
        f"<h1 style='font-size:1.3rem'>நினைவு லைட் “{safe}” என்ற பெயருக்குப் பதில் தராது</h1>"
        "<p>இந்தக் கணினியின் முகவரியுடன் திறக்கவும் (கட்டுப்பாட்டுப் பலகத்தில் காட்டப்படும், "
        "எ.கா. http://192.168.1.20:8080). இந்தப் பெயர் வேலை செய்ய வேண்டுமெனில், தரவுக் "
        f"கோப்புறையில் உள்ள settings.json இல் <code>allowed_hosts</code> இல் <code>{safe}</code> "
        "ஐச் சேர்த்து மறுதொடக்கம் செய்யவும்.</p></div>")
    from flask import Response
    return Response(page, 400, mimetype="text/html")


def host_allowed(host: str, extra: list[str] | tuple[str, ...] = ()) -> bool:
    """Whether a request's Host names this computer.

    A web page anywhere can point a name it owns at this computer's address
    (DNS rebinding), and the browser then treats Ninaivu Lite as that page's
    own site. Such a page always arrives under its own name, so only names
    this computer has are answered: an address typed directly (any IP), this
    computer's name on the home network, ``localhost``, and whatever the
    household added to ``allowed_hosts`` in settings.json or to the
    ``NINAIVU_ALLOWED_HOSTS`` environment variable, separated by commas (for
    a reverse proxy, a name of their own, or a container, whose own name is
    not the computer's)."""
    name = host.strip().lower()
    if name.startswith("["):                          # [::1]:8080
        name = name[1:name.find("]")] if "]" in name else name[1:]
    elif name.count(":") == 1:
        name = name.split(":", 1)[0]
    name = name.rstrip(".")
    if not name:
        return False
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    if name == "localhost" or name.endswith(".localhost"):
        return True
    own = {n.lower() for n in (socket.gethostname(), socket.gethostname().split(".")[0]) if n}
    if any(name == f"{base}{suffix}" for base in own for suffix in LOCAL_SUFFIXES):
        return True
    named = [*extra, *os.environ.get("NINAIVU_ALLOWED_HOSTS", "").split(",")]
    return name in {h.strip().lower().rstrip(".") for h in named if isinstance(h, str)}


#: Body types a page on another site may send without asking first (CORS's
#: "simple" requests); anything else, or the header below, needs the
#: browser's permission, which Ninaivu Lite never gives.
SIMPLE_TYPES = {"", "application/x-www-form-urlencoded", "multipart/form-data", "text/plain"}
SCRIPT_HEADER = "X-Ninaivu"


def script_sent() -> bool:
    """Whether this request could only have come from a script on this site."""
    return bool(request.headers.get(SCRIPT_HEADER)) or request.mimetype not in SIMPLE_TYPES


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
    app.config["IMPORTER"] = Importer(cfg.data_dir)

    def imported(destination: str) -> None:
        # Files the importer (or a phone) put inside a library folder show in
        # the gallery now, not at the next half-hourly look (or never, with
        # watching off).
        from .importer import is_within
        if any(is_within(destination, root) for root in cfg.folders):
            app.config["SCANNER"].rescan()

    app.config["IMPORTER"].on_files = imported
    app.config["DRIVES"] = drives.Watcher()
    app.config["EXPORTER"] = drives.Exporter()
    app.config["PHONE_IMPORT"] = phones.PhoneImport()
    app.json.ensure_ascii = False
    app.json.sort_keys = False
    app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES

    from . import (api_admin, api_auth, api_drives, api_gallery, api_import, api_share,
                   api_sudar, pages)
    for module in (pages, api_auth, api_gallery, api_share, api_admin, api_import, api_drives,
                   api_sudar):
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
        from .version import about
        return {"asset": asset, "tamil_font": tamil_font, "map_tiles": False,
                "version": __version__, "about": about()}

    @app.before_request
    def guard():
        # Only for this computer's own names: never a stranger's name pointed here.
        if not host_allowed(request.host, cfg.allowed_hosts):
            return refuse_host(request.host)
        # Writes only from this site's own pages.
        if request.method in UNSAFE:
            site = request.headers.get("Sec-Fetch-Site")
            origin = request.headers.get("Origin")
            if site is not None:
                if site not in ("same-origin", "none"):
                    raise ApiError(403, "Cross-origin request refused.")
            elif origin and origin != "null":
                if urlsplit(origin).netloc != request.host:
                    raise ApiError(403, "Cross-origin request refused.")
            elif request.cookies.get(auth.SESSION_COOKIE) and not script_sent():
                # No origin to go by (plain HTTP sends no Sec-Fetch-Site, a
                # sandboxed or no-referrer page says "null"): a signed-in
                # write must be one a form or another site could not send.
                raise ApiError(403, "Cross-origin request refused.")
        # A closed library answers nobody who has not signed in.
        path = request.path
        if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
            return None
        if not cfg.open_browsing and common.user().anonymous:
            raise ApiError(401, "This library is private. Please sign in.", private=True)
        # A password an administrator set is only for getting in: nothing is
        # changed with it until its owner has chosen their own.
        if request.method in UNSAFE and path.startswith("/api/") \
                and path not in MUST_CHANGE_PATHS and common.user().must_change:
            raise ApiError(403, "Choose your own password first.", must_change=True)
        return None

    @app.after_request
    def headers(response):
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.path.startswith("/api/"):
            response.headers.add("Vary", "Cookie")
            response.headers.setdefault("Cache-Control", "no-store")
        signing_in = any(c.startswith(f"{auth.SESSION_COOKIE}=")
                         for c in response.headers.getlist("Set-Cookie"))
        if g.get("session_ended") and not signing_in:
            response.headers["X-Ninaivu-Session"] = "ended"
            if request.path.startswith("/api/"):
                # Pages read this header; a picture never does, so the cookie
                # is ended only where the page will hear why.
                response.headers["Clear-Site-Data"] = '"cache"'
                response.delete_cookie(auth.SESSION_COOKIE, path="/")
        return response

    # Registered after `headers`, so it runs first (Flask runs the hooks in
    # reverse): the gzip and the year-long cache for versioned static files
    # are decided with the headers above already in place.
    compress.install(app)

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

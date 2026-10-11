"""Large request bodies refused before they are read.

waitress reads a whole request, body and all, into memory or a temporary
file before Flask sees it, so a limit checked in a route comes too late: a
stranger on the home network could make the server spool a 100 MB body per
connection, a hundred connections at once, to the SD card of a Pi. Here the
request's first line and headers are looked at as soon as they have
arrived. A body larger than :data:`SMALL_BODY` (every JSON call takes at
most 1 MB) is read only for the two routes that take pictures, and only
from a signed-in person allowed to use them; anything else is a 413 at once,
before a byte of it is stored.
"""

from __future__ import annotations

import re
import sqlite3
import time
from http.cookies import CookieError, SimpleCookie
from pathlib import Path

from . import auth, db

#: Any body up to this size is read as before (the JSON calls take 1 MB).
SMALL_BODY = 2 * 1024 * 1024
#: Routes that take a picture, the most each takes, and who may send it.
LARGE_ROUTES = (
    # A video's preview picture, drawn by a family member's browser.
    (re.compile(r"^/api/asset/\d+/poster$"), 4 * 1024 * 1024 + 65536, auth.ROLE_FAMILY),
    # Sudar's edited copy, saved by an administrator.
    (re.compile(r"^/api/asset/\d+/edited-copy$"), 100 * 1024 * 1024, auth.ROLE_ADMIN),
)


def signed_in_role(data_dir: str, cookie_header: str) -> str | None:
    """The role of the person whose session cookie this is, read without
    writing anything, or None for no valid session."""
    try:
        token = SimpleCookie(cookie_header)[auth.SESSION_COOKIE].value
    except (CookieError, KeyError):
        return None
    if not token:
        return None
    path = Path(data_dir) / db.DB_FILE
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5)
    except (sqlite3.Error, ValueError):
        return None
    try:
        row = conn.execute(
            "SELECT u.role FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1",
            (auth.hash_token(token), time.time())).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return row[0] if row else None


def body_limit(data_dir: str, method: str, path: str, cookie_header: str) -> int:
    """The largest body this request may send."""
    if method in ("POST", "PUT", "PATCH"):
        for pattern, most, role in LARGE_ROUTES:
            if pattern.match(path):
                found = signed_in_role(data_dir, cookie_header)
                if found is not None and \
                        auth.ROLE_RANK.get(found, 0) >= auth.ROLE_RANK[role]:
                    return most
                break
    return SMALL_BODY


def channel_class(data_dir: str):
    """A waitress channel whose requests are held to :func:`body_limit`."""
    from waitress.channel import HTTPChannel
    from waitress.parser import HTTPRequestParser
    from waitress.utilities import RequestEntityTooLarge

    class Parser(HTTPRequestParser):
        limit: int | None = None

        def received(self, data):
            consumed = super().received(data)
            if self.error is not None:
                return consumed
            if self.limit is None and self.headers_finished:
                self.limit = body_limit(data_dir, self.command or "", self.path or "",
                                        self.headers.get("COOKIE", ""))
            if self.limit is not None and (self.content_length > self.limit
                                           or self.body_bytes_received > self.limit):
                self.error = RequestEntityTooLarge(f"exceeds max_body of {self.limit}")
                self.completed = True
            return consumed

    class Channel(HTTPChannel):
        parser_class = Parser

    return Channel

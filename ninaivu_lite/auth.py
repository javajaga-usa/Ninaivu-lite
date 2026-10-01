"""People, passwords, PINs and sessions — Ninaivu's model, kept small.

Three roles, each seeing up to one visibility level:

========  ==========================  =====================================
Role      Sees                        Signs in
========  ==========================  =====================================
admin     Public, Family and Hidden   username and password (console too)
family    Public and Family           tap a profile; PIN or password if set
guest     Public only                 tap a profile; PIN or password if set
========  ==========================  =====================================

Someone browsing without signing in ("Just looking") is the anonymous guest,
allowed only while ``open_browsing`` is on.

Messages are Ninaivu's own English sentences, so the Tamil translations that
came with Ninaivu's screens apply to them unchanged. Session cookies are
random; only their SHA-256 is stored.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any

from .db import VIS_FAMILY, VIS_HIDDEN, VIS_PUBLIC

ROLE_GUEST, ROLE_FAMILY, ROLE_ADMIN = "guest", "family", "admin"
ROLES = (ROLE_GUEST, ROLE_FAMILY, ROLE_ADMIN)
ROLE_RANK = {ROLE_GUEST: 0, ROLE_FAMILY: 1, ROLE_ADMIN: 2}
ROLE_LABELS = {ROLE_GUEST: "Guest", ROLE_FAMILY: "Family member", ROLE_ADMIN: "Admin"}
MAX_VISIBILITY = {ROLE_ADMIN: VIS_HIDDEN, ROLE_FAMILY: VIS_FAMILY, ROLE_GUEST: VIS_PUBLIC}

SESSION_COOKIE = "ninaivu_session"
SESSION_TTL = 30 * 24 * 3600
SESSION_REFRESH_AFTER = 24 * 3600
MIN_PASSWORD = 8
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,30}$")
PIN_RE = re.compile(r"^\d{4,8}$")
COMMON_PASSWORDS = {"password", "12345678", "qwertyui", "ninaivu123"}
EASY_PINS = {"0000", "1234", "1111", "123456", "000000"}
AVATAR_COLORS = ["#1f6fb2", "#6247d6", "#b5306f", "#b4531a",
                 "#1d7a47", "#0d7477", "#c02e36", "#7a5c1e"]


class AccountError(ValueError):
    """A problem to show the person, in Ninaivu's words."""


# --- secrets ---------------------------------------------------------------------

_SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)
_PBKDF2_ROUNDS = 310_000


def hash_password(secret: str) -> str:
    """``scrypt$n$r$p$salt$hash`` — the format Ninaivu verifies. PBKDF2 only
    for a Python built without scrypt (LibreSSL)."""
    salt = secrets.token_bytes(16)
    raw = secret.encode("utf-8")
    if hasattr(hashlib, "scrypt"):
        digest = hashlib.scrypt(raw, salt=salt, **_SCRYPT)
        return "scrypt${n}${r}${p}${s}${h}".format(**_SCRYPT, s=salt.hex(), h=digest.hex())
    digest = hashlib.pbkdf2_hmac("sha256", raw, salt, _PBKDF2_ROUNDS)
    return f"pbkdf2${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(secret: str, stored: str | None) -> bool:
    if not stored or not secret:
        return False
    try:
        parts = stored.split("$")
        raw = secret.encode("utf-8")
        if parts[0] == "scrypt":
            _, n, r, p, salt, expected = parts
            digest = hashlib.scrypt(raw, salt=bytes.fromhex(salt), n=int(n), r=int(r),
                                    p=int(p), dklen=len(expected) // 2)
        elif parts[0] == "pbkdf2":
            _, rounds, salt, expected = parts
            digest = hashlib.pbkdf2_hmac("sha256", raw, bytes.fromhex(salt), int(rounds))
        else:
            return False
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError, AttributeError):
        return False


def password_problem(password: str) -> str | None:
    if len(password or "") < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if password.lower() in COMMON_PASSWORDS:
        return "That password is too common — pick something else."
    return None


def username_problem(username: str) -> str | None:
    if not USERNAME_RE.match(username or ""):
        return ("Usernames are 2–31 characters: lowercase letters, digits, "
                "dots, dashes or underscores, starting with a letter or digit.")
    return None


def pin_problem(pin: str) -> str | None:
    if not PIN_RE.match(pin or ""):
        return "A PIN is 4 to 8 digits."
    if pin in EASY_PINS:
        return "That PIN is too easy to guess."
    return None


# --- people ----------------------------------------------------------------------------

def initials(name: str) -> str:
    parts = [p for p in re.split(r"[\s_-]+", (name or "").strip()) if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    role: str
    active: bool = True
    color: str | None = None
    must_change: bool = False
    created_at: float = 0.0
    last_login: float | None = None
    library: str | None = None
    has_pin: bool = False
    has_password: bool = False
    home_label: str | None = None
    language: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def is_guest(self) -> bool:
        return self.role == ROLE_GUEST

    @property
    def anonymous(self) -> bool:
        return self.id == 0

    @property
    def family_or_more(self) -> bool:
        return ROLE_RANK.get(self.role, 0) >= ROLE_RANK[ROLE_FAMILY]

    @property
    def max_visibility(self) -> int:
        return MAX_VISIBILITY.get(self.role, VIS_PUBLIC)

    @property
    def requires_secret(self) -> bool:
        return self.is_admin or self.has_pin or self.has_password

    @property
    def assigned_library(self) -> str | None:
        return None if self.is_admin else (self.library or None)

    @property
    def entry(self) -> str:
        return "pin" if self.has_pin else ("password" if self.has_password else "open")

    def colour(self) -> str:
        if self.id == 0:
            return self.color or "#8b94a3"
        return self.color or AVATAR_COLORS[self.id % len(AVATAR_COLORS)]

    def public(self) -> dict[str, Any]:
        """The shape Ninaivu's screens read."""
        return {
            "id": self.id,
            "username": self.username,
            "name": self.display_name,
            "role": self.role,
            "role_label": ROLE_LABELS.get(self.role, self.role),
            "active": self.active,
            "avatar": None,
            "color": self.colour(),
            "initials": initials(self.display_name),
            "anonymous": self.id == 0,
            "must_change": self.must_change,
            "scope": None,
            "library": self.assigned_library,
            "locked": self.requires_secret,
            "home_label": self.home_label or "",
            "language": self.language or "",
            "can": {
                "download": self.family_or_more,
                "favorite": self.family_or_more,
                "rotate": False,               # Lite never changes a photograph
                "set_visibility": self.is_admin,
                "manage_people": self.is_admin,
                "delete_media": False,         # Lite never deletes a photograph
                "manage_library": self.is_admin,
                "see_hidden": self.is_admin,
            },
        }

    def picker_entry(self) -> dict[str, Any]:
        public = self.public()
        return {key: public[key] for key in ("id", "name", "role", "role_label", "avatar",
                                             "color", "initials", "locked")} | {"kind": self.entry}


ANONYMOUS = User(id=0, username="guest", display_name="Guest", role=ROLE_GUEST, color="#8b94a3")


def _user(row: sqlite3.Row | None) -> User | None:
    if row is None:
        return None
    return User(
        id=row["id"], username=row["username"], display_name=row["display_name"],
        role=row["role"], active=bool(row["active"]), color=row["color"],
        must_change=bool(row["must_change"]), created_at=row["created_at"] or 0.0,
        last_login=row["last_login"], library=row["library"],
        has_pin=bool(row["pin"]), has_password=bool(row["password"]),
        home_label=row["home_label"], language=row["language"],
    )


def get_user(conn: sqlite3.Connection, user_id: int) -> User | None:
    return _user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def get_user_by_name(conn: sqlite3.Connection, username: str) -> User | None:
    return _user(conn.execute("SELECT * FROM users WHERE username = ?",
                              ((username or "").strip().lower(),)).fetchone())


def list_users(conn: sqlite3.Connection, include_inactive: bool = True) -> list[User]:
    sql = "SELECT * FROM users" + ("" if include_inactive else " WHERE active = 1")
    rows = conn.execute(sql + " ORDER BY display_name COLLATE NOCASE")
    return [u for u in (_user(r) for r in rows) if u]


def pickable_profiles(conn: sqlite3.Connection) -> list[User]:
    """Profiles on the picker: active, not administrators."""
    return [u for u in list_users(conn, include_inactive=False) if not u.is_admin]


def needs_setup(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM users WHERE role = ? AND active = 1 LIMIT 1",
                        (ROLE_ADMIN,)).fetchone() is None


def count_admins(conn: sqlite3.Connection, *, active_only: bool = True) -> int:
    sql = "SELECT COUNT(*) FROM users WHERE role = ?" + (" AND active = 1" if active_only else "")
    return conn.execute(sql, (ROLE_ADMIN,)).fetchone()[0]


def create_user(conn: sqlite3.Connection, username: str, *, password: str = "",
                pin: str = "", name: str = "", role: str = ROLE_FAMILY,
                library: str | None = None, must_change: bool | None = None,
                created_by: int | None = None, language: str | None = None) -> User:
    username = (username or "").strip().lower()
    if role not in ROLES:
        raise AccountError("Unknown role.")
    problem = username_problem(username)
    if not problem and password:
        problem = password_problem(password)
    if not problem and pin:
        problem = pin_problem(pin)
    if not problem and role == ROLE_ADMIN and not password:
        problem = "An administrator signs in with a password. Give them one."
    if problem:
        raise AccountError(problem)
    if must_change is None:
        must_change = bool(password) and created_by is not None
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO users (username, display_name, role, password, pin, library, "
                "must_change, created_by, language) VALUES (?,?,?,?,?,?,?,?,?)",
                (username, (name or "").strip()[:60] or username, role,
                 hash_password(password) if password else None,
                 hash_password(pin) if pin else None, library or None,
                 int(must_change), created_by, language))
    except sqlite3.IntegrityError:
        raise AccountError("That username is already taken.") from None
    user = get_user(conn, int(cur.lastrowid))
    assert user is not None
    return user


def set_password(conn: sqlite3.Connection, user_id: int, password: str, *,
                 must_change: bool = False, keep_token: str | None = None) -> None:
    """A new password; every other session of that person ends."""
    problem = password_problem(password)
    if problem:
        raise AccountError(problem)
    with conn:
        conn.execute("UPDATE users SET password = ?, must_change = ? WHERE id = ?",
                     (hash_password(password), int(must_change), user_id))
        conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                     (user_id, hash_token(keep_token) if keep_token else ""))


def set_pin(conn: sqlite3.Connection, user_id: int, pin: str | None) -> None:
    if pin:
        problem = pin_problem(pin)
        if problem:
            raise AccountError(problem)
    with conn:
        conn.execute("UPDATE users SET pin = ? WHERE id = ?",
                     (hash_password(pin) if pin else None, user_id))


def update_profile(conn: sqlite3.Connection, user_id: int, **fields: Any) -> None:
    allowed = {k: v for k, v in fields.items()
               if k in {"display_name", "color", "role", "library", "home_label", "language",
                        "active"}}
    if not allowed:
        return
    with conn:
        conn.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k in allowed)} WHERE id = ?",
                     (*allowed.values(), user_id))
        if allowed.get("active") in (0, False):
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def end_all_sessions(conn: sqlite3.Connection, user_id: int) -> int:
    with conn:
        return conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,)).rowcount


_DECOY: str | None = None


def _waste_one_hash(secret: str) -> None:
    """The same work as a real check, so the time taken does not tell which
    usernames exist."""
    global _DECOY
    if _DECOY is None:
        _DECOY = hash_password(secrets.token_urlsafe(16))
    verify_password(secret or "-", _DECOY)


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> User | None:
    row = conn.execute("SELECT * FROM users WHERE username = ?",
                       ((username or "").strip().lower(),)).fetchone()
    if row is None or not row["password"]:
        _waste_one_hash(password)
        return None
    if not verify_password(password, row["password"]) or not row["active"]:
        return None
    with conn:
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (time.time(), row["id"]))
    return _user(row)


def enter_profile(conn: sqlite3.Connection, user_id: int, secret: str = "") -> User | None:
    """Sign in from the profile picker. Administrators use the password login."""
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    user = _user(row)
    if user is None or not user.active or user.is_admin:
        return None
    if user.has_pin:
        if not verify_password(secret, row["pin"]):
            return None
    elif user.has_password:
        if not verify_password(secret, row["password"]):
            return None
    with conn:
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (time.time(), user_id))
    return user


def check_own_password(conn: sqlite3.Connection, user_id: int, password: str) -> bool:
    row = conn.execute("SELECT password FROM users WHERE id = ? AND active = 1",
                       (user_id,)).fetchone()
    return bool(row) and verify_password(password, row["password"])


# --- sessions ---------------------------------------------------------------------------

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def start_session(conn: sqlite3.Connection, user_id: int, agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, seen_at, expires_at, agent) "
            "VALUES (?,?,?,?,?,?)",
            (hash_token(token), user_id, now, now, now + SESSION_TTL, (agent or "")[:200]))
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    return token


def session_user(conn: sqlite3.Connection, token: str | None) -> User | None:
    if not token:
        return None
    key = hash_token(token)
    row = conn.execute("SELECT user_id, seen_at, expires_at FROM sessions WHERE token_hash = ?",
                       (key,)).fetchone()
    if row is None:
        return None
    now = time.time()
    user = get_user(conn, row["user_id"])
    if row["expires_at"] < now or user is None or not user.active:
        with conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (key,))
        return None
    if now - row["seen_at"] > SESSION_REFRESH_AFTER:
        with conn:
            conn.execute("UPDATE sessions SET seen_at = ?, expires_at = ? WHERE token_hash = ?",
                         (now, now + SESSION_TTL, key))
    return user


def end_session(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        with conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))


def session_counts(conn: sqlite3.Connection) -> dict[int, int]:
    now = time.time()
    return {r[0]: r[1] for r in conn.execute(
        "SELECT user_id, COUNT(*) FROM sessions WHERE expires_at > ? GROUP BY user_id", (now,))}


# --- limits -------------------------------------------------------------------------------

class Throttle:
    """After *limit* failures for a key within *window* seconds, refuse that key
    until the window has passed. In memory: a restart forgives, which is fine."""

    def __init__(self, limit: int = 8, window: float = 300.0) -> None:
        self.limit, self.window = limit, window
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, *keys: str) -> bool:
        now = time.time()
        with self._lock:
            for key in keys:
                kept = [t for t in self._fails.get(key, []) if now - t < self.window]
                self._fails[key] = kept
                if len(kept) >= self.limit:
                    return True
        return False

    def fail(self, *keys: str) -> None:
        now = time.time()
        with self._lock:
            if len(self._fails) > 10_000:
                self._fails.clear()
            for key in keys:
                self._fails.setdefault(key, []).append(now)

    def forget(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._fails.pop(key, None)


# --- the first administrator ---------------------------------------------------------

_SETUP_CODE: str | None = None


def setup_code() -> str:
    """A one-time code for making the first administrator from another device.
    Printed where the server starts."""
    global _SETUP_CODE
    if _SETUP_CODE is None:
        _SETUP_CODE = secrets.token_hex(5).upper()
    return _SETUP_CODE


FORWARDING_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "X-Real-IP", "Forwarded",
                      "CF-Connecting-IP", "True-Client-IP")


def is_local_request(remote_addr: str | None, headers) -> bool:
    """Did this request start on this computer? A loopback address with a
    proxy's forwarding header on it is somebody else's request."""
    try:
        loopback = ipaddress.ip_address((remote_addr or "").split("%", 1)[0]).is_loopback
    except ValueError:
        return False
    return loopback and not any(headers.get(h) for h in FORWARDING_HEADERS)

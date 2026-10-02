"""Is there a newer Ninaivu Lite? Asked of GitHub's releases page, once a day.

One small request to ``api.github.com`` for the latest release's tag, with a
five-second timeout, from a background thread, and the answer kept in the
data folder so the question is not asked again for a day. Nothing about the
household goes with it: the request carries no identifier beyond the
program's name. It can be switched off in the Control Panel, and the switch
lives in the same small file, not in the server's settings.

Standard library only; never raises.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from collections.abc import Callable

from .version import APP_NAME, __version__

REPO = "javajaga-usa/Ninaivu-lite"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
STATE_FILE = "update-check.json"
MAX_AGE = 24 * 60 * 60
TIMEOUT = 5.0

_NUMBERS = re.compile(r"\d+")


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.3.2' → (1, 3, 2). Anything unreadable is (0,)."""
    numbers = _NUMBERS.findall(str(text or "").split("-")[0])
    return tuple(int(n) for n in numbers) or (0,)


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse_version(latest) > parse_version(current)


def fetch_latest(timeout: float = TIMEOUT) -> dict[str, str] | None:
    """The latest release's version and page, from GitHub, or None."""
    request = urllib.request.Request(
        RELEASES_API, headers={"Accept": "application/vnd.github+json",
                               "User-Agent": f"{APP_NAME}/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read(200_000).decode("utf-8", "replace"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    tag = data.get("tag_name") if isinstance(data, dict) else None
    if not isinstance(tag, str) or not tag.strip():
        return None
    version = tag.strip().lstrip("vV")
    page = data.get("html_url") if isinstance(data.get("html_url"), str) else RELEASES_PAGE
    return {"version": version, "url": page}


def _path(data_dir: str | os.PathLike) -> Path:
    return Path(data_dir) / STATE_FILE


def load_state(data_dir: str | os.PathLike) -> dict[str, Any]:
    try:
        data = json.loads(_path(data_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(data_dir: str | os.PathLike, state: dict[str, Any]) -> None:
    path = _path(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".update-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
    except OSError:
        pass


def enabled(data_dir: str | os.PathLike) -> bool:
    return load_state(data_dir).get("enabled", True) is not False


def set_enabled(data_dir: str | os.PathLike, on: bool) -> None:
    state = load_state(data_dir)
    state["enabled"] = bool(on)
    save_state(data_dir, state)


def check(data_dir: str | os.PathLike, *, force: bool = False, current: str = __version__,
          fetch: Callable[[], dict[str, str] | None] = fetch_latest,
          now: Callable[[], float] = time.time) -> dict[str, Any] | None:
    """What is known about the latest version: ``{"version", "url",
    "available", "checked_at"}``, or None when nothing is known yet.

    Asks GitHub only when the last answer is older than a day (or *force*),
    and keeps the last answer when GitHub cannot be reached.
    """
    state = load_state(data_dir)
    last = state.get("latest") if isinstance(state.get("latest"), dict) else None
    fresh = last and now() - float(state.get("checked_at") or 0) < MAX_AGE
    if force or not fresh:
        found = fetch()
        if found:
            last = {"version": found["version"], "url": found.get("url") or RELEASES_PAGE}
            state.update(latest=last, checked_at=now())
            save_state(data_dir, state)
    if not last:
        return None
    return {"version": last["version"], "url": last.get("url") or RELEASES_PAGE,
            "available": is_newer(last["version"], current),
            "checked_at": state.get("checked_at")}

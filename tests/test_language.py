"""The guard that keeps the locale files honest (see the comment in i18n.js).

The keys are English sentences, written in the scripts as ``t('…')`` or
``i18n.key('…')`` and in the templates as ``data-i18n…="…"``. Every one of them
must be in ``en.json`` and ``ta.json``; a Tamil sentence must carry the same
``{placeholders}`` as its English; and every message the server sends as an
error (``fail(…, "…")``) must have its Tamil, because the page shows those
through ``i18n.t()`` as well.
"""

from __future__ import annotations

import ast
import html
import json
import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "ninaivu_lite"
STATIC = PACKAGE / "static"

#: t('…'), i18n.t("…"), key(`…`), i18n.key('…') — a literal first argument.
_CALL = re.compile(
    r"""(?<![\w$])(?:i18n\.)?(?:t|key)\(\s*"""
    r"""(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"|`((?:[^`\\$]|\\.)*)`)""")
_MARKER = re.compile(r'data-i18n(?:-title|-label|-placeholder|-rich)?="([^"]*)"')
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _unescape(text: str, quote: str) -> str:
    return (text.replace("\\" + quote, quote).replace("\\n", "\n")
            .replace("\\\\", "\\"))


def used_keys() -> dict[str, str]:
    """Every key the page's scripts and templates name, with where."""
    uses: dict[str, str] = {}
    scripts = sorted(STATIC.joinpath("js").rglob("*.js")) + sorted(STATIC.joinpath("js").rglob("*.mjs"))
    for path in scripts:
        source = path.read_text(encoding="utf-8")
        for match in _CALL.finditer(source):
            # `x.t(` is some other object's method, not a translation.
            before = source[max(0, match.start() - 1):match.start()]
            if before == "." and not source[max(0, match.start() - 5):match.start()] == "i18n.":
                continue
            quote = "'" if match.group(1) is not None else '"' if match.group(2) is not None else "`"
            text = next(g for g in match.groups() if g is not None)
            line = source.count("\n", 0, match.start()) + 1
            uses.setdefault(_unescape(text, quote), f"{path.name}:{line}")
    for path in sorted(PACKAGE.joinpath("templates").glob("*.html")):
        source = path.read_text(encoding="utf-8")
        for match in _MARKER.finditer(source):
            text = re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()
            if text:
                line = source.count("\n", 0, match.start()) + 1
                uses.setdefault(text, f"{path.name}:{line}")
    return uses


def server_messages() -> dict[str, str]:
    """Every literal message in a ``fail()`` or ``ApiError()`` of the server."""
    found: dict[str, str] = {}
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            message = node.args[1]
            status = node.args[0]
            # fail(status, message): a throttle's fail(key, bucket) is not one.
            if name in ("fail", "ApiError") and isinstance(status, ast.Constant) \
                    and isinstance(status.value, int) and isinstance(message, ast.Constant) \
                    and isinstance(message.value, str):
                found.setdefault(message.value, f"{path.name}:{node.lineno}")
    return found


def locale(code: str) -> dict[str, str]:
    return json.loads(STATIC.joinpath("i18n", f"{code}.json").read_text(encoding="utf-8"))


def test_the_guard_finds_what_it_guards():
    uses = used_keys()
    assert len(uses) > 500
    assert "1 item" in uses and "{count} items" in uses       # i18n.key() in i18n.js
    assert "Skip to gallery" in uses                         # data-i18n in a template
    assert "Sign in to do that." in server_messages()


@pytest.mark.parametrize("code", ["en", "ta"])
def test_every_key_the_page_uses_is_in_the_locale(code):
    table = locale(code)
    missing = {key: where for key, where in used_keys().items() if key not in table}
    assert not missing, f"missing from {code}.json: {missing}"


def test_english_maps_every_key_to_itself():
    assert {k: v for k, v in locale("en").items() if k != v} == {}


def test_tamil_keeps_the_placeholders():
    wrong = {key: value for key, value in locale("ta").items()
             if set(_PLACEHOLDER.findall(key)) != set(_PLACEHOLDER.findall(value))}
    assert not wrong


def test_every_server_message_has_its_tamil():
    tamil = locale("ta")
    missing = {text: where for text, where in server_messages().items() if text not in tamil}
    assert not missing, f"server messages missing from ta.json: {missing}"


def test_tamil_values_are_tamil():
    """A value left in English reads as a missing translation."""
    allowed = {"RGB", "HDR", "{pixels} px", "{items} → {level}.", "{percent}% {what} · {title}"}
    english = {key: value for key, value in locale("ta").items()
               if key not in allowed and re.search(r"[A-Za-z]{3}", value)
               and not re.search("[஀-௿]", value)}
    assert not english

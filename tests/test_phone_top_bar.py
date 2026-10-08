"""M15: on an iPhone the console's top bar (and the family app's) was drawn
soft, because Safari drew the bar's own name and buttons into its
backdrop-blur layer. The blur belongs on a layer behind the bar."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parent.parent / "ninaivu_lite" / "static" / "css"


def rules(css: str, selector: str) -> list[str]:
    """The bodies of every top-level rule (or rule in a media block) whose
    selector is exactly *selector*, in source order."""
    pattern = re.compile(r"(?:^|[}\n])\s*" + re.escape(selector) + r"\s*\{([^}]*)\}")
    return pattern.findall(css)


@pytest.mark.parametrize(("sheet", "bar"), [("admin.css", ".admin-bar"), ("style.css", ".topbar")])
def test_the_top_bar_blurs_behind_itself_not_its_content(sheet, bar):
    css = (CSS / sheet).read_text(encoding="utf-8")
    blurs = [body for body in rules(css, bar) if "backdrop-filter" in body]
    # Whatever sets a blur on the bar itself, the last word is "none".
    assert blurs and "backdrop-filter: none" in blurs[-1]
    assert "-webkit-backdrop-filter: none" in blurs[-1]
    layer = rules(css, bar + "::before")
    assert layer, f"{bar}::before is missing"
    first = layer[0]
    assert 'content: ""' in first and "z-index: -1" in first and "pointer-events: none" in first
    assert "-webkit-backdrop-filter:" in first and "blur(" in first

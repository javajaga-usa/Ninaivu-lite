"""The pages name every script module they import, however deep, as a
modulepreload link (PERF1): split into gallery/ and console/, the gallery's
thirty modules were found a level at a time, four round trips deep, and a
first visit over a 250 ms link took 3.5 s to its first picture where it took
2.8 s before the split (3.2 s with the links)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from conftest import ids

from ninaivu_lite.app import module_closure

PRELOAD = re.compile(r'<link rel="modulepreload" href="([^"]+)">')


def _page(client, path: str) -> tuple[list[str], dict, str]:
    page = client.get(path).get_data(as_text=True)
    mapping = json.loads(re.search(r'<script type="importmap">(.*?)</script>', page, re.S).group(1))
    return PRELOAD.findall(page), mapping["imports"], page


def _entry(page: str) -> str:
    """The page's own script: the last module (modern.js, the old-browser
    check, comes first)."""
    return re.findall(r'<script type="module" src="([^"?]+)', page)[-1]


def test_each_page_preloads_every_module_it_imports(app, admin):
    i = ids(app)
    token = admin.post("/api/shares", json={"scope": "asset", "target_id": i["beach.jpg"]}).json
    token = token.get("token") or token["share"]["token"]
    for path, client in (("/", admin), ("/admin", admin), (f"/share/{token}", app.test_client())):
        links, imports, page = _page(client, path)
        entry = _entry(page)
        wanted = [entry, *module_closure(app.static_folder, entry)]
        # At the address the import map gives each, which is the one the
        # browser keeps for a year and the one an import resolves to.
        assert links == [imports[url] for url in wanted], path
        # Asked for before the page's own script is even reached.
        assert page.index('rel="modulepreload"') < page.index('type="module"')
    links, _imports, _page_text = _page(admin, "/")
    # The deepest of the gallery's modules, reached only through two others.
    assert any(link.startswith("/static/js/activity.js?v=") for link in links)
    assert any(link.startswith("/static/js/gallery/state.js?v=") for link in links)
    # Sudar is loaded only when opened: not with the page.
    assert not any("sudar" in link for link in links)


def test_the_closure_follows_every_kind_of_static_import(tmp_path):
    js = tmp_path / "js"
    (js / "sub").mkdir(parents=True)
    (js / "entry.js").write_text(
        "import { a } from './a.js';\n"
        "import './side.js';\n"
        "export { b } from './sub/b.js';\n"
        "import {\n  c,\n  d,\n} from './c.js';\n"
        "const later = () => import('./lazy.js');\n"
        "// import { no } from './comment-free-text-is-fine.js' is never at a line start\n",
        encoding="utf-8")
    for name in ("a.js", "side.js", "c.js", "lazy.js"):
        (js / name).write_text("export const a = 1, c = 2, d = 3;\n", encoding="utf-8")
    (js / "sub" / "b.js").write_text("import { a } from '../a.js';\nexport const b = a;\n",
                                     encoding="utf-8")
    found = module_closure(str(tmp_path), "/static/js/entry.js")
    assert found == ["/static/js/a.js", "/static/js/side.js", "/static/js/sub/b.js",
                     "/static/js/c.js"]


def test_every_listed_module_exists(app):
    static = Path(app.static_folder)
    for entry in ("app.js", "admin.js", "share.js"):
        for url in module_closure(app.static_folder, f"/static/js/{entry}"):
            assert (static / url.removeprefix("/static/")).is_file(), url

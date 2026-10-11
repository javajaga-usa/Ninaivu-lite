"""Installers, release and the first day, audited again after Live Photos,
Duplicates, On this day and HEIC in every installer (index version 11): the
optional face straightening's OpenCV, wheels-only installer builds, the release
notes taken from the changelog, the role preview before any folder is chosen,
going back from a newer index, the guides, and an iPhone HEIC photo end to end."""

from __future__ import annotations

import io
import json
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from conftest import ids
from PIL import Image
from test_gallery_api import rescan
from test_live_photos_duplicates import make_clip

from ninaivu_lite import db

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "ninaivu_lite" / "static"


# --- packaging ----------------------------------------------------------------------------


def test_the_straightening_extra_asks_for_an_opencv_that_has_the_face_cascades():
    """OpenCV 5 has no CascadeClassifier and carries no cascades: installed
    from requirements-straighten.txt it left every untagged photo unturned,
    and the console kept saying to install OpenCV."""
    lines = [line.split("#")[0].strip() for line in
             (ROOT / "requirements-straighten.txt").read_text(encoding="utf-8").splitlines()]
    spec = next(line for line in lines if line.startswith("opencv"))
    assert spec.startswith("opencv-python-headless") and "<5" in spec.replace(" ", "")
    # Dependabot must not propose the 5.x bump again.
    bot = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    pip = bot[bot.index("package-ecosystem: pip"):]
    pip = pip[:pip.find("package-ecosystem", 1)] if "package-ecosystem" in pip[1:] else pip
    assert "dependency-name: opencv-python-headless" in pip
    assert "version-update:semver-major" in pip


def test_installer_builds_take_wheels_only():
    """A package with no wheel for the bundled Python stops the Windows and Mac
    builds by name instead of being compiled (pillow-heif needs libheif)."""
    ps1 = (ROOT / "installers" / "windows" / "build.ps1").read_text(encoding="utf-8")
    line = next(x for x in ps1.splitlines() if "requirements-installer.txt" in x and "pip" in x)
    assert "--only-binary=:all:" in line
    mac = (ROOT / "installers" / "macos" / "build.sh").read_text(encoding="utf-8")
    line = next(x for x in mac.splitlines() if "requirements-installer.txt" in x and "pip" in x)
    assert "--only-binary=:all:" in line
    linux = (ROOT / "installers" / "linux" / "build.sh").read_text(encoding="utf-8")
    assert "--only-binary=:all:" in linux


def test_a_linux_install_with_sudo_does_not_warn_about_pip_as_root():
    """`sudo sh Ninaivu-Lite-…-linux-arm64.sh` printed pip's warning that
    running it as root could render the system unusable."""
    install = (ROOT / "installers" / "linux" / "install.sh").read_text(encoding="utf-8")
    call = install[install.index("-m pip install"):]
    call = call[:call.index("wheels/*.whl")]
    assert "--root-user-action=ignore" in call


def test_a_release_waits_for_the_docker_image():
    """People build the Docker image from the released tag: a Dockerfile that
    no longer builds must stop the release, as a broken installer does."""
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    job = release[release.index("\n  release:"):]
    needs = re.search(r"needs: \[([^\]]*)\]", job).group(1)
    assert {"version", "tests", "windows", "macos", "linux", "docker"} <= {
        n.strip() for n in needs.split(",")}


def test_ci_runs_the_tests_with_what_the_installers_carry():
    """Every installer carries pillow-heif; the tests run with it too, so the
    HEIC path that ships is the path that is tested."""
    dev = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "-r requirements-installer.txt" in dev


def _notes_program() -> str:
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    line = next(x for x in release.splitlines() if "awk -v v=" in x and "CHANGELOG" in x)
    return re.search(r"awk -v v=\"\$version\" '(.*)' src/CHANGELOG.md", line).group(1)


@pytest.mark.skipif(shutil.which("awk") is None, reason="needs awk")
def test_release_notes_take_the_version_section_with_or_without_a_date():
    """check-release-version.sh accepts a bare `## 1.13.0` heading; the notes
    then came out with nothing under "What is in 1.13.0"."""
    program = _notes_program()

    def notes(version: str, changelog: str) -> str:
        return subprocess.run(["awk", "-v", f"v={version}", program], input=changelog,
                              capture_output=True, text=True, check=True).stdout

    log = "# Changelog\n\n## 1.13.0\n\nNew things.\n\n## 1.12.0 — 2026-10-10\n\nOld things.\n"
    assert notes("1.13.0", log).strip() == "New things."
    assert notes("1.12.0", log).strip() == "Old things."
    dated = "## 1.13.0 — 2026-10-12\n\nNew.\n\n## 1.13.01 — x\n\nNot this.\n"
    assert notes("1.13.0", dated).strip() == "New."
    # The real changelog's last release is found.
    real = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "Rotate, flip, then Save" in notes("1.12.0", real)


# --- the console before any folder is chosen ----------------------------------------------


@pytest.fixture()
def empty_admin(tmp_path):
    from conftest import sign_in

    from ninaivu_lite import auth, create_app
    from ninaivu_lite.config import Config

    data = tmp_path / "data"
    application = create_app(Config(data_dir=str(data), folders=[]), addresses=["192.168.1.20"])
    conn = db.connect(data)
    auth.create_user(conn, "appa", password="admin passphrase", name="Appa", role="admin")
    auth.create_user(conn, "amma", name="Amma", role="family")
    client = application.test_client()
    sign_in(application, client, "admin")
    return client, conn


def test_the_role_preview_says_as_whom_before_a_folder_is_chosen(empty_admin):
    """The Overview's "What each role sees" said "0 items · as undefined" on
    the first day, before a library folder was chosen."""
    client, conn = empty_admin
    guest = client.get("/api/admin/preview?role=guest").json
    assert guest["total"] == 0 and guest["items"] == []
    assert guest["as"] == "Guest" and guest["role_label"] == "Guest"
    assert guest["folders"] == []          # the page then says to choose a folder
    family = client.get("/api/admin/preview?role=family").json
    assert family["role_label"] == "Family member"
    amma = conn.execute("SELECT id FROM users WHERE username = 'amma'").fetchone()[0]
    person = client.get(f"/api/admin/preview?person={amma}").json
    assert person["as"] == "Amma" and person["total"] == 0
    assert client.get("/api/admin/preview?person=99999").status_code == 404
    assert client.get("/api/admin/preview?role=wizard").status_code == 400


def test_the_first_day_tour_names_the_new_features():
    source = (STATIC / "js" / "first-day.js").read_text(encoding="utf-8")
    tour = source[source.index("const TOUR"):source.index("];", source.index("const TOUR"))]
    assert "On this day, and nearby" in tour and "Live Photos and copies" in tour
    ta = json.loads((STATIC / "i18n" / "ta.json").read_text(encoding="utf-8"))
    assert ta["Live Photos and copies"] != "Live Photos and copies"
    # One Tamil word for a Live Photo.
    assert ta["Live Photos"].startswith("லைவ்")


# --- going back from a newer index --------------------------------------------------------


def test_a_newer_index_says_how_to_go_back(tmp_path):
    data = tmp_path / "data"
    db.connect(data).close()
    newer = len(db.MIGRATIONS) + 1
    raw = sqlite3.connect(data / db.DB_FILE)
    raw.execute(f"PRAGMA user_version={newer}")
    raw.commit()
    raw.close()
    with pytest.raises(db.NewerIndex) as said:
        db.connect(data)
    text = str(said.value)
    assert "Update Ninaivu Lite" in text and "--restore" in text
    assert f"before-update-from-index-{len(db.MIGRATIONS)}-" in text
    # Printed on a Windows console as it is: nothing outside ASCII.
    assert text.isascii()


# --- the guides ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["en.html", "ta.html"])
def test_the_html_guides_cover_the_new_features_and_index_11(name):
    """The PDFs are built from these: they had nothing on On this day, Photos
    taken nearby, Live Photos or Duplicates, nor on going back to 1.12.0."""
    html = (ROOT / "docs" / "guide" / name).read_text(encoding="utf-8")
    assert "before-update-from-index-10-" in html
    lang = json.loads((STATIC / "i18n" / f"{name[:2]}.json").read_text(encoding="utf-8"))
    for key in ("On this day", "Photos taken nearby", "LIVE", "Duplicates", "Open in gallery"):
        assert f'<span class="ui">{lang[key]}</span>' in html \
            or f'<span class="ui btn">{lang[key]}</span>' in html, key
    assert "MacOS/ninaivu-lite\" --server --reset-password" in html


@pytest.mark.parametrize("name", ["README.md", "docs/USER-GUIDE.md", "docs/USER-GUIDE.ta.md"])
def test_a_mac_user_is_told_how_to_reset_a_password_or_restore(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "/Applications/Ninaivu Lite.app/Contents/MacOS/ninaivu-lite\" --server" in text


def test_the_readme_says_how_to_go_back_and_names_the_new_features():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    updating = readme[readme.index("### Updating"):readme.index("**The releases are not")]
    assert "--restore" in updating and "before-update-" in updating
    for words in ("On this day", "Photos taken nearby", "Live Photo", "Duplicates"):
        assert words in readme, words


# --- an iPhone HEIC photo, end to end -----------------------------------------------------


def test_an_iphone_heic_and_its_movie_show_as_one_photograph(app, admin, library):
    """What every installer now carries: a HEIC is read for its date, size and
    a thumbnail, shown through a viewing copy, and paired with its .MOV."""
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    root, _ = library
    still = root / "iphone" / "IMG_7001.HEIC"
    still.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    exif[0x0132] = "2024:03:01 08:15:00"
    exif.get_ifd(0x8769)[0x9003] = "2024:03:01 08:15:00"
    Image.new("RGB", (640, 480), (240, 200, 40)).save(still, "HEIF", exif=exif.tobytes())
    make_clip(root / "iphone" / "IMG_7001.MOV", datetime(2024, 3, 1, 2, 45), 2.0)
    before = still.read_bytes()
    rescan(app)
    found = ids(app)
    sid, mid = found["IMG_7001.HEIC"], found["IMG_7001.MOV"]
    conn = db.connect(app.config["LITE"].data_dir)
    row = conn.execute("SELECT date_key, width, height, kind, live_id, thumb FROM assets "
                       "WHERE id = ?", (sid,)).fetchone()
    assert (row["kind"], row["width"], row["height"], row["live_id"]) == ("picture", 640, 480, mid)
    assert row["date_key"] == "2024-03-01"
    item = admin.get(f"/api/asset/{sid}").json
    assert item["is_live"] is True and item["view"].startswith("/api/preview/")
    view = admin.get(item["view"])
    assert view.status_code == 200
    assert Image.open(io.BytesIO(view.data)).size[0] > 0
    thumb = admin.get(f"/api/thumb/{sid}")
    assert thumb.status_code == 200 and Image.open(io.BytesIO(thumb.data)).size[0] > 0
    # Never written to.
    assert still.read_bytes() == before


# --- an import that leaves small files out says so ----------------------------------------


def test_an_import_says_how_many_small_files_it_left_out(tmp_path):
    """Photos under 24 KB (an old phone's VGA pictures) are left out of an
    import; the run said only "Finished: 0 files archived and verified", so a
    card could be wiped believing everything was safe."""
    from test_import import noisy_jpeg, run

    src = tmp_path / "Old phone"
    noisy_jpeg(src / "big.jpg", seed=3)
    for n in range(2):
        tiny = src / f"tiny-{n}.jpg"
        Image.new("RGB", (64, 48), (n * 90, 40, 200)).save(tiny, "JPEG")
        assert tiny.stat().st_size < 24 * 1024
    for mode in ("dry-run", "copy"):
        done = run(tmp_path / f"data-{mode}", [src], tmp_path / f"Archive-{mode}", mode=mode)
        said = done.job["message"]
        small = "{small} files under 24 KB were left out as icons or thumbnails."
        assert small in [part["key"] for part in said["more"]], mode
        assert "2 files under 24 KB were left out" in said["text"]
    ta = json.loads((STATIC / "i18n" / "ta.json").read_text(encoding="utf-8"))
    assert "{small}" in ta[small]


def test_a_restore_under_a_running_linux_service_says_how_to_stop_it(tmp_path, monkeypatch,
                                                                     capsys):
    """A Linux server has no Control Panel: the refusal named only its Stop."""
    from ninaivu_lite import __main__ as cli
    from ninaivu_lite import net
    monkeypatch.setattr(net, "already_running", lambda port, instance=None: True)
    assert cli.main(["--data", str(tmp_path / "data"), "--restore", str(tmp_path / "x.zip")]) == 2
    said = capsys.readouterr().err
    assert "Control Panel" in said and "sudo systemctl stop ninaivu-lite" in said


# --- the first day's Start stays off while the import it started runs ------------------------

FIRST_DAY_PAGE = """<!doctype html><meta charset="utf-8">
<div id="first-day" hidden><ol id="fd-steps"></ol><button id="fd-skip-all"></button>
<h2 id="fd-title"></h2><div id="fd-body"></div><p id="fd-note"></p>
<button id="fd-back"></button><button id="fd-next"></button></div>
<script type="module">
import { FirstDay } from '/static/js/first-day.js';
window.calls = [];
window.running = false;
const state = () => ({
  done: false, people: 0,
  library: { chosen: true, root: '/photos', roots: ['/photos'] },
  import: { running: window.running, sources: ['/old'], destination: '/archive' },
});
const json = async (url, opts = {}) => {
  window.calls.push(`${opts.method || 'GET'} ${url}`);
  if (url === '/api/archive/start') { window.running = true; return { destination: '/archive' }; }
  if (url === '/api/admin/first-day' && !opts.method) return state();
  return {};
};
const fd = new FirstDay({ json, toast: () => {}, pickFolder: () => {}, openPage: () => {},
                          refresh: () => {} });
await fd.maybeOpen();
window.ready = true;
</script>"""


def test_the_first_day_start_stays_off_until_the_import_has_finished():
    """Start the import stayed pressable once the import had started, so a
    second press started it again (or was refused with a cryptic answer)."""
    sync_api = pytest.importorskip("playwright.sync_api")
    import os
    with sync_api.sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch(
                executable_path=os.environ.get("NINAIVU_TEST_CHROMIUM") or None)
        except Exception as exc:  # noqa: BLE001
            if os.environ.get("NINAIVU_BROWSER_TESTS") == "1":
                raise
            pytest.skip(f"no Chromium for Playwright: {exc.__class__.__name__}")
        page = chromium.new_page()
        problems: list[str] = []
        page.on("pageerror", lambda error: problems.append(str(error)))

        def answer(route):
            path = route.request.url.split("fd.test", 1)[1].split("?", 1)[0]
            if path == "/":
                route.fulfill(body=FIRST_DAY_PAGE, content_type="text/html")
            elif path.startswith("/static/"):
                file = STATIC / path[len("/static/"):]
                kind = "application/json" if file.suffix == ".json" else "text/javascript"
                route.fulfill(body=file.read_bytes() if file.is_file() else b"{}",
                              content_type=kind, status=200 if file.is_file() else 404)
            else:
                route.fulfill(body="{}", content_type="application/json")

        page.route("http://fd.test/**", answer)
        page.goto("http://fd.test/")
        page.wait_for_function("window.ready === true")
        start = page.locator("#fd-start-import")
        assert start.is_enabled()
        start.click()
        page.wait_for_function("window.calls.includes('POST /api/archive/start')")
        page.wait_for_function("document.querySelector('#fd-start-import').disabled")
        # Still off on the next look, while the import runs...
        page.wait_for_function(
            "window.calls.filter((c) => c === 'GET /api/admin/first-day').length >= 3")
        assert start.is_disabled()
        assert page.evaluate("window.calls.filter((c) => c === 'POST /api/archive/start').length") == 1
        # ...and on again once it has finished (or failed).
        page.evaluate("window.running = false")
        page.wait_for_function("!document.querySelector('#fd-start-import').disabled",
                               timeout=10_000)
        chromium.close()
    assert not problems, problems


# --- labels in sentence case, like the rest of the app --------------------------------------


def test_the_family_app_labels_are_in_sentence_case():
    en = json.loads((STATIC / "i18n" / "en.json").read_text(encoding="utf-8"))
    ta = json.loads((STATIC / "i18n" / "ta.json").read_text(encoding="utf-8"))
    for title, sentence in (("Delete Album", "Delete album"), ("Generate Link", "Generate link"),
                            ("Create New Album", "Create new album"),
                            ("Retry Connection", "Retry connection"),
                            ("Share with Family & Friends", "Share with family & friends")):
        assert title not in en and title not in ta
        assert en[sentence] == sentence and sentence in ta
    sources = "".join(p.read_text(encoding="utf-8") for p in (
        STATIC / "js" / "app.js", ROOT / "ninaivu_lite" / "templates" / "index.html"))
    assert "Delete Album" not in sources and "Generate Link" not in sources


# --- a Live Photo whose motion this browser cannot play says what to do ----------------------


def test_an_unplayable_live_photo_motion_names_a_browser_that_can():
    """The clip of an iPhone Live Photo is HEVC, which Firefox and Chrome on
    Windows cannot play; Ninaivu has no transcoder, so the viewer says the
    photo stays and which browsers show the motion."""
    key = ("This browser can’t play the motion of this Live Photo. "
           "The photo stays; try Safari or Edge to see it move.")
    viewer = (STATIC / "js" / "viewer.js").read_text(encoding="utf-8")
    assert f"i18n.t('{key}')" in viewer and "could not be played" not in viewer
    ta = json.loads((STATIC / "i18n" / "ta.json").read_text(encoding="utf-8"))
    assert "Safari" in ta[key] and "Edge" in ta[key]

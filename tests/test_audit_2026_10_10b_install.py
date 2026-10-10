"""Installers, release checks and guides, audited again after Rotate and Flip
(index version 10): what the Linux installer says when it cannot run anything
where it unpacks, the release's version check when GitHub gives no answer, and
the guides on going back to an earlier version."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_audit_fixes_2026_10_07_backup_install import (INSTALL, stand_in_payload,
                                                        user_install_env)

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "tools" / "check-release-version.sh"


def needs_sh() -> None:
    if os.name == "nt" or shutil.which("sh") is None:
        pytest.skip("needs a POSIX shell")


# --- the Linux installer unpacked where nothing may run ---------------------------------------


def test_a_python_that_may_not_be_run_is_not_called_the_wrong_file(tmp_path):
    """A /tmp mounted noexec: the file is the right one for this machine, so
    the installer must not send the person to the other architecture's."""
    env, home, _calls = user_install_env(tmp_path)
    payload = stand_in_payload(tmp_path)
    (payload / "python" / "bin" / "python3").chmod(0o644)    # as noexec shows it
    done = subprocess.run(["sh", str(INSTALL), str(payload), "--quiet"], env=env,
                          capture_output=True, text=True)
    assert done.returncode == 1
    assert "Programs cannot be run from" in done.stderr and "TMPDIR=" in done.stderr
    assert "Nothing was changed." in done.stderr
    assert "linux-arm64" not in done.stderr and "cannot run on this machine" not in done.stderr
    assert not (home / ".local" / "lib" / "ninaivu-lite").exists()


# --- the release's version check: no answer is not "new" -------------------------------------


def run_check(tmp_path: Path, gh: str, git_status: int, ref_type: str = "branch"):
    """check-release-version.sh in a stand-in checkout of version 9.9.9, with
    gh printing *gh* (exit 0 when it starts with "found") and git ls-remote
    ending with *git_status*."""
    needs_sh()
    repo, shims = tmp_path / "repo", tmp_path / "shims"
    (repo / "ninaivu_lite").mkdir(parents=True)
    (repo / "ninaivu_lite" / "version.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## 9.9.9 — 2026-10-11\n\n- x\n",
                                       encoding="utf-8")
    shims.mkdir()
    status = 0 if gh.startswith("found") else 1
    for name, body in (("gh", f'echo "{gh}" >&2\nexit {status}\n'),
                       ("git", f"exit {git_status}\n")):
        (shims / name).write_text("#!/bin/sh\n" + body, encoding="utf-8")
        (shims / name).chmod(0o755)
    env = {**os.environ, "PATH": f"{shims}{os.pathsep}{os.environ.get('PATH', '')}",
           "REF_TYPE": ref_type, "REF_NAME": "v9.9.9" if ref_type == "tag" else "main"}
    return subprocess.run(["sh", str(CHECK)], cwd=repo, env=env, capture_output=True, text=True)


def test_a_new_version_passes(tmp_path):
    done = run_check(tmp_path, "release not found", 2)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Releasing 9.9.9 as v9.9.9." in done.stdout


def test_a_released_version_is_refused(tmp_path):
    done = run_check(tmp_path, "found", 2)
    assert done.returncode == 1 and "already published" in done.stdout


@pytest.mark.parametrize("said", ["HTTP 502: Bad Gateway (https://api.github.com/...)",
                                  "HTTP 403: API rate limit exceeded",
                                  "error connecting to api.github.com"])
def test_no_answer_from_github_publishes_nothing(tmp_path, said):
    done = run_check(tmp_path, said, 2)
    assert done.returncode == 1
    assert "Could not ask GitHub whether v9.9.9 is already released" in done.stdout


def test_no_answer_about_the_tag_publishes_nothing(tmp_path):
    done = run_check(tmp_path, "release not found", 128)
    assert done.returncode == 1 and "Could not ask the repository" in done.stdout
    assert run_check(tmp_path / "again", "release not found", 0).stdout.count("already tagged") == 1


def test_a_tag_push_does_not_ask_about_its_own_tag(tmp_path):
    done = run_check(tmp_path, "release not found", 128, ref_type="tag")
    assert done.returncode == 0, done.stdout


# --- the guides: going back from index version 10 ---------------------------------------------


GUIDES = ("docs/USER-GUIDE.md", "docs/USER-GUIDE.ta.md", "docs/guide/en.html", "docs/guide/ta.html")


@pytest.mark.parametrize("guide", GUIDES)
def test_the_guides_say_how_to_go_back_from_index_version_10(guide):
    text = (ROOT / guide).read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", text)
    assert "before-update-from-index-9-" in flat and "--restore" in flat
    assert "10" in flat[flat.index("before-update-from-index-9-") - 400:
                        flat.index("before-update-from-index-9-")]
    # No line left saying a step back from 1.11.0 is all there is.
    assert "Going back from 1.11.0 to" not in flat and "1.11.0-இலிருந்து" not in flat

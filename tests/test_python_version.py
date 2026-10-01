"""One Python everywhere: .python-version is the single pin, and everything
that builds or bundles a Python follows it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PINNED = (ROOT / ".python-version").read_text().strip()
MINOR = PINNED.rsplit(".", 1)[0]


def read(path: str) -> str:
    file = ROOT / path
    if not file.exists():            # workflows are not in every copy of the tree
        pytest.skip(f"{path} is not here")
    return file.read_text(encoding="utf-8")


def test_the_pin_is_an_exact_version():
    assert re.fullmatch(r"3\.\d+\.\d+", PINNED)
    assert re.fullmatch(r"\d{8}", (ROOT / "installers" / "PBS_RELEASE").read_text().strip())


def test_installers_read_the_pin_instead_of_naming_a_python():
    assert "version=__PYTHON__" in read("installers/windows/installer.cfg")
    assert '.Replace("__PYTHON__", $pinned)' in read("installers/windows/build.ps1")
    for script in ("installers/linux/build.sh", "installers/macos/build.sh"):
        text = read(script)
        assert '"$root/.python-version"' in text and "PBS_RELEASE\"" in text
        assert not re.search(r"PBS_PYTHON:-3\.", text)


def test_docker_uses_the_same_python():
    match = re.search(r"^ARG PYTHON_VERSION=(\S+)$", read("installers/docker/Dockerfile"), re.M)
    assert match and match.group(1) == PINNED


def test_it_is_a_python_the_project_supports():
    minimum = re.search(r'requires-python = ">=(\d+\.\d+)"', read("pyproject.toml")).group(1)
    assert tuple(map(int, MINOR.split("."))) >= tuple(map(int, minimum.split(".")))


def test_workflows_follow_the_pin():
    release = read(".github/workflows/release.yml")
    assert "python-version-file: .python-version" in release
    assert not re.search(r'python-version: "', release)
    matrix = re.search(r"python-version: \[(.+)\]", read(".github/workflows/tests.yml")).group(1)
    assert f'"{MINOR}"' in matrix


def test_only_a_tag_or_a_person_publishes_a_release():
    release = read(".github/workflows/release.yml")
    job = release[release.index("\n  release:"):]
    assert "if: github.ref_type == 'tag' || github.event_name == 'workflow_dispatch'" in job


def test_windows_installer_template_is_plain_ascii():
    """pynsist writes it in the Windows code page; one arrow in a comment stops the build."""
    for name in ("ninaivu-lite.nsi", "installer.cfg"):
        assert read(f"installers/windows/{name}").isascii(), name

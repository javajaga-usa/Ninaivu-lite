"""The launcher's decisions: when to make the environment and when to install."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def start(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("start", ROOT / "launcher" / "start.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "VENV", tmp_path / ".venv")
    monkeypatch.setattr(module, "STAMP", tmp_path / ".venv" / "stamp")
    created, installs = [], []

    class Builder:
        def __init__(self, **kwargs):
            pass

        def create(self, path):
            created.append(path)
            python = module.venv_python()
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("")

    monkeypatch.setattr(module.venv, "EnvBuilder", Builder)
    result = {"code": 0}

    def run(cmd, **kwargs):
        installs.append(cmd)
        return subprocess.CompletedProcess(cmd, result["code"])

    monkeypatch.setattr(module.subprocess, "run", run)
    module.created, module.installs, module.result = created, installs, result
    return module


def test_first_start_creates_and_installs_once(start):
    start.ensure_environment()
    start.ensure_environment()
    assert len(start.created) == 1 and len(start.installs) == 1
    assert "-r" in start.installs[0]


def test_changed_requirements_install_again(start, monkeypatch):
    start.ensure_environment()
    monkeypatch.setattr(start, "requirements_hash", lambda: "different")
    start.ensure_environment()
    assert len(start.installs) == 2


def test_failed_update_still_starts(start, monkeypatch):
    start.ensure_environment()
    monkeypatch.setattr(start, "requirements_hash", lambda: "different")
    start.result["code"] = 1
    assert start.ensure_environment() == start.venv_python()


def test_failed_first_install_stops_with_a_message(start, capsys):
    start.result["code"] = 1
    with pytest.raises(SystemExit):
        start.ensure_environment()
    assert "internet" in capsys.readouterr().out


def test_old_python_is_refused(start, monkeypatch, capsys):
    monkeypatch.setattr(start.sys, "version_info", (3, 9, 0))
    assert start.main([]) == 1
    assert "3.10" in capsys.readouterr().out


def test_windows_console_gets_english_only(start, monkeypatch, capsys):
    monkeypatch.setattr(start.os, "name", "nt")
    start.say("Hello", "வணக்கம்")
    assert capsys.readouterr().out == "  Hello\n"


def test_first_start_opens_the_control_panel(start, monkeypatch):
    opened, served = [], []
    monkeypatch.setattr(start, "open_panel", lambda python, argv: opened.append(argv) or True)
    monkeypatch.setattr(start.subprocess, "call", lambda cmd, **kw: served.append(cmd) or 0)
    start.main(["--data", "D:/lite"])
    start.main([])                       # the second start: just the server
    assert opened == [["--data", "D:/lite"]]
    assert len(served) == 2 and served[0][1:3] == ["-m", "ninaivu_lite"]


def test_panel_only(start, monkeypatch):
    opened, served = [], []
    monkeypatch.setattr(start, "open_panel", lambda python, argv: opened.append(argv) or True)
    monkeypatch.setattr(start.subprocess, "call", lambda cmd, **kw: served.append(cmd) or 0)
    assert start.main(["--panel"]) == 0
    assert opened == [["--panel"]] and served == []


def test_data_argument(start):
    assert start.data_argument(["x", "--data", "D:/a"]) == ["--data", "D:/a"]
    assert start.data_argument(["--data=D:/b"]) == ["--data", "D:/b"]
    assert start.data_argument(["D:/Photos"]) == []

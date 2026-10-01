"""Set up and start Ninaivu Lite. Run by start.cmd (Windows) and start.sh (macOS, Linux).

1. Checks this Python is new enough (3.10+).
2. Makes a private environment in ``.venv`` beside this folder, once.
3. Installs what Ninaivu Lite needs, and again only when requirements.txt changes.
4. Starts Ninaivu Lite, passing on any folders or options given.

Standard library only: it has to run before anything is installed. Messages are
in English and Tamil, because the person reading them may not have chosen yet.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
VENV = HERE / ".venv"
REQUIREMENTS = HERE / "requirements.txt"
STAMP = VENV / "installed-requirements.sha256"
MINIMUM = (3, 10)


def say(english: str, tamil: str) -> None:
    for line in (english, tamil):
        try:
            print(f"  {line}", flush=True)
        except UnicodeEncodeError:          # a console that cannot show Tamil
            print(f"  {line.encode('ascii', 'replace').decode()}", flush=True)


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def requirements_hash() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def ensure_environment() -> Path:
    python = venv_python()
    if not python.exists():
        say("First start: preparing Ninaivu Lite (a minute or two)...",
            "முதல் தொடக்கம்: நினைவு லைட்டைத் தயார் செய்கிறது (ஓரிரு நிமிடங்கள்)...")
        venv.EnvBuilder(with_pip=True, clear=True).create(VENV)
    if not STAMP.exists() or STAMP.read_text().strip() != requirements_hash():
        say("Installing what Ninaivu Lite needs...",
            "நினைவு லைட்டுக்குத் தேவையானவற்றை நிறுவுகிறது...")
        result = subprocess.run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--quiet",
             "-r", str(REQUIREMENTS)], cwd=HERE, check=False)
        if result.returncode != 0:
            if STAMP.exists():
                # Installed before; an update failed (offline, perhaps). The old
                # set still works, so start with it rather than not at all.
                say("Could not update; starting with what is already installed.",
                    "புதுப்பிக்க முடியவில்லை; ஏற்கனவே நிறுவியவற்றுடன் தொடங்குகிறது.")
                return python
            say("Installation failed. Is this computer connected to the internet?",
                "நிறுவல் தோல்வியடைந்தது. இந்தக் கணினி இணையத்துடன் இணைக்கப்பட்டுள்ளதா?")
            raise SystemExit(1)
        STAMP.write_text(requirements_hash())
    return python


def main(argv: list[str]) -> int:
    if sys.version_info < MINIMUM:
        say(f"Ninaivu Lite needs Python 3.10 or newer; this is {sys.version.split()[0]}.",
            f"நினைவு லைட்டுக்கு Python 3.10 அல்லது புதியது தேவை; இது {sys.version.split()[0]}.")
        say("Get it from https://www.python.org/downloads/",
            "https://www.python.org/downloads/ இலிருந்து பெறவும்")
        return 1
    python = ensure_environment()
    command = [str(python), "-m", "ninaivu_lite", *argv]
    try:
        return subprocess.call(command, cwd=HERE)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

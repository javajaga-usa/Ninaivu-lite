#!/bin/sh
# Ninaivu Lite for macOS and Linux:  sh start.sh  [photo folder]
# The first start prepares everything (a minute or two); later starts are quick.
cd "$(dirname "$0")" || exit 1

PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$candidate"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "  Ninaivu Lite needs Python 3.10 or newer. Install it from https://www.python.org/downloads/"
  echo "  (Debian/Ubuntu/Raspberry Pi OS: sudo apt install python3 python3-venv)"
  echo "  நினைவு லைட்டுக்கு Python 3.10 அல்லது புதியது தேவை."
  exit 1
fi

# Debian-family systems ship venv separately.
if ! "$PY" -c 'import ensurepip, venv' 2>/dev/null; then
  echo "  Please install python3-venv first:  sudo apt install python3-venv"
  echo "  முதலில் python3-venv ஐ நிறுவவும்:  sudo apt install python3-venv"
  exit 1
fi

exec "$PY" launcher/start.py "$@"

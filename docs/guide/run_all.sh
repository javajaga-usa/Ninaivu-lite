#!/bin/sh
# run_all.sh  — make the library, then every screenshot in English and Tamil.
set -e
G=$(cd "$(dirname "$0")" && pwd)
PY=${PY:-/tmp/claude-0/venv/bin/python}
export PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_BROWSERS_PATH:-/opt/pw-browsers}
"$PY" "$G/make_library.py" "$G/work/Photos"
for L in ${LANGS:-en ta}; do
  rm -rf /home/family; mkdir -p /home/family
  cp -r "$G/work/Photos" /home/family/Photos
  cp -r "$G/work/old-drive" "/home/family/Old backup 2011"
  # the Linux data folder, so Settings shows a natural path for the backups
  DATA=/home/family/.local/share/ninaivu-lite "$G/serve.sh" "$L" 8811 fresh
  (cd "$G" && LANG_GUIDE=$L BASE=http://127.0.0.1:8811 OUT=shots/$L node shots.js)
  kill "$(cat "$G/run/$L.pid")"
done
"$G/panel_shot.sh" "$G/shots/panel.png"

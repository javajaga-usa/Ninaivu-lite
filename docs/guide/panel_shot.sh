#!/bin/sh
# panel_shot.sh <out.png>  the Control Panel (Tk) on a virtual screen, for a fresh
# Ninaivu Lite with no administrator yet, so it shows the setup code.
# Needs Xvfb, ImageMagick's import, and a Python with tkinter (PANEL_PY).
G=$(cd "$(dirname "$0")" && pwd)
APP=${APP:-$G/../..}
PY=${PY:-/tmp/claude-0/venv/bin/python}
PANEL_PY=${PANEL_PY:-python3.12}
OUT=$1
D=$G/run/data-panel
rm -rf "$D"; mkdir -p "$D"
(cd "$APP" && exec "$PY" -m ninaivu_lite --data "$D" --port 8080 --no-browser > "$G/run/panel-app.log" 2>&1) &
APPPID=$!
sleep 4
Xvfb :77 -dpi 150 -screen 0 1800x1400x24 >/dev/null 2>&1 &
XPID=$!
sleep 1
(cd "$APP" && DISPLAY=:77 HOME=$G/run/panel-home exec "$PANEL_PY" -m ninaivu_lite.panel --data "$D" > "$G/run/panel.log" 2>&1) &
PPID2=$!
sleep 6
WID=
DISPLAY=:77 import -window root "$OUT" && convert "$OUT" -fuzz 2% -trim +repage "$OUT"
kill $PPID2 $XPID $APPPID 2>/dev/null
grep -i "code" "$G/run/panel-app.log"

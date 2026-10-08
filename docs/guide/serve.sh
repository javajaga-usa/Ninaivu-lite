#!/bin/sh
# serve.sh <lang> <port> [fresh]   start (or restart) a Ninaivu Lite for the screenshots
# (DATA= its data folder; default run/data-<lang>)
G=$(cd "$(dirname "$0")" && pwd)
APP=${APP:-$G/../..}
PY=${PY:-/tmp/claude-0/venv/bin/python}
L=$1; P=$2
D=${DATA:-$G/run/data-$L}
[ -f "$G/run/$L.pid" ] && kill "$(cat "$G/run/$L.pid")" 2>/dev/null && sleep 1
[ "$3" = fresh ] && rm -rf "$D" "$G/shots/$L"
mkdir -p "$G/run"
cd "$APP" && nohup "$PY" -m ninaivu_lite --data "$D" --port "$P" --no-browser > "$G/run/$L.log" 2>&1 &
echo $! > "$G/run/$L.pid"
sleep 3; head -3 "$G/run/$L.log"

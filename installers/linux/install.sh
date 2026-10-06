#!/bin/sh
# Install Ninaivu Lite from an unpacked payload (header.sh unpacks it and calls this).
#
#   install.sh PAYLOAD [--prefix DIR] [--photos DIR] [--no-service] [--quiet]
#
# What it does, in order: copies the private Python and installs every bundled
# wheel into it, offline; makes the `ninaivu-lite` command (and
# `ninaivu-lite-panel`, the Control Panel, for a desktop); writes a desktop
# entry; sets up a systemd service that starts Ninaivu Lite at boot (a user
# service, or a system one when run as root, which runs as its own unprivileged
# account, never as root); starts it; says where to open it.
# Run a newer installer to upgrade in place: settings, people and the index are
# kept, because they live in the data folder, not with the program. The
# photographs are only ever read.
set -e
payload=$1; shift
version=$(cat "$payload/VERSION")
prefix=""; photos=""; service=1; quiet=0
while [ $# -gt 0 ]; do
    case "$1" in
        --prefix) prefix=$2; shift 2 ;;
        --photos) photos=$2; shift 2 ;;
        --no-service) service=0; shift ;;
        --quiet) quiet=1; shift ;;
        -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
say() { [ "$quiet" = 1 ] || echo "$@"; }

if [ "$(id -u)" = 0 ]; then
    default_prefix=/opt/ninaivu-lite; bindir=/usr/local/bin; apps=/usr/local/share/applications
    data=/var/lib/ninaivu-lite
else
    default_prefix="$HOME/.local/lib/ninaivu-lite"; bindir="$HOME/.local/bin"
    apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    data="${XDG_DATA_HOME:-$HOME/.local/share}/ninaivu-lite"       # Ninaivu Lite's own default
fi
prefix=${prefix:-$default_prefix}
say "Ninaivu Lite $version → $prefix"

# An upgrade: ask the one that is running to stop, so the new one can start.
if [ -x "$prefix/python/bin/python3" ]; then
    "$prefix/python/bin/python3" -m ninaivu_lite.control --data "$data" --stop >/dev/null 2>&1 || true
fi

# The machine's own Python is not used, and it need not have one.
mkdir -p "$prefix" "$bindir" "$data"
rm -rf "$prefix/python.new"
cp -R "$payload/python" "$prefix/python.new"
"$prefix/python.new/bin/python3" -m pip install --quiet --no-index --no-deps \
    --no-warn-script-location --disable-pip-version-check "$payload"/wheels/*.whl
# Only now is the old one replaced, so a failure above leaves a working install.
rm -rf "$prefix/python"
mv "$prefix/python.new" "$prefix/python"
cp "$payload/LICENSE" "$payload/README.md" "$prefix/"
printf '%s\n' "$version" > "$prefix/VERSION"
py="$prefix/python/bin/python3"

# The commands.
cat > "$prefix/ninaivu-lite" <<WRAP
#!/bin/sh
exec "$py" -m ninaivu_lite --data "$data" "\$@"
WRAP
cat > "$prefix/ninaivu-lite-panel" <<WRAP
#!/bin/sh
exec "$py" -m ninaivu_lite.panel --data "$data" "\$@"
WRAP
cat > "$prefix/uninstall" <<WRAP
#!/bin/sh
# Remove Ninaivu Lite's program, commands, desktop entry and service. The
# photographs are never touched; settings, people and the index under
# $data are kept unless --purge.
"$py" -m ninaivu_lite.control --data "$data" --stop --autostart off >/dev/null 2>&1 || true
systemctl --user disable --now ninaivu-lite 2>/dev/null || true
[ "\$(id -u)" = 0 ] && systemctl disable --now ninaivu-lite 2>/dev/null || true
rm -f "$bindir/ninaivu-lite" "$bindir/ninaivu-lite-panel" "$apps/ninaivu-lite.desktop" \\
      "\$HOME/Desktop/ninaivu-lite.desktop" \\
      "\${XDG_CONFIG_HOME:-\$HOME/.config}/systemd/user/ninaivu-lite.service" \\
      /etc/systemd/system/ninaivu-lite.service 2>/dev/null
[ "\$1" = "--purge" ] && rm -rf "$data"
[ "\$1" = "--purge" ] && [ "\$(id -u)" = 0 ] && userdel ninaivu-lite 2>/dev/null || true
rm -rf "$prefix"
echo "Ninaivu Lite removed."
WRAP
chmod +x "$prefix/ninaivu-lite" "$prefix/ninaivu-lite-panel" "$prefix/uninstall"
ln -sf "$prefix/ninaivu-lite" "$bindir/ninaivu-lite"
ln -sf "$prefix/ninaivu-lite-panel" "$bindir/ninaivu-lite-panel"

# The Control Panel in the applications menu and on the Desktop, for the
# machines that have one.
icon=$("$py" -c "import ninaivu_lite, os; print(os.path.join(os.path.dirname(ninaivu_lite.__file__), 'static', 'icons', 'icon-192.png'))")
mkdir -p "$apps"
cat > "$apps/ninaivu-lite.desktop" <<ENTRY
[Desktop Entry]
Type=Application
Name=Ninaivu Lite Control Panel
Comment=Start and stop Ninaivu Lite, and open the family's photographs
Exec=$prefix/ninaivu-lite-panel
Icon=$icon
Terminal=false
Categories=Graphics;Photography;
ENTRY
if [ "$(id -u)" != 0 ] && [ -d "$HOME/Desktop" ]; then
    cp "$apps/ninaivu-lite.desktop" "$HOME/Desktop/ninaivu-lite.desktop"
    chmod +x "$HOME/Desktop/ninaivu-lite.desktop"
    command -v gio >/dev/null 2>&1 && gio set "$HOME/Desktop/ninaivu-lite.desktop" metadata::trusted true 2>/dev/null || true
fi

# A photographs folder is optional here: without one, the console's first-day
# guide asks for it in the browser.
folder_arg=""
if [ -n "$photos" ]; then
    photos=$(cd "$photos" 2>/dev/null && pwd || echo "$photos")
    [ -d "$photos" ] || { echo "$photos is not a folder." >&2; exit 2; }
    folder_arg=" \"$photos\""
fi

# A system service never runs as root: it answers the network, and its
# administrators can import and export files. It gets an account of its own
# that owns the data folder and only reads the photographs it is let read.
account=ninaivu-lite
make_account() {
    id -u "$account" >/dev/null 2>&1 && return 0
    nologin=$(command -v nologin 2>/dev/null || echo /usr/sbin/nologin)
    if command -v useradd >/dev/null 2>&1; then
        useradd --system --user-group --home-dir "$data" --no-create-home \
            --shell "$nologin" --comment "Ninaivu Lite" "$account"
    elif command -v adduser >/dev/null 2>&1; then
        addgroup -S "$account" 2>/dev/null || true
        adduser -S -D -H -h "$data" -s "$nologin" -G "$account" "$account"
    else
        return 1
    fi
}

# Folders the service account cannot read (the library's, and --photos), so
# the installer can say so.
unreadable_for_account() {
    command -v runuser >/dev/null 2>&1 || return 0
    {
        "$py" -c 'import sys; from ninaivu_lite.config import Config
print("\n".join(Config.load(sys.argv[1]).folders))' "$data" 2>/dev/null || true
        [ -n "$photos" ] && printf '%s\n' "$photos"
    } | sort -u | while IFS= read -r folder; do
        [ -n "$folder" ] || continue
        runuser -u "$account" -- test -r "$folder" -a -x "$folder" 2>/dev/null || printf '%s\n' "$folder"
    done
}

# The service: Ninaivu Lite at boot, restarted if it fails.
started=0
if [ "$service" = 1 ] && command -v systemctl >/dev/null 2>&1; then
    run_as=""
    if [ "$(id -u)" = 0 ]; then
        unit=/etc/systemd/system/ninaivu-lite.service; scope=""; wanted=multi-user.target
        if make_account; then
            chown -R "$account:$account" "$data"
            run_as="User=$account
Group=$account
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes"
        else
            service=0
            say "No way to make the $account account here (no useradd or adduser), so no"
            say "system service was set up: Ninaivu Lite is not run as root."
        fi
    else
        unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/ninaivu-lite.service"; scope="--user"; wanted=default.target
    fi
fi
if [ "$service" = 1 ] && command -v systemctl >/dev/null 2>&1; then
    mkdir -p "$(dirname "$unit")"
    cat > "$unit" <<UNIT
[Unit]
Description=Ninaivu Lite — the family's photographs, at home
After=network-online.target
Wants=network-online.target

[Service]
ExecStart="$py" -m ninaivu_lite --no-browser --data "$data"$folder_arg
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=5
$run_as

[Install]
WantedBy=$wanted
UNIT
    if [ -n "$run_as" ]; then
        blocked=$(unreadable_for_account)
        if [ -n "$blocked" ]; then
            say "The service runs as the '$account' account, which cannot read:"
            printf '%s\n' "$blocked" | while IFS= read -r folder; do
                say "    $folder"
                say "  Let it read that folder, for example:  setfacl -R -m u:$account:rX \"$folder\""
            done
        fi
    fi
    if systemctl $scope daemon-reload 2>/dev/null && systemctl $scope enable --now ninaivu-lite 2>/dev/null; then
        started=1
        say "Started as a service; it starts again at boot."
        if [ -n "$scope" ] && command -v loginctl >/dev/null 2>&1; then
            loginctl enable-linger "$(id -un)" 2>/dev/null && say "It keeps running when you sign out." || true
        fi
    else
        rm -f "$unit"
        say "Could not set up the service (no systemd session?)."
    fi
fi
[ "$started" = 1 ] || say "Start it with: ninaivu-lite$folder_arg     (or from the Control Panel)"

address=$(hostname -I 2>/dev/null | awk '{print $1}')
say ""
say "Ninaivu Lite $version is installed."
say "  Open it:        http://${address:-localhost}:8080   (the first visit makes the administrator)"
[ "$started" = 1 ] && [ "$(id -u)" = 0 ] && say "  Setup code:     journalctl -u ninaivu-lite | grep code    (asked for when setting up from another device)"
[ "$started" = 1 ] && [ "$(id -u)" != 0 ] && say "  Setup code:     journalctl --user -u ninaivu-lite | grep code    (asked for when setting up from another device)"
say "  Control Panel:  ninaivu-lite-panel, in the applications menu and on the Desktop"
say "  Command:        ninaivu-lite [photos folder]     ($bindir should be on PATH)"
say "  Data and log:   $data"
say "  Remove it:      $prefix/uninstall"
exit 0

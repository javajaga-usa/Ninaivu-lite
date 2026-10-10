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
# As root it is a system-wide install: Ninaivu Lite then always runs as its own
# account, by the service or by the `ninaivu-lite` command, and there is no
# Control Panel (the service is started and stopped with systemctl).
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

# NINAIVU_TEST_ROOT: for the tests only, the system folders under another one.
sys=${NINAIVU_TEST_ROOT:-}
if [ "$(id -u)" = 0 ]; then
    default_prefix=$sys/opt/ninaivu-lite; bindir=$sys/usr/local/bin; apps=$sys/usr/local/share/applications
    data=$sys/var/lib/ninaivu-lite
else
    default_prefix="$HOME/.local/lib/ninaivu-lite"; bindir="$HOME/.local/bin"
    apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    data="${XDG_DATA_HOME:-$HOME/.local/share}/ninaivu-lite"       # Ninaivu Lite's own default
fi
prefix=${prefix:-$default_prefix}
say "Ninaivu Lite $version → $prefix"

# Unpacked where no program may run (a /tmp mounted noexec, as hardened
# servers have it): the right file for this machine, so not the advice below.
if [ ! -x "$payload/python/bin/python3" ]; then
    echo "Programs cannot be run from $(dirname "$payload"), where this installer unpacks itself" >&2
    echo "(mounted noexec?). Run it again with a folder of your own for that, for example:" >&2
    echo "  TMPDIR=\"\$HOME\" sh <this installer>" >&2
    echo "Nothing was changed." >&2
    exit 1
fi
# The Python inside must run on this machine before anything is stopped or
# replaced (A167): the other architecture's file, or a 32-bit OS, cannot.
if ! "$payload/python/bin/python3" -c '' >/dev/null 2>&1; then
    echo "The Python inside this installer cannot run on this machine ($(uname -m), $(getconf LONG_BIT 2>/dev/null || echo '?')-bit)." >&2
    echo "Use the installer for this machine: linux-amd64 for a 64-bit PC, linux-arm64 for a" >&2
    echo "Raspberry Pi 4 or 5 with the 64-bit Raspberry Pi OS. Nothing was changed." >&2
    exit 1
fi
# Whether an earlier version is here, for what a failure says.
had_old=0
[ -x "$prefix/python/bin/python3" ] && had_old=1

# An upgrade: stop the one that is running before its files are replaced. Its
# service first (asked through the server, the service would only start it
# again), then one started any other way. Whether it was running is
# remembered: it is started again afterwards, and again if the upgrade fails.
was_running=0
if [ -x "$prefix/python/bin/python3" ] && \
        "$prefix/python/bin/python3" -m ninaivu_lite.control --data "$data" --running >/dev/null 2>&1; then
    was_running=1
    say "Ninaivu Lite $(cat "$prefix/VERSION" 2>/dev/null) is running. It is best stopped before an update"
    say "(Stop in the Control Panel, or systemctl stop ninaivu-lite); stopping it now, and it"
    say "is started again when the update is done. Photos, people, settings and the index are kept."
fi
start_old() {
    [ "$was_running" = 1 ] || return 0
    if command -v systemctl >/dev/null 2>&1 && { [ "$(id -u)" = 0 ] && systemctl start ninaivu-lite >/dev/null 2>&1 \
            || systemctl --user start ninaivu-lite >/dev/null 2>&1; }; then return 0; fi
    [ -x "$prefix/python/bin/python3" ] && \
        "$prefix/python/bin/python3" -m ninaivu_lite.control --data "$data" --start >/dev/null 2>&1 || true
}
if command -v systemctl >/dev/null 2>&1; then
    if [ "$(id -u)" = 0 ]; then systemctl stop ninaivu-lite >/dev/null 2>&1 || true
    else systemctl --user stop ninaivu-lite >/dev/null 2>&1 || true; fi
fi
if [ -x "$prefix/python/bin/python3" ]; then
    "$prefix/python/bin/python3" -m ninaivu_lite.control --data "$data" --stop >/dev/null 2>&1 || true
    # Still running (started in a terminal, which only its own window can
    # stop): its program is not replaced under it, which would leave it
    # running half old and half new. Nothing has been changed yet.
    if "$prefix/python/bin/python3" -m ninaivu_lite.control --data "$data" --running >/dev/null 2>&1; then
        start_old
        echo "Ninaivu Lite is still running, so it was not updated. Nothing was changed." >&2
        echo "Stop it (Ctrl+C in the window it was started from, or Stop in the Control Panel)," >&2
        echo "then run this installer again." >&2
        exit 1
    fi
fi

# The machine's own Python is not used, and it need not have one.
mkdir -p "$prefix" "$bindir" "$data"
# The data folder holds the index, everyone's PIN hashes, share links and the
# previews of hidden photos: no one else's to read.
chmod 0700 "$data"
rm -rf "$prefix/python.new"
# A failure from here until the swap (a damaged download, a full disk) leaves
# the earlier version as it was: its half-made replacement goes, and it is
# started again if it was running.
failed_upgrade() {
    rm -rf "$prefix/python.new"
    start_old
    if [ "$had_old" = 1 ]; then
        echo "The upgrade did not finish. The earlier version is still installed$( [ "$was_running" = 1 ] && echo ' and was started again')." >&2
    else
        echo "The installation did not finish (see the message above). Nothing was installed." >&2
    fi
    exit 1
}
trap failed_upgrade EXIT
cp -R "$payload/python" "$prefix/python.new"
"$prefix/python.new/bin/python3" -m pip install --quiet --no-index --no-deps \
    --no-warn-script-location --disable-pip-version-check "$payload"/wheels/*.whl
trap - EXIT
# Only now is the old one replaced, so a failure above leaves a working install.
rm -rf "$prefix/python"
mv "$prefix/python.new" "$prefix/python"
cp "$payload/LICENSE" "$payload/README.md" "$prefix/"
printf '%s\n' "$version" > "$prefix/VERSION"
py="$prefix/python/bin/python3"

# A system service never runs as root: it answers the network, and its
# administrators can import and export files. It gets an account of its own
# that owns the data folder and only reads the photographs it is let read; so
# does the `ninaivu-lite` command of a system-wide install, with or without
# the service.
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

own_account=0
if [ "$(id -u)" = 0 ]; then
    if make_account; then
        own_account=1
        chown -R "$account:$account" "$data"
    fi
    chmod 0700 "$data"
fi

# The commands.
if [ "$own_account" = 1 ]; then
    cat > "$prefix/ninaivu-lite" <<WRAP
#!/bin/sh
# Ninaivu Lite as its own account, never as root or as whoever typed this, so
# its data folder stays its own. (The service: systemctl start|stop ninaivu-lite.)
if [ "\$(id -un)" = $account ]; then
    exec "$py" -m ninaivu_lite --data "$data" "\$@"
elif [ "\$(id -u)" = 0 ] && command -v runuser >/dev/null 2>&1; then
    exec runuser -u $account -- "$py" -m ninaivu_lite --data "$data" "\$@"
fi
exec sudo -u $account "$py" -m ninaivu_lite --data "$data" "\$@"
WRAP
else
    cat > "$prefix/ninaivu-lite" <<WRAP
#!/bin/sh
exec "$py" -m ninaivu_lite --data "$data" "\$@"
WRAP
fi
if [ "$(id -u)" != 0 ]; then
    cat > "$prefix/ninaivu-lite-panel" <<WRAP
#!/bin/sh
exec "$py" -m ninaivu_lite.panel --data "$data" "\$@"
WRAP
fi
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
      "$sys/etc/systemd/system/ninaivu-lite.service" 2>/dev/null
[ "\$1" = "--purge" ] && rm -rf "$data"
[ "\$1" = "--purge" ] && [ "\$(id -u)" = 0 ] && userdel ninaivu-lite 2>/dev/null || true
# Only what this installer put there: --prefix may have named a folder that
# holds other things (/opt, a home folder), and those are never removed.
rm -rf "$prefix/python" "$prefix/python.new"
rm -f "$prefix/ninaivu-lite" "$prefix/ninaivu-lite-panel" "$prefix/VERSION" \
      "$prefix/LICENSE" "$prefix/README.md" "$prefix/uninstall"
rmdir "$prefix" 2>/dev/null || true
if [ "\$1" = "--purge" ]; then
    echo "Ninaivu Lite removed, with its settings, people and index. Your photographs were not touched."
else
    echo "Ninaivu Lite removed. Your photographs were not touched."
    echo "Its settings, people and index are kept in $data, for a later install."
    echo "To remove them too: delete that folder (or uninstall with --purge next time)."
fi
WRAP
chmod +x "$prefix/ninaivu-lite" "$prefix/uninstall"
ln -sf "$prefix/ninaivu-lite" "$bindir/ninaivu-lite"

# The Control Panel in the applications menu and on the Desktop, for the
# machines that have one; a person's own install only. A system-wide one's data
# folder is the service account's, which another person's panel can neither
# read nor stop, so it has none (and an earlier version's is taken away).
if [ "$(id -u)" = 0 ]; then
    rm -f "$prefix/ninaivu-lite-panel" "$bindir/ninaivu-lite-panel" "$apps/ninaivu-lite.desktop"
else
    chmod +x "$prefix/ninaivu-lite-panel"
    ln -sf "$prefix/ninaivu-lite-panel" "$bindir/ninaivu-lite-panel"
    icon=$("$py" -c "import ninaivu_lite, os; print(os.path.join(os.path.dirname(ninaivu_lite.__file__), 'static', 'icons', 'icon-192.png'))")
    mkdir -p "$apps"
    # Exec quoted: a --prefix with a space in it is one path (A173).
    cat > "$apps/ninaivu-lite.desktop" <<ENTRY
[Desktop Entry]
Type=Application
Name=Ninaivu Lite Control Panel
Comment=Start and stop Ninaivu Lite, and open the family's photographs
Exec="$prefix/ninaivu-lite-panel"
Icon=$icon
Terminal=false
Categories=Graphics;Photography;
ENTRY
    if [ -d "$HOME/Desktop" ]; then
        cp "$apps/ninaivu-lite.desktop" "$HOME/Desktop/ninaivu-lite.desktop"
        chmod +x "$HOME/Desktop/ninaivu-lite.desktop"
        command -v gio >/dev/null 2>&1 && gio set "$HOME/Desktop/ninaivu-lite.desktop" metadata::trusted true 2>/dev/null || true
    fi
fi

# A photographs folder is optional here: without one, the console's first-day
# guide asks for it in the browser.
folder_arg=""
if [ -n "$photos" ]; then
    photos=$(cd "$photos" 2>/dev/null && pwd || echo "$photos")
    [ -d "$photos" ] || { echo "$photos is not a folder." >&2; exit 2; }
    folder_arg=" \"$photos\""
fi

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
# Who started it decides where the setup code is to be found (A169): the
# journal for the service, the Control Panel and the log otherwise.
started_by=""
if [ "$service" = 1 ] && command -v systemctl >/dev/null 2>&1; then
    run_as=""
    if [ "$(id -u)" = 0 ]; then
        unit=$sys/etc/systemd/system/ninaivu-lite.service; scope=""; wanted=multi-user.target
        if [ "$own_account" = 1 ]; then
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
# What it writes (the index, previews, backups, logs) is not for other accounts.
UMask=0027
Restart=on-failure
RestartSec=5
$run_as
# This file is written again by every upgrade: a port or other change of your
# own goes in a drop-in (systemctl${scope:+ $scope} edit ninaivu-lite), which is kept (A169).

[Install]
WantedBy=$wanted
UNIT
    if [ -n "$run_as" ]; then
        blocked=$(unreadable_for_account)
        if [ -n "$blocked" ]; then
            say "The service runs as the '$account' account, which cannot read:"
            printf '%s\n' "$blocked" | while IFS= read -r folder; do
                say "    $folder"
                say "  Let it read that folder, and what is added to it later, for example:"
                # The folders above it too, where it may not pass through.
                parent=$(dirname "$folder")
                while [ "$parent" != / ] && [ "$parent" != . ]; do
                    runuser -u "$account" -- test -x "$parent" 2>/dev/null \
                        || say "    setfacl -m u:$account:x \"$parent\""
                    parent=$(dirname "$parent")
                done
                say "    setfacl -R -m u:$account:rX \"$folder\""
                say "    find \"$folder\" -type d -exec setfacl -m d:u:$account:rX {} +"
            done
        fi
    fi
    # restart, not enable --now: an upgrade's service may still be active.
    if systemctl $scope daemon-reload 2>/dev/null && systemctl $scope enable ninaivu-lite 2>/dev/null \
            && systemctl $scope restart ninaivu-lite 2>/dev/null; then
        started=1; started_by=service
        say "Started as a service; it starts again at boot."
        if [ -n "$scope" ] && command -v loginctl >/dev/null 2>&1; then
            loginctl enable-linger "$(id -un)" 2>/dev/null && say "It keeps running when you sign out." || true
        fi
    else
        rm -f "$unit"
        say "Could not set up the service (no systemd session?)."
    fi
fi
if [ "$started" != 1 ]; then
    if [ "$(id -u)" = 0 ]; then say "Start it with: ninaivu-lite$folder_arg"
    else say "Start it with: ninaivu-lite$folder_arg     (or from the Control Panel)"; fi
fi

if [ "$started" != 1 ] && [ "$was_running" = 1 ]; then
    # Stopped for the upgrade, and nothing above started it again.
    if "$py" -m ninaivu_lite.control --data "$data" --start >/dev/null 2>&1; then
        started=1; started_by=control; say "It was running before the upgrade, so it was started again."
    else
        say "It was running before the upgrade and is stopped now."
    fi
fi
address=$(hostname -I 2>/dev/null | awk '{print $1}')
# The port it really listens on (the next free one when 8080 was taken).
port=8080
if [ "$started" = 1 ]; then
    sleep 2
    found=$(sed -n 's/.*"port": *\([0-9][0-9]*\).*/\1/p' "$data/server.json" 2>/dev/null | head -n 1)
    [ -n "$found" ] && port=$found
fi
say ""
say "Ninaivu Lite $version is installed."
if "$py" -m ninaivu_lite.control --data "$data" --needs-setup >/dev/null 2>&1; then
    say "  Open it:        http://${address:-localhost}:$port   (the first visit makes the administrator)"
    [ "$started_by" = service ] && [ "$(id -u)" = 0 ] && say "  Setup code:     journalctl -u ninaivu-lite | grep code    (asked for when setting up from another device)"
    [ "$started_by" = service ] && [ "$(id -u)" != 0 ] && say "  Setup code:     journalctl --user -u ninaivu-lite | grep code    (asked for when setting up from another device)"
    if [ "$started_by" != service ]; then
        if [ "$(id -u)" = 0 ]; then say "  Setup code:     in $data/logs/ninaivu-lite.log    (asked for when setting up from another device)"
        else say "  Setup code:     in the Control Panel, and in $data/logs/ninaivu-lite.log    (asked for when setting up from another device)"; fi
    fi
else
    say "  Open it:        http://${address:-localhost}:$port   (your library, people and settings are as they were)"
fi
if [ "$(id -u)" = 0 ]; then
    [ "$started" = 1 ] && say "  Start, stop:    systemctl start|stop|restart ninaivu-lite"
    [ "$own_account" = 1 ] && say "  Command:        ninaivu-lite --reset-password NAME, and the rest, run as the $account account"
else
    say "  Control Panel:  ninaivu-lite-panel, in the applications menu and on the Desktop"
    say "  Command:        ninaivu-lite [photos folder]     ($bindir should be on PATH)"
fi
say "  Data and log:   $data"
say "  Remove it:      $prefix/uninstall"
exit 0

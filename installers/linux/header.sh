#!/bin/sh
# Ninaivu Lite __VERSION__ for Linux (__ARCH__) — your family's photographs, at home.
# © 2026 Jagadeesh Rajendran. MIT licence.
#
#   sh Ninaivu-Lite-__VERSION__-linux-__ARCH__.sh [--prefix DIR] [--photos DIR] [--no-service] [--quiet]
#
# Everything is inside this file: a private Python, so the machine needs none
# of its own, and every package Ninaivu Lite uses. Nothing is downloaded.
# Without root it installs under ~/.local/lib/ninaivu-lite; as root, under
# /opt/ninaivu-lite.
set -e
# The right file for this machine (A167): a Raspberry Pi with a 32-bit OS
# (often a 64-bit kernel under it, so uname alone says aarch64), or the PC
# file on a Pi, cannot run the Python inside, and pip's error says nothing.
machine=$(uname -m 2>/dev/null || echo unknown)
case "$machine" in
    x86_64|amd64) machine=amd64 ;;
    aarch64|arm64) machine=arm64 ;;
esac
bits=$(getconf LONG_BIT 2>/dev/null || echo 64)
if [ "$machine" != "__ARCH__" ] || [ "$bits" != 64 ]; then
    echo "This installer is for 64-bit Linux on __ARCH__; this machine is $machine with a ${bits}-bit system." >&2
    case "$machine" in
        arm64|armv7l|armv6l|armhf) echo "On a Raspberry Pi 4 or 5, install the 64-bit Raspberry Pi OS and use Ninaivu-Lite-__VERSION__-linux-arm64.sh." >&2 ;;
        amd64) echo "Use Ninaivu-Lite-__VERSION__-linux-amd64.sh." >&2 ;;
    esac
    echo "Nothing was changed." >&2
    exit 1
fi
tmp=$(mktemp -d "${TMPDIR:-/tmp}/ninaivu-lite-install.XXXXXX")
trap 'rm -rf "$tmp"' EXIT INT TERM
lines=$(awk '/^__PAYLOAD_BELOW__$/ { print NR + 1; exit 0; }' "$0")
tail -n +"$lines" "$0" | tar -xzf - -C "$tmp"
sh "$tmp/install.sh" "$tmp" "$@"
exit 0
__PAYLOAD_BELOW__

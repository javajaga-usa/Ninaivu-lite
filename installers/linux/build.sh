#!/usr/bin/env bash
# Build the Linux / Raspberry Pi installer: one self-contained file per architecture.
#
#   bash installers/linux/build.sh amd64      # a PC
#   bash installers/linux/build.sh arm64      # Raspberry Pi 4 or 5 (64-bit OS), other arm64 boards
#
# Makes installers/linux/build/Ninaivu-Lite-<version>-linux-<arch>.sh: a shell
# script with a tarball on its end. Inside: a private, relocatable Python (a
# python-build-standalone build, so the machine needs no Python of its own, and
# it has Tk for the Control Panel), every wheel Ninaivu Lite needs for that
# architecture, Ninaivu Lite itself, and install.sh, which puts it all in one
# folder, makes the `ninaivu-lite` command, a desktop entry and a systemd
# service, and starts it. Nothing is downloaded when it is run.
#
# Either architecture builds on any Linux machine: the wheels are fetched for
# the target with pip's --platform, and nothing is executed from the target's
# Python.
set -euo pipefail

arch=${1:-}
case "$arch" in
    amd64) triple=x86_64-unknown-linux-gnu; platforms=(manylinux_2_28_x86_64 manylinux_2_17_x86_64 manylinux2014_x86_64 manylinux_2_5_x86_64 manylinux1_x86_64) ;;
    arm64) triple=aarch64-unknown-linux-gnu; platforms=(manylinux_2_28_aarch64 manylinux_2_17_aarch64 manylinux2014_aarch64) ;;
    *) echo "usage: $0 amd64|arm64" >&2; exit 2 ;;
esac

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
version=$(sed -nE 's/^__version__ *= *"([^"]+)".*/\1/p' "$root/ninaivu_lite/version.py")
[ -n "$version" ] || { echo "no __version__ in ninaivu_lite/version.py" >&2; exit 1; }
echo "Ninaivu Lite $version for linux-$arch"

build="$here/build"
payload="$build/payload-$arch"
rm -rf "$payload"
mkdir -p "$payload/wheels" "$build"

# 1. The Python: the one version every installer carries, from .python-version
#    at the repository root, as a python-build-standalone build (the release is
#    in installers/PBS_RELEASE). PBS_PYTHON and PBS_RELEASE override both.
#    Checked against installers/PYTHON_SHA256SUMS, downloaded or kept from an
#    earlier build alike: a file replaced upstream is not shipped.
pbs_python=${PBS_PYTHON:-$(tr -d '[:space:]' < "$root/.python-version")}
pbs_release=${PBS_RELEASE:-$(tr -d '[:space:]' < "$here/../PBS_RELEASE")}
minor=${pbs_python%.*}
tarball="cpython-${pbs_python}+${pbs_release}-${triple}-install_only_stripped.tar.gz"
expected=$(awk -v f="$tarball" '$2 == f { print $1 }' "$here/../PYTHON_SHA256SUMS")
[ -n "$expected" ] || { echo "no SHA-256 for $tarball in installers/PYTHON_SHA256SUMS" >&2; exit 1; }
if [ ! -f "$build/$tarball" ]; then
    curl -fsSL -o "$build/$tarball.part" \
        "https://github.com/astral-sh/python-build-standalone/releases/download/${pbs_release}/${tarball}"
    mv "$build/$tarball.part" "$build/$tarball"
fi
actual=$(sha256sum "$build/$tarball" | cut -d' ' -f1)
if [ "$actual" != "$expected" ]; then
    rm -f "$build/$tarball"
    echo "$tarball: SHA-256 $actual, expected $expected (installers/PYTHON_SHA256SUMS)" >&2
    exit 1
fi
tar -xzf "$build/$tarball" -C "$payload"           # unpacks to ./python
rm -rf "$payload/python/lib/python$minor/test" "$payload/python/lib/python$minor/idlelib" \
       "$payload/python/lib/python$minor/ensurepip/_bundled"/setuptools* "$payload/python/share/man"
find "$payload/python" -name "__pycache__" -type d -prune -exec rm -rf {} +

# 2. The wheels, for the target (Pillow and MarkupSafe are compiled; the rest
#    are the same for every machine). WHEELS_DIR uses wheels already at hand
#    instead of fetching them.
if [ -n "${WHEELS_DIR:-}" ]; then
    cp "$WHEELS_DIR"/*.whl "$payload/wheels/"
else
    platform_args=()
    for p in "${platforms[@]}"; do platform_args+=(--platform "$p"); done
    python3 -m pip download --quiet --dest "$payload/wheels" --only-binary=:all: \
        --python-version "$minor" --implementation cp --abi "cp${minor/./}" --abi abi3 --abi none \
        "${platform_args[@]}" -r "$root/requirements.txt"
fi

# 3. Ninaivu Lite itself.
python3 -m pip wheel --quiet --wheel-dir "$payload/wheels" --no-deps "$root"

# 4. What install.sh needs, and the installer itself.
cp "$here/install.sh" "$root/LICENSE" "$root/README.md" "$payload/"
printf '%s\n' "$version" > "$payload/VERSION"
out="$build/Ninaivu-Lite-$version-linux-$arch.sh"
{
    sed -e "s/__VERSION__/$version/g" -e "s/__ARCH__/$arch/g" "$here/header.sh"
    tar -C "$payload" -czf - .
} > "$out"
chmod +x "$out"
rm -rf "$payload"
hash=$(sha256sum "$out" | cut -d' ' -f1)
echo "$out"
echo "SHA256 $hash"

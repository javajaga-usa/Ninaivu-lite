#!/usr/bin/env bash
# Build "Ninaivu Lite.app" and a .dmg, signed and notarised when the identity is set.
#
#   bash installers/macos/build.sh            on a Mac, from the repository root
#
# The app is a plain bundle: a private, relocatable Python (a
# python-build-standalone build, with Tk) with Ninaivu Lite and its packages
# installed into it, and a launcher that opens the Control Panel, from which
# Ninaivu Lite is started and stopped. Nothing is downloaded when it runs.
# Settings, people and the index live in ~/Library/Application Support/Ninaivu-lite.
#
# Signing and notarising happen when these are set; otherwise the build is
# unsigned and says so, and Gatekeeper asks before opening it on another Mac
# (System Settings → Privacy & Security → Open Anyway).
#
#   NINAIVU_MAC_SIGN_IDENTITY    "Developer ID Application: Name (TEAMID)"
#   NINAIVU_NOTARY_PROFILE       a `xcrun notarytool store-credentials` profile
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
version=$(sed -nE 's/^__version__ *= *"([^"]+)".*/\1/p' "$root/ninaivu_lite/version.py")
[ -n "$version" ] || { echo "no __version__ in ninaivu_lite/version.py" >&2; exit 1; }
arch=$(uname -m)
echo "Ninaivu Lite $version ($arch)"

build="$here/build"
app="$build/Ninaivu Lite.app"
rm -rf "$build"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"

# 1. A private, relocatable Python with everything installed: the one version
#    every installer carries, from .python-version at the repository root
#    (the python-build-standalone release is in installers/PBS_RELEASE).
pbs_python=${PBS_PYTHON:-$(tr -d '[:space:]' < "$root/.python-version")}
pbs_release=${PBS_RELEASE:-$(tr -d '[:space:]' < "$here/../PBS_RELEASE")}
case "$arch" in
    arm64) triple=aarch64-apple-darwin ;;
    x86_64) triple=x86_64-apple-darwin ;;
    *) echo "unsupported architecture: $arch" >&2; exit 1 ;;
esac
tarball="cpython-${pbs_python}+${pbs_release}-${triple}-install_only_stripped.tar.gz"
# Checked against installers/PYTHON_SHA256SUMS: a file replaced upstream is not shipped.
expected=$(awk -v f="$tarball" '$2 == f { print $1 }' "$here/../PYTHON_SHA256SUMS")
[ -n "$expected" ] || { echo "no SHA-256 for $tarball in installers/PYTHON_SHA256SUMS" >&2; exit 1; }
curl -fsSL -o "$build/$tarball" \
    "https://github.com/astral-sh/python-build-standalone/releases/download/${pbs_release}/${tarball}"
actual=$(shasum -a 256 "$build/$tarball" | cut -d' ' -f1)
if [ "$actual" != "$expected" ]; then
    rm -f "$build/$tarball"
    echo "$tarball: SHA-256 $actual, expected $expected (installers/PYTHON_SHA256SUMS)" >&2
    exit 1
fi
tar -xzf "$build/$tarball" -C "$app/Contents/Resources"      # unpacks to ./python
rm "$build/$tarball"
py="$app/Contents/Resources/python/bin/python3"
"$py" -m pip install --quiet --disable-pip-version-check -r "$root/requirements.txt"
"$py" -m pip install --quiet --disable-pip-version-check --no-deps "$root"
"$py" -c "import tkinter, ninaivu_lite.panel" || { echo "the bundled Python cannot open the Control Panel" >&2; exit 1; }
find "$app/Contents/Resources/python" -name "__pycache__" -type d -prune -exec rm -rf {} +

# 2. The launcher: the Control Panel. `--server` runs Ninaivu Lite itself in a
#    terminal, for those who want that.
cat > "$app/Contents/MacOS/ninaivu-lite" <<'LAUNCH'
#!/bin/sh
here=$(cd "$(dirname "$0")/.." && pwd)
python="$here/Resources/python/bin/python3"
case "$1" in
    --server) shift; exec "$python" -m ninaivu_lite "$@" ;;
esac
exec "$python" -m ninaivu_lite.panel "$@"
LAUNCH
chmod +x "$app/Contents/MacOS/ninaivu-lite"

# 3. The icon and the plist.
iconutil -c icns "$here/ninaivu-lite.iconset" -o "$app/Contents/Resources/ninaivu-lite.icns"
cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Ninaivu Lite</string>
  <key>CFBundleDisplayName</key><string>Ninaivu Lite</string>
  <key>CFBundleIdentifier</key><string>org.ninaivu.lite</string>
  <key>CFBundleVersion</key><string>$version</string>
  <key>CFBundleShortVersionString</key><string>$version</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>ninaivu-lite</string>
  <key>CFBundleIconFile</key><string>ninaivu-lite</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>© 2026 Jagadeesh Rajendran · MIT licence</string>
  <key>NSLocalNetworkUsageDescription</key><string>Ninaivu Lite answers to phones and tablets on the home network.</string>
</dict></plist>
PLIST

# 4. Sign — every Mach-O inside, then the bundle — and notarise.
if [ -n "${NINAIVU_MAC_SIGN_IDENTITY:-}" ]; then
    find "$app/Contents/Resources/python" -type f \( -name "*.so" -o -name "*.dylib" -o -perm +111 \) \
        -print0 | while IFS= read -r -d '' file; do
            if file -b "$file" | grep -q Mach-O; then
                codesign --force --options runtime --timestamp --sign "$NINAIVU_MAC_SIGN_IDENTITY" \
                    --entitlements "$here/entitlements.plist" "$file"
            fi
        done
    codesign --force --deep --options runtime --timestamp --sign "$NINAIVU_MAC_SIGN_IDENTITY" \
        --entitlements "$here/entitlements.plist" "$app"
    codesign --verify --deep --strict "$app"
else
    echo "warning: unsigned app; set NINAIVU_MAC_SIGN_IDENTITY for a release" >&2
fi

dmg="$build/Ninaivu-Lite-$version-macos-$arch.dmg"
staging="$build/dmg"
mkdir -p "$staging"
cp -R "$app" "$staging/"
ln -s /Applications "$staging/Applications"
# Dragging a new app over the old one replaces the program, not the data
# folder; replacing it under a running server leaves that server half old.
cat > "$staging/Before updating - read me.txt" <<'NOTE'
Updating Ninaivu Lite
=====================

1. Stop Ninaivu Lite first: in the Ninaivu Lite Control Panel press Stop, then
   quit the Control Panel (or press "Get ready to update", which does both).
2. Drag the new Ninaivu Lite into Applications and choose Replace.
3. Open the Control Panel again and press Start.

Photos, people, settings, the index and backups are kept: they live in
~/Library/Application Support/Ninaivu-lite, not in the app. Ninaivu Lite never
checks for updates by itself.

நினைவு லைட்டைப் புதுப்பித்தல்
==============================

1. முதலில் நினைவு லைட்டை நிறுத்துங்கள்: Ninaivu Lite Control Panel-இல் Stop
   அழுத்தி, Control Panel-ஐ மூடுங்கள் ("Get ready to update" இரண்டையும் செய்யும்).
2. புதிய Ninaivu Lite-ஐ Applications-க்குள் இழுத்து Replace தேர்ந்தெடுங்கள்.
3. Control Panel-ஐ மீண்டும் திறந்து Start அழுத்துங்கள்.

படங்கள், நபர்கள், அமைப்புகள், அட்டவணை, காப்புப் பிரதிகள் அப்படியே இருக்கும்:
அவை ~/Library/Application Support/Ninaivu-lite இல் உள்ளன, செயலிக்குள் அல்ல.
நினைவு லைட் தானாகப் புதுப்பிப்புகளைத் தேடுவதில்லை.
NOTE
hdiutil create -volname "Ninaivu Lite" -srcfolder "$staging" -ov -format UDZO "$dmg" >/dev/null
rm -rf "$staging"

if [ -n "${NINAIVU_MAC_SIGN_IDENTITY:-}" ] && [ -n "${NINAIVU_NOTARY_PROFILE:-}" ]; then
    codesign --force --timestamp --sign "$NINAIVU_MAC_SIGN_IDENTITY" "$dmg"
    xcrun notarytool submit "$dmg" --keychain-profile "$NINAIVU_NOTARY_PROFILE" \
        ${NINAIVU_NOTARY_KEYCHAIN:+--keychain "$NINAIVU_NOTARY_KEYCHAIN"} --wait
    xcrun stapler staple "$dmg"
    xcrun stapler staple "$app"
fi

hash=$(shasum -a 256 "$dmg" | cut -d' ' -f1)
echo "$dmg"
echo "SHA256 $hash"

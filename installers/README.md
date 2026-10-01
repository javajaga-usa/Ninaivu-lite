# Installers

How to put Ninaivu Lite on a computer without asking anyone to install Python.
Every installer carries its own Python and every package: nothing is downloaded
when it installs or runs. Each one opens the **Control Panel**, from which
Ninaivu Lite is started and stopped; the first visit in the browser makes the
administrator, and the console's first-day guide asks for the photo folder.

| Folder | Makes | Build with |
| --- | --- | --- |
| `windows/` | `Ninaivu-Lite-<version>-windows-x64.exe` (pynsist + NSIS) | `installers\windows\build.ps1` on Windows |
| `linux/` | `Ninaivu-Lite-<version>-linux-amd64.sh` and `-arm64.sh` (Raspberry Pi 4/5, 64-bit OS) | `bash installers/linux/build.sh amd64` (or `arm64`) on any Linux |
| `macos/` | `Ninaivu-Lite-<version>-macos-<arch>.dmg` | `bash installers/macos/build.sh` on a Mac |
| `docker/` | a container image | `docker compose -f installers/docker/docker-compose.yml up -d` |

## One Python everywhere

**`.python-version`** at the repository root names the one Python (currently
3.13.7) that every installer carries and every build uses: the Windows
installer, the Linux and macOS bundles, the Docker image and the release
workflow all read it (the Dockerfile repeats it as a default, and
`tests/test_python_version.py` fails if anything drifts). To move to a newer
Python, change that one file (and the Dockerfile default), check that
`installers/PBS_RELEASE` has a build of it, and build. Running from a checkout
(`start.cmd`, `start.sh`) still accepts any Python from 3.10 up, preferring this
one, and the tests run on 3.10 and on this one.

The version comes from `ninaivu_lite/version.py`. Build output goes to each
folder's `build/` (ignored by git). `.github/workflows/release.yml` builds all
of them on a version tag (`v1.2.0`) and attaches them to the GitHub release with
a `SHA256SUMS.txt`.

## Windows

Needs the Python in `.python-version` from python.org (for its Tk), `pip install pynsist`, and NSIS
(`choco install nsis`). The installer:

- installs for the current user, with no administrator rights;
- adds **Ninaivu Lite** (the Control Panel) to the Start menu and the Desktop;
- has a *Start Ninaivu Lite at sign-in* box (ticked) — the same switch as the one
  in the Control Panel;
- offers to **open the Control Panel** on its last page (ticked);
- carries Noto Sans Tamil (SIL Open Font License) for clear Tamil on Windows
  and on Android phones;
- on upgrade or uninstall, asks a running Ninaivu Lite to stop first. Uninstall
  leaves the data folder (`%LOCALAPPDATA%\Ninaivu-lite`) and, always, the photos.

**The uninstaller is signed too.** NSIS normally writes `uninstall.exe` on the
person's computer, where nothing can sign it, so the build makes it first:
`build.ps1 -MakeUninstaller` builds the installer and leaves
`build\uninstaller\uninstall.exe`; that file is signed; `build.ps1 -Finalize
-Uninstaller <file>` packs it into the installer, which is signed in turn.
`build.ps1 -Sign` does all of that in one go with a certificate of your own. The
release workflow runs the same steps on every build (unsigned when there is
nothing to sign with), then installs the result silently on the runner, opens
the Control Panel's window, starts and stops Ninaivu Lite and uninstalls it.

**Signing.** Unsigned, Windows SmartScreen says *Windows protected your PC*
(**More info → Run anyway**), and a PC with **Smart App Control** on refuses the
installer outright. The release workflow signs it for free through
[SignPath Foundation](https://signpath.org) once the repository has the secret
`SIGNPATH_API_TOKEN` and the variables `SIGNPATH_ORGANIZATION_ID`,
`SIGNPATH_PROJECT_SLUG` and `SIGNPATH_SIGNING_POLICY_SLUG`; the policy it is
signed under is [docs/CODE-SIGNING.md](../docs/CODE-SIGNING.md). A release makes
**two** signing requests, the uninstaller and then the installer, and each waits
for approval in SignPath. Both arrive as a zip with one `.exe`, so the SignPath
project needs this artifact configuration:

```xml
<artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
  <zip-file>
    <pe-file path="*.exe">
      <authenticode-sign/>
    </pe-file>
  </zip-file>
</artifact-configuration>
```

With a
certificate of your own instead, `build.ps1 -Sign` uses the one whose thumbprint
is in `NINAIVU_SIGN_THUMBPRINT`.

## Linux and Raspberry Pi

```sh
sh Ninaivu-Lite-<version>-linux-amd64.sh            # or -arm64.sh on a Raspberry Pi
sh Ninaivu-Lite-<version>-linux-amd64.sh --photos ~/Pictures --no-service
```

Without root it installs under `~/.local/lib/ninaivu-lite` (as root:
`/opt/ninaivu-lite`), makes the commands `ninaivu-lite` and `ninaivu-lite-panel`,
a desktop entry for the Control Panel, and a systemd service that starts it at
boot. Running a newer installer upgrades in place. `…/ninaivu-lite/uninstall`
removes the program (`--purge` also removes settings and the index).

The bundled Python is a
[python-build-standalone](https://github.com/astral-sh/python-build-standalone/releases)
build of the version in `.python-version`, from the release in
`installers/PBS_RELEASE` (`PBS_PYTHON` and `PBS_RELEASE` override them for one
build). The 20250708 builds have a Tk that aborts on some X servers — do not go
back to them. `WHEELS_DIR` uses
wheels already downloaded instead of fetching them.

## macOS

Needs Xcode's command-line tools. Makes `Ninaivu Lite.app` in a disk image for
the Mac's own architecture (build on Apple silicon and on Intel for both).
Opening the app opens the Control Panel. Set `NINAIVU_MAC_SIGN_IDENTITY` (and
`NINAIVU_NOTARY_PROFILE`) to sign and notarise; unsigned, macOS asks before
opening it: **System Settings → Privacy & Security → Open Anyway**.

## Docker

```sh
MEDIA_DIR=/path/to/Photos docker compose -f installers/docker/docker-compose.yml up -d
docker logs ninaivu-lite        # the address, and the setup code for the first visit
```

The photos are mounted read-only; settings and the index are in the
`ninaivu_lite_data` volume. There is no Control Panel in a container.

## From a checkout, without an installer

`start.cmd` (Windows) or `./start.sh` — see the main README. On a server,
`tools/ninaivu-lite.service` is a systemd unit for a checkout.

## What has been tested

The Linux installer's whole path (build, install as an ordinary user, start,
Control Panel, stop, uninstall) has been run. The Windows, macOS and Docker
builds follow Ninaivu's own, proven recipes but **have not been built yet** —
build each once and try it before publishing a release.

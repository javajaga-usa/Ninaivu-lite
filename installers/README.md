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
a `SHA256SUMS.txt`. It first runs the whole test suite (`tests.yml`) on that same
commit, and publishes nothing unless it passes.

## Windows

Needs the Python in `.python-version` from python.org (for its Tk), `pip install pynsist==2.8`, and NSIS 3.10
(`choco install nsis --version=3.10`); the release workflow pins both. The installer:

- asks whether to install for the current user (the default) or for everyone
  (into Program Files). Installing for
  everyone needs administrator rights, and on an administrator's account Windows
  may ask for permission (UAC) when the installer starts (pynsist's
  `MULTIUSER_EXECUTIONLEVEL Highest`). Either way each person's data stays in
  their own `%LOCALAPPDATA%\Ninaivu-lite`;
- puts the `ninaivu-lite` command on the PATH (for `--reset-password` and
  `--restore`, in a new Command Prompt);
- adds **Ninaivu Lite** (the Control Panel) to the Start menu and the Desktop;
- has a *Start Ninaivu Lite at sign-in* box (ticked) — the same switch as the one
  in the Control Panel;
- offers to **open the Control Panel** on its last page (ticked);
- carries Noto Sans Tamil (SIL Open Font License) for clear Tamil on Windows
  and on Android phones;
- on upgrade or uninstall, asks a running Ninaivu Lite to stop first, and while
  anything of it is still in use (the server started from its own window, or
  the Control Panel left open) asks the person to stop it and close the panel,
  with Retry, instead of failing on the first file it cannot write. An upgrade
  then deletes the old program (its Python, packages and commands) before the new
  files are written, so none of an old version is left behind. Upgrade and
  uninstall both leave the data folder (`%LOCALAPPDATA%\Ninaivu-lite`: people,
  settings, index) and, always, the photos.

**The uninstaller is signed too.** NSIS normally writes `uninstall.exe` on the
person's computer, where nothing can sign it, so the build makes it first:
`build.ps1 -MakeUninstaller` builds the installer and leaves
`build\uninstaller\uninstall.exe`; that file is signed; `build.ps1 -Finalize
-Uninstaller <file>` packs it into the installer, which is signed in turn.
`build.ps1 -Sign` does all of that in one go with a certificate of your own. The
release workflow runs the same steps on every build (unsigned when there is
nothing to sign with), then installs the result silently on the runner, opens
the Control Panel's window, starts and stops Ninaivu Lite and uninstalls it.

**Signing.** The releases are **not signed yet**: the application to the SignPath
Foundation is pending, and the repository has none of the secrets below, so every
build is unsigned for now. Unsigned, Windows SmartScreen says *Windows protected your PC*
(**More info → Run anyway**), and a PC with **Smart App Control** on refuses the
installer outright. Once the application is approved, the release workflow signs it for free through
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

### The portable zip

Every Windows build also makes `Ninaivu-Lite-<version>-windows-x64-portable.zip`,
for running without installing. The release workflow installs the installer
silently, copies the installed folder once Ninaivu Lite has been stopped, leaves
out `uninstall.exe`, and adds the two files in `windows/portable/`:
*Ninaivu Lite Control Panel.vbs*, which opens the Control Panel with
`--data <this folder>\data`, and `README-PORTABLE.txt` (English and Tamil). The
private Python finds the packages through its `._pth` file (`..\pkgs`), so the
folder runs from any path. The workflow then extracts the zip to a path with a
space and Tamil letters, opens the Control Panel through the launcher, starts
and stops Ninaivu Lite from it, and checks its data was written beside it.

**Signing and the zip.** A zip needs no signing to be made or shared, but it does
not avoid Windows' download checks: what comes out of a downloaded zip carries
the same *from the internet* mark as the zip. The program that runs is the
Python Software Foundation's signed `pythonw.exe`, so SmartScreen has no unsigned
`.exe` to stop; Windows instead asks once before running the `.vbs`, unless the
zip is unblocked (*Properties → Unblock*) before it is extracted. Signing the
installer (above) does not change the zip.

## Linux and Raspberry Pi

```sh
sh Ninaivu-Lite-<version>-linux-amd64.sh            # or -arm64.sh on a Raspberry Pi
sh Ninaivu-Lite-<version>-linux-amd64.sh --photos ~/Pictures --no-service
```

Without root it installs under `~/.local/lib/ninaivu-lite` (as root:
`/opt/ninaivu-lite`), makes the commands `ninaivu-lite` and `ninaivu-lite-panel`,
a desktop entry for the Control Panel, and a systemd service that starts it at
boot. As root there is no Control Panel or desktop entry (use
`systemctl start|stop|restart ninaivu-lite`), and the `ninaivu-lite` command
always runs as the service's account. As root, the service runs as its own unprivileged `ninaivu-lite` account,
which owns `/var/lib/ninaivu-lite`; the installer names any photo folder that
account cannot read. Grant it read access, to what is added later too (a default
ACL), and passage through every folder above it, then restart the service:

```sh
sudo setfacl -R -m u:ninaivu-lite:rX /home/me/Pictures
sudo setfacl -R -d -m u:ninaivu-lite:rX /home/me/Pictures
sudo setfacl -m u:ninaivu-lite:x /home/me
sudo systemctl restart ninaivu-lite
```

The setup code for the first visit from another device is in
`journalctl -u ninaivu-lite` (`journalctl --user -u ninaivu-lite` without root),
and the log in `<data folder>/logs/ninaivu-lite.log`. A forgotten password:
`sudo systemctl stop ninaivu-lite`, then
`sudo -u ninaivu-lite /usr/local/bin/ninaivu-lite --reset-password NAME` (the
command already passes `--data /var/lib/ninaivu-lite`; run it as that account so
the index stays its own). Under the service account, the drive and phone prompt
may not see drives and phones the desktop opens for the person signed in.

A firewall may keep phones out: `sudo ufw allow 8080/tcp` (Ubuntu, Raspberry Pi
OS) or `sudo firewall-cmd --permanent --add-port=8080/tcp && sudo firewall-cmd
--reload` (Fedora).

Running a newer installer upgrades in place. `…/ninaivu-lite/uninstall`
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
`ninaivu_lite_data` volume. There is no Control Panel in a container. Inside a
container the computer's own name is not known, so Ninaivu Lite answers only to
its addresses and `localhost`; a name for it (`mypc.local`) goes in `ALLOWED_HOSTS`
(comma-separated) when starting compose.

## From a checkout, without an installer

`start.cmd` (Windows) or `./start.sh` — see the main README. On a server, the
Linux installer above is simpler; `tools/ninaivu-lite.service` is a systemd unit
for a checkout, to be edited for its user and paths.

## What has been tested

The release workflow builds every installer on each tag. It also installs,
starts, checks and removes the Windows installer (silently, Control Panel
included), the portable zip (from a path with a space and Tamil letters) and the
Linux amd64 installer (as an ordinary user, without the service). The macOS app
and the arm64 installer are built but not run there, and an install with `sudo`
(with its service) and the Docker image are not tried by the workflow at all: try
those by hand before publishing a release.

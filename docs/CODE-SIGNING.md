# Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by
[SignPath Foundation](https://signpath.org).

This applies to the Windows installer of Ninaivu Lite,
`Ninaivu-Lite-<version>-windows-x64.exe`, published on the
[releases page](https://github.com/javajaga-usa/Ninaivu-lite/releases), and to the
uninstaller it puts on the computer (`uninstall.exe`), which is made and signed by the same
build before it is packed into the installer.

## How a signed release is made

- Every release is built from the source in this repository by the public workflow
  [`.github/workflows/release.yml`](../.github/workflows/release.yml), on GitHub-hosted
  runners, from a version tag. Nothing is built or signed on a personal computer.
- The installer contains only what that build produces: Ninaivu Lite from this
  repository, Python from python.org, and the packages named in
  [`requirements.txt`](../requirements.txt) (Flask, Pillow, waitress and what they need).
- Each signing request is approved by hand before the certificate is used.
- The installer's product name is *Ninaivu Lite* and its product version is the version
  in [`ninaivu_lite/version.py`](../ninaivu_lite/version.py), for every build.

## Team roles

- Committers and reviewers: [Jagadeesh Rajendran](https://github.com/javajaga-usa)
- Approvers: [Jagadeesh Rajendran](https://github.com/javajaga-usa)

Changes from anyone else arrive as pull requests and are reviewed before they are merged.
The accounts above use multi-factor authentication for GitHub and for SignPath.

## Privacy policy

This program will not transfer any information to other networked systems unless
specifically requested by the user or the person installing or operating it.

Ninaivu Lite runs on a computer in your home and answers only to devices on your home
network. It has no accounts with us, no telemetry and no cloud service: your photographs,
their details and the people you add stay on your computer. The one request it can make
outside the house is to ask GitHub for the latest release's version number, and only when
you ask: by pressing *Check now* in the Control Panel, or by ticking *Tell me when a new
version is available* (then once a day, until unticked). The box starts unticked, and the
request carries nothing about you or your library.

## Reporting a problem

If you believe a signed file is not what it claims to be, or the certificate has been
misused, open a private security advisory on this repository (*Security* → *Report a
vulnerability*) — see [SECURITY.md](../SECURITY.md).

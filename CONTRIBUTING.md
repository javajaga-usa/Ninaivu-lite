# Contributing to Ninaivu Lite

Bug reports, fixes, documentation and Tamil translations are welcome.
For security problems, follow [SECURITY.md](SECURITY.md) instead of posting details publicly.

## Development setup

Use Python 3.10 or newer. The installer Python is pinned in `.python-version`.

```sh
python -m venv .venv
# macOS / Linux
. .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
ruff check .
python -m pytest
```

Start a development instance with a separate data folder and copies of sample photos:

```sh
python -m ninaivu_lite --data ./dev-data --no-browser
```

Keep personal photographs, passwords, settings and database files out of commits and reports.

## Pull requests

Branch from `main`, keep changes focused, and describe the problem and resulting behavior.
Run the lint and test commands above; include the results and any manual checks in the PR.
For behavior changes, add a useful regression test. For screen changes, include a screenshot
using sample media and check both Tamil and English on desktop and phone layouts.
Strings live in `ninaivu_lite/static/i18n/en.json` and `ta.json`.
Preserve the original media and the home-network privacy model.

GitHub CI checks Windows, macOS and Linux on Python 3.10 and the installer Python, on every
push to `main` and every pull request.
Installer changes also run the installer workflow. Maintainers publish releases through a
version tag or manual release workflow; see [installers/README.md](installers/README.md)
and [the code signing policy](docs/CODE-SIGNING.md).

## Reporting bugs and requesting features

Use the repository's issue forms. Include the app version, operating system, installation
method, reproduction steps, and expected and actual behavior. Remove private paths,
photographs and secrets from screenshots and logs before attaching them.

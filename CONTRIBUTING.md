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

The screens in a browser (every `tests/*_browser.py`), and the stylesheet and script checks
(Node 20 or newer), as CI runs them. Without Playwright and its Chromium those tests are
skipped; `NINAIVU_BROWSER_TESTS=1` makes a missing browser a failure instead. The video
tests need `ffmpeg` on the PATH, and the face straightening `requirements-straighten.txt`:

```sh
python -m pip install -r requirements-browser.txt -r requirements-straighten.txt
python -m playwright install chromium
NINAIVU_BROWSER_TESTS=1 python -m pytest      # Windows PowerShell: $env:NINAIVU_BROWSER_TESTS=1
npm ci
npm run lint
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

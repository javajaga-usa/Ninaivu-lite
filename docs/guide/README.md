# Ninaivu Lite user guide — source

Rebuilds [`../Ninaivu-Lite-User-Guide-English.pdf`](../Ninaivu-Lite-User-Guide-English.pdf) and [`../Ninaivu-Lite-User-Guide-Tamil.pdf`](../Ninaivu-Lite-User-Guide-Tamil.pdf)
(A4, printed by headless Chromium). Screenshots and sample photos are not kept here;
they are made again from the app.

| File | What it is |
| --- | --- |
| `en.html`, `ta.html`, `guide.css` | the guide text and layout; UI labels are `<span class="ui">` and must be strings from the app's `static/i18n/en.json` / `ta.json` |
| `make_library.py` | draws the sample library (120 Pillow pictures with EXIF dates, cameras and GPS, 2012–2025, plus 3 ffmpeg clips) and an "old drive" for the Import page |
| `shots.js` | Playwright: every screenshot, stage by stage, against a fresh Ninaivu Lite (`LANG_GUIDE=en|ta`) |
| `serve.sh`, `run_all.sh` | start a fresh app per language; `run_all.sh` does library + both languages + the Control Panel |
| `panel_shot.sh` | the Control Panel (Tk) on Xvfb, before an administrator exists (so it shows the setup code) |
| `prepare_images.py`, `build.js` | PNG → JPEG into `img/`, then HTML → PDF (printed twice: `toc_pages.py` reads which page each chapter starts on from the first printing's contents links, needs `pypdf` in `PY`) |
| `guide.js` | runs in the page before printing: frames screenshots as windows or phones, sets out chapter openers, fills the contents |
| `fonts/` | Inter (TrueType, from Google Fonts) and Noto Sans Tamil; TrueType so Chromium embeds them as real fonts, not Type 3 |
| `check_labels.py` | fails if a labelled UI string is not in the app's locale files |
| `montage.py` | contact sheet of PNGs, for checking pages and shots by eye |

## Rebuild

Needs: this checkout at the version documented (the scripts use the app two folders up;
set `APP=` for another checkout), a Python with the app's requirements
(`PY=`, default `/tmp/claude-0/venv/bin/python`), Node with Playwright and its Chromium
(`PLAYWRIGHT_BROWSERS_PATH`), ffmpeg, Xvfb + ImageMagick + a Python with tkinter
(`PANEL_PY=`, default `python3.12`; `apt install python3-tk`), poppler-utils, and the
**Noto Sans Tamil** font installed (or its TTFs in `fonts/`), or Tamil prints as boxes.
Root is needed because the library is put at `/home/family` so the paths in the
screenshots read naturally; port 8080 must be free for the panel shot.

```sh
./run_all.sh                          # library, shots/en, shots/ta, shots/panel.png (~10 min)
python prepare_images.py              # shots/*.png -> img/*.jpg
python check_labels.py
node build.js en out/en.pdf
node build.js ta out/ta.pdf
pdfinfo out/en.pdf; pdffonts out/ta.pdf | grep -i tamil
pdftoppm -r 40 -png out/ta.pdf out/png/ta && python montage.py sheet.png 8 330 out/png/ta-*.png
```

Then copy `out/en.pdf` and `out/ta.pdf` over the two PDFs in `docs/` and remove `/home/family`.
`shots/`, `img/`, `work/`, `run/` and `out/` are made by the build and are not kept in git. Drive and phone prompts are played back by `shots.js` (Playwright
routes); everything else is the real app. For *Library settings* `shots.js` adds a second
folder, `Old laptop photos`, and takes it off the disk so the page shows **Moved?**, then
removes it and waits for the first daily backup (two minutes after the start) so *Settings*
lists the copies kept. `run_all.sh` puts each app's data folder at
`/home/family/.local/share/ninaivu-lite` (`DATA=` for `serve.sh`), so the backups path in
*Settings* reads naturally. A mount that will not run scripts (`bad interpreter:
Permission denied`): copy this folder somewhere else and build there. For a new release update the version and date on the cover, the contents box and the back cover of both HTML files, and the running header in `guide.css` (`--running-version`). Page header and footer come from `@page` margin boxes in `guide.css`; they use system fonts (Liberation Sans, Noto Sans Tamil) because a web font there stops Chromium drawing them.

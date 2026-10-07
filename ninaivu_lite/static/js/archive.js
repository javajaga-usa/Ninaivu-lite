/**
 * The Import page — consolidating drives into one hash-verified archive.
 *
 * Ninaivu's archive panel, lighter: the front end for `api_import.py`. A
 * self-contained module: it owns its form state and its polling, and the
 * console hands it only what it cannot supply itself — a toast, the shared
 * folder picker, and a way to say the library changed.
 *
 * The form is the source of truth for a job. Nothing is saved as you type;
 * pressing Start (or Dry run) sends sources and destination to the server,
 * which remembers them for next time.
 */

import { reportUnauthorized } from './api.js';
import * as i18n from './i18n.js';

const $ = (sel) => document.querySelector(sel);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
};

async function json(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      Accept: 'application/json',
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    if (response.status === 401) reportUnauthorized(url);
    throw Object.assign(new Error(i18n.t(data?.error || response.statusText)),
      { status: response.status, data });
  }
  return data;
}

export const archiveApi = {
  status: () => json('/api/archive/status'),
  settings: () => json('/api/archive/settings'),
  validate: (body) => json('/api/archive/validate', { method: 'POST', body }),
  capacity: (body) => json('/api/archive/capacity', { method: 'POST', body }),
  capacityProgress: (token) =>
    json(`/api/archive/capacity/progress?token=${encodeURIComponent(token)}`),
  capacityCancel: (token) =>
    json('/api/archive/capacity/cancel', { method: 'POST', body: { token } }),
  start: (body) => json('/api/archive/start', { method: 'POST', body }),
  stop: () => json('/api/archive/stop', { method: 'POST' }),
  pause: () => json('/api/archive/pause', { method: 'POST' }),
  resume: () => json('/api/archive/resume', { method: 'POST' }),
  recent: (status, limit = 60) => json(
    `/api/archive/recent?limit=${limit}${status && status !== 'all' ? `&status=${status}` : ''}`),
  years: () => json('/api/archive/years'),
  retryErrors: () => json('/api/archive/retry-errors', { method: 'POST' }),
  reset: () => json('/api/archive/reset', { method: 'POST' }),
  adopt: (path) => json('/api/archive/adopt', { method: 'POST', body: { path } }),
  takeoutAlbums: () => json('/api/archive/takeout-albums'),
  recreateTakeoutAlbums: () => json('/api/archive/takeout-albums', { method: 'POST', body: {} }),
};

// Marked with key() and translated where they are shown: this table is built
// as the file loads, before any language has been fetched.
const KINDS = [
  ['image', i18n.key('Photos')],
  ['video', i18n.key('Video')],
];

/** "44,545 video" — how many of one kind, in the estimate. */
const KIND_COUNTS = {
  image: i18n.key('{count} photos'),
  video: i18n.key('{count} video'),
};

const PLAN_STATES = new Set(['planned', 'plan-duplicate', 'plan-skip']);
const POLL_MS = 2000;

/* ======================================================================== */

export class ArchivePanel {
  constructor({ toast, pickFolder, onLibraryChanged }) {
    this.toast = toast;
    this.pickFolder = pickFolder;
    this.onLibraryChanged = onLibraryChanged || (() => {});

    /** @type {string[]} */
    this.sources = [];
    this.filter = 'all';
    this.last = null;
    this.poll = null;
    this.loaded = false;
    this.validateTimer = null;
    /** The walk currently being watched, and the poll watching it. */
    this.estimateToken = null;
    this.estimateTimer = null;
    /** The tab title before a run borrowed it. */
    this.plainTitle = null;
    /** The estimate on screen, to redraw in another language. */
    this.estimateShown = null;

    i18n.onChange(() => {
      if (!this.loaded) return;
      this.renderSources();
      if (this.last) this.render(this.last);
      const line = $('#ar-capacity');
      if (this.estimateShown && line && !line.hidden && !line.classList.contains('working')) {
        this.showEstimate(this.estimateShown);
      }
      if (!$('#ar-takeout')?.hidden) this.loadTakeoutAlbums();
      this.loadRecent();
      this.loadYears();
    });
  }

  /* -- lifecycle -------------------------------------------------------- */

  wire() {
    $('#ar-add-source').onclick = () => this.addSource($('#ar-source-input').value);
    $('#ar-source-input').addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        this.addSource($('#ar-source-input').value);
      }
    });
    $('#ar-browse-source').onclick = () => this.pickFolder({
      title: i18n.t('Choose a folder to sweep'),
      cta: i18n.t('Add as a source'),
      anyFolder: true,
      start: this.sources[this.sources.length - 1] || '',
      pick: (path) => this.addSource(path),
    });
    $('#ar-browse-dest').onclick = () => this.pickFolder({
      title: i18n.t('Choose where the archive is built'),
      cta: i18n.t('Use this folder'),
      anyFolder: true,
      start: $('#ar-dest-input').value.trim(),
      pick: (path) => {
        $('#ar-dest-input').value = path;
        this.revalidate();
      },
    });
    $('#ar-dest-input').addEventListener('input', () => this.revalidate());
    for (const [kind] of KINDS) $(`#ar-type-${kind}`).onchange = () => this.revalidate();

    $('#ar-start').onclick = () => this.run('copy');
    $('#ar-dry').onclick = () => this.run('dry-run');
    $('#ar-audit').onclick = () => this.run('verify');
    $('#ar-stop').onclick = () => this.control('stop', i18n.t('Stopping…'));
    $('#ar-pause').onclick = () => {
      const paused = this.last?.is_paused;
      this.control(paused ? 'resume' : 'pause', paused ? i18n.t('Resuming…') : i18n.t('Pausing…'));
    };
    $('#ar-retry').onclick = () => this.simple('retryErrors');
    $('#ar-reset').onclick = () => this.simple('reset');
    $('#ar-export').onclick = () => { window.location.href = '/api/archive/manifest.csv'; };
    $('#ar-adopt').onclick = () => this.adopt();
    $('#ar-takeout-make').onclick = () => this.makeTakeoutAlbums();

    for (const button of document.querySelectorAll('#ar-filters button')) {
      button.onclick = () => {
        this.filter = button.dataset.status;
        document.querySelectorAll('#ar-filters button').forEach(
          (b) => b.classList.toggle('active', b === button));
        this.loadRecent();
      };
    }
  }

  /** Called when the Import page becomes visible. */
  async show() {
    if (!this.loaded) {
      this.loaded = true;
      await this.loadSettings();
    }
    await this.tick();
    this.loadRecent();
    this.loadYears();
    if (!this.poll) this.poll = setInterval(() => this.tick(), POLL_MS);
  }

  /** Called when the admin navigates away — stop polling, keep the state. */
  hide() {
    if (this.poll) { clearInterval(this.poll); this.poll = null; }
    // Nobody is looking at the estimate any more, so stop reading the drive
    // for it. Coming back to the page starts a fresh one.
    this.cancelEstimate();
  }

  async tick() {
    try {
      this.render(await archiveApi.status());
    } catch { /* the console's own error handling covers a dead server */ }
  }

  /* -- form state ------------------------------------------------------- */

  kinds() {
    return KINDS.map(([kind]) => kind).filter((kind) => $(`#ar-type-${kind}`).checked);
  }

  async loadSettings() {
    try {
      const saved = await archiveApi.settings();
      this.sources = (saved.source_dirs || []).map((entry) => entry.path).filter(Boolean);
      $('#ar-dest-input').value = saved.destination_dir || '';
      const kinds = saved.media_types || KINDS.map(([k]) => k);
      for (const [kind] of KINDS) $(`#ar-type-${kind}`).checked = kinds.includes(kind);
    } catch { /* first run, nothing saved */ }
    this.renderSources();
    this.revalidate();
  }

  /** A drive just plugged in, and Import chosen for it: the drive is the
   *  one source, since the sources saved last time may be drives that are
   *  not plugged in now, and would stop the job from starting. */
  async useDrive(path) {
    if (!this.loaded) {
      this.loaded = true;
      await this.loadSettings();
    }
    this.sources = [path];
    this.renderSources();
    this.revalidate();
  }

  addSource(raw) {
    const path = (raw || '').trim();
    if (!path) {
      this.toast(i18n.t('Type a folder, or press Browse.'), true);
      return;
    }
    if (this.sources.some((s) => samePath(s, path))) {
      this.toast(i18n.t('That folder is already a source.'), true);
      return;
    }
    this.sources.push(path);
    $('#ar-source-input').value = '';
    this.renderSources();
    this.revalidate();
  }

  renderSources() {
    const list = $('#ar-sources');
    list.innerHTML = '';
    $('#ar-sources-empty').hidden = this.sources.length > 0;
    this.sources.forEach((source, index) => {
      const row = el('div', 'ar-source');
      const path = el('span', 'ar-source-path', source);
      path.title = source;
      row.appendChild(path);
      const remove = el('button', 'btn ghost small', i18n.t('Remove'));
      remove.type = 'button';
      remove.onclick = () => {
        this.sources.splice(index, 1);
        this.renderSources();
        this.revalidate();
      };
      row.appendChild(remove);
      list.appendChild(row);
    });
  }

  job(mode) {
    return {
      source_dirs: this.sources.map((path) => ({ path })),
      destination_dir: $('#ar-dest-input').value.trim(),
      media_types: this.kinds(),
      ...(mode ? { mode } : {}),
    };
  }

  /* -- validation ------------------------------------------------------- */

  revalidate() {
    clearTimeout(this.validateTimer);
    this.validateTimer = setTimeout(() => this.validate(), 350);
  }

  async validate() {
    const job = this.job();
    if (!job.source_dirs.length || !job.destination_dir) {
      this.cancelEstimate();
      this.showNotes({ problems: [], notices: [], resolution: null });
      $('#ar-capacity').hidden = true;
      return;
    }
    // Reaching a sleeping external drive takes seconds on its own, so say so
    // rather than leaving the panel looking as though nothing was pressed.
    this.sayWorking(i18n.t('Checking that folder…'));
    try {
      const result = await archiveApi.validate(job);
      this.showNotes(result);
      if (result.ok) this.estimate(job);
      else { this.cancelEstimate(); $('#ar-capacity').hidden = true; }
    } catch {
      this.cancelEstimate();
      $('#ar-capacity').hidden = true;
    }
  }

  /** Put the capacity line into its working state. */
  sayWorking(text) {
    const line = $('#ar-capacity');
    line.hidden = false;
    line.classList.remove('tight');
    line.classList.add('working');
    line.innerHTML = '';
    line.append(el('span', 'ar-spinner'), el('span', 'ar-working-text', text));
  }

  /**
   * Call off the walk that is running, if any, and stop watching it. Every
   * edit to the form starts a new estimate; without this, removing a drive
   * left the old walk reading it for minutes, and a slow answer for a job
   * the user had already changed could land on top of the right one.
   */
  cancelEstimate() {
    clearInterval(this.estimateTimer);
    this.estimateTimer = null;
    const token = this.estimateToken;
    this.estimateToken = null;
    if (token) archiveApi.capacityCancel(token).catch(() => {});
  }

  /** "1m 12s" — short enough for a status line, honest about a long wait. */
  static clock(seconds) {
    const whole = Math.max(0, Math.round(seconds || 0));
    if (whole < 60) return i18n.t('{seconds}s', { seconds: whole });
    const mins = Math.floor(whole / 60);
    if (mins < 60) {
      return i18n.t('{minutes}m {seconds}s', { minutes: mins, seconds: String(whole % 60).padStart(2, '0') });
    }
    return i18n.t('{hours}h {minutes}m', { hours: Math.floor(mins / 60), minutes: String(mins % 60).padStart(2, '0') });
  }

  /** Repaint the working line from a progress snapshot. */
  showEstimateProgress(snapshot) {
    const line = $('#ar-capacity');
    if (!line.classList.contains('working')) return;
    const text = line.querySelector('.ar-working-text');
    if (!text) return;
    text.innerHTML = '';
    if (!snapshot || !snapshot.known) {
      text.append(document.createTextNode(i18n.t('Looking through the folder…')));
      return;
    }
    text.append(
      ...rich(i18n.t('Counting… {files} files so far · {size}', { size: bytes(snapshot.bytes || 0) }),
        { files: Number(snapshot.files || 0).toLocaleString() }),
      document.createTextNode(` · ${ArchivePanel.clock(snapshot.elapsed)}`),
    );
    if (snapshot.folder) {
      const where = el('span', 'ar-working-where', snapshot.folder);
      where.title = snapshot.folder;
      text.append(document.createTextNode(' · '), ...rich(i18n.t('now in {folder}'), { folder: where }));
    }
  }

  async estimate(job) {
    const line = $('#ar-capacity');
    this.cancelEstimate();

    const token = `est-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    this.estimateToken = token;
    this.sayWorking(i18n.t('Looking through the folder…'));

    const ask = async () => {
      if (this.estimateToken !== token) return;
      try {
        const snapshot = await archiveApi.capacityProgress(token);
        if (this.estimateToken === token) this.showEstimateProgress(snapshot);
      } catch { /* a dropped poll is not worth reporting */ }
    };
    ask();
    this.estimateTimer = setInterval(ask, 500);

    try {
      const result = await archiveApi.capacity({ ...job, progress_token: token });
      // A newer estimate started while this one was walking: its answer is
      // the right one, and this one must not overwrite it.
      if (this.estimateToken !== token) return;
      clearInterval(this.estimateTimer);
      this.estimateTimer = null;
      this.estimateToken = null;
      if (!result.ok) {
        this.estimateShown = null;
        line.hidden = true;
        return;
      }
      this.showEstimate(result);
    } catch {
      if (this.estimateToken === token) {
        clearInterval(this.estimateTimer);
        this.estimateTimer = null;
        this.estimateToken = null;
        line.classList.remove('working');
        line.hidden = true;
      }
    }
  }

  /** Draw a finished estimate. */
  showEstimate(result) {
    this.estimateShown = result;
    const line = $('#ar-capacity');
    line.classList.remove('working');
    line.innerHTML = '';
    const counted = { files: result.files.toLocaleString(), size: bytes(result.bytes) };
    line.append(...rich(result.truncated
      ? i18n.t('At least {files} files, {size}') : i18n.t('About {files} files, {size}'), counted));

    // What those files actually are: "44,545 video" shows a wrong selection
    // at a glance where one total cannot.
    const parts = KINDS
      .filter(([kind]) => result.by_kind?.[kind])
      .map(([kind]) =>
        i18n.t(KIND_COUNTS[kind], { count: result.by_kind[kind].toLocaleString() })
        + (result.bytes_by_kind?.[kind] ? ` (${bytes(result.bytes_by_kind[kind])})` : ''));
    if (parts.length > 1) line.append(document.createTextNode(` — ${parts.join(' · ')}`));

    if (result.free != null) {
      const room = { needed: bytes(result.needed), free: bytes(result.free) };
      line.append(document.createTextNode('. '), ...rich(result.fits
        ? i18n.t('Needs {needed} free; the destination has {free}.')
        : i18n.t('Needs {needed} free; the destination has {free} — not enough room.'), room));
    }
    if (result.truncated) {
      line.append(document.createTextNode(
        ` ${i18n.t('Counting stopped at 200,000 files — the real job is larger.')}`));
    }
    line.classList.toggle('tight', !result.fits);
  }

  showNotes({ problems, notices, resolution }) {
    const fix = $('#ar-resolution');
    if (resolution?.corrected) {
      fix.hidden = false;
      $('#ar-resolution-text').textContent =
        `${i18n.t(resolution.reason || 'That folder is inside an existing archive.')} `
        + i18n.t('Using “{folder}” instead.', { folder: resolution.destination });
    } else {
      fix.hidden = true;
    }
    fillList('#ar-notices', '#ar-notice-list', notices);
    fillList('#ar-problems', '#ar-problem-list', problems);
  }

  /* -- running ---------------------------------------------------------- */

  async run(mode) {
    const button = { copy: $('#ar-start'), 'dry-run': $('#ar-dry'), verify: $('#ar-audit') }[mode];
    const label = button.textContent;
    button.disabled = true;
    button.textContent = i18n.t('Starting…');
    try {
      const result = await archiveApi.start(this.job(mode));
      this.showNotes({ problems: [], notices: [], resolution: result.resolution });
      if (result.destination) $('#ar-dest-input').value = result.destination;
      this.toast(i18n.t(result.message));
      this.loadRecent();
      // Now is the moment to ask: they have just started something that may
      // run for hours. A prompt on page load gets dismissed by reflex.
      this.askToNotify();
    } catch (exc) {
      this.showNotes({
        problems: exc.data?.problems || [exc.message],
        notices: [],
        resolution: exc.data?.resolution,
      });
      this.toast(exc.message, true);
    } finally {
      button.disabled = false;
      button.textContent = label;
      this.tick();
    }
  }

  async control(action, busy) {
    try {
      this.toast(busy);
      const result = await archiveApi[action]();
      if (result?.message) this.toast(i18n.t(result.message));
    } catch (exc) {
      this.toast(exc.message, true);
    }
    this.tick();
  }

  async simple(action) {
    try {
      const result = await archiveApi[action]();
      this.toast(i18n.t(result.message));
      this.loadRecent();
      this.loadYears();
      this.tick();
    } catch (exc) {
      this.toast(exc.message, true);
    }
  }

  async adopt() {
    const button = $('#ar-adopt');
    button.disabled = true;
    const label = button.textContent;
    button.textContent = i18n.t('Adding…');
    try {
      const result = await archiveApi.adopt(this.last?.handoff?.destination || '');
      this.toast(i18n.t(result.message));
      await this.onLibraryChanged();
      this.tick();
    } catch (exc) {
      this.toast(exc.message, true);
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  }

  /* A Google Photos export keeps its albums as folders. Once the import has
     run, the archive knows where every member went, so the albums can be made
     in the library without reading the export again. */
  async loadTakeoutAlbums() {
    const box = $('#ar-takeout');
    let found;
    try {
      found = (await archiveApi.takeoutAlbums()).albums || [];
    } catch { box.hidden = true; return; }
    if (!found.length) { box.hidden = true; return; }
    box.hidden = false;
    const files = found.reduce((n, a) => n + a.files, 0);
    $('#ar-takeout-title').textContent = found.length === 1
      ? i18n.t('Google Photos album found: {title}', { title: found[0].title })
      : i18n.t('{count} Google Photos albums found in the sources', { count: found.length });
    $('#ar-takeout-sub').textContent = i18n.t('{count} photographs across {albums}. Make them in the library, once the archive has been added and indexed.', {
      count: files.toLocaleString(),
      albums: found.slice(0, 4).map((a) => a.title).join(', ') + (found.length > 4 ? '…' : ''),
    });
  }

  async makeTakeoutAlbums() {
    const button = $('#ar-takeout-make');
    button.disabled = true;
    try {
      const result = await archiveApi.recreateTakeoutAlbums();
      const added = result.albums.reduce((n, a) => n + a.added, 0);
      const lines = [result.albums.length
        ? i18n.t('{albums} albums, {photos} photographs.', {
          albums: result.albums.length, photos: added.toLocaleString() })
        : i18n.t('Nothing to add yet.')];
      if (result.unmatched) {
        lines.push(i18n.t('{count} not indexed yet — run this again after the scan.', {
          count: result.unmatched.toLocaleString() }));
      }
      this.toast(lines.join(' '), !result.albums.length);
    } catch (exc) {
      this.toast(exc.message, true);
    } finally {
      button.disabled = false;
    }
  }

  /* -- rendering -------------------------------------------------------- */

  render(data) {
    const wasRunning = this.last?.is_scanning;
    this.last = data;

    const running = data.is_scanning;
    const dry = data.job_mode === 'dry-run';

    const status = $('#ar-status-text').parentElement;
    status.classList.toggle('busy', running);
    status.classList.toggle('done', !running && data.phase === 'done');
    status.classList.toggle('bad', !running && ['failed', 'incomplete', 'interrupted'].includes(data.phase));
    $('#ar-status-text').textContent = statusLine(data);

    // Controls
    $('#ar-start').hidden = running;
    $('#ar-dry').hidden = running;
    $('#ar-audit').hidden = running;
    $('#ar-stop').hidden = !running;
    $('#ar-pause').hidden = !running;
    $('#ar-pause').textContent = data.is_paused ? i18n.t('Resume') : i18n.t('Pause');

    // Progress
    const progress = $('#ar-progress');
    const total = data.total_files || 0;
    const done = data.processed || 0;
    progress.hidden = !running;
    if (!progress.hidden) {
      const pct = total ? Math.min(100, (done / total) * 100) : 0;
      $('#ar-fill').style.width = `${pct}%`;
      // On a resume the walk meets finished files mixed in with new ones.
      // One number for both made a correct resume look like the archive
      // being copied again, so the two are told apart.
      const stepped = Number(data.stepped_over || 0);
      const fresh = Math.max(0, done - stepped);
      const counts = {
        done: done.toLocaleString(),
        total: total.toLocaleString(),
        fresh: fresh.toLocaleString(),
        stepped: stepped.toLocaleString(),
      };
      $('#ar-count').textContent = data.phase === 'counting'
        ? i18n.t('Counting… {files} files so far · {size}', { files: counts.total, size: bytes(data.total_bytes || 0) })
        : stepped
          ? i18n.t('{done} of {total} files checked · {fresh} new this run · {stepped} already done, stepped over', counts)
          : i18n.t('{done} of {total} files', counts);
      $('#ar-eta').textContent = data.eta_seconds != null
        ? i18n.t('about {time} left', { time: duration(data.eta_seconds) }) : '';
      $('#ar-bytes').textContent = data.bytes_copied
        ? i18n.t('{size} copied', { size: bytes(data.bytes_copied) }) : '';
    }

    // Start has always been Resume — progress lives in the index, so Start
    // after a power cut continues rather than starting over. Saying so is the
    // point of this banner: a run stepping over three hundred thousand files
    // looks exactly like one beginning, bar a counter moving impossibly fast.
    const resume = $('#ar-resume-banner');
    resume.hidden = !(running && data.is_resume && !dry);
    if (!resume.hidden) {
      $('#ar-resume-text').textContent = i18n.t('{done} files were already done by an earlier run and will be stepped over.', {
        done: Number(data.stepped_over || 0).toLocaleString() });
    }

    $('#ar-dry-banner').hidden = !dry;

    // A dry run predicts rather than does, so the cards must not claim files
    // were verified when nothing has been written.
    $('#ar-label-verified').textContent = dry ? i18n.t('Would archive') : i18n.t('Verified');
    $('#ar-verified').textContent = (dry ? data.planned : data.verified || 0).toLocaleString();
    $('#ar-duplicates').textContent =
      (dry ? data.plan_duplicates : data.duplicates || 0).toLocaleString();
    $('#ar-skipped').textContent =
      (dry ? data.plan_skipped : data.skipped || 0).toLocaleString();
    const errors = data.errors || 0;
    $('#ar-errors').textContent = errors.toLocaleString();
    $('#ar-errors-card').classList.toggle('warn', errors > 0);

    this.renderHandoff(data.handoff);
    if (data.handoff?.available && !this._takeoutChecked) {
      this._takeoutChecked = true;
      this.loadTakeoutAlbums();
    }

    this.showInTitle(data);

    // A run that just finished has new files and new years to show.
    if (wasRunning && !running) {
      this.loadRecent();
      this.loadYears();
      this.toast(dry ? i18n.t('Dry run finished.') : i18n.t('Run finished.'));
      this.announceFinish(data);
    }
  }

  /* -- telling somebody who is not looking ------------------------------ */

  /**
   * Put the run in the tab title. A consolidation runs for hours behind
   * eleven other tabs; the first characters of the title are all that is
   * visible of it. Restored exactly when the run ends.
   */
  showInTitle(data) {
    if (this.plainTitle == null) this.plainTitle = document.title;
    if (!data.is_scanning) {
      document.title = this.plainTitle;
      return;
    }
    const total = data.total_files || 0;
    const done = data.processed || 0;
    const pct = total ? Math.min(100, Math.floor((done / total) * 100)) : 0;
    const what = { 'dry-run': i18n.t('dry run'), verify: i18n.t('audit') }[data.job_mode] || i18n.t('archiving');
    document.title = data.is_paused
      ? i18n.t('Paused · {title}', { title: this.plainTitle })
      : i18n.t('{percent}% {what} · {title}', { percent: pct, what, title: this.plainTitle });
  }

  /** A browser notification when a run ends, if permission was given. */
  announceFinish(data) {
    if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
    const dry = data.job_mode === 'dry-run';
    const bad = data.phase === 'failed' || (data.errors || 0) > 0;
    const counted = (n) => Number(n || 0).toLocaleString();
    let body;
    if (bad) {
      body = i18n.t('{count} files could not be archived. Open the console to see which.', {
        count: counted(data.errors) });
    } else if (dry) {
      body = i18n.t('{count} files would be archived, {duplicates} are duplicates. Nothing was written.', {
        count: counted(data.planned), duplicates: counted(data.plan_duplicates) });
    } else if (data.duplicates) {
      body = i18n.t('{count} files archived and verified, {duplicates} duplicates skipped.', {
        count: counted(data.verified), duplicates: counted(data.duplicates) });
    } else {
      body = i18n.t('{count} files archived and verified.', { count: counted(data.verified) });
    }
    try {
      new Notification(
        bad ? i18n.t('Ninaivu — the run stopped with errors') : i18n.t('Ninaivu — the run has finished'),
        { body, tag: 'ninaivu-archive-run' },
      );
    } catch { /* a refused or unavailable notification is not a failure */ }
  }

  /** Ask once, when a run starts — never on page load. */
  askToNotify() {
    if (typeof Notification === 'undefined' || Notification.permission !== 'default') return;
    try {
      Notification.requestPermission().catch(() => {});
    } catch { /* older browsers took a callback */ }
  }

  renderHandoff(handoff) {
    const box = $('#ar-handoff');
    if (!handoff || !handoff.available) { box.hidden = true; return; }
    box.hidden = false;
    const settled = handoff.in_library;
    box.classList.toggle('settled', settled);
    $('#ar-handoff-title').textContent = settled
      ? i18n.t('This archive is in your library')
      : i18n.t('Add this archive to your library?');
    $('#ar-handoff-sub').textContent = settled
      ? i18n.t('{folder} — Ninaivu indexes it, so the household can browse it.', { folder: handoff.destination })
      : i18n.t('{folder} holds {count} verified files. Adding it lets Ninaivu index it so the household can browse it.', {
        folder: handoff.destination, count: handoff.verified.toLocaleString() });
    $('#ar-adopt').hidden = settled;
  }

  async loadRecent() {
    const body = $('#ar-tbody');
    let rows;
    try {
      rows = await archiveApi.recent(this.filter);
    } catch { return; }
    body.innerHTML = '';
    if (!rows.length) {
      const empty = el('tr');
      const cell = el('td', 'ar-empty', i18n.t('Nothing here yet'));
      cell.colSpan = 3;
      empty.appendChild(cell);
      body.appendChild(empty);
      return;
    }
    for (const row of rows) {
      const tr = el('tr');
      const name = el('td', null, row.filename || row.source_path || '');
      name.title = row.source_path || '';
      tr.appendChild(name);
      const state = el('td');
      state.appendChild(el('span', `ar-state ${row.status}`, stateLabel(row.status)));
      tr.appendChild(state);
      tr.appendChild(el('td', null, detail(row)));
      body.appendChild(tr);
    }
  }

  async loadYears() {
    let years;
    try {
      years = await archiveApi.years();
    } catch { return; }
    const box = $('#ar-years');
    const chart = $('#ar-years-chart');
    if (!years.length) { box.hidden = true; return; }
    box.hidden = false;
    chart.innerHTML = '';
    const peak = Math.max(...years.map((y) => y.count), 1);
    for (const year of years) {
      const column = el('div', 'ar-year');
      column.title = i18n.t('{year}: {count} files', { year: year.year, count: year.count.toLocaleString() });
      const bar = el('i');
      bar.style.height = `${Math.max(3, (year.count / peak) * 100)}%`;
      column.appendChild(bar);
      column.appendChild(el('span', null, year.year));
      chart.appendChild(column);
    }
  }
}

/* -- helpers ------------------------------------------------------------- */

/**
 * A translated sentence as nodes, with some of its `{placeholders}` drawn as
 * elements: a string becomes a <strong>, a node goes in as it is.
 */
function rich(text, parts) {
  const nodes = [];
  let at = 0;
  for (const match of text.matchAll(/\{(\w+)\}/g)) {
    if (!(match[1] in parts)) continue;
    if (match.index > at) nodes.push(document.createTextNode(text.slice(at, match.index)));
    const part = parts[match[1]];
    nodes.push(typeof part === 'string' ? strong(part) : part);
    at = match.index + match[0].length;
  }
  if (at < text.length) nodes.push(document.createTextNode(text.slice(at)));
  return nodes;
}

function strong(text) {
  const node = document.createElement('strong');
  node.textContent = text;
  return node;
}

/** A sentence from the server: `{key, vars}` is translated and filled, a
 *  plain string looked up as it is. */
export function said(item) {
  if (item && typeof item === 'object') return i18n.t(item.key, item.vars || {});
  return i18n.t(String(item ?? ''));
}

function fillList(boxSel, listSel, items) {
  const box = $(boxSel);
  const list = $(listSel);
  if (!items || !items.length) { box.hidden = true; return; }
  box.hidden = false;
  list.innerHTML = '';
  for (const item of items) list.appendChild(el('li', null, said(item)));
}

function statusLine(data) {
  if (data.is_paused) return i18n.t('Paused');
  if (data.is_scanning) {
    const what = { 'dry-run': i18n.t('Dry run'), verify: i18n.t('Auditing the archive') }[data.job_mode]
      || i18n.t('Consolidating');
    if (data.phase === 'counting') return `${what} — ${i18n.t('Looking through the folder…')}`;
    return `${what}…`;
  }
  // The server's sentence, translated: {key, vars} (and more sentences after it).
  const message = data.job_said
    ? [data.job_said, ...(data.job_said.more || [])].map((part) => said(part)).join(' ')
    : (data.job_message ? i18n.t(data.job_message) : '');
  if (data.phase === 'done') return message || i18n.t('Finished');
  if (data.phase === 'failed') return message || i18n.t('Stopped with errors');
  if (data.phase === 'stopped') return message || i18n.t('Ready — Start also resumes');
  if (data.phase === 'incomplete' || data.phase === 'interrupted') return message;
  if (data.total_scanned) return i18n.t('Ready — Start also resumes');
  return i18n.t('Ready');
}

function stateLabel(status) {
  const words = {
    verified: i18n.t('verified'),
    duplicate: i18n.t('duplicate'),
    skipped: i18n.t('skipped'),
    error: i18n.t('error'),
    pending: i18n.t('queued'),
    planned: i18n.t('would copy'),
    'plan-duplicate': i18n.t('would skip'),
    'plan-skip': i18n.t('would skip'),
  };
  return words[status] || status;
}

function detail(row) {
  if (row.error) return i18n.t(row.error);
  if (row.duplicate_of) return i18n.t('same bytes as {file}', { file: row.duplicate_of });
  if (row.destination_path) {
    return PLAN_STATES.has(row.status) ? `→ ${row.destination_path}` : row.destination_path;
  }
  if (row.date) return `${row.date} (${row.date_source || i18n.t('date')})`;
  return '';
}

export function bytes(n) {
  const value = Number(n) || 0;
  if (value < 1024) return `${value} B`;
  const units = ['KB', 'MB', 'GB', 'TB', 'PB'];
  let size = value / 1024;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
  return `${size < 10 ? size.toFixed(1) : Math.round(size)} ${units[unit]}`;
}

function duration(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 60) return i18n.t('{seconds}s', { seconds: s });
  if (s < 3600) return i18n.t('{minutes} min', { minutes: Math.round(s / 60) });
  const hours = Math.floor(s / 3600);
  const minutes = Math.round((s % 3600) / 60);
  return minutes ? i18n.t('{hours}h {minutes}m', { hours, minutes }) : i18n.t('{hours}h', { hours });
}

/** Windows is case-insensitive and both separators reach the same folder. */
function samePath(a, b) {
  const key = (p) => String(p).replace(/\\/g, '/').replace(/\/+$/, '');
  const norm = (p) => (/^[a-zA-Z]:/.test(key(p)) ? key(p).toLowerCase() : key(p));
  return norm(a) === norm(b);
}

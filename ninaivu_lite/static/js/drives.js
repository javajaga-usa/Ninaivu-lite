/**
 * A pendrive, an external hard drive or a phone was plugged in: one question.
 *
 *   Import media from this drive   → the Import page, with the drive as its source;
 *                                    a phone Windows reaches only through Explorer
 *                                    is fetched, imported and tidied by the server
 *                                    (phones.PhoneImport)
 *   Export media to this drive     → the library copied onto it (drives.Exporter);
 *                                    not offered for a phone
 *   Not now                        → asked again only when it is plugged in again
 *
 * The console asks the server which drives are plugged in every few seconds
 * while it is open and on screen. The Control Panel asks the same question on
 * the computer itself and sends its answer here as `?drive=<path>&do=import`
 * (or `export`): an import opens the Import page on arrival; an export, or a
 * phone fetched by the server, is asked here once more before anything copies.
 *
 * The dialog is built here rather than in admin.html, which the browser may
 * still have cached from before this file existed.
 */

import * as i18n from './i18n.js';
import { bytes as size, said } from './archive.js';

const POLL_MS = 4000;

/** The two things that run on after the question: their routes and words. */
const JOBS = {
  export: {
    start: '/api/admin/drives/export',
    status: '/api/admin/drives/export',
    stop: '/api/admin/drives/export/stop',
    title: i18n.key('Copying to the drive'),
  },
  phone: {
    start: '/api/admin/drives/phone-import',
    status: '/api/admin/drives/phone-import',
    stop: '/api/admin/drives/phone-import/stop',
    title: i18n.key('Importing from the phone'),
  },
};

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
};

function samePath(a, b) {
  const key = (p) => String(p).replace(/\\/g, '/').replace(/\/+$/, '');
  const norm = (p) => (/^[a-zA-Z]:/.test(key(p)) ? key(p).toLowerCase() : key(p));
  return norm(a) === norm(b);
}

export class DrivePrompt {
  /**
   * @param {{json: Function, toast: Function, openImport: (path: string) => Promise<void>}} deps
   */
  constructor({ json, toast, openImport }) {
    this.json = json;
    this.toast = toast;
    this.openImport = openImport;
    this.timer = null;
    this.modal = null;
    /** The drive on screen, and the job ('export' or 'phone') the dialog follows, if any. */
    this.drive = null;
    this.job = null;
    this.jobTimer = null;
    this.lastJob = null;
    /** The drive ids already shown this visit, so Esc does not bring it straight back. */
    this.shown = new Set();
    i18n.onChange(() => { if (this.modal && !this.modal.hidden) this.redraw(); });
  }

  start() {
    if (this.timer) return;
    this.followLink();
    this.check();
    this.timer = setInterval(() => { if (!document.hidden) this.check(); }, POLL_MS);
  }

  /* -- asking ------------------------------------------------------------- */

  async check() {
    if (this.job) return;
    let data;
    try {
      data = await this.json('/api/admin/drives');
    } catch { return; /* the console's own error handling covers a dead server */ }
    if (this.job) return;
    // A copy to a drive, or a phone import, still going after the console
    // was reloaded or closed: shown again, so nobody pulls the drive mid-copy.
    const going = ['export', 'phone'].find((job) => data[job]?.running);
    if (going && !this.jobTimer) {
      this.drive = { id: '', label: data[going].drive || data[going].phone || '' };
      this.job = going;
      this.lastJob = data[going];
      this.redraw();
      this.modal.hidden = false;
      this.showJob(data[going]);
      this.follow(going);
      return;
    }
    if (this.modal && !this.modal.hidden) {
      // Taken out, or answered on another screen (the Control Panel, another
      // tab): nothing left to ask here.
      const still = this.drive && data.drives.find((d) => d.id === this.drive.id);
      if (!still || !still.pending) this.close();
      return;
    }
    // Not over another dialog, or a locked screen: asked when they are gone.
    if (document.body.classList.contains('screen-locked')
        || document.querySelector('.modal:not([hidden]):not(.drive-modal), .sheet:not([hidden])')) return;
    const next = data.drives.find((d) => d.pending && !this.shown.has(d.id));
    if (next) this.ask(next);
  }

  /** The Control Panel's answer, carried in the address. */
  async followLink() {
    const params = new URLSearchParams(window.location.search);
    const path = params.get('drive');
    const action = params.get('do');
    if (!path) return;
    params.delete('drive');
    params.delete('do');
    const rest = params.toString();
    window.history.replaceState(null, '', `${window.location.pathname}${rest ? `?${rest}` : ''}${window.location.hash}`);
    let data;
    try { data = await this.json('/api/admin/drives'); } catch { return; }
    const drive = data.drives.find((d) => samePath(d.path, path));
    if (!drive) {
      this.toast(i18n.t('That drive is no longer plugged in.'), true);
      return;
    }
    this.shown.add(drive.id);
    // Only opening the Import page follows a link straight away. Anything
    // that copies (the library onto the drive, a phone into the archive) is
    // asked here first: a link is something anybody can send.
    if (action === 'import' && !drive.shell) this.importFrom(drive);
    else this.ask(drive);
  }

  ask(drive) {
    this.drive = drive;
    this.shown.add(drive.id);
    this.job = null;
    this.redraw();
    this.modal.hidden = false;
    this.modal.querySelector('.btn.primary')?.focus();
  }

  build() {
    if (this.modal) return;
    const modal = el('div', 'modal drive-modal');
    modal.hidden = true;
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-labelledby', 'drive-title');
    modal.addEventListener('click', (event) => { if (event.target === modal) this.notNow(); });
    modal.addEventListener('keydown', (event) => { if (event.key === 'Escape') this.notNow(); });
    modal.appendChild(el('div', 'modal-card narrow'));
    document.body.appendChild(modal);
    this.modal = modal;
  }

  redraw() {
    this.build();
    const card = this.modal.firstChild;
    card.replaceChildren();
    const drive = this.drive;
    if (!drive) return;
    const phone = drive.kind === 'phone';
    const heading = this.job ? JOBS[this.job].title
      : (phone ? 'A phone was connected' : 'A drive was connected');
    const title = el('h2', '', i18n.t(heading));
    title.id = 'drive-title';
    card.appendChild(title);
    const name = drive.label && drive.label !== drive.path && !drive.shell
      ? `${drive.label} (${drive.path})` : drive.label || drive.path;
    card.appendChild(el('p', 'hint drive-name', name));
    if (drive.total) {
      card.appendChild(el('p', 'hint', i18n.t('{free} free of {total}',
        { free: size(drive.free), total: size(drive.total) })));
    }

    if (this.job) {
      const bar = el('div', 'scan-bar');
      this.fill = el('i');
      bar.appendChild(this.fill);
      card.appendChild(bar);
      this.line = el('p', 'hint drive-progress');
      card.appendChild(this.line);
      const foot = el('div', 'modal-foot');
      foot.appendChild(el('div', 'spacer'));
      this.stopButton = el('button', 'btn ghost', i18n.t('Stop'));
      this.stopButton.type = 'button';
      this.stopButton.onclick = () => this.stopJob();
      const close = el('button', 'btn primary', i18n.t('Close'));
      close.type = 'button';
      close.onclick = () => this.close();
      foot.append(this.stopButton, close);
      card.appendChild(foot);
      if (this.lastJob) this.showJob(this.lastJob);
      return;
    }

    card.appendChild(el('p', 'hint', i18n.t('What would you like to do with it?')));
    const choices = el('div', 'drive-choices');
    const choice = (label, hint, primary, action) => {
      const button = el('button', `btn ${primary ? 'primary' : ''} drive-choice`);
      button.type = 'button';
      button.appendChild(el('span', 'drive-choice-label', i18n.t(label)));
      button.appendChild(el('span', 'drive-choice-hint', i18n.t(hint)));
      button.onclick = action;
      return button;
    };
    if (phone) {
      choices.append(choice('Import media from this phone',
        'Copy its camera photos and videos into the archive, sorted by date. Nothing on the phone is changed.',
        true, () => this.importFrom(drive)));
    } else {
      choices.append(
        choice('Import media from this drive',
          'Copy its photos and videos into the archive, sorted by date. The drive is not changed.',
          true, () => this.importFrom(drive)),
        choice('Export media to this drive',
          'Copy the library’s photos and videos onto the drive, in a “Ninaivu Lite” folder.',
          false, () => this.run('export', drive)),
      );
    }
    card.appendChild(choices);
    if (phone) {
      card.appendChild(el('p', 'hint subtle drive-note', i18n.t(
        'Nothing showing? Unlock the phone and choose File transfer (on an iPhone, Trust this computer).')));
    }
    const foot = el('div', 'modal-foot');
    foot.appendChild(el('div', 'spacer'));
    const later = el('button', 'btn ghost', i18n.t('Not now'));
    later.type = 'button';
    later.onclick = () => this.notNow();
    foot.appendChild(later);
    card.appendChild(foot);
  }

  close() {
    if (this.modal) this.modal.hidden = true;
    this.job = null;
    this.drive = null;
  }

  /* -- the answers -------------------------------------------------------- */

  async answer(drive) {
    try {
      await this.json('/api/admin/drives/answer', { method: 'POST', body: { id: drive.id } });
    } catch { /* asked again next time it is plugged in: harmless */ }
  }

  notNow() {
    if (this.job) { this.close(); return; }
    if (this.drive) this.answer(this.drive);
    this.close();
  }

  async importFrom(drive) {
    // A phone Windows shows only in Explorer has no folder to open: the
    // server fetches it over the cable and imports it in one go.
    if (drive.shell) { this.run('phone', drive); return; }
    this.close();
    await this.answer(drive);
    await this.openImport(drive.path);
    this.toast(i18n.t('The drive is the source. Check the destination, then press Start.'));
  }

  async run(job, drive) {
    try {
      await this.json(JOBS[job].start, { method: 'POST', body: { id: drive.id } });
    } catch (exc) {
      this.toast(exc.message, true);
      this.close();
      return;
    }
    this.drive = drive;
    this.job = job;
    this.lastJob = null;
    this.redraw();
    this.modal.hidden = false;
    this.follow(job);
  }

  /** Until the job ends, even with the dialog closed: then say how it went. */
  follow(job) {
    if (this.jobTimer) return;
    this.jobTimer = setInterval(async () => {
      let state;
      try { state = await this.json(JOBS[job].status); } catch { return; }
      this.lastJob = state;
      const watching = this.job === job;
      if (watching) this.showJob(state);
      if (!state.running) {
        clearInterval(this.jobTimer);
        this.jobTimer = null;
        // Said in the dialog when it is open; a toast when it was closed.
        if (state.message && !watching) this.toast(said(state.message), state.phase === 'failed');
      }
    }, 1000);
  }

  showJob(state) {
    if (!state || !this.fill) return;
    const total = this.job === 'export' ? state.bytes_total : state.total;
    const done = this.job === 'export' ? state.bytes_done : state.done;
    const percent = total ? Math.min(100, Math.round(((done || 0) / total) * 100))
      : (state.running ? 0 : 100);
    this.fill.style.width = `${percent}%`;
    const count = { done: (state.done || 0).toLocaleString(), total: (state.total || 0).toLocaleString() };
    if (!state.running) this.line.textContent = state.message ? said(state.message) : '';
    else if (state.phase === 'counting') this.line.textContent = i18n.t('Counting the photos and videos…');
    else if (state.phase === 'fetching') {
      this.line.textContent = state.total
        ? i18n.t('Copying from the phone: {done} of {total} files', count)
        : i18n.t('Looking for photos and videos on the phone…');
    } else if (state.phase === 'importing') {
      this.line.textContent = i18n.t('Adding to the archive: {done} of {total} files', count);
    } else this.line.textContent = i18n.t('{done} of {total} files', count);
    this.stopButton.hidden = !state.running;
  }

  async stopJob() {
    if (!this.job) return;
    try {
      await this.json(JOBS[this.job].stop, { method: 'POST' });
    } catch (exc) { this.toast(exc.message, true); }
  }
}

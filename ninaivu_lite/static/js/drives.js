/**
 * A pendrive, an external hard drive, a memory card or a phone was plugged in:
 * a notice at the top of the console, never a box over it.
 *
 *   the notice itself     → the Import page, with the drive as its source;
 *                           a phone Windows reaches only through Explorer
 *                           is fetched, imported and tidied by the server
 *                           (phones.PhoneImport)
 *   Export                → the library copied onto it (drives.Exporter);
 *                           not offered for a phone
 *   Don't ask again       → that drive is never mentioned again (Settings
 *                           has Ask again)
 *   ×                     → mentioned again only when it is plugged in again
 *
 * A copy onto the drive, or a phone import, carries on in the same notice
 * with its progress and a Stop button.
 *
 * The console asks the server which drives are plugged in every few seconds
 * while it is open and on screen. An address carrying `?drive=<path>&do=import`
 * (from an older Control Panel) opens the Import page on arrival; anything
 * that would copy is only shown as the notice, never started from a link.
 *
 * The notice is built here rather than in admin.html, which the browser may
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
    this.notice = null;
    /** The drive on screen, and the job ('export' or 'phone') the notice follows, if any. */
    this.drive = null;
    this.job = null;
    this.jobTimer = null;
    this.lastJob = null;
    /** The drive ids already shown this visit, so × does not bring it straight back. */
    this.shown = new Set();
    i18n.onChange(() => { if (this.notice && !this.notice.hidden) this.redraw(); });
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
      this.show();
      this.showJob(data[going]);
      this.follow(going);
      return;
    }
    if (this.drive && this.notice && !this.notice.hidden) {
      // Taken out, or answered on another screen (another tab): nothing
      // left to say here.
      const still = data.drives.find((d) => d.id === this.drive.id);
      if (!still || !still.pending) this.close();
      return;
    }
    const next = data.drives.find((d) => d.pending && !this.shown.has(d.id));
    if (next) this.ask(next);
  }

  /** An older Control Panel's answer, carried in the address. */
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
    // left to the notice: a link is something anybody can send.
    if (action === 'import' && !drive.shell) this.importFrom(drive);
    else this.ask(drive);
  }

  ask(drive) {
    this.drive = drive;
    this.shown.add(drive.id);
    this.job = null;
    this.show();
  }

  /** At the top of the console, above the page heading, on every page. */
  build() {
    if (this.notice) return;
    const notice = el('section', 'drive-notice');
    notice.hidden = true;
    notice.setAttribute('role', 'status');
    notice.setAttribute('aria-live', 'polite');
    const main = document.getElementById('main') || document.body;
    main.prepend(notice);
    this.notice = notice;
  }

  show() {
    this.redraw();
    this.notice.hidden = false;
  }

  redraw() {
    this.build();
    const notice = this.notice;
    notice.replaceChildren();
    const drive = this.drive;
    if (!drive) return;
    const phone = drive.kind === 'phone';
    notice.classList.toggle('busy', Boolean(this.job));
    const heading = this.job ? JOBS[this.job].title
      : (phone ? 'A phone was connected' : 'A drive was connected');
    const name = drive.label && drive.label !== drive.path && !drive.shell
      ? `${drive.label} (${drive.path})` : drive.label || drive.path;

    // The notice itself: one large button when there is something to open.
    const head = el(this.job ? 'div' : 'button', 'drive-notice-main');
    if (!this.job) {
      head.type = 'button';
      head.onclick = () => this.importFrom(drive);
    }
    const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    icon.setAttribute('viewBox', '0 0 24 24');
    icon.setAttribute('aria-hidden', 'true');
    icon.setAttribute('class', 'drive-notice-icon');
    icon.innerHTML = phone
      ? '<rect x="7" y="2.5" width="10" height="19" rx="2.2"/><path d="M11 18.5h2"/>'
      : '<path d="M9 2.5h6v5H9z"/><rect x="6.5" y="7.5" width="11" height="14" rx="2"/><path d="M10 4.5h1M13 4.5h1"/>';
    head.appendChild(icon);
    const text = el('span', 'drive-notice-text');
    text.appendChild(el('strong', 'drive-notice-title', i18n.t(heading)));
    const facts = [name];
    if (drive.total) {
      facts.push(i18n.t('{free} free of {total}', { free: size(drive.free), total: size(drive.total) }));
    }
    text.appendChild(el('span', 'drive-notice-name', facts.join(' · ')));
    if (!this.job) {
      // A phone Windows shows only in Explorer is fetched by the server in
      // one go; anything else opens the Import page with it as the source.
      let hint = 'Import from it: opens the Import page with this drive as the source.';
      if (drive.shell) hint = 'Copy its camera photos and videos into the archive, sorted by date. Nothing on the phone is changed.';
      else if (phone) hint = 'Import from it: opens the Import page with this phone as the source.';
      text.appendChild(el('span', 'drive-notice-hint', i18n.t(hint)));
      if (phone) {
        text.appendChild(el('span', 'drive-notice-hint subtle', i18n.t(
          'Nothing showing? Unlock the phone and choose File transfer (on an iPhone, Trust this computer).')));
      }
    }
    head.appendChild(text);
    if (!this.job) {
      const go = el('span', 'drive-notice-go');
      go.setAttribute('aria-hidden', 'true');
      go.textContent = '›';
      head.appendChild(go);
    }
    notice.appendChild(head);

    if (this.job) {
      const bar = el('div', 'scan-bar');
      this.fill = el('i');
      bar.appendChild(this.fill);
      notice.appendChild(bar);
      this.line = el('p', 'hint drive-progress');
      notice.appendChild(this.line);
    }

    // Under it: the other answers.
    const actions = el('div', 'drive-notice-actions');
    const button = (label, className, action) => {
      const b = el('button', `btn small ${className}`, i18n.t(label));
      b.type = 'button';
      b.onclick = action;
      actions.appendChild(b);
      return b;
    };
    if (this.job) {
      this.stopButton = button('Stop', 'danger', () => this.stopJob());
    } else {
      if (!phone) button('Export', '', () => this.run('export', drive));
      button('Don’t ask again', 'ghost', () => this.never(drive));
    }
    const close = el('button', 'btn small ghost drive-notice-close');
    close.type = 'button';
    close.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>';
    close.onclick = () => this.notNow();
    close.setAttribute('aria-label', i18n.t('Close'));
    close.title = i18n.t('Close');
    actions.appendChild(close);
    notice.appendChild(actions);
    if (this.job && this.lastJob) this.showJob(this.lastJob);
  }

  close() {
    if (this.notice) this.notice.hidden = true;
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

  async never(drive) {
    this.close();
    try {
      await this.json('/api/admin/drives/never', { method: 'POST', body: { id: drive.id } });
    } catch (exc) {
      this.toast(exc.message, true);
      return;
    }
    this.toast(i18n.t('This drive will not be mentioned again. You can still add it as a source on the Import page.'));
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
    this.show();
    this.follow(job);
  }

  /** Until the job ends, even with the notice closed: then say how it went. */
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
        // Said in the notice when it is open; a toast when it was closed.
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
    else if (state.phase === 'counting') {
      this.line.textContent = state.found
        ? i18n.t('Counting the photos and videos… {found} so far', { found: state.found.toLocaleString() })
        : i18n.t('Counting the photos and videos…');
    }
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

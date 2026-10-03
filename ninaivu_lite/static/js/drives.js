/**
 * A pendrive or an external hard drive was plugged in: one question.
 *
 *   Import media from this drive   → the Import page, with the drive as its source
 *   Export media to this drive     → the library copied onto it (drives.Exporter)
 *   Not now                        → asked again only when it is plugged in again
 *
 * The console asks the server which drives are plugged in every few seconds
 * while it is open and on screen. The Control Panel asks the same question on
 * the computer itself and sends its answer here as `?drive=<path>&do=import`
 * (or `export`), which this module carries out on arrival.
 *
 * The dialog is built here rather than in admin.html, which the browser may
 * still have cached from before this file existed.
 */

import * as i18n from './i18n.js';
import { bytes as size, said } from './archive.js';

const POLL_MS = 4000;

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
    /** The drive on screen, and whether the dialog shows the copy's progress. */
    this.drive = null;
    this.watchingExport = false;
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
    let data;
    try {
      data = await this.json('/api/admin/drives');
    } catch { return; /* the console's own error handling covers a dead server */ }
    this.last = data;
    if (this.watchingExport) {
      this.showExport(data.export);
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
    if (action === 'import') this.importFrom(drive);
    else if (action === 'export') this.exportTo(drive);
    else this.ask(drive);
  }

  ask(drive) {
    this.drive = drive;
    this.shown.add(drive.id);
    this.watchingExport = false;
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
    const title = el('h2', '', i18n.t(this.watchingExport ? 'Copying to the drive'
      : 'A drive was connected'));
    title.id = 'drive-title';
    card.appendChild(title);
    const name = drive.label && drive.label !== drive.path ? `${drive.label} (${drive.path})` : drive.path;
    card.appendChild(el('p', 'hint drive-name', name));
    card.appendChild(el('p', 'hint', i18n.t('{free} free of {total}',
      { free: size(drive.free), total: size(drive.total) })));

    if (this.watchingExport) {
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
      this.stopButton.onclick = () => this.stopExport();
      const close = el('button', 'btn primary', i18n.t('Close'));
      close.type = 'button';
      close.onclick = () => this.close();
      foot.append(this.stopButton, close);
      card.appendChild(foot);
      if (this.last?.export) this.showExport(this.last.export);
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
    choices.append(
      choice('Import media from this drive',
        'Copy its photos and videos into the archive, sorted by date. The drive is not changed.',
        true, () => this.importFrom(drive)),
      choice('Export media to this drive',
        'Copy the library’s photos and videos onto the drive, in a “Ninaivu Lite” folder.',
        false, () => this.exportTo(drive)),
    );
    card.appendChild(choices);
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
    this.watchingExport = false;
    this.drive = null;
  }

  /* -- the answers -------------------------------------------------------- */

  async answer(drive) {
    try {
      await this.json('/api/admin/drives/answer', { method: 'POST', body: { id: drive.id } });
    } catch { /* asked again next time it is plugged in: harmless */ }
  }

  notNow() {
    if (this.watchingExport) { this.close(); return; }
    if (this.drive) this.answer(this.drive);
    this.close();
  }

  async importFrom(drive) {
    this.close();
    await this.answer(drive);
    await this.openImport(drive.path);
    this.toast(i18n.t('The drive is the source. Check the destination, then press Start.'));
  }

  async exportTo(drive) {
    try {
      await this.json('/api/admin/drives/export', { method: 'POST', body: { id: drive.id } });
    } catch (exc) {
      this.toast(exc.message, true);
      this.close();
      return;
    }
    this.drive = drive;
    this.watchingExport = true;
    this.redraw();
    this.modal.hidden = false;
    this.followExport();
  }

  /** Until the copy ends, even with the dialog closed: then say how it went. */
  followExport() {
    if (this.exportTimer) return;
    this.exportTimer = setInterval(async () => {
      let state;
      try { state = await this.json('/api/admin/drives/export'); } catch { return; }
      if (this.watchingExport) this.showExport(state);
      if (!state.running) {
        clearInterval(this.exportTimer);
        this.exportTimer = null;
        // Said in the dialog when it is open; a toast when it was closed.
        if (state.message && !this.watchingExport) this.toast(said(state.message), state.phase === 'failed');
      }
    }, 1000);
  }

  showExport(state) {
    if (!state || !this.fill) return;
    const total = state.bytes_total || 0;
    const percent = total ? Math.min(100, Math.round((state.bytes_done / total) * 100))
      : (state.running ? 0 : 100);
    this.fill.style.width = `${percent}%`;
    if (state.phase === 'counting') this.line.textContent = i18n.t('Counting the photos and videos…');
    else if (state.running) {
      this.line.textContent = i18n.t('{done} of {total} files', {
        done: (state.done || 0).toLocaleString(), total: (state.total || 0).toLocaleString(),
      });
    } else this.line.textContent = state.message ? said(state.message) : '';
    this.stopButton.hidden = !state.running;
  }

  async stopExport() {
    try {
      await this.json('/api/admin/drives/export/stop', { method: 'POST' });
    } catch (exc) { this.toast(exc.message, true); }
  }
}

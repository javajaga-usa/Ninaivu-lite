/* The first day: what a new library needs, in three steps, right after the
   administrator is made — a folder of photographs, the old drives and backup
   folders to bring into it, and the household. Opens once (first_day_done)
   and never again.

   Nothing here is new machinery. Each step calls the route the page behind it
   calls — the folder picker and People — so finishing the walk-through leaves
   the console exactly as if the person had visited those pages themselves.
   Every step can be skipped. */

import { said } from './archive.js';
import * as i18n from './i18n.js';

const $ = (sel) => document.querySelector(sel);

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

const STEPS = ['library', 'import', 'people', 'tour'];

/** The gallery's features, as a family would list them. Written as keys so
 *  the locale guard sees them; shown on the first day's last step. */
const TOUR = [
  [i18n.key('Timeline'), i18n.key('Every photograph and video by the day it was taken, with a date bar to jump years and a search by name, folder, date or camera.')],
  [i18n.key('Phones and tablets'), i18n.key('The same gallery on every screen in the house over Wi-Fi; add it to a phone\'s home screen like an app.')],
  [i18n.key('Favourites and albums'), i18n.key('Each person keeps their own favourites; albums gather photographs from any folder and can be shared.')],
  [i18n.key('Who sees what'), i18n.key('Public, Family or Hidden, per folder or per photo, with undo; a Just looking tile for visitors.')],
  [i18n.key('Share links'), i18n.key('One photo or an album, with a password and an expiry if you like; the visitor\'s copy carries no location.')],
  [i18n.key('Sudar, the photo studio'), i18n.key('Light, colour, detail, crops and looks in the browser, or say it in plain words; the original is never touched.')],
  [i18n.key('Sideways photos put right'), i18n.key('The camera\'s tag, then the faces in a photo, decide which way is up during the scan; a turn by hand in the viewer corrects any.')],
  [i18n.key('Import old drives'), i18n.key('Sweep cards, phone backups and old disks into one archive filed by date, every copy checked, duplicates left in place.')],
  [i18n.key('Tamil and English'), i18n.key('Every screen in either language, chosen by each person.')],
  [i18n.key('Backups and the Control Panel'), i18n.key('A backup of settings, people and albums whenever you ask; a small window starts, stops and watches Ninaivu Lite.')],
];

export class FirstDay {
  constructor({ json, toast, pickFolder, openPage, refresh }) {
    this.json = json;
    this.toast = toast;
    this.pickFolder = pickFolder;
    this.openPage = openPage;
    this.refresh = refresh;
    this.state = null;
    this.step = 0;
    this.added = [];                       // people added during this walk-through
    this.sources = null;                   // folders to sweep, chosen on the import step
    this.destination = '';
    this.wired = false;
    i18n.onChange(() => {
      const box = $('#first-day');
      if (this.state && box && !box.hidden) this.render();
    });
  }

  /* Open if it has not been finished. Called once the console has signed in. */
  async maybeOpen() {
    try {
      this.state = await this.json('/api/admin/first-day');
    } catch { return; }
    if (this.state.done) return;
    // An established library — a folder and a household already — has had
    // its first day. Say so and stay out of the way.
    if (this.state.library.chosen && this.state.people > 0) {
      try { await this.json('/api/admin/first-day', { method: 'POST', body: {} }); } catch { /* next time */ }
      return;
    }
    this.wire();
    this.step = this.state.library.chosen ? 1 : 0;
    $('#first-day').hidden = false;
    this.render();
  }

  wire() {
    if (this.wired) return;
    this.wired = true;
    $('#fd-next').onclick = () => this.next();
    $('#fd-back').onclick = () => { if (this.step > 0) { this.step -= 1; this.render(); } };
    $('#fd-skip-all').onclick = () => this.finish(true);
  }

  async reload() {
    try { this.state = await this.json('/api/admin/first-day'); } catch { /* keep what we have */ }
  }

  async next() {
    // Somebody typed in but not yet added is added now, not thrown away; if
    // that is refused (a PIN too easy to guess, say), stay here and say why.
    if (STEPS[this.step] === 'people' && this.pending?.filled() && !(await this.pending.add())) return;
    // Folders added as sources but never started: Next used to drop them
    // without a word (the Start button can be below the fold in Tamil).
    if (STEPS[this.step] === 'import' && this.sources.length && this.destination
        && !this.state.import?.running && !this.importStarted) {
      if (confirm(i18n.t('Start the import of the folders you added now? Cancel goes on without importing; Import, under Library, can do it later.'))) {
        const start = $('#fd-start-import');
        if (start) await this.startImport(start);
      }
    }
    if (this.step >= STEPS.length - 1) { await this.finish(false); return; }
    this.step += 1;
    await this.reload();
    this.render();
  }

  async finish(skipped) {
    try { await this.json('/api/admin/first-day', { method: 'POST', body: {} }); } catch { /* it will ask again */ }
    $('#first-day').hidden = true;
    this.toast(skipped ? i18n.t('You can do all of this from the pages on the left, any time.')
      : i18n.t('That is the first day done. The Overview shows how your library looks.'));
    this.refresh?.();
  }

  render() {
    const name = STEPS[this.step];
    document.querySelectorAll('#fd-steps li').forEach((li, i) => {
      li.classList.toggle('current', i === this.step);
      li.classList.toggle('done', i < this.step);
    });
    $('#fd-back').hidden = this.step === 0;
    $('#fd-next').textContent = this.step === STEPS.length - 1 ? i18n.t('Open the console') : i18n.t('Next');
    $('#fd-note').textContent = '';
    const body = $('#fd-body');
    body.replaceChildren();
    this[`render_${name}`](body);
  }

  /* -- 4. what it can do: the whole of it on one page, before the console -- */

  render_tour(body) {
    $('#fd-title').textContent = i18n.t('What your family can do');
    body.append(el('p', 'lede',
      i18n.t('Everything here works on the computer at home. Nothing leaves the house, and no file is ever changed.')));
    const grid = el('div', 'fd-tour');
    for (const [title, line] of TOUR) {
      const card = el('div', 'fd-tour-card');
      card.append(el('strong', null, i18n.t(title)), el('span', null, i18n.t(line)));
      grid.append(card);
    }
    body.append(grid);
  }

  /* -- 1. the library folder -------------------------------------------- */

  render_library(body) {
    $('#fd-title').textContent = i18n.t('Where are the photographs?');
    body.append(el('p', 'lede',
      i18n.t('Choose the folder that holds them. Ninaivu only ever reads it; nothing in it is moved, renamed or changed. You can add more folders later on Library settings.')));
    const chosen = el('div', 'fd-chosen', this.state.library.chosen
      ? this.state.library.root : i18n.t('No folder chosen yet'));
    body.append(chosen);
    const row = el('div', 'row');
    const pick = el('button', 'btn primary', this.state.library.chosen
      ? i18n.t('Choose a different folder') : i18n.t('Choose a folder'));
    pick.type = 'button';
    pick.onclick = () => this.pickFolder({
      title: i18n.t('Choose the folder that holds the photographs'),
      cta: i18n.t('Use this folder'),
      pick: async (path) => {
        try {
          await this.json('/api/library/root', { method: 'POST', body: { path } });
          this.toast(i18n.t('Indexing the library…'));
          await this.reload();
          this.render();
        } catch (exc) {
          this.toast(exc.message, true);
        }
      },
    });
    row.append(pick);
    body.append(row);
    if (!this.state.library.chosen) {
      $('#fd-note').textContent = i18n.t('Skipping leaves the library empty until a folder is chosen.');
    }
  }

  /* -- 2. the old drives and backup folders ----------------------------- */

  render_import(body) {
    $('#fd-title').textContent = i18n.t('Are there old photos to bring in?');
    body.append(el('p', 'lede',
      i18n.t('Ninaivu can sweep the photos and videos off old drives, memory cards, phone backups and backup folders into one archive, filed by the day each was taken, and show them in the library. Add every folder or drive that holds them: the top folder is enough, because every folder inside it is scanned, however deep. Each file is copied and checked; the originals are never changed, moved or deleted.')));
    if (this.sources === null) this.sources = [...(this.state.import?.sources || [])];
    if (!this.destination) this.destination = this.state.import?.destination || '';

    body.append(el('p', 'fd-label', i18n.t('Folders and drives to scan for photos and videos')));
    const list = el('ul', 'fd-people');
    for (const [index, source] of this.sources.entries()) {
      const li = el('li');
      li.append(el('code', 'li-path', source));
      const remove = el('button', 'btn ghost small', i18n.t('Remove'));
      remove.type = 'button';
      remove.onclick = () => { this.sources.splice(index, 1); this.render(); };
      li.append(remove);
      list.append(li);
    }
    if (!this.sources.length) list.append(el('li', 'hint', i18n.t('No folders added yet. A drive letter or a whole backup folder is fine.')));
    body.append(list);

    const row = el('form', 'row');
    const typed = el('input', 'input');
    typed.placeholder = i18n.t('e.g. D:\\OldPhotos or /Volumes/Backup');
    typed.setAttribute('aria-label', i18n.t('Folder to scan'));
    const addTyped = el('button', 'btn', i18n.t('Add folder'));
    addTyped.type = 'submit';
    const browse = el('button', 'btn primary', i18n.t('Browse…'));
    browse.type = 'button';
    browse.onclick = () => this.pickFolder({
      title: i18n.t('Choose a folder to sweep'),
      cta: i18n.t('Add as a source'),
      anyFolder: true,
      start: this.sources[this.sources.length - 1] || '',
      pick: (path) => this.addSource(path),
    });
    row.onsubmit = (event) => { event.preventDefault(); this.addSource(typed.value); };
    row.append(typed, addTyped, browse);
    body.append(row);

    body.append(el('p', 'fd-label', i18n.t('Where the archive is built')));
    const where = el('div', 'row');
    const chosen = el('div', 'fd-chosen', this.destination || i18n.t('No folder chosen yet'));
    chosen.style.flex = '1';
    const change = el('button', 'btn ghost small', i18n.t('Change'));
    change.type = 'button';
    change.onclick = () => this.pickFolder({
      title: i18n.t('Choose where the archive is built'),
      cta: i18n.t('Use this folder'),
      anyFolder: true,
      start: this.destination,
      pick: (path) => { this.destination = path; this.render(); },
    });
    where.append(chosen, change);
    body.append(where);
    const inside = (this.state.library.roots || [this.state.library.root]).some((root) => root
      && this.destination && (this.destination === root
        || this.destination.startsWith(`${root.replace(/[\\/]+$/, '')}/`)
        || this.destination.startsWith(`${root.replace(/[\\/]+$/, '')}\\`)));
    body.append(el('p', 'hint', inside
      ? i18n.t('Inside your library folder, so the family sees the archive as soon as it is indexed. Nothing already in the library is touched.')
      : (this.state.library.chosen
        ? i18n.t('Outside your library folder: it is added to the library as a folder of its own when the import starts.')
        : i18n.t('Choose a folder on a drive with room for everything. It is added to the library when the import starts.'))));

    const run = el('div', 'row');
    const start = el('button', 'btn primary', i18n.t('Start the import'));
    start.id = 'fd-start-import';
    start.type = 'button';
    start.disabled = !this.sources.length || !this.destination || !!this.state.import?.running;
    start.onclick = () => this.startImport(start);
    run.append(start);
    body.append(run);
    $('#fd-note').textContent = this.state.import?.running
      ? i18n.t('The import is running. Watch it on Import, under Library.')
      : i18n.t('Skipping is fine: Import, under Library, does the same at any time.');
  }

  addSource(raw) {
    const path = (raw || '').trim();
    if (!path) { this.toast(i18n.t('Type a folder, or press Browse.'), true); return; }
    if (this.sources.includes(path)) { this.toast(i18n.t('That folder is already a source.'), true); return; }
    this.sources.push(path);
    this.render();
  }

  async startImport(button) {
    button.disabled = true;
    const label = button.textContent;
    button.textContent = i18n.t('Starting…');
    try {
      const job = {
        source_dirs: this.sources.map((path) => ({ path })),
        destination_dir: this.destination,
        media_types: ['image', 'video'],
        mode: 'copy',
      };
      const started = await this.json('/api/archive/start', { method: 'POST', body: job });
      if (started.destination) this.destination = started.destination;
      // The archive joins the library now, so what the import brings in is
      // indexed as it lands rather than waiting for somebody to add it.
      this.importStarted = true;
      try {
        await this.json('/api/archive/adopt', { method: 'POST', body: { path: this.destination } });
        this.toast(i18n.t('Import started. Watch it on Import, under Library.'));
      } catch (exc) {
        // Said here: the family would otherwise wait for photos that never show.
        this.toast(i18n.t('The import started, but its folder could not be added to the library: {why} Add it on Library settings.', { why: exc.message }), true);
      }
      await this.reload();
      this.render();
    } catch (exc) {
      const problems = exc.data?.problems || [exc.message];
      this.toast(problems.map((p) => said(p)).join(' '), true);
      button.disabled = false;
      button.textContent = label;
    }
  }

  /* -- 3. the household ------------------------------------------------- */

  render_people(body) {
    $('#fd-title').textContent = i18n.t('Who is in the household?');
    body.append(el('p', 'lede',
      i18n.t('A family member taps their name and enters a PIN. A guest signs in with a password and sees only what you make public. Add the people you know of now; the rest can come later on People.')));
    const list = el('ul', 'fd-people');
    for (const person of this.added) {
      const li = el('li');
      li.append(el('strong', null, person.name), el('span', 'hint', person.role === 'guest'
        ? i18n.t('guest · password')
        : (person.pin ? i18n.t('family · PIN {pin}', { pin: person.pin }) : i18n.t('family · no PIN'))));
      list.append(li);
    }
    if (!this.added.length && this.state.people) {
      list.append(el('li', 'hint', i18n.t('{count} already added.', { count: this.state.people })));
    }
    body.append(list);
    const form = el('form', 'row');
    const name = el('input', 'input'); name.placeholder = i18n.t('Name'); name.required = true; name.maxLength = 60;
    const role = el('select', 'input');
    for (const [v, t] of [['family', i18n.t('Family member')], ['guest', i18n.t('Guest')]]) {
      const o = el('option', null, t); o.value = v; role.append(o);
    }
    const secret = el('input', 'input'); secret.placeholder = i18n.t('PIN (4–8 digits)'); secret.inputMode = 'numeric';
    role.onchange = () => {
      secret.placeholder = role.value === 'guest' ? i18n.t('Password') : i18n.t('PIN (4–8 digits)');
      secret.type = role.value === 'guest' ? 'password' : 'text';
    };
    const add = el('button', 'btn', i18n.t('Add')); add.type = 'submit';
    form.append(name, role, secret, add);
    // Said right under the form: a note at the foot of the screen is easy to
    // miss, and the person would look added when they were not.
    const error = el('p', 'gate-error');
    error.hidden = true;
    const addPerson = async () => {
      error.hidden = true;
      const username = name.value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '') || `person${Date.now() % 10000}`;
      const body = { name: name.value.trim(), username, role: role.value };
      if (role.value === 'guest') body.password = secret.value; else body.pin = secret.value;
      add.disabled = true;
      try {
        await this.json('/api/people', { method: 'POST', body });
        this.added.push({ name: body.name, role: role.value, pin: body.pin });
        name.value = ''; secret.value = '';
        this.render();
        return true;
      } catch (exc) {
        error.textContent = i18n.t('{name} was not added: {reason}', { name: body.name, reason: exc.message });
        error.hidden = false;
        (secret.value ? secret : name).focus();
        return false;
      } finally {
        add.disabled = false;
      }
    };
    form.onsubmit = (event) => { event.preventDefault(); addPerson(); };
    this.pending = { filled: () => !!name.value.trim(), add: addPerson };
    body.append(form, error);
    body.append(el('p', 'hint',
      i18n.t('A family member without a PIN can be opened from any phone or computer on your home network. Give a PIN to anyone whose photographs should stay theirs.')));
  }
}

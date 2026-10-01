/* The first day: what a new library needs, in two steps, right after the
   administrator is made — a folder of photographs, and the household.
   Opens once (first_day_done) and never again.

   Nothing here is new machinery. Each step calls the route the page behind it
   calls — the folder picker and People — so finishing the walk-through leaves
   the console exactly as if the person had visited those pages themselves.
   Every step can be skipped. */

import * as i18n from './i18n.js';

const $ = (sel) => document.querySelector(sel);

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

const STEPS = ['library', 'people'];

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

  /* -- 2. the household ------------------------------------------------- */

  render_people(body) {
    $('#fd-title').textContent = i18n.t('Who is in the household?');
    body.append(el('p', 'lede',
      i18n.t('A family member taps their name and enters a PIN. A guest signs in with a password and sees only what you make public. Add the people you know of now; the rest can come later on People.')));
    const list = el('ul', 'fd-people');
    for (const person of this.added) {
      const li = el('li');
      li.append(el('strong', null, person.name), el('span', 'hint', person.role === 'guest'
        ? i18n.t('guest · password') : i18n.t('family · PIN {pin}', { pin: person.pin })));
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
    form.onsubmit = async (event) => {
      event.preventDefault();
      const username = name.value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '') || `person${Date.now() % 10000}`;
      const body = { name: name.value.trim(), username, role: role.value };
      if (role.value === 'guest') body.password = secret.value; else body.pin = secret.value;
      add.disabled = true;
      try {
        await this.json('/api/people', { method: 'POST', body });
        this.added.push({ name: body.name, role: role.value, pin: body.pin });
        name.value = ''; secret.value = '';
        this.render();
      } catch (exc) {
        this.toast(exc.message, true);
      } finally {
        add.disabled = false;
      }
    };
    body.append(form);
    body.append(el('p', 'hint',
      i18n.t('A family member without a PIN can be opened from any phone or computer on your home network. Give a PIN to anyone whose photographs should stay theirs.')));
  }
}

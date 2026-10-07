/**
 * Sign-in, profiles and the profile sheet.
 *
 * The server is the only authority on permissions — everything here is
 * presentation. Hiding a button the API would refuse anyway keeps the
 * interface honest about what each role can actually do.
 */

import { reportUnauthorized } from './api.js';
import * as i18n from './i18n.js';

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
};

async function json(url, options = {}) {
  const isForm = options.body instanceof FormData;
  // A FormData body must keep the browser's own multipart Content-Type,
  // boundary and all — setting application/json here silently drops the file.
  const headers = { Accept: 'application/json' };
  if (options.body && !isForm && typeof options.body !== 'string') {
    headers['Content-Type'] = 'application/json';
  }
  const response = await fetch(url, {
    ...options,
    headers: { ...headers, ...(options.headers || {}) },
    body: isForm || typeof options.body === 'string' || options.body === undefined
      ? options.body
      : JSON.stringify(options.body),
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    // A 401 carrying `needs_password` is a request asking you to confirm
    // who you are before something irreversible — not a session that has
    // ended. See api.js: the same 401 means two different things, and
    // only one of them should raise the sign-in screen.
    if (response.status === 401 && !data?.needs_password) reportUnauthorized(url);
    throw Object.assign(new Error(i18n.t(data?.error || response.statusText)), { status: response.status, data });
  }
  return data;
}

export const accountsApi = {
  state: () => json('/api/auth/state'),
  setup: (body) => json('/api/auth/setup', { method: 'POST', body }),
  login: (body) => json('/api/auth/login', { method: 'POST', body }),
  logout: () => json('/api/auth/logout', { method: 'POST' }),
  me: () => json('/api/me'),
  updateMe: (body) => json('/api/me', { method: 'POST', body }),
  setAvatar: (assetId) => json('/api/me/avatar', { method: 'POST', body: { asset_id: assetId } }),
  removeAvatar: () => json('/api/me/avatar', { method: 'DELETE' }),
  removePersonAvatar: (id) => json(`/api/people/${id}/avatar`, { method: 'DELETE' }),
  changePassword: (body) => json('/api/me/password', { method: 'POST', body }),
  profiles: () => json('/api/auth/profiles'),
  enter: (id, secret) => json('/api/auth/enter', { method: 'POST', body: { id, secret } }),
  people: () => json('/api/people'),
  createPerson: (body) => json('/api/people', { method: 'POST', body }),
  updatePerson: (id, body) => json(`/api/people/${id}`, { method: 'POST', body }),
  signOutPerson: (id) => json(`/api/people/${id}/signout`, { method: 'POST' }),
  deletePerson: (id) => json(`/api/people/${id}`, { method: 'DELETE' }),
  setVisibility: (ids, visibility) =>
    json('/api/visibility', { method: 'POST', body: { ids, visibility } }),
  shares: () => json('/api/shares'),
  endShare: (token) => json(`/api/shares/${encodeURIComponent(token)}`, { method: 'DELETE' }),
};

/**
 * Put a piece of text on the clipboard.
 *
 * `navigator.clipboard` only exists in a secure context, and a home server is
 * often opened over plain http on its LAN address — so when it is missing (or
 * refuses), the text is put in a read-only field, selected, and copied the old
 * way. Returns true when the copy happened; on false the field stays selected
 * so the person can copy it themselves.
 */
export async function copyText(text, field = null) {
  try {
    if (navigator.clipboard?.writeText && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* fall through to the old way */ }
  let input = field;
  const temporary = !input;
  if (temporary) {
    input = el('input');
    input.readOnly = true;
    input.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;opacity:0';
    document.body.appendChild(input);
  }
  input.value = text;
  input.focus();
  input.select();
  input.setSelectionRange?.(0, text.length);
  let copied = false;
  try { copied = document.execCommand('copy'); } catch { copied = false; }
  if (temporary) input.remove();
  return copied;
}

/** The address a share link is opened at. */
export function shareUrl(token) {
  return `${window.location.origin}/share/${token}`;
}

/* ======================================================================== */

/* The colours behind people's initials — the same eight as AVATAR_COLORS in
   ninaivu/server/auth.py, each dark enough for white initials to read. The
   server already sends each person's colour; this is only the fallback for a
   payload without one, worked out the same way (from the id) so a face never
   changes colour between the picker, the lock screen and the top bar. */
export const AVATAR_COLOURS = [
  '#1f6fb2', '#6247d6', '#b5306f', '#b4531a',
  '#1d7a47', '#0d7477', '#c02e36', '#7a5c1e',
];

export function avatarColour(person) {
  if (person && person.color) return person.color;
  const id = Number(person && person.id);
  if (Number.isFinite(id)) return AVATAR_COLOURS[Math.abs(id) % AVATAR_COLOURS.length];
  let hash = 0;
  for (const ch of String((person && person.name) || '')) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
  return AVATAR_COLOURS[hash % AVATAR_COLOURS.length];
}

export function avatarNode(person, size = 32) {
  const wrap = el('span', 'avatar');
  wrap.style.width = `${size}px`;
  wrap.style.height = `${size}px`;
  wrap.style.fontSize = `${Math.round(size * 0.38)}px`;
  if (person.avatar) {
    const img = el('img');
    img.alt = '';
    // A picture that cannot be fetched shows the initials, not a broken image.
    img.onerror = () => {
      img.remove();
      wrap.style.background = avatarColour(person);
      wrap.appendChild(el('span', null, person.initials || '?'));
    };
    img.src = person.avatar;
    wrap.appendChild(img);
  } else {
    wrap.style.background = avatarColour(person);
    wrap.appendChild(el('span', null, person.initials || '?'));
  }
  if (person.role) wrap.dataset.role = person.role;
  return wrap;
}

export function roleBadge(role, label) {
  const badge = el('span', `role-badge role-${role}`, label || role);
  return badge;
}

/* ========================================================================
   Sign-in / first-run screen
   ======================================================================== */

export class Gate {
  constructor(root, { onSignedIn, toast }) {
    this.root = root;
    this.onSignedIn = onSignedIn;
    this.toast = toast;
    this.mode = 'picker';
    this.selected = null;
  }

  show(state, mode) {
    this.state = state;
    this.mode = mode
      || (state.setup_required ? 'setup'
        : state.face === 'admin' ? 'login' : 'picker');
    this.selected = null;
    this.root.hidden = false;
    this.render();
  }

  hide() {
    this.root.hidden = true;
  }

  render() {
    this.root.innerHTML = '';
    const card = el('div', this.mode === 'picker' ? 'gate-card picker' : 'gate-card');
    card.appendChild(this.brand());

    if (this.mode === 'setup') this.renderSetup(card);
    else if (this.mode === 'login') this.renderLogin(card);
    else if (this.mode === 'unlock') this.renderUnlock(card);
    else this.renderPicker(card);

    card.appendChild(this.languages());
    this.root.appendChild(card);
    setTimeout(() => card.querySelector('input')?.focus(), 60);
  }

  /**
   * A language switch on the sign-in card.
   *
   * It belongs here and not only in the topbar: `.gate` is `position: fixed;
   * inset: 0`, so the topbar's own language button is underneath it. Somebody
   * who reads only Tamil met an English sign-in screen with the control that
   * would have changed it covered up.
   *
   * Only shown when there is a choice to make.
   */
  languages() {
    const row = el('div', 'gate-languages');
    if (i18n.LANGUAGES.length < 2) return row;
    for (const { code, name } of i18n.LANGUAGES) {
      const pick = el('button', 'gate-lang', name);
      pick.type = 'button';
      pick.lang = code;
      pick.setAttribute('aria-pressed', String(code === i18n.language()));
      if (code === i18n.language()) pick.classList.add('on');
      pick.onclick = async () => {
        await i18n.use(code);
        this.render();                 // built from scratch, so this is enough
      };
      row.appendChild(pick);
    }
    return row;
  }

  brand() {
    const brand = el('div', 'gate-brand');
    brand.innerHTML = `
      <svg viewBox="0 0 32 32" aria-hidden="true" class="gate-mark">
        <path d="M4 15 16 5l12 10v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z"/>
        <path d="M12 28v-7a4 4 0 0 1 8 0v7"/>
      </svg>`;
    // The household's own name on the family app, as the page shows once in;
    // the console keeps Ninaivu's.
    // "Ninaivu" from the server means nobody has named the home: that is the
    // product's own name, so it is written in the page's language.
    const given = this.state?.house_name;
    const named = Boolean(given) && given !== 'Ninaivu';
    const name = this.state?.face === 'admin' || !named ? i18n.t('Ninaivu') : given;
    brand.appendChild(el('h1', null, name));
    if (this.state?.face === 'admin') {
      brand.appendChild(el('span', 'gate-face', i18n.t('Admin console')));
    }
    return brand;
  }

  /* -- who's watching? -------------------------------------------------- */

  renderPicker(card) {
    const profiles = this.state.profiles || [];
    card.appendChild(el('p', 'gate-lede', profiles.length
      ? i18n.t("Who's watching?")
      : i18n.t('No profiles yet — an admin can add them in the console.')));

    const grid = el('div', 'picker-grid');
    for (const person of profiles) {
      const tile = el('button', 'picker-tile');
      tile.type = 'button';
      tile.appendChild(avatarNode(person, 88));
      const name = el('span', 'picker-name', person.name);
      if (person.locked) {
        const lock = el('span', 'picker-lock');
        lock.innerHTML = '<svg viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>';
        name.appendChild(lock);
      }
      tile.appendChild(name);
      tile.appendChild(el('span', 'picker-role', i18n.role(person.role_label)));
      tile.onclick = () => this.choose(person);
      grid.appendChild(tile);
    }

    if (this.state.open_browsing) {
      const tile = el('button', 'picker-tile guest-tile');
      tile.type = 'button';
      const face = el('span', 'avatar');
      face.style.cssText = 'width:88px;height:88px;background:var(--surface-2);color:var(--text-3)';
      face.innerHTML = '<svg viewBox="0 0 24 24" style="width:34px;height:34px"><circle cx="12" cy="8" r="3.6"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/></svg>';
      tile.appendChild(face);
      tile.appendChild(el('span', 'picker-name', i18n.t('Just looking')));
      tile.appendChild(el('span', 'picker-role', i18n.t('Public media only')));
      tile.onclick = () => {
        this.hide();
        this.onSignedIn(null);
      };
      grid.appendChild(tile);
    }
    card.appendChild(grid);

    const foot = el('div', 'picker-foot');
    const byName = el('button', 'gate-skip',
      i18n.t('Sign in with a username instead'));
    byName.type = 'button';
    byName.onclick = () => { this.mode = 'login'; this.render(); };
    foot.appendChild(byName);
    card.appendChild(foot);
  }

  async choose(person) {
    if (!person.locked) {
      try {
        const result = await accountsApi.enter(person.id, '');
        this.hide();
        this.onSignedIn(result.user);
      } catch (exc) {
        this.toast(exc.message, true);
      }
      return;
    }
    this.selected = person;
    this.mode = 'unlock';
    this.render();
  }

  /* -- PIN / password for a locked profile ------------------------------ */

  renderUnlock(card) {
    const person = this.selected;
    const isPin = person.kind === 'pin';

    const head = el('div', 'unlock-head');
    head.appendChild(avatarNode(person, 64));
    const who = el('div');
    who.appendChild(el('strong', null, person.name));
    who.appendChild(el('div', 'hint',
      isPin ? i18n.t('Enter your PIN') : i18n.t('Enter your password')));
    head.appendChild(who);
    card.appendChild(head);

    const form = el('form', 'gate-form');
    const input = el('input', 'pin-input');
    input.type = 'password';
    input.name = 'secret';
    input.autocomplete = isPin ? 'one-time-code' : 'current-password';
    if (isPin) {
      input.inputMode = 'numeric';
      input.pattern = '[0-9]*';
      input.maxLength = 8;
      input.placeholder = '••••';
    }
    form.appendChild(input);

    const error = el('p', 'gate-error');
    error.hidden = true;
    form.appendChild(error);

    const submit = el('button', 'btn primary gate-submit', i18n.t('Enter'));
    submit.type = 'submit';
    form.appendChild(submit);

    form.onsubmit = async (event) => {
      event.preventDefault();
      error.hidden = true;
      submit.disabled = true;
      try {
        const result = await accountsApi.enter(person.id, input.value);
        this.hide();
        this.onSignedIn(result.user);
      } catch (exc) {
        error.textContent = exc.message;
        error.hidden = false;
        submit.disabled = false;
        input.value = '';
        input.focus();
      }
    };
    card.appendChild(form);

    const back = el('button', 'gate-skip', i18n.t('← Someone else'));
    back.type = 'button';
    back.onclick = () => { this.mode = 'picker'; this.render(); };
    card.appendChild(back);
  }

  /* -- username + password ---------------------------------------------- */

  renderSetup(card) {
    card.appendChild(el('p', 'gate-lede', i18n.t(
      'Welcome. Create the administrator profile — the person who decides what everyone else can see.')));
    this.credentialForm(card, {
      withName: true,
      withCode: !!this.state.setup_code_required,
      submit: i18n.t('Create profile'), busy: i18n.t('Creating…'),
      // The language the card was read in goes on the new profile, or the
      // console would open in the home's default and undo the choice.
      action: (body) => accountsApi.setup({ ...body, language: i18n.language() }),
    });
  }

  renderLogin(card) {
    card.appendChild(el('p', 'gate-lede', this.state.face === 'admin'
      ? i18n.t('Sign in with your administrator account.')
      : i18n.t("Your family's media, at home.")));
    this.credentialForm(card, {
      submit: i18n.t('Sign in'), busy: i18n.t('Signing in…'),
      action: (body) => accountsApi.login(body),
    });

    if (this.state.face !== 'admin' && (this.state.profiles || []).length) {
      const back = el('button', 'gate-skip', i18n.t('← Back to profiles'));
      back.type = 'button';
      back.onclick = () => { this.mode = 'picker'; this.render(); };
      card.appendChild(back);
    } else if (this.state.face !== 'admin' && this.state.open_browsing) {
      const skip = el('button', 'gate-skip', i18n.t('Continue as a guest'));
      skip.type = 'button';
      skip.onclick = () => { this.hide(); this.onSignedIn(null); };
      card.appendChild(skip);
    }
  }

  credentialForm(card, { withName = false, withCode = false, submit: label, busy, action }) {
    const form = el('form', 'gate-form');
    form.autocomplete = 'on';
    if (withCode) {
      form.appendChild(el('p', 'gate-lede', i18n.t(
        'You are setting up from another device. Enter the setup code shown where Ninaivu started — in its window or its log.')));
      form.appendChild(this.field('setup_code', i18n.t('Setup code'), 'text', {
        autocomplete: 'off', required: true, autocapitalize: 'characters', placeholder: 'A1B2C3',
      }));
    }
    if (withName) {
      form.appendChild(this.field('name', i18n.t('Your name'), 'text', {
        autocomplete: 'name', placeholder: i18n.t('e.g. Alex'),
      }));
    }
    form.appendChild(this.field('username', i18n.t('Username'), 'text', {
      autocomplete: 'username', required: true, autocapitalize: 'none',
      placeholder: withName ? i18n.t('lowercase, no spaces') : '',
    }));
    form.appendChild(this.field('password', i18n.t('Password'), 'password', {
      autocomplete: withName ? 'new-password' : 'current-password', required: true,
      placeholder: withName ? i18n.t('at least 8 characters') : '',
    }));

    const error = el('p', 'gate-error');
    error.hidden = true;
    form.appendChild(error);

    const button = el('button', 'btn primary gate-submit', label);
    button.type = 'submit';
    form.appendChild(button);

    form.onsubmit = async (event) => {
      event.preventDefault();
      error.hidden = true;
      button.disabled = true;
      button.textContent = busy;
      try {
        const result = await action(Object.fromEntries(new FormData(form).entries()));
        this.hide();
        this.onSignedIn(result.user);
      } catch (exc) {
        error.textContent = exc.message;
        error.hidden = false;
        button.disabled = false;
        button.textContent = label;
      }
    };
    card.appendChild(form);
  }

  field(name, label, type, attrs = {}) {
    const wrap = el('label', 'gate-field');
    wrap.appendChild(el('span', null, label));
    const input = el('input');
    input.name = name;
    input.type = type;
    Object.entries(attrs).forEach(([key, value]) => {
      if (value === true) input.setAttribute(key, '');
      else if (value) input.setAttribute(key, value);
    });
    wrap.appendChild(input);
    return wrap;
  }
}

/* ========================================================================
   Profile sheet — name, colour, password, share links
   ======================================================================== */

export class ProfileSheet {
  constructor(root, { toast, onChange, houseName, face, albumName }) {
    this.root = root;
    this.toast = toast;
    this.onChange = onChange;
    //: shown as the placeholder, so "empty" visibly means "the household name"
    this.houseName = houseName || '';
    //: 'home' or 'admin' — decides whether leaving is called signing out
    this.face = face || 'home';
    //: (id) -> the album's name, when the page knows it; for the share list
    this.albumName = albumName || (() => '');
  }

  open(user) {
    this.user = user;
    this.root.hidden = false;
    this.render();
  }

  close() {
    this.root.hidden = true;
  }

  render() {
    const user = this.user;
    this.root.innerHTML = '';
    const card = el('div', 'sheet-card');

    const head = el('div', 'sheet-head');
    head.appendChild(el('h2', null, i18n.t('Your profile')));
    const close = el('button', 'icon-btn');
    close.type = 'button';
    close.setAttribute('aria-label', i18n.t('Close'));
    close.innerHTML = '<svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>';
    close.onclick = () => this.close();
    head.appendChild(close);
    card.appendChild(head);

    // --- picture -------------------------------------------------------
    // A photograph the person chose from the library, or their initials on
    // their colour. Choosing happens in the viewer, where the photographs
    // are; here is the way there, and the way back to initials.
    const pictureRow = el('div', 'profile-picture');
    pictureRow.appendChild(avatarNode(user, 88));
    const pictureActions = el('div', 'profile-picture-actions');
    if (user.avatar) {
      const removePicture = el('button', 'btn small ghost', i18n.t('Remove picture'));
      removePicture.type = 'button';
      removePicture.onclick = async () => {
        try {
          const updated = await accountsApi.removeAvatar();
          this.user = updated;
          this.onChange(updated);
          this.render();
          this.toast(i18n.t('Picture removed.'));
        } catch (exc) { this.toast(exc.message, true); }
      };
      pictureActions.appendChild(removePicture);
    }
    pictureActions.appendChild(el('p', 'hint',
      i18n.t('To use a photograph: open it in the gallery and choose “Use as my profile picture” from its ⋯ menu.')));
    pictureRow.appendChild(pictureActions);
    card.appendChild(pictureRow);

    // --- identity ------------------------------------------------------
    const identity = el('div', 'sheet-section');
    identity.appendChild(el('h3', null, i18n.t('Display name')));
    const nameRow = el('div', 'row');
    const nameInput = el('input', 'input');
    nameInput.value = user.name;
    nameInput.maxLength = 60;
    const saveName = el('button', 'btn', i18n.t('Save'));
    saveName.type = 'button';
    saveName.onclick = async () => {
      try {
        const updated = await accountsApi.updateMe({ name: nameInput.value });
        this.user = updated;
        this.onChange(updated);
        this.render();
        this.toast(i18n.t('Profile updated.'));
      } catch (exc) { this.toast(exc.message, true); }
    };
    nameRow.append(nameInput, saveName);
    identity.appendChild(nameRow);

    // --- what they call the home ---------------------------------------
    //
    // Only they ever see this, which is exactly what makes it safe to let a
    // child set it to something daft. Guests are left out: a guest tile is
    // usually shared and short-lived.
    if (user.role !== 'guest') {
      identity.appendChild(el('h3', null, i18n.t('Name for this home')));
      identity.appendChild(el('p', 'hint', i18n.t(
        'Shown at the top of your gallery, and on your phone’s tab. Only you see it — leave it empty to use the household name.')));
      const homeRow = el('div', 'row');
      const homeInput = el('input', 'input');
      homeInput.value = user.home_label || '';
      homeInput.maxLength = 40;
      homeInput.placeholder = this.houseName || i18n.t('Ninaivu');
      homeInput.setAttribute('aria-label', i18n.t('Name for this home'));
      const saveHome = el('button', 'btn', i18n.t('Save'));
      saveHome.type = 'button';
      const commitHome = async () => {
        saveHome.disabled = true;
        try {
          const updated = await accountsApi.updateMe({ home_label: homeInput.value });
          this.user = updated;
          this.onChange(updated);
          this.render();
          this.toast(updated.home_label
            ? i18n.t('This home is now “{name}” for you.',
              { name: updated.home_label })
            : i18n.t('Back to the household name.'));
        } catch (exc) {
          this.toast(exc.message, true);
          saveHome.disabled = false;
        }
      };
      saveHome.onclick = commitHome;
      homeInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') commitHome(); });
      homeRow.append(homeInput, saveHome);
      identity.appendChild(homeRow);
    }

    identity.appendChild(el('h3', null, i18n.t('Colour')));
    const swatches = el('div', 'swatches');
    for (const color of AVATAR_COLOURS) {
      const swatch = el('button', 'swatch');
      swatch.type = 'button';
      swatch.style.background = color;
      swatch.setAttribute('aria-label', color);
      if (color === user.color) swatch.classList.add('active');
      swatch.onclick = async () => {
        const updated = await accountsApi.updateMe({ color });
        this.user = updated;
        this.onChange(updated);
        this.render();
      };
      swatches.appendChild(swatch);
    }
    identity.appendChild(swatches);
    card.appendChild(identity);

    // --- password ------------------------------------------------------
    const security = el('div', 'sheet-section');
    security.appendChild(el('h3', null, user.must_change
      ? i18n.t('Set your password') : i18n.t('Change password')));
    if (user.must_change) {
      security.appendChild(el('p', 'hint warn', i18n.t(
        'An admin set a temporary password for you. Choose your own now.')));
    }
    const passwordForm = el('form', 'stack');
    if (!user.must_change) {
      const current = el('input', 'input');
      current.type = 'password';
      current.name = 'current';
      current.placeholder = i18n.t('Current password');
      current.autocomplete = 'current-password';
      passwordForm.appendChild(current);
    }
    const next = el('input', 'input');
    next.type = 'password';
    next.name = 'password';
    next.placeholder = i18n.t('New password (8+ characters)');
    next.autocomplete = 'new-password';
    passwordForm.appendChild(next);

    const passwordError = el('p', 'gate-error');
    passwordError.hidden = true;
    passwordForm.appendChild(passwordError);

    const savePassword = el('button', 'btn primary', i18n.t('Update password'));
    savePassword.type = 'submit';
    passwordForm.appendChild(savePassword);
    passwordForm.onsubmit = async (event) => {
      event.preventDefault();
      passwordError.hidden = true;
      const body = Object.fromEntries(new FormData(passwordForm).entries());
      try {
        await accountsApi.changePassword(body);
        this.toast(i18n.t('Password updated. Other devices were signed out.'));
        this.user = { ...this.user, must_change: false };
        this.onChange(this.user);
        this.render();
      } catch (exc) {
        passwordError.textContent = exc.message;
        passwordError.hidden = false;
      }
    };
    security.appendChild(passwordForm);
    card.appendChild(security);

    // --- share links ---------------------------------------------------
    // Family members and admins make links; this is where they see and end
    // them. A guest makes none, so the section is not shown to one.
    if (user.role === 'family' || user.role === 'admin') {
      card.appendChild(this.sharesSection());
    }

    // --- footer --------------------------------------------------------
    const foot = el('div', 'sheet-foot');
    const role = el('div', 'role-line');
    role.append(roleBadge(user.role, i18n.role(user.role_label)));
    if (user.scope) {
      role.appendChild(el('span', 'hint',
        i18n.t('Library scope: {scope}', { scope: user.scope })));
    }
    foot.appendChild(role);
    const footActions = el('div', 'row');
    // Both faces leave the same way and say so the same way: the door
    // symbol and "Sign out". The gallery once called it "Switch profile",
    // which read as something other than leaving.
    const leaving = el('button', 'btn ghost');
    leaving.type = 'button';
    leaving.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 17l5-5-5-5M21 12H9M12 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h6"/></svg>';
    leaving.appendChild(document.createTextNode(i18n.t('Sign out')));
    leaving.onclick = async () => {
      try {
        await accountsApi.logout();
      } catch { /* already gone; reload lands on the sign-in screen anyway */ }
      window.location.reload();
    };
    footActions.appendChild(leaving);
    foot.appendChild(footActions);
    card.appendChild(foot);

    this.root.appendChild(card);
  }

  /** "Your share links": every link this person made (an admin sees all). */
  sharesSection() {
    const section = el('div', 'sheet-section');
    section.appendChild(el('h3', null, i18n.t('Your share links')));
    const list = el('div', 'people-list');
    list.appendChild(el('p', 'hint', i18n.t('Loading…')));
    section.appendChild(list);
    this.loadShares(list);
    return section;
  }

  async loadShares(list) {
    let shares;
    try {
      shares = (await accountsApi.shares()).shares || [];
    } catch (exc) {
      list.replaceChildren(el('p', 'hint', exc.message || i18n.t('Could not load your share links.')));
      return;
    }
    list.replaceChildren();
    if (!shares.length) {
      list.appendChild(el('p', 'hint', i18n.t('No share links yet')));
      return;
    }
    for (const share of shares) list.appendChild(this.shareRow(share, list));
  }

  shareRow(share, list) {
    const row = el('div', `person${share.expired ? ' disabled' : ''}`);
    const identity = el('div', 'person-identity');
    const line = el('div', 'person-name');
    const isAlbum = share.scope === 'album';
    const named = isAlbum ? this.albumName(share.target_id) : '';
    // Which photograph, not just "Photo": the server sends its file name.
    const label = isAlbum
      ? (named ? `${i18n.t('Album')}: ${named}` : i18n.t('Album'))
      : (share.name ? `${i18n.t('Photo')}: ${share.name}` : i18n.t('Photo'));
    line.appendChild(el('strong', null, label));
    if (share.expired) line.appendChild(el('span', 'off-tag', i18n.t('Expired')));
    identity.appendChild(line);

    const meta = [];
    if (share.expires_at) {
      const when = new Date(share.expires_at * 1000).toLocaleDateString(i18n.locale(),
        { day: 'numeric', month: 'short', year: 'numeric' });
      meta.push(share.expired
        ? i18n.t('Ended {date}', { date: when })
        : i18n.t('Expires {date}', { date: when }));
    } else {
      meta.push(i18n.t('Never expires'));
    }
    const views = Number(share.view_count || 0);
    meta.push(views === 1 ? i18n.t('Opened once')
      : i18n.t('Opened {count} times', { count: views.toLocaleString(i18n.locale()) }));
    identity.appendChild(el('div', 'person-meta', meta.join(' · ')));

    // The address itself, read-only: something to select by hand when the
    // browser will not copy for us (plain http on the home network).
    const address = el('input', 'share-url-input');
    address.style.cssText = 'display:block;width:100%;margin-top:6px';
    address.readOnly = true;
    address.value = shareUrl(share.token);
    address.setAttribute('aria-label', i18n.t('Share link'));
    identity.appendChild(address);
    row.appendChild(identity);

    const actions = el('div', 'person-actions');
    if (!share.expired) {
      const copy = el('button', 'btn small ghost', i18n.t('Copy link'));
      copy.type = 'button';
      copy.onclick = async () => {
        const done = await copyText(address.value, address);
        if (done) this.toast(i18n.t('Link copied to clipboard!'));
        else this.toast(i18n.t('Could not copy automatically. Copy the selected link manually.'), true);
      };
      actions.appendChild(copy);
    }
    const end = el('button', 'btn small ghost', i18n.t('End link'));
    end.type = 'button';
    end.onclick = async () => {
      if (!confirm(i18n.t('End this link? Anyone who has it will no longer be able to open it.'))) return;
      end.disabled = true;
      try {
        await accountsApi.endShare(share.token);
        this.toast(i18n.t('Link ended.'));
        this.loadShares(list);
      } catch (exc) {
        this.toast(exc.message, true);
        end.disabled = false;
      }
    };
    actions.appendChild(end);
    row.appendChild(actions);
    return row;
  }
}

/** The People page: each profile's card, adding someone, and removing them. */

import { accountsApi, avatarNode, roleBadge } from '../accounts.js';
import * as i18n from '../i18n.js';
import { refresh } from '../admin.js';
import { currentPreview, renderOverview, renderPreview } from './overview.js';
import { renderLibraryFolders } from './library.js';
import { $, adminApi, el, state, toast } from './shared.js';

export async function loadAssignable() {
  try {
    state.assignable = (await adminApi.assignable()).folders;
  } catch { state.assignable = []; }
}

export async function loadPeople() {
  try {
    const data = await accountsApi.people();
    state.people = data.people;
    state.roles = data.roles;
  } catch (exc) {
    toast(exc.message, true);
    return;
  }
  renderPeople();
}

export function renderPeople() {
  state.libraryCount = (state.overview?.library?.folders || []).length;
  const list = $('#people-list');
  list.innerHTML = '';
  for (const person of state.people) list.appendChild(personCard(person));
}

function personCard(person) {
  const card = el('div', `admin-person${person.active ? '' : ' disabled'}`);

  const head = el('div', 'ap-head');
  head.appendChild(avatarNode(person, 44));
  const identity = el('div', 'ap-identity');
  const line = el('div', 'ap-name');
  line.appendChild(el('strong', null, person.name));
  line.appendChild(roleBadge(person.role, i18n.role(person.role_label)));
  if (person.id === state.user.id) line.appendChild(el('span', 'you-tag', i18n.t('you')));
  if (!person.active) line.appendChild(el('span', 'off-tag', i18n.t('disabled')));
  identity.appendChild(line);

  const bits = [`@${person.username}`];
  if (person.role === 'admin') bits.push(i18n.t('every folder'));
  else if (person.library) bits.push(i18n.t('only {folder}', { folder: person.library }));
  else if (person.scope) bits.push(i18n.t('only {folder}', { folder: person.scope }));
  else bits.push(i18n.t('all library folders'));
  if (typeof person.visible_count === 'number') {
    bits.push(person.visible_count === 1 ? i18n.t('1 item visible')
      : i18n.t('{count} items visible', { count: person.visible_count.toLocaleString() }));
  }
  const entry = {
    password: i18n.key('password required'), pin: i18n.key('PIN required'), open: i18n.key('tap to enter'),
  }[person.entry];
  if (entry) bits.push(i18n.t(entry));
  if (person.sessions) {
    bits.push(person.sessions === 1 ? i18n.t('1 device')
      : i18n.t('{count} devices', { count: person.sessions }));
  }
  identity.appendChild(el('div', 'ap-meta', bits.join(' · ')));
  head.appendChild(identity);
  card.appendChild(head);

  const controls = el('div', 'ap-controls');

  controls.appendChild(labelled(i18n.t('Role'), (() => {
    const select = el('select', 'select');
    for (const role of state.roles) {
      const option = el('option', null, i18n.role(role.label));
      option.value = role.value;
      if (role.value === person.role) option.selected = true;
      select.appendChild(option);
    }
    select.onchange = () => {
      const body = { role: select.value };
      // An administrator signs in on the console, which only takes a
      // password, so one without a password could never sign in.
      if (select.value === 'admin' && person.role !== 'admin' && !person.has_password) {
        const password = prompt(
          i18n.t('{name} has no password yet. Temporary password for them — they’ll choose their own on first sign-in.', { name: person.name }),
          randomPassword());
        if (!password) { select.value = person.role; return; }
        body.password = password;
      }
      patchPerson(person.id, body);
    };
    return select;
  })()));

  controls.appendChild(labelled(i18n.t('Library folder'), (() => {
    const select = el('select', 'select wide');
    const all = el('option', null,
      state.libraryCount > 1 ? i18n.t('All library folders') : i18n.t('The whole library'));
    all.value = '';
    select.appendChild(all);

    for (const folder of state.assignable) {
      const option = el('option', null,
        `${'\u00a0\u00a0'.repeat(folder.depth)}${folder.is_root ? '📁 ' : ''}${folder.label} (${folder.count})`);
      option.value = folder.path;
      if (samePath(folder.path, person.library)) option.selected = true;
      select.appendChild(option);
    }

    // A folder that no longer exists must still show, or saving would
    // silently move them somewhere else.
    if (person.library && !state.assignable.some(
      (f) => samePath(f.path, person.library))) {
      const orphan = el('option', null, i18n.t('{folder} (missing)', { folder: person.library }));
      orphan.value = person.library;
      orphan.selected = true;
      select.appendChild(orphan);
    }

    select.disabled = person.role === 'admin';
    select.title = person.role === 'admin'
      ? i18n.t('Admins always see every library folder')
      : i18n.t('The only folder this profile can see');
    select.onchange = () => patchPerson(person.id, { library: select.value });
    return select;
  })()));

  if (person.role !== 'admin') {
    controls.appendChild(labelled(i18n.t('Entry'), (() => {
      const wrap = el('div', 'row');
      const select = el('select', 'select');
      for (const [value, text] of [['open', i18n.t('Tap to enter')], ['pin', i18n.t('PIN')]]) {
        const option = el('option', null, text);
        option.value = value;
        if ((person.entry === 'pin' ? 'pin' : 'open') === value) option.selected = true;
        select.appendChild(option);
      }
      select.onchange = async () => {
        if (select.value === 'open') {
          await patchPerson(person.id, { pin: '' });
          return;
        }
        const pin = prompt(i18n.t('Set a PIN for {name} (4–8 digits).', { name: person.name }), '');
        if (!pin) { select.value = person.entry === 'pin' ? 'pin' : 'open'; return; }
        await patchPerson(person.id, { pin });
      };
      wrap.appendChild(select);
      return wrap;
    })()));
  }

  const actions = el('div', 'ap-actions');
  const toggle = el('button', 'btn small ghost', person.active ? i18n.t('Disable') : i18n.t('Enable'));
  toggle.type = 'button';
  toggle.disabled = person.id === state.user.id;
  toggle.onclick = () => patchPerson(person.id, { active: !person.active });
  actions.appendChild(toggle);

  const reset = el('button', 'btn small ghost', i18n.t('Reset password'));
  reset.type = 'button';
  reset.onclick = async () => {
    const password = prompt(
      i18n.t('Temporary password for {name}. They’ll choose their own on first sign-in.', { name: person.name }),
      randomPassword());
    if (!password) return;
    try {
      await accountsApi.updatePerson(person.id, { password });
      toast(i18n.t('New password for {name}: {password}', { name: person.name, password }));
      loadPeople();
    } catch (exc) { toast(exc.message, true); }
  };
  actions.appendChild(reset);

  if (person.avatar) {
    const picture = el('button', 'btn small ghost', i18n.t('Remove picture'));
    picture.type = 'button';
    picture.onclick = async () => {
      try {
        await accountsApi.removePersonAvatar(person.id);
        toast(i18n.t('Picture removed.'));
        loadPeople();
      } catch (exc) { toast(exc.message, true); }
    };
    actions.appendChild(picture);
  }

  if (person.sessions) {
    const signout = el('button', 'btn small ghost', i18n.t('Sign out everywhere'));
    signout.type = 'button';
    signout.onclick = async () => {
      await accountsApi.signOutPerson(person.id);
      toast(i18n.t('{name} was signed out.', { name: person.name }));
      loadPeople();
    };
    actions.appendChild(signout);
  }

  // Deleting is irreversible, so it sits apart from the reversible actions
  // and always asks first.
  const remove = el('button', 'btn small danger', i18n.t('Delete'));
  remove.type = 'button';
  remove.dataset.action = 'delete-person';
  remove.dataset.person = String(person.id);
  if (person.id === state.user.id) {
    remove.disabled = true;
    remove.title = i18n.t('You can’t delete the profile you’re signed in with.');
  } else {
    remove.title = i18n.t('Remove {name} permanently', { name: person.name });
    remove.onclick = () => confirmDelete(person);
  }
  actions.appendChild(remove);

  controls.appendChild(actions);
  card.appendChild(controls);
  return card;
}

/** Ask before removing a profile, and say exactly what goes with it. */
function confirmDelete(person) {
  const modal = $('#delete-modal');
  const losses = [];
  if (person.favorites) {
    losses.push(person.favorites === 1 ? i18n.t('1 favourite')
      : i18n.t('{count} favourites', { count: person.favorites.toLocaleString() }));
  }
  if (person.sessions) {
    losses.push(person.sessions === 1 ? i18n.t('1 signed-in device')
      : i18n.t('{count} signed-in devices', { count: person.sessions }));
  }

  $('#delete-who').textContent = `${person.name} (@${person.username})`;
  $('#delete-loses').textContent = !losses.length
    ? i18n.t('They have no favourites or open sessions.')
    : losses.length === 1 ? i18n.t('Their {what} will be removed with them.', { what: losses[0] })
      : i18n.t('Their {first} and {second} will be removed with them.', { first: losses[0], second: losses[1] });

  const confirm = $('#delete-confirm');
  confirm.onclick = async () => {
    confirm.disabled = true;
    try {
      const result = await accountsApi.deletePerson(person.id);
      closeDelete();
      const gone = result.removed?.favorites
        ? i18n.t('{name}’s profile and {count} favourites were deleted.', { name: person.name, count: result.removed.favorites })
        : i18n.t('{name}’s profile was deleted.', { name: person.name });
      toast(gone);
      await refresh();
    } catch (exc) {
      toast(exc.message, true);
    } finally {
      confirm.disabled = false;
    }
  };

  modal.hidden = false;
  confirm.focus();
}

export function closeDelete() {
  $('#delete-modal').hidden = true;
}

/** Path equality that tolerates slashes and Windows case. */
function samePath(a, b) {
  if (!a || !b) return false;
  const norm = (p) => {
    const t = String(p).replace(/\\/g, '/').replace(/\/+$/, '');
    return /^[a-zA-Z]:/.test(t) ? t.toLowerCase() : t;
  };
  return norm(a) === norm(b);
}

function labelled(label, control) {
  const wrap = el('label', 'field');
  wrap.appendChild(el('span', null, label));
  wrap.appendChild(control);
  return wrap;
}

async function patchPerson(id, body) {
  try {
    await accountsApi.updatePerson(id, body);
    toast(i18n.t('Saved.'));
  } catch (exc) {
    toast(exc.message, true);
  }
  // Assignment counts and "what they see" both live on the overview payload,
  // so re-read it rather than leaving the Library tab showing stale numbers.
  try {
    state.overview = await adminApi.overview();
    renderOverview();
    renderLibraryFolders();
  } catch { /* the toast above already reported it */ }
  await loadPeople();
  renderPreview(currentPreview);
}

export function toggleAddPerson() {
  const block = $('#add-person-block');
  if (!block.hidden) { block.hidden = true; return; }
  block.hidden = false;
  block.innerHTML = '';
  block.appendChild(el('h2', null, i18n.t('Add someone')));

  const form = el('form', 'add-form');
  const grid = el('div', 'add-grid');

  const name = input('name', i18n.t('Name'));
  const username = input('username', i18n.t('username'));
  username.required = true;
  username.autocapitalize = 'none';

  const role = el('select', 'select');
  role.name = 'role';
  for (const option of state.roles) {
    const node = el('option', null, i18n.role(option.label));
    node.value = option.value;
    if (option.value === 'family') node.selected = true;
    role.appendChild(node);
  }

  const scope = el('select', 'select');
  scope.name = 'library';
  const all = el('option', null, i18n.t('All library folders'));
  all.value = '';
  scope.appendChild(all);
  for (const folder of state.assignable) {
    const option = el('option', null,
      `${'\u00a0\u00a0'.repeat(folder.depth)}${folder.is_root ? '\u{1F4C1} ' : ''}${folder.label} (${folder.count})`);
    option.value = folder.path;
    scope.appendChild(option);
  }

  const entry = el('select', 'select');
  entry.name = 'entry';
  for (const [value, text] of [
    ['open', i18n.t('Tap to enter — no secret')],
    ['pin', i18n.t('PIN')],
    ['password', i18n.t('Username + password')],
  ]) {
    const option = el('option', null, text);
    option.value = value;
    entry.appendChild(option);
  }

  const secret = input('secret', i18n.t('PIN or password'));
  secret.hidden = true;
  entry.onchange = () => {
    secret.hidden = entry.value === 'open';
    secret.placeholder = entry.value === 'pin' ? i18n.t('4–8 digits') : i18n.t('at least 8 characters');
    secret.value = entry.value === 'pin' ? '' : randomPassword();
  };

  grid.append(
    labelled(i18n.t('Name'), name), labelled(i18n.t('Username'), username),
    labelled(i18n.t('Role'), role), labelled(i18n.t('Library folder'), scope),
    labelled(i18n.t('How they sign in'), entry), labelled(i18n.t('PIN / password'), secret),
  );
  form.appendChild(grid);

  // Tap to enter is right for a shared tablet, and it is also a profile any
  // device at home can open. Said where the choice is made.
  const openNote = el('p', 'hint warn-note',
    i18n.t('Anyone with a phone or computer on your home network can open a tap-to-enter profile. Give it a PIN if it can see anything private.'));
  const showOpenNote = () => { openNote.hidden = entry.value !== 'open'; };
  entry.addEventListener('change', showOpenNote);
  showOpenNote();
  form.appendChild(openNote);

  const error = el('p', 'gate-error');
  error.hidden = true;
  form.appendChild(error);

  const submit = el('button', 'btn primary', i18n.t('Create profile'));
  submit.type = 'submit';
  form.appendChild(submit);

  form.onsubmit = async (event) => {
    event.preventDefault();
    error.hidden = true;
    const body = {
      name: name.value,
      username: username.value,
      role: role.value,
      library: scope.value,
      password: entry.value === 'password' ? secret.value : '',
      pin: entry.value === 'pin' ? secret.value : '',
    };
    try {
      await accountsApi.createPerson(body);
      const who = body.name || body.username;
      toast(entry.value === 'open' ? i18n.t('{name} added — no secret needed', { name: who })
        : entry.value === 'pin' ? i18n.t('{name} added — PIN: {secret}', { name: who, secret: secret.value })
          : i18n.t('{name} added — password: {secret}', { name: who, secret: secret.value }));
      block.hidden = true;
      await loadPeople();
    } catch (exc) {
      error.textContent = exc.message;
      error.hidden = false;
    }
  };
  block.appendChild(form);
}

function input(name, placeholder) {
  const node = el('input', 'input');
  node.name = name;
  node.placeholder = placeholder;
  return node;
}

function randomPassword() {
  const words = ['river', 'maple', 'copper', 'lantern', 'harbour', 'meadow',
    'cedar', 'amber', 'summit', 'willow', 'cobalt', 'ember'];
  const pick = () => words[Math.floor(Math.random() * words.length)];
  return `${pick()}-${pick()}-${Math.floor(10 + Math.random() * 89)}`;
}

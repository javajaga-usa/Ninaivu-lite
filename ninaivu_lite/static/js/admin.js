/**
 * The admin console (/admin, on the family app's own port).
 *
 * A management tool, not a gallery: profiles, folder visibility and library
 * controls — plus a live preview that answers the question an admin actually
 * has, which is "what will they see?"
 */

import {
  accountsApi, avatarNode, Gate, ProfileSheet, roleBadge,
} from './accounts.js';
import { onUnauthorized, reportUnauthorized, sessionRestored, thumbUrl, SCAN_COUNTS, timeLeft } from './api.js';
import { renderActivity, subscribeActivity } from './activity.js';
import { ArchivePanel } from './archive.js';
import { DrivePrompt } from './drives.js';
import { enterPressesTheButton } from './enter-key.js';
import { FirstDay } from './first-day.js';
import * as i18n from './i18n.js';
import { consoleCommands, initPalette } from './palette.js';

const $ = (sel) => document.querySelector(sel);

/* Like $, but never null.
 *
 * admin.html is compiled once and cached by Flask, while this file is served
 * with no-cache and updates immediately, so between an edit and a restart the
 * browser runs new JavaScript against old markup. A bare
 * `$('#missing').onclick =` throws there and abandons the rest of the function
 * it is in — which is how adding a sign-out button silently killed the
 * storage-integrity button wired forty lines below it. Handlers bound to the
 * stub simply never fire.
 */
const DETACHED = document.createElement('div');
const $$ = (sel) => document.querySelector(sel) || DETACHED;
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
    // A 401 carrying `needs_password` is a request asking you to confirm
    // who you are before something irreversible — not a session that has
    // ended. See api.js: the same 401 means two different things, and
    // only one of them should raise the sign-in screen.
    if (response.status === 401 && !data?.needs_password) reportUnauthorized(url);
    throw Object.assign(new Error(i18n.t(data?.error || response.statusText)),
      { status: response.status, data });
  }
  return data;
}

const adminApi = {
  overview: () => json('/api/admin/overview'),
  folders: () => json('/api/admin/folders'),
  preview: (query) => json(`/api/admin/preview?${new URLSearchParams(query)}`),
  settings: (body) => json('/api/admin/settings', { method: 'POST', body }),
  askAboutDrivesAgain: () => json('/api/admin/drives/ask-again', { method: 'POST' }),
  setFolderVisibility: (folder, visibility, confirm = false) =>
    json('/api/visibility/folder',
      { method: 'POST', body: { folder, visibility, confirm } }),
  undoVisibility: (batchId) =>
    json('/api/visibility/undo', { method: 'POST', body: { batch_id: batchId } }),
  visibilityHistory: () => json('/api/visibility/history'),
  browse: (path) => json(`/api/library/browse?path=${encodeURIComponent(path || '')}`),
  setRoot: (path) => json('/api/library/root', { method: 'POST', body: { path } }),
  rescan: (full) => json('/api/scan', { method: 'POST', body: { full } }),
  removeLibrary: (path, force) =>
    json(`/api/admin/libraries?path=${encodeURIComponent(path)}${force ? '&force=1' : ''}`,
      { method: 'DELETE' }),
  setActiveLibrary: (path) =>
    json('/api/admin/libraries/active', { method: 'POST', body: { path } }),
  moveLibrary: (path, to) =>
    json('/api/admin/libraries/move', { method: 'POST', body: { path, to } }),
  assignable: () => json('/api/admin/assignable'),
  backups: () => json('/api/admin/backups'),
};

/* The copies Ninaivu Lite keeps by itself: how many, the newest, and a
   daily copy that failed, which used to go only to the log. */
async function renderBackups() {
  const kept = $('#backup-kept');
  const failure = $('#backup-failure');
  if (!kept || !failure) return;
  let data;
  try { data = await adminApi.backups(); } catch { return; }
  const list = data.backups || [];
  const newest = list.length ? new Date(list[0].at * 1000).toLocaleString(i18n.locale()) : '';
  if (list.length > 1) {
    kept.textContent = i18n.t('{count} copies are kept in {folder}. The newest is from {when}.', {
      count: list.length, folder: data.folder, when: newest,
    });
  } else if (list.length === 1) {
    kept.textContent = i18n.t('One copy is kept in {folder}, from {when}.', {
      folder: data.folder, when: newest,
    });
  } else {
    kept.textContent = i18n.t('No copy has been kept yet: one is made each day while Ninaivu Lite runs.');
  }
  failure.hidden = !data.failure;
  if (data.failure) {
    failure.textContent = i18n.t('The last daily copy failed ({when}): {why}', {
      when: new Date(data.failure.at * 1000).toLocaleString(i18n.locale()), why: data.failure.why,
    });
  }
}

/* One port serves the family app and this console, so the server cannot tell
   from the address which of the two is asking. The console says so itself:
   its sign-in state is asked with `?face=admin` (the server then answers as
   the console — no profile picker, no guest door), and its sign-in form
   carries `console: true`, so a family member's username and password are
   refused with a sentence rather than signed in and bounced. */
const consoleState = () => json('/api/auth/state?face=admin');
{
  const familyLogin = accountsApi.login;
  accountsApi.login = (body) => familyLogin({ ...body, console: true });
}

const state = {
  user: null, overview: null, people: [], roles: [], folders: [],
  libraries: [], assignable: [],
};
let currentPreview = 'guest';
let gate;
let profileSheet;
let firstDay;
let archivePanel;
let drivePrompt;

/* ======================================================================== */

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem('ninaivu.theme', JSON.stringify(theme));
    localStorage.setItem('mv.theme', JSON.stringify(theme));
  } catch { /* private */ }
}

window.addEventListener('storage', (event) => {
  if (event.key === 'ninaivu.theme' || event.key === 'mv.theme') {
    try {
      const next = JSON.parse(event.newValue);
      if (next && ['system', 'light', 'dark'].includes(next)) {
        document.documentElement.dataset.theme = next;
      }
    } catch {}
  }
});

function setupAdminIOSInstallPrompt() {
  const isIPad = /iPad/.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const isIPhone = /iPhone|iPod/.test(navigator.userAgent);
  const isIOS = (isIPhone || isIPad) && !window.MSStream;
  const isStandalone = window.navigator.standalone === true || window.matchMedia('(display-mode: standalone)').matches;
  if (!isIOS || isStandalone) return;

  const dismissed = localStorage.getItem('ninaivu:admin-ios-install-dismissed');
  if (dismissed) return;

  const banner = document.getElementById('ios-install-banner');
  const title = document.getElementById('ios-install-title');
  const desc = document.getElementById('ios-install-desc');
  const closeBtn = document.getElementById('ios-install-close');
  if (!banner) return;

  // The share glyph goes in as a substitution so the sentence around it can
  // be translated whole; the markup itself is nobody's words.
  const share = '<svg class="ios-share-glyph" viewBox="0 0 24 24"><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8M16 6l-4-4-4 4M12 2v13"/></svg>';
  if (isIPad) {
    if (title) title.textContent = i18n.t('Install Ninaivu Admin on your iPad');
    if (desc) {
      desc.innerHTML = i18n.t('Tap {share} Share in Safari’s top toolbar, then select <strong>Add to Home Screen</strong> [+]', { share });
    }
  } else {
    if (title) title.textContent = i18n.t('Install Ninaivu Admin on your iPhone');
    if (desc) {
      desc.innerHTML = i18n.t('Tap {share} Share below, then select <strong>Add to Home Screen</strong> [+]', { share });
    }
  }

  setTimeout(() => {
    banner.hidden = false;
  }, 2500);

  closeBtn?.addEventListener('click', () => {
    banner.hidden = true;
    localStorage.setItem('ninaivu:admin-ios-install-dismissed', '1');
  });
}

async function init() {
  try {
    applyTheme(JSON.parse(localStorage.getItem('ninaivu.theme') || localStorage.getItem('mv.theme') || '"system"'));
  } catch { applyTheme('system'); }

  // Before anything is drawn, as the family app does: the console is marked
  // up in English and translated in place, so starting later would show one
  // language turning into another.
  await i18n.start();
  wireLanguage();
  initPalette({ commands: consoleCommands });

  // No service worker is removed here: the console now shares its origin with
  // the family app, and that worker is the family app's.
  setupAdminIOSInstallPrompt();

  gate = new Gate($('#gate'), {
    toast,
    onSignedIn: (user) => { sessionRestored(); start(user); },
  });

  // A session can end while the console sits open — reset, signed out from
  // another device, or simply expired. Until now every request after that
  // failed with a toast and the only way back was to reload the page by hand.
  // Now the first 401 raises the same sign-in screen the console boots with,
  // over whatever tab is on screen, and signing back in resumes there.
  onUnauthorized(async () => {
    // Only a session that started can end. A 401 before anybody has signed in
    // means a request went out too early — show the gate, which is the right
    // thing either way, but do not tell somebody their session expired when
    // they have not had one.
    if (state.user) {
      toast(i18n.t('Your session has ended. Please sign in again.'), true);
    }
    let authState = {};
    try {
      authState = await consoleState();
    } catch {
      // Server unreachable rather than signed out. Show the form anyway —
      // it is the only thing that can recover, and it says so when it fails.
      authState = {};
    }
    showGate(authState);
  });
  profileSheet = new ProfileSheet($('#profile-sheet'), {
    face: 'admin',
    toast, onChange: (user) => { state.user = user; renderIdentity(); },
  });

  wireChrome();

  let auth;
  try {
    auth = await consoleState();
    state.auth = auth;
  } catch {
    toast(i18n.t('Cannot reach the server. Retrying automatically…'), true);
    const retryInterval = setInterval(async () => {
      try {
        const a = await consoleState();
        clearInterval(retryInterval);
        state.auth = a;
        toast(i18n.t('Connected to Ninaivu server.'));
        if (a.setup_required || !a.signed_in || a.user?.role !== 'admin') return showGate(a);
        start(a.user);
      } catch { /* keep retrying */ }
    }, 3000);
    return;
  }

  // The console is where a brand-new install can get its first administrator.
  if (auth.setup_required || !auth.signed_in || auth.user?.role !== 'admin') return showGate(auth);
  start(auth.user);
}

/* The console's sign-in card: the administrator's username and password, or
   the first-administrator form on a new install. Whatever the server said,
   it is drawn as the console's — no profile picker and no guest door, which
   would sign in somebody the console then has to turn away. */
function showGate(authState) {
  const asConsole = { ...(authState || {}), face: 'admin', profiles: [] };
  gate.show(asConsole, asConsole.setup_required ? 'setup' : 'login');
}

async function start(user) {
  if (!user || user.role !== 'admin') {
    let authState = {};
    try { authState = await consoleState(); } catch { /* the form says so when it fails */ }
    showGate(authState);
    return;
  }
  state.user = user;
  // The language follows the person, as it does in the family app: what they
  // chose once, on any device, is what the console opens in.
  if (user.language && user.language !== i18n.language()
      && i18n.LANGUAGES.some((l) => l.code === user.language)) {
    await i18n.use(user.language);
  }
  renderIdentity();
  // What is running, in the heading strip, and the scan's own bar on the
  // Library page — both from the same poll; see onActivity.
  watchActivity();
  // The Import page: wired once, before the first refresh, so a tap on its
  // tab during that refresh finds it ready rather than empty.
  if (!archivePanel) {
    archivePanel = new ArchivePanel({
      toast,
      pickFolder: (options) => openFolderPicker(options),
      onLibraryChanged: refresh,
    });
    archivePanel.wire();
  }
  await refresh();
  // The first day: once, right after the administrator is made.
  firstDay ||= new FirstDay({
    json, toast, openPage: (page) => showTab(page), refresh,
    pickFolder: (options) => openFolderPicker(options),
  });
  firstDay.maybeOpen();
  if (user.must_change) profileSheet.open(user);
  // A pendrive or an external drive plugged in: bring its photos in, or copy
  // the library out to it.
  drivePrompt ||= new DrivePrompt({
    json, toast,
    openImport: async (path) => {
      await archivePanel.useDrive(path);
      showTab('archive');
    },
  });
  drivePrompt.start();
}

async function refresh() {
  try {
    state.overview = await adminApi.overview();
  } catch (exc) {
    toast(exc.message, true);
    return;
  }
  renderOverview();
  renderLibrary();
  await Promise.all([loadPeople(), loadFolders(), loadAssignable()]);
  renderPreview(currentPreview);
}

function renderIdentity() {
  const button = $('#profile-btn');
  button.innerHTML = '';
  if (!state.user) return;
  button.appendChild(avatarNode(state.user, 30));
  button.title = `${state.user.name} · ${i18n.role(state.user.role_label)}`;
}

/* -- Language ------------------------------------------------------------ */

/* The same switch the family app's sign-in card and lock screen carry — one
   button per language, each named in itself — in the sidebar's foot and on
   the console's sign-in screen. Either placeholder may be missing from an
   older cached admin.html, so each is drawn only if it is there. */
function renderLanguageSwitches() {
  renderLanguageToggle($('#console-lang'));
  for (const holder of [$('#console-signin-lang')]) {
    if (!holder) continue;
    holder.replaceChildren();
    if (i18n.LANGUAGES.length < 2) continue;
    const row = el('div', 'gate-languages');
    row.setAttribute('role', 'group');
    row.setAttribute('aria-label', i18n.t('Language'));
    for (const { code, name } of i18n.LANGUAGES) {
      const pick = el('button', 'gate-lang', name);
      pick.type = 'button';
      pick.lang = code;
      const on = code === i18n.language();
      pick.classList.toggle('on', on);
      pick.setAttribute('aria-pressed', String(on));
      pick.onclick = async () => {
        if (code === i18n.language()) return;
        await i18n.use(code);
        rememberLanguage(code);
      };
      row.appendChild(pick);
    }
    holder.appendChild(row);
  }
}

/* The top bar's switch is one round letter, as in the family app: the letter
   of the language a press would give, its full name (in itself) the tooltip. */
function renderLanguageToggle(holder) {
  if (!holder) return;
  holder.replaceChildren();
  if (i18n.LANGUAGES.length < 2) return;
  const codes = i18n.LANGUAGES.map((l) => l.code);
  const next = i18n.LANGUAGES[(codes.indexOf(i18n.language()) + 1) % codes.length];
  const button = el('button', 'lang-toggle');
  button.type = 'button';
  button.title = next.name;
  button.setAttribute('aria-label', next.name);
  const letter = el('span', null, next.letter);
  letter.lang = next.code;
  button.appendChild(letter);
  button.onclick = async () => {
    await i18n.use(next.code);
    rememberLanguage(next.code);
  };
  holder.appendChild(button);
}

/** Save the language on the profile, so every device of theirs follows. */
async function rememberLanguage(code) {
  if (!state.user || !state.user.id) return;
  try {
    state.user = await accountsApi.updateMe({ language: code });
  } catch { /* this device still remembers it */ }
}

/* i18n.apply() redraws whatever admin.html marks up; everything this file
   builds itself is drawn again here. What it still holds is drawn from that;
   the page on screen, whose answer it did not keep, asks again. */
function relabel() {
  renderLanguageSwitches();
  // Before anybody has signed in there is nothing drawn but the sign-in card,
  // which redraws itself.
  if (!state.user) return;
  renderIdentity();
  const current = document.querySelector('#tabs button.active')?.dataset.tab;
  if (current) showPageHeading(current);
  if (state.overview) {
    renderOverview();
    renderLibrary();
    renderPeople();
    renderFolderTree();
    renderPreview(currentPreview);
  }
  refreshUndo();
}

function wireLanguage() {
  renderLanguageSwitches();
  i18n.onChange(relabel);
  // The lock screen says which language it switched to; keep it on the profile.
  document.addEventListener('ninaivu:language', (event) => rememberLanguage(event.detail));
}

/* ======================================================================== */

function wireChrome() {
  // Enter in a field does what the button beside it does, everywhere.
  enterPressesTheButton();
  // Every handler below is bound through $$ on purpose. These elements live
  // in admin.html, which Flask compiles once and caches, while this file is
  // served with no-cache and updates immediately. Between an edit and a
  // restart the browser therefore runs new JavaScript against old markup, and
  // one `$('#missing').onclick =` used to throw and abandon the rest of this
  // function.
  document.querySelectorAll('#tabs button').forEach((tab) => {
    tab.onclick = () => showTab(tab.dataset.tab);
  });
  // The markup hides other sections' pages, which is right only for the top bar.
  syncTabVisibility();
  // A group is not a destination of its own: choosing one opens the first page
  // in it, so a click always lands somewhere rather than on an empty section.
  document.querySelectorAll('#tab-groups button').forEach((group) => {
    group.onclick = () => {
      const first = document.querySelector(`#tabs button[data-group="${group.dataset.group}"]`);
      if (first) showTab(first.dataset.tab);
    };
  });
  $$('#theme-btn').onclick = () => {
    const order = ['system', 'light', 'dark'];
    const current = document.documentElement.dataset.theme || 'system';
    applyTheme(order[(order.indexOf(current) + 1) % order.length]);
  };
  $$('#profile-btn').onclick = () => profileSheet.open(state.user);
  $$('#signout-btn').onclick = async () => {
    try {
      await accountsApi.logout();
    } catch { /* the session may already be gone; land on the door either way */ }
    window.location.reload();
  };
  $$('#add-person-btn').onclick = () => toggleAddPerson();
  $$('#change-root').onclick = () => openFolderPicker();
  $$('#rescan-btn').onclick = () => runScan(false);
  $$('#full-rescan-btn').onclick = () => runScan(true);
  $$('#vis-undo-btn').onclick = undoLastVisibility;
  $$('#fm-manual').addEventListener('input', refreshApply);
  $$('#fm-cancel').onclick = () => ($('#folder-modal').hidden = true);
  $$('#fm-apply').onclick = () => applyRoot();
  $$('#delete-cancel').onclick = () => closeDelete();
  $$('#delete-modal').addEventListener('click', (event) => {
    if (event.target === $('#delete-modal')) closeDelete();
  });
  $$('#profile-sheet').addEventListener('click', (event) => {
    if (event.target === $('#profile-sheet')) $('#profile-sheet').hidden = true;
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    // The one on top: the folder picker opened from the first-day steps comes
    // later in the page than the steps, and Escape closed the steps under it.
    const open = [...document.querySelectorAll('.modal, .sheet')].reverse().find((m) => !m.hidden);
    if (open) open.hidden = true;
  });
}

// The line under each page's title. Kept beside showTab rather than in the
// markup so a page added to #tabs without one simply shows no line.
const PAGE_DESCRIPTIONS = {
  overview: i18n.key('Your family library, at a glance.'),
  people: i18n.key('Manage the people who share your library.'),
  visibility: i18n.key('Who can see each part of your library.'),
  library: i18n.key('Library folders, and how they are indexed.'),
  archive: i18n.key('Bring old drives, cards and backup folders into one archive.'),
  settings: i18n.key('This home’s name, its language, and copies of everything Ninaivu keeps.'),
};

// The pages this console has. Anything else a server or an older link names
// (the activity strip says which page owns each job) lands on the nearest one.
const PAGES = new Set(Object.keys(PAGE_DESCRIPTIONS));
const pageOrHome = (page) => (PAGES.has(page) ? page : 'overview');

// Keep in step with the sidebar breakpoint in admin.css.
const sidebarLayout = window.matchMedia('(min-width: 1100px)');

// The sidebar lists every page under its section; the narrow top bar shows
// only the pages of the chosen section.
function syncTabVisibility() {
  const group = document.querySelector('#tab-groups button.active')?.dataset.group || '';
  document.querySelectorAll('#tabs button').forEach((tab) => {
    tab.hidden = !sidebarLayout.matches && Boolean(group) && tab.dataset.group !== group;
  });
}
sidebarLayout.addEventListener('change', syncTabVisibility);

// Scroll *box* just enough that *item* is in it. Not scrollIntoView: that
// scrolls every ancestor too, so opening a page from a shortcut halfway down
// the Overview would move the page as well as the list.
function keepInView(item, box) {
  if (!item || !box) return;
  const inner = item.getBoundingClientRect();
  const outer = box.getBoundingClientRect();
  const margin = 24;
  if (box.scrollWidth > box.clientWidth) {
    if (inner.left < outer.left) box.scrollLeft -= outer.left - inner.left + margin;
    else if (inner.right > outer.right) box.scrollLeft += inner.right - outer.right + margin;
  }
  if (box.scrollHeight > box.clientHeight) {
    if (inner.top < outer.top) box.scrollTop -= outer.top - inner.top + margin;
    else if (inner.bottom > outer.bottom) box.scrollTop += inner.bottom - outer.bottom + margin;
  }
}

/* The heading over the page: its section, its name and the line under it.
   Apart from showTab so a change of language can draw it again. */
function showPageHeading(name) {
  const opened = document.querySelector(`#tabs button[data-tab="${name}"]`);
  const group = opened ? opened.dataset.group : '';
  const title = $('#page-title');
  // The label alone: the Uploads tab also carries a live count, which the
  // heading would freeze at whatever it was when the page opened.
  if (title) title.textContent = opened?.querySelector('.tab-label')?.textContent || i18n.t('Overview');
  const section = $('#page-section');
  if (section) section.textContent =
    document.querySelector(`#tab-groups button[data-group="${group}"]`)?.textContent || i18n.t('Home');
  const description = $('#page-description');
  if (description) description.textContent = PAGE_DESCRIPTIONS[name] ? i18n.t(PAGE_DESCRIPTIONS[name]) : '';
}

function showTab(page) {
  const name = pageOrHome(page);
  // Which group owns this page. Deep links and the post-sign-in resume both
  // call showTab directly, so the group row is derived here rather than in the
  // click handlers -- otherwise arriving at a page would leave the wrong
  // section highlighted and its siblings hidden.
  const opened = document.querySelector(`#tabs button[data-tab="${name}"]`);
  const group = opened ? opened.dataset.group : '';
  showPageHeading(name);
  document.querySelectorAll('#tab-groups button').forEach((button) => {
    const selected = button.dataset.group === group;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-selected', String(selected));
  });
  syncTabVisibility();
  document.querySelectorAll('#tabs button').forEach((tab) => {
    const selected = tab.dataset.tab === name;
    tab.classList.toggle('active', selected);
    // #tabs declares role="tablist"/role="tab", which promises a screen
    // reader that the current tab is exposed via aria-selected — the visual
    // ".active" class alone says nothing to assistive tech.
    tab.setAttribute('aria-selected', String(selected));
  });
  // A page opened from elsewhere — a shortcut, a deep link, the resume after
  // sign-in — may be one the sidebar or the phone's tab row has scrolled out
  // of sight; the highlight is no use where nobody can see it.
  keepInView(opened, sidebarLayout.matches ? opened?.closest('.admin-nav') : $('#tabs'));
  if (!sidebarLayout.matches) {
    keepInView(document.querySelector('#tab-groups button.active'), $('#tab-groups'));
  }
  document.querySelectorAll('.panel').forEach(
    (panel) => panel.classList.toggle('active', panel.dataset.panel === name));
  if (name === 'visibility') { loadFolders(); refreshUndo(); }
  if (name === 'library') renderLibraryFolders();
  // The Import page polls while it is open and stops when it is left.
  if (name === 'archive') archivePanel?.show(); else archivePanel?.hide();
  if (name === 'overview') {
    // Visibility may have changed on another tab; re-ask rather than
    // showing a stale "what will they see" answer.
    renderPreview(currentPreview);
  }
}

/* -- Overview ------------------------------------------------------------ */

function renderOverview() {
  const data = state.overview;
  const stats = data.stats || {};
  const cards = $('#overview-cards');
  cards.innerHTML = '';

  // Every card opens the page that changes what it counts. A number that
  // says "AI: off" and stops there tells you the problem and hides the fix.
  const card = (label, value, sub, tone, page) => {
    const node = el(page ? 'button' : 'div', `card${tone ? ` ${tone}` : ''}`);
    if (page) {
      node.type = 'button';
      node.onclick = () => showTab(page);
      // The page by the name the sidebar gives it, which is already in the
      // reader's language.
      const pageName = document.querySelector(`#tabs button[data-tab="${page}"] .tab-label`)?.textContent
        || page.replace('-', ' ');
      node.setAttribute('aria-label', i18n.t('{label}: {value}. Open {page}', { label, value, page: pageName }));
    }
    node.appendChild(el('div', 'card-label', label));
    node.appendChild(el('div', 'card-value', value));
    if (sub) node.appendChild(el('div', 'card-sub', sub));
    return node;
  };

  cards.append(
    card(i18n.t('In the library'), (stats.count || 0).toLocaleString(),
      i18n.t('{photos} photos · {videos} videos',
        { photos: stats.pictures || 0, videos: stats.videos || 0 }),
      '', 'library'),
    card(i18n.t('Public'), (stats.public || 0).toLocaleString(), i18n.t('visible to guests'),
      stats.public ? 'good' : '', 'visibility'),
    card(i18n.t('Hidden'), (stats.hidden || 0).toLocaleString(), i18n.t('admins only'),
      stats.hidden ? 'warn' : '', 'visibility'),
    card(i18n.t('Profiles'), String(data.people.total),
      Object.entries(data.people.by_role)
        .filter(([, n]) => n)
        .map(([role, n]) => (ROLE_COUNTS[role] ? i18n.t(ROLE_COUNTS[role], { count: n }) : `${n} ${role}`))
        .join(' · '), '', 'people'),
    // People, not sessions: one person on a phone, a tablet and two tabs is
    // one person signed in. `sessions` is the fallback for an older server.
    card(i18n.t('Signed in now'), String(data.people.signed_in ?? data.people.sessions),
      i18n.t('people with a live session'), '', 'people'),
  );
}

// How many of each role the Profiles card counts — "2 admin · 1 family",
// as the server names the roles.
const ROLE_COUNTS = {
  guest: i18n.key('{count} guest'),
  family: i18n.key('{count} family'),
  admin: i18n.key('{count} admin'),
};

async function loadAssignable() {
  try {
    state.assignable = (await adminApi.assignable()).folders;
  } catch { state.assignable = []; }
}

function renderLibraryFolders() {
  const list = $('#library-list');
  if (!list) return;
  const folders = state.overview?.library?.folders || [];
  list.innerHTML = '';

  if (!folders.length) {
    list.appendChild(el('p', 'hint', i18n.t('No folders yet — add one to get started.')));
    return;
  }

  for (const folder of folders) {
    const row = el('div', `library-item${folder.exists ? '' : ' missing'}`);

    const main = el('div', 'li-main');
    const title = el('div', 'li-name');
    title.appendChild(el('strong', null, folder.name));
    if (folder.active) title.appendChild(el('span', 'tag family', i18n.t('default')));
    if (!folder.exists) title.appendChild(el('span', 'tag hidden', i18n.t('missing')));
    main.appendChild(title);
    main.appendChild(el('code', 'li-path', folder.path));
    const bits = [i18n.items(folder.count)];
    if (folder.assigned) {
      bits.push(folder.assigned === 1 ? i18n.t('1 person assigned')
        : i18n.t('{count} people assigned', { count: folder.assigned }));
    }
    main.appendChild(el('div', 'li-meta', bits.join(' · ')));
    row.appendChild(main);

    const actions = el('div', 'li-actions');
    if (!folder.active) {
      const makeDefault = el('button', 'btn small ghost', i18n.t('Make default'));
      makeDefault.type = 'button';
      makeDefault.title = i18n.t('The folder the Visibility tab works on');
      makeDefault.onclick = async () => {
        try {
          await adminApi.setActiveLibrary(folder.path);
          await refresh();
        } catch (exc) { toast(exc.message, true); }
      };
      actions.appendChild(makeDefault);
    }
    if (!folder.exists) {
      // Photos on another drive letter, or a new computer after a restore:
      // point the folder there, keeping everything decided about them.
      const moved = el('button', 'btn small', i18n.t('Moved?'));
      moved.type = 'button';
      moved.title = i18n.t('The photographs are somewhere else now: choose where');
      moved.onclick = () => openFolderPicker({
        title: i18n.t('Where is {name} now?', { name: folder.name }),
        cta: i18n.t('It is here'),
        start: '',
        pick: async (to) => {
          try {
            await adminApi.moveLibrary(folder.path, to);
            toast(i18n.t('{name} now points to {path}. Everything set for its photos is kept.',
              { name: folder.name, path: to }));
            await refresh();
          } catch (exc) { toast(exc.message, true); }
        },
      });
      actions.appendChild(moved);
    }
    const remove = el('button', 'btn small ghost', i18n.t('Remove'));
    remove.type = 'button';
    remove.title = i18n.t('Stop indexing this folder. Your files are not touched.');
    remove.onclick = () => removeLibrary(folder);
    actions.appendChild(remove);
    row.appendChild(actions);

    list.appendChild(row);
  }
}

async function removeLibrary(folder) {
  const ask = folder.exists
    ? i18n.t('Take {name} out of the library? Its photos leave the gallery. Your files are not touched, and who sees what, favourites, albums and share links come back if you add the same folder again.', { name: folder.name })
    : i18n.t('{name} cannot be found. If its photos are somewhere else now, use "Moved?" instead. Take it out of the library anyway?', { name: folder.name });
  if (!confirm(ask)) return;
  try {
    await adminApi.removeLibrary(folder.path, false);
    toast(i18n.t('{name} is no longer indexed. Your files were not touched.', { name: folder.name }));
    await refresh();
  } catch (exc) {
    if (exc.status === 409) {
      // Someone is assigned to it — say who, and let the admin decide.
      if (!confirm(`${exc.message}\n\n${i18n.t('Remove it anyway? They will see nothing until you assign them another folder.')}`)) return;
      try {
        await adminApi.removeLibrary(folder.path, true);
        toast(i18n.t('{name} removed. The people assigned to it see nothing until you reassign them.', { name: folder.name }));
        await refresh();
      } catch (inner) { toast(inner.message, true); }
      return;
    }
    toast(exc.message, true);
  }
}

function renderLibrary() {
  const data = state.overview;
  renderLibraryFolders();
  renderBackups();
  // The family app is at "/" on this same address.
  const box = $('#overview-library');
  box.innerHTML = '';
  const folderCount = (data.library.folders || []).length;
  // The first of each row says which it is, the second is what is shown.
  const rows = [
    ['folders', i18n.t('Library folders'), folderCount
      ? (folderCount === 1 ? i18n.t('1 folder') : i18n.t('{count} folders', { count: folderCount }))
      : i18n.t('None yet')],
    ['app', i18n.t('Family app'), data.app.home_url || '/'],
    ['guests', i18n.t('Guests without a login'), data.app.open_browsing ? i18n.t('Allowed') : i18n.t('Blocked')],
    ['watch', i18n.t('Watching for changes'), data.app.watch ? i18n.t('Yes') : i18n.t('No')],
  ];
  const list = el('dl', 'kv');
  for (const [which, key, value] of rows) {
    list.appendChild(el('dt', null, key));
    const dd = el('dd');
    if (which === 'app' && value && /^(https?:\/\/|\/)/.test(value)) {
      // The family app is on this same address now, at "/".
      const a = el('a', null, value.startsWith('/') ? `${location.origin}${value}` : value);
      a.href = value;
      dd.appendChild(a);
    } else {
      dd.textContent = value;
    }
    // The default folder's path on a line of its own, in the same type the
    // Settings page shows paths in: run on after the count it broke mid-word
    // wherever the column happened to end.
    if (which === 'folders' && folderCount && data.library.root) {
      dd.appendChild(el('code', 'kv-path', data.library.root));
    }
    list.appendChild(dd);
  }
  box.appendChild(list);

  // Settings. Each switch is rendered on the page of the thing it governs:
  // indexing on Library settings, and the home's name, its language and who
  // may look without signing in here on Settings.
  const settings = $$('#settings');
  const indexing = $('#library-switches') || settings;
  for (const holder of new Set([settings, indexing])) holder.innerHTML = '';

  // Which way up: the camera's tag always; faces only with OpenCV installed.
  const upright = el('p', 'hint setting-note');
  upright.textContent = data.capabilities?.opencv
    ? i18n.t('Sideways photographs are turned upright during the scan: by the camera\'s own tag, and for photographs without one, by the faces in them. Nothing is asked and no file is changed; Rotate in the viewer corrects any that are wrong.')
    : i18n.t('Sideways photographs are turned upright by the camera\'s own tag. To judge photographs without one by the faces in them, install OpenCV (requirements-straighten.txt) and restart. Rotate in the viewer corrects any by hand.');
  indexing.appendChild(upright);

  // What the household is called. This is the default everyone sees; family
  // members may keep their own name for it instead, which only they see.
  const nameBlock = el('div', 'setting-field');
  nameBlock.appendChild(el('label', null, i18n.t('Name for this home')));
  const nameRow = el('div', 'row');
  const nameInput = el('input', 'input');
  nameInput.value = data.app.house_name || '';
  nameInput.maxLength = 40;
  nameInput.placeholder = i18n.t('Ninaivu');
  nameInput.setAttribute('aria-label', i18n.t('Name for this home'));
  const nameSave = el('button', 'btn', i18n.t('Save'));
  nameSave.type = 'button';
  const saveHouseName = async () => {
    nameSave.disabled = true;
    try {
      const result = await adminApi.settings({ house_name: nameInput.value });
      state.overview.app.house_name = result.settings.house_name;
      nameInput.value = result.settings.house_name || '';
      toast(i18n.t('Everyone now sees “{name}”.', { name: result.settings.house_name_effective }));
    } catch (exc) {
      toast(exc.message, true);
    } finally {
      nameSave.disabled = false;
    }
  };
  nameSave.onclick = saveHouseName;
  nameInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveHouseName(); });
  nameRow.append(nameInput, nameSave);
  const versionLine = $('#app-version');
  if (versionLine) {
    versionLine.textContent = [
      data.app.version ? i18n.t('Ninaivu Lite {version}', { version: data.app.version }) : '',
      data.app.copyright || '',
      data.app.licence ? i18n.t('Licence: {licence}', { licence: data.app.licence }) : '',
    ].filter(Boolean).join(' · ');
  }
  nameBlock.appendChild(nameRow);
  nameBlock.appendChild(el('p', 'hint',
    i18n.t('Shown at the top of the family app and on the home screen icon. Family members can keep their own name for it instead — that one is private to them.')));
  settings.appendChild(nameBlock);

  // The language the family app and this console open in for somebody who
  // has not chosen one. Each person's own choice, on their profile, still
  // wins. The languages are named in themselves, as on every switch.
  const languageBlock = el('div', 'setting-field');
  languageBlock.appendChild(el('label', null, i18n.t('Default language')));
  const languageRow = el('div', 'row');
  const languagePick = el('select', 'input');
  languagePick.setAttribute('aria-label', i18n.t('Default language'));
  const defaultLanguage = data.app.default_language || 'en';
  for (const { code, name } of i18n.LANGUAGES) {
    const option = el('option', null, name);
    option.value = code;
    option.lang = code;
    option.selected = code === defaultLanguage;
    languagePick.appendChild(option);
  }
  languagePick.onchange = async () => {
    const chosen = languagePick.value;
    try {
      const result = await adminApi.settings({ language: chosen });
      const saved = result?.settings?.default_language ?? result?.settings?.language ?? chosen;
      state.overview.app.default_language = saved;
      toast(i18n.t('Saved.'));
    } catch (exc) {
      toast(exc.message, true);
      languagePick.value = state.overview.app.default_language || 'en';
    }
  };
  languageRow.appendChild(languagePick);
  languageBlock.appendChild(languageRow);
  languageBlock.appendChild(el('p', 'hint',
    i18n.t('What the family app and this console open in for anyone who has not picked a language of their own.')));
  settings.appendChild(languageBlock);

  const toggles = [
    [settings, 'open_browsing', i18n.t('Let visitors browse public media without signing in'),
      data.app.open_browsing],
    [indexing, 'watch', i18n.t('Watch the folder and index new files automatically'), data.app.watch],
    [settings, 'video_originals',
      i18n.t('Send videos to guests and share links as they are when their location cannot be removed'),
      data.app.video_originals],
  ];
  // Visitors kept out still leaves a Family profile without a PIN one tap
  // away for anyone on the home network, originals and locations included.
  const openFamily = el('p', 'hint warn');
  const showOpenFamily = () => {
    const names = state.overview.people?.open_family || [];
    openFamily.hidden = state.overview.app.open_browsing || !names.length;
    openFamily.textContent = i18n.t('Visitors must sign in, but anyone on your home network can still tap into these Family profiles, which have no PIN: {names}. Give them a PIN on the People page to keep them private.',
      { names: names.join(', ') });
  };
  for (const [where, key, label, value] of toggles) {
    const row = el('label', 'toggle');
    const input = el('input');
    input.type = 'checkbox';
    input.checked = Boolean(value);
    input.dataset.setting = key;
    input.onchange = async () => {
      try {
        const result = await adminApi.settings({ [key]: input.checked });
        toast(i18n.t('Saved.'));
        // What the server made of it, not what was sent.
        const saved = result?.settings?.[key];
        state.overview.app[key] = saved === undefined ? input.checked : saved;
        if (key === 'open_browsing') showOpenFamily();
      } catch (exc) {
        toast(exc.message, true);
        input.checked = !input.checked;
      }
    };
    row.append(input, el('span', null, label));
    where.appendChild(row);
    if (key === 'open_browsing') {
      showOpenFamily();
      where.appendChild(openFamily);
    }
  }

  // Drives and phones whose notice was answered with Don't ask again.
  const quiet = data.app.quiet_drives || 0;
  if (quiet) {
    const drivesBlock = el('div', 'setting-field');
    drivesBlock.appendChild(el('label', null, i18n.t('Plugged-in drives')));
    const drivesRow = el('div', 'row');
    drivesRow.appendChild(el('p', 'hint', i18n.t('Drives and phones set to Don’t ask again: {count}. Ask again shows their notice the next time they are plugged in.',
      { count: quiet.toLocaleString() })));
    const again = el('button', 'btn small', i18n.t('Ask again'));
    again.type = 'button';
    again.onclick = async () => {
      try {
        await adminApi.askAboutDrivesAgain();
      } catch (exc) {
        toast(exc.message, true);
        return;
      }
      state.overview.app.quiet_drives = 0;
      drivesBlock.remove();
      toast(i18n.t('Every drive and phone will be mentioned again when it is plugged in.'));
    };
    drivesRow.appendChild(again);
    drivesBlock.appendChild(drivesRow);
    settings.appendChild(drivesBlock);
  }
}

/* -- Preview ------------------------------------------------------------- */

function renderPreviewTabs(active) {
  const tabs = $('#preview-tabs');
  tabs.innerHTML = '';
  const options = [
    { key: 'guest', label: i18n.t('A guest') },
    { key: 'family', label: i18n.t('A family member') },
    ...state.people
      .filter((p) => p.role !== 'admin' && p.active)
      .map((p) => ({ key: `person:${p.id}`, label: p.name })),
  ];
  for (const option of options) {
    const button = el('button', `chip${option.key === active ? ' active' : ''}`,
      option.label);
    button.type = 'button';
    button.onclick = () => renderPreview(option.key);
    tabs.appendChild(button);
  }
}

async function renderPreview(key) {
  currentPreview = key;
  renderPreviewTabs(key);
  const box = $('#preview');
  box.replaceChildren(el('p', 'hint', i18n.t('Checking…')));
  const query = key.startsWith('person:')
    ? { person: key.split(':')[1] }
    : { role: key };
  let data;
  try {
    data = await adminApi.preview(query);
  } catch (exc) {
    box.innerHTML = '';
    box.appendChild(el('p', 'hint', exc.message));
    return;
  }

  box.innerHTML = '';
  const summary = el('div', 'preview-summary');
  summary.appendChild(el('strong', null, i18n.items(data.total)));
  // A role's name (Guest, Family member) is the server's English: said in
  // the page's language. A person's name is theirs, as it is.
  const who = key.startsWith('person:') ? data.as : i18n.t(data.role_label || data.as);
  const detail = [i18n.t('as {who}', { who })];
  if (data.scope) detail.push(i18n.t('limited to {folder}', { folder: data.scope }));
  summary.appendChild(el('span', 'hint', detail.join(' · ')));
  box.appendChild(summary);

  if (!data.total) {
    box.appendChild(el('p', 'empty-note',
      i18n.t('Nothing at all. Publish a folder on the Visibility tab to let them see something.')));
    return;
  }

  const strip = el('div', 'preview-strip');
  for (const item of data.items.slice(0, 12)) {
    const tile = el('div', 'preview-tile');
    if (item.has_thumb) {
      const img = el('img');
      img.src = thumbUrl(item.id, 256, item.thumb_v);
      img.alt = item.name;
      img.loading = 'lazy';
      tile.appendChild(img);
    } else {
      tile.appendChild(el('span', 'preview-glyph', '♪'));
    }
    tile.title = `${item.name} — ${item.folder || i18n.t('root')}`;
    strip.appendChild(tile);
  }
  box.appendChild(strip);
}

/* -- People -------------------------------------------------------------- */

async function loadPeople() {
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

function renderPeople() {
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

function closeDelete() {
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

function toggleAddPerson() {
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

/* -- Visibility ---------------------------------------------------------- */

// The three levels as the server names them, which is also how the tags on
// the page have always read.
const VISIBILITY_WORDS = {
  public: i18n.key('public'),
  family: i18n.key('family'),
  hidden: i18n.key('hidden'),
};

function visibilityWord(level) {
  return VISIBILITY_WORDS[level] ? i18n.t(VISIBILITY_WORDS[level]) : level;
}

async function loadFolders() {
  try {
    const data = await adminApi.folders();
    state.folders = data.folders;
    state.rootRule = data.root_rule;
  } catch (exc) {
    toast(exc.message, true);
    return;
  }
  renderFolderTree();
}

/* The tree opens on its top folders (a dated library's years) with everything
   below folded: every folder at once was thousands of rows to scroll past to
   reach one. A folder opened stays open while the page is, so setting one
   does not fold the tree back up around it. */
const openFolders = new Set();

function parentOf(path) {
  const cut = path.lastIndexOf('/');
  return cut < 0 ? '' : path.slice(0, cut);
}

function shownInTree(path) {
  for (let above = parentOf(path); above; above = parentOf(above)) {
    if (!openFolders.has(above)) return false;
  }
  return true;
}

/* Tree order, a folder's name at a time. Sorted as text, "2019 trip" came
   between "2019" and "2019/07", so an opened 2019 showed its months under the
   wrong folder. */
function byTreeOrder(a, b) {
  const left = a.path.split('/');
  const right = b.path.split('/');
  for (let i = 0; i < Math.min(left.length, right.length); i += 1) {
    const order = left[i].localeCompare(right[i], undefined, { numeric: true });
    if (order) return order;
  }
  return left.length - right.length;
}

function renderFolderTree() {
  const tree = $('#folder-tree');
  tree.innerHTML = '';
  const total = state.overview?.stats?.count || 0;

  tree.appendChild(folderRow({
    path: '', name: i18n.t('Whole library'), depth: 0, count: total,
    rule: state.rootRule,
  }));
  const parents = new Set(state.folders.map((folder) => parentOf(folder.path)));
  for (const folder of [...state.folders].sort(byTreeOrder)) {
    if (shownInTree(folder.path)) {
      tree.appendChild(folderRow(folder, parents.has(folder.path)));
    }
  }
  if (!state.folders.length) {
    tree.appendChild(el('p', 'hint', i18n.t('No folders indexed yet.')));
  }
}

function folderToggle(folder, hasChildren) {
  if (!hasChildren) return el('span', 'folder-toggle');
  const open = openFolders.has(folder.path);
  const toggle = el('button', 'folder-toggle');
  toggle.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>';
  toggle.type = 'button';
  toggle.setAttribute('aria-expanded', String(open));
  toggle.setAttribute('aria-label', open ? i18n.t('Collapse {folder}', { folder: folder.name || folder.path })
    : i18n.t('Expand {folder}', { folder: folder.name || folder.path }));
  toggle.onclick = () => {
    if (open) {
      // Folding a folder folds everything inside it, so it opens as it closed.
      for (const path of [...openFolders]) {
        if (path === folder.path || path.startsWith(`${folder.path}/`)) openFolders.delete(path);
      }
    } else {
      openFolders.add(folder.path);
    }
    renderFolderTree();
    $(`#folder-tree .folder-toggle[data-path="${CSS.escape(folder.path)}"]`)?.focus();
  };
  toggle.dataset.path = folder.path;
  return toggle;
}

function folderRow(folder, hasChildren = false) {
  const row = el('div', 'folder-row');
  // The library root has no buttons. There is no decision anybody makes about
  // *every photograph in the house* that is worth a control, and it was the
  // one place where a slip cost everything — so it is a summary line now, and
  // the server refuses the change even if something asks for it.
  const isRoot = !folder.path;
  if (isRoot) row.classList.add('is-root');

  const name = el('div', 'folder-name');
  name.style.paddingLeft = `${folder.depth * 16}px`;
  if (!isRoot) name.appendChild(folderToggle(folder, hasChildren));
  name.appendChild(el('span', null, folder.name || folder.path || i18n.t('Whole library')));
  const count = el('span', 'hint', ` ${folder.count}`);
  name.appendChild(count);
  if (folder.rule) name.appendChild(el('span', `tag ${folder.rule}`, visibilityWord(folder.rule)));
  row.appendChild(name);

  const mix = el('div', 'folder-mix');
  for (const [key, value] of [['public', folder.public], ['family', folder.family],
    ['hidden', folder.hidden]]) {
    if (!value) continue;
    const chunk = el('i', `mix ${key}`);
    chunk.style.flex = String(value);
    chunk.title = `${value} ${visibilityWord(key)}`;
    mix.appendChild(chunk);
  }
  row.appendChild(mix);

  if (isRoot) {
    row.appendChild(el('div', 'folder-note',
      i18n.t('Set on a folder below, or on the photographs themselves.')));
    return row;
  }

  const picker = el('div', 'vis-picker');
  for (const [value, text] of [['public', i18n.t('Everyone')], ['family', i18n.t('Family')],
    ['hidden', i18n.t('Nobody')]]) {
    const button = el('button', null, text);
    button.type = 'button';
    button.dataset.vis = value;
    if (folder.rule === value) button.classList.add('active');
    button.onclick = () => applyFolderVisibility(folder.path, value);
    picker.appendChild(button);
  }
  row.appendChild(picker);
  return row;
}

/* -- Library ------------------------------------------------------------- */

async function runScan(full) {
  try {
    await adminApi.rescan(full);
    toast(full ? i18n.t('Full re-index started.') : i18n.t('Scanning for changes…'));
    watchActivity();
  } catch (exc) { toast(exc.message, true); }
}

let browsePath = '';
let browseSelectable = false;

/**
 * The one folder picker in the console.
 *
 * The Library tab uses it to add a library folder, and so does the first-day
 * walkthrough. `pick` lets a caller take over what happens on Use, and
 * `anyFolder` relaxes the library's selectability check for a caller that
 * does not need it.
 */
let picker = null;

function openFolderPicker(options = {}) {
  picker = {
    start: options.start ?? (state.overview?.library?.root || ''),
    title: options.title || i18n.t('Choose a library folder'),
    cta: options.cta || i18n.t('Use this folder'),
    anyFolder: !!options.anyFolder,
    pick: options.pick || null,
  };
  $('#folder-modal').hidden = false;
  $('#fm-manual').value = '';
  $('#folder-modal h2').textContent = picker.title;
  $('#fm-apply').textContent = picker.cta;
  return loadBrowse(picker.start);
}

/* The typed path and the browsed folder are two ways of answering the same
   question, so both have to feed the same decision. Without this the text box
   is inert: typing a perfectly good path leaves "Use this folder" greyed out
   whenever the tree happens to be sitting on a folder that cannot be picked. */
function refreshApply() {
  const typed = $('#fm-manual').value.trim();
  const apply = $('#fm-apply');
  apply.disabled = !typed && !browseSelectable;
  apply.title = apply.disabled ? i18n.t('Pick a folder inside this one, or type a path') : '';
}

async function loadBrowse(path) {
  const list = $('#fm-dirs');
  const loading = el('p', 'hint', i18n.t('Loading…'));
  loading.style.padding = '12px';
  list.replaceChildren(loading);
  let data;
  try {
    data = await adminApi.browse(path);
  } catch (exc) {
    list.innerHTML = '';
    list.appendChild(el('p', 'gate-error', exc.message));
    return;
  }
  browsePath = data.path;
  browseSelectable = picker?.anyFolder ? true : data.selectable !== false;
  // Choosing from the tree is an answer, so it replaces whatever was typed
  // earlier — otherwise `typed || browsePath` submits the old string and the
  // folder the admin just clicked is silently ignored.
  $('#fm-manual').value = '';
  $('#fm-path').textContent = data.path;

  refreshApply();

  list.innerHTML = '';

  const folderIcon = '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/></svg>';
  const homeIcon = '<svg viewBox="0 0 24 24"><path d="M4 11 12 4l8 7v8a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1Z"/></svg>';

  // Shortcuts first: home, Pictures, and every drive on the machine, so
  // C:\Master is two clicks away instead of a climb from wherever we started.
  if (data.shortcuts?.length) {
    const bar = el('div', 'fm-shortcuts');
    for (const shortcut of data.shortcuts) {
      const chip = el('button', 'chip', shortcut.name || shortcut.path);
      chip.type = 'button';
      chip.title = shortcut.path;
      chip.onclick = () => loadBrowse(shortcut.path);
      bar.appendChild(chip);
    }
    list.appendChild(bar);
  }

  if (data.parent) {
    const up = el('button');
    up.type = 'button';
    up.innerHTML = homeIcon;
    up.appendChild(el('span', null, i18n.t('.. (up one level)')));
    up.onclick = () => loadBrowse(data.parent);
    list.appendChild(up);
  }
  for (const dir of data.dirs) {
    const button = el('button');
    button.type = 'button';
    button.innerHTML = folderIcon;
    button.appendChild(el('span', null, dir.name));
    button.onclick = () => loadBrowse(dir.path);
    list.appendChild(button);
  }
  if (!data.dirs.length) {
    list.appendChild(el('p', 'hint',
      browseSelectable
        ? i18n.t('No subfolders here — use this folder, or go back up.')
        : i18n.t('No subfolders here.')));
  }
}

async function applyRoot() {
  const typed = $('#fm-manual').value.trim();
  const path = typed || browsePath;
  if (!path) return;

  // A caller that supplied its own handler just wants the path back.
  if (picker?.pick) {
    const handler = picker.pick;
    $('#folder-modal').hidden = true;
    handler(path);
    return;
  }

  const button = $('#fm-apply');
  const label = button.textContent;
  button.disabled = true;
  button.textContent = i18n.t('Opening…');
  try {
    await adminApi.setRoot(path);
    $('#folder-modal').hidden = true;
    toast(i18n.t('Indexing the library…'));
    watchActivity();
    setTimeout(refresh, 1500);
  } catch (exc) {
    toast(exc.message, true);
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

/* -- Progress + toasts --------------------------------------------------- */

let lastPhase = null;

// The indexing bar on the Settings page, and the gallery refresh when a
// scan finishes. The strip in the heading is `onActivity` below, which
// shows this job alongside every other one that is running.
function onProgress(scan) {
  if (!scan) return;
  const strip = $('#scan-strip');
  if (strip) strip.hidden = !scan.running;
  if (scan.running) {
    const counter = scan.tag_total ? SCAN_COUNTS[scan.status] : null;
    const percent = counter
      ? Math.round((scan.tagged / scan.tag_total) * 100) : scan.percent;
    const counted = counter
      ? counter(scan.tagged.toLocaleString(), scan.tag_total.toLocaleString())
      : scan.message || i18n.t('Indexing {done} / {total}',
        { done: scan.processed.toLocaleString(), total: scan.total.toLocaleString() });
    const left = timeLeft(scan.eta);
    const line = left ? `${counted} · ${left}` : counted;
    const text = scan.folder ? `${line} · ${scan.folder}` : line;
    const fill = $('#scan-fill');
    if (fill) fill.style.width = `${percent}%`;
    const strapline = $('#scan-text');
    if (strapline) strapline.textContent = text;
  }
  const phase = `${scan.status}:${scan.added}:${scan.removed}:${scan.unreadable || 0}`;
  if (phase !== lastPhase && (scan.status === 'done' || scan.status === 'error')) {
    lastPhase = phase;
    if (scan.unreadable) toast(i18n.t('Some folders could not be read, so their photos were kept as they were. Check that this computer may read them, then rescan.'), true);
    refresh();
  }
}

// Everything running right now, in the heading, on whichever page is open —
// in Lite that is the scan. Each row goes through to the page that owns its
// job; a page this console does not have lands on the Overview instead.
//
// The same poll feeds the Library page's own bar: the activity answer
// carries the scan's counters, and where a server leaves them out they are
// asked for on their own.
function onActivity(activity) {
  const strip = $('#scan-live');
  if (strip) strip.hidden = !activity?.running;
  renderActivity($('#job-rows'), activity?.jobs || [],
                 (page) => showTab(page));
  if (activity?.scan) onProgress(activity.scan);
  else json('/api/status/scan').then(onProgress).catch(() => {});
}

let stopActivity = null;

/* Start (or restart, so a scan just asked for shows at once) the poll. */
function watchActivity() {
  stopActivity?.();
  stopActivity = subscribeActivity(onActivity);
}

/* --- bulk visibility: ask twice, and keep the way back ------------------ */

const VIS_WORDS = { public: i18n.key('everyone, guests included'),
                    family: i18n.key('family members and admins'),
                    hidden: i18n.key('admins only') };

function strong(text) {
  const node = document.createElement('strong');
  node.textContent = text;
  return node;
}

/* A translated sentence with nodes in it — a name in bold, say. The sentence
   is translated whole and the nodes are put where its placeholders fall, since
   the words either side of them are in a different order in another language.
   Plain values fill in as text. */
function emphasised(key, parts) {
  return i18n.t(key).split(/(\{\w+\})/).filter(Boolean).map((piece) => {
    const name = /^\{(\w+)\}$/.exec(piece)?.[1];
    if (!name || !(name in parts)) return piece;
    const part = parts[name];
    return part instanceof Node ? part : String(part);
  });
}

/* A count in a sentence, in the current language. `one` and `many` are keys
   marked with i18n.key() where they are written — "1 file" and "{count}
   files" — because the words around a number move with the language. */
function plural(n, one, many) {
  return n === 1 ? i18n.t(one) : i18n.t(many, { count: n.toLocaleString() });
}

/**
 * Apply a folder rule, stopping first if it would reveal anything.
 *
 * The server refuses a widening change that has not been confirmed, and says
 * how much it would expose. That refusal is the first question; this modal is
 * the second. One mis-click on a control whose three buttons sit a few pixels
 * apart should not be able to publish photographs somebody hid on purpose.
 */
async function applyFolderVisibility(path, value) {
  try {
    let result;
    let confirmed = false;
    // One question, asked only when it applies: a change that would reveal
    // something has to be confirmed after the reader has seen how much. The
    // whole-library password that used to live here went with the control it
    // guarded — the root is refused outright now, not asked about.
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        result = await adminApi.setFolderVisibility(path, value, confirmed);
        break;
      } catch (exc) {
        if (exc.status === 409 && exc.data?.needs_confirmation) {
          if (!await confirmExposure(exc.data.impact, path, value)) return;
          confirmed = true;
          continue;
        }
        throw exc;
      }
    }
    if (!result) return;
    toast(i18n.t('{items} → {level}.', { items: i18n.items(result.updated), level: visibilityWord(value) }));
    state.overview = await adminApi.overview();
    renderOverview();
    await loadFolders();
    await refreshUndo();
    renderPreview(currentPreview);
  } catch (exc) { toast(exc.message, true); }
}

function confirmExposure(impact, path, value) {
  return new Promise((resolve) => {
    const where = path ? `“${path}”` : i18n.t('the whole library');
    $('#expose-what').replaceChildren(
      ...emphasised(i18n.key('Setting {where} to {level} makes it visible to {who}.'), {
        where: strong(where),
        level: strong(visibilityWord(value)),
        who: VIS_WORDS[value] ? i18n.t(VIS_WORDS[value]) : value,
      }),
      ' ',
      ...emphasised(impact.exposed === 1
        ? i18n.key('{count} file is currently more private than that, and would become visible.')
        : i18n.key('{count} files are currently more private than that, and would become visible.'),
      { count: strong(Number(impact.exposed || 0).toLocaleString()) }),
    );

    // Name *why* they are private, because "I hid those myself" and "the
    // filesystem hid those" are different decisions to be second-guessing.
    const notes = [];
    if (impact.decided_individually) {
      notes.push(plural(impact.decided_individually,
        i18n.key('1 file was set individually and would be overwritten'),
        i18n.key('{count} files were set individually and would be overwritten')));
    }
    if (impact.hidden_by_the_filesystem) {
      notes.push(plural(impact.hidden_by_the_filesystem,
        i18n.key('1 file is hidden because of where it sits on disk'),
        i18n.key('{count} files are hidden because of where they sit on disk')));
    }
    if (impact.child_rules) {
      notes.push(plural(impact.child_rules,
        i18n.key('1 folder rule inside it would be overridden'),
        i18n.key('{count} folder rules inside it would be overridden')));
    }
    $('#expose-detail').textContent = notes.length ? `${notes.join('; ')}.` : '';
    $('#expose-detail').hidden = !notes.length;

    const modal = $('#expose-modal');
    modal.hidden = false;
    const close = (answer) => {
      modal.hidden = true;
      $('#expose-confirm').onclick = null;
      $('#expose-cancel').onclick = null;
      resolve(answer);
    };
    $('#expose-confirm').onclick = () => close(true);
    $('#expose-cancel').onclick = () => close(false);
  });
}

async function refreshUndo() {
  const strip = $('#vis-undo');
  if (!strip) return;
  try {
    const { changes } = await adminApi.visibilityHistory();
    const last = (changes || []).find((c) => !c.undone_at && c.restorable > 0);
    if (!last) { strip.hidden = true; delete strip.dataset.batch; return; }
    // The button undoes the change the strip names, not whatever is newest.
    strip.dataset.batch = String(last.id);
    const where = last.scope === 'folder'
      ? (last.folder ? `“${last.folder}”` : i18n.t('the whole library'))
      : i18n.items(last.affected);
    const level = VIS_NAMES_BY_VALUE[last.visibility];
    $('#vis-undo-what').textContent = i18n.t('Last change: {where} → {level}',
      { where, level: level ? visibilityWord(level) : last.visibility });
    $('#vis-undo-detail').textContent = last.exposed
      ? `${plural(last.exposed, i18n.key('1 file became visible to more people.'),
        i18n.key('{count} files became visible to more people.'))} ${
        i18n.t('Undo puts every file back exactly as it was.')}`
      : plural(last.restorable, i18n.key('1 file can be put back exactly as it was.'),
        i18n.key('{count} files can be put back exactly as they were.'));
    strip.hidden = false;
  } catch { strip.hidden = true; }
}

const VIS_NAMES_BY_VALUE = { 0: 'public', 1: 'family', 2: 'hidden' };

async function undoLastVisibility() {
  try {
    const named = Number($('#vis-undo')?.dataset.batch) || null;
    const result = await adminApi.undoVisibility(named);
    toast(plural(result.restored, i18n.key('Put 1 file back as it was.'),
      i18n.key('Put {count} files back as they were.')));
    state.overview = await adminApi.overview();
    renderOverview();
    await loadFolders();
    await refreshUndo();
    renderPreview(currentPreview);
  } catch (exc) { toast(exc.message, true); }
}

function toast(message, isError = false) {
  // Nothing to say while the screen is locked: nobody is there to read it,
  // and requests refused behind the lock would pile their errors up here.
  if (document.body.classList.contains('screen-locked')) return;
  const node = el('div', `toast${isError ? ' error' : ''}`, message);
  $('#toasts').appendChild(node);
  setTimeout(() => {
    node.style.opacity = '0';
    setTimeout(() => node.remove(), 250);
  }, isError ? 5200 : 3200);
}

function randomPassword() {
  const words = ['river', 'maple', 'copper', 'lantern', 'harbour', 'meadow',
    'cedar', 'amber', 'summit', 'willow', 'cobalt', 'ember'];
  const pick = () => words[Math.floor(Math.random() * words.length)];
  return `${pick()}-${pick()}-${Math.floor(10 + Math.random() * 89)}`;
}

/* ======================================================================== */

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

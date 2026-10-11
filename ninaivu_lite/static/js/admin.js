/**
 * The admin console (/admin, on the family app's own port).
 *
 * A management tool, not a gallery: profiles, folder visibility and library
 * controls — plus a live preview that answers the question an admin actually
 * has, which is "what will they see?"
 */

import {
  accountsApi, avatarNode, Gate, ProfileSheet,
} from './accounts.js';
import { onUnauthorized, sessionRestored } from './api.js';
import { ArchivePanel } from './archive.js';
import { wireDialogs } from './dialogs.js';
import { DrivePrompt } from './drives.js';
import { enterPressesTheButton } from './enter-key.js';
import { FirstDay } from './first-day.js';
import * as i18n from './i18n.js';
import { consoleCommands, initPalette } from './palette.js';
import { loadDuplicates, renderDuplicates } from './console/duplicates.js';
import { applyRoot, openFolderPicker, refreshApply } from './console/folder-picker.js';
import { renderLibrary, renderLibraryFolders } from './console/library.js';
import { currentPreview, renderOverview, renderPreview } from './console/overview.js';
import {
  closeDelete, loadAssignable, loadPeople, renderPeople, toggleAddPerson,
} from './console/people.js';
import { runScan, watchActivity } from './console/scan.js';
import { $, $$, adminApi, el, json, state, toast } from './console/shared.js';
import {
  loadFolders, refreshUndo, renderFolderTree, undoLastVisibility,
} from './console/visibility.js';

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
  // The console's dialogs (the first-day steps, the folder picker, the
  // sign-in screen, the profile sheet) behave like the family app's: the
  // page behind is inert, so Tab no longer walks out of the first-day steps
  // into the console underneath them.
  wireDialogs();

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

export async function refresh() {
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
  renderDuplicates();
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
  duplicates: i18n.key('The same photo or video kept in more than one place.'),
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

export function showTab(page) {
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
  if (name === 'duplicates') loadDuplicates();
  // The Import page polls while it is open and stops when it is left.
  if (name === 'archive') archivePanel?.show(); else archivePanel?.hide();
  if (name === 'overview') {
    // Visibility may have changed on another tab; re-ask rather than
    // showing a stale "what will they see" answer.
    renderPreview(currentPreview);
  }
}

/* ======================================================================== */

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

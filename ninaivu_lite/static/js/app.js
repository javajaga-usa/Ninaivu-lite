/** Application shell: startup, who is viewing, the chrome, and everything wired
    together. The gallery's parts are in gallery/. */

import { api, onUnauthorized, sessionRestored, setAnonymousViewer } from './api.js';
import * as i18n from './i18n.js';
import { Grid } from './grid.js';
import { Viewer } from './viewer.js';
import { MODES } from './layout.js';
import { accountsApi, avatarNode, Gate, ProfileSheet } from './accounts.js';
import { enterPressesTheButton } from './enter-key.js';
import { initPalette } from './palette.js';
import { wireDialogs } from './dialogs.js';
import { PosterMaker } from './posters.js';
import { wireJump } from './jump.js';
import { wireOnThisDay } from './on-this-day.js';
import { wireAlbums } from './gallery/albums.js';
import { bootDone, bootStep } from './gallery/boot.js';
import { checkConnection, markStarted, setServerOffline, setupNetworkStatusListeners } from './gallery/connection.js';
import {
  anyFilter, currentFilters, dropResults, loadMoreIfNear, paging, refreshCounts, refreshFacets,
  refreshStatus, reload, renderLibraryName,
} from './gallery/data.js';
import { clearFilters, watchSearchHint, wireSearch } from './gallery/filters.js';
import { forgetCachedShell, registerServiceWorker, setupAndroidInstallPrompt, setupIOSInstallPrompt } from './gallery/install.js';
import { wireKeyboard } from './gallery/keyboard.js';
import { wireMenu } from './gallery/menus.js';
import { buildScrubber, labelScrubber, positionScrubber, wireScrubber } from './gallery/scrubber.js';
import { wireSelection } from './gallery/selection.js';
import { wireSharing } from './gallery/sharing.js';
import {
  $, grid, setGrid, setJump, setOnThisDay, setViewer, state, store, viewer,
} from './gallery/state.js';
import { toast } from './gallery/toast.js';
import { syncViewerVisibility, wireVisibility } from './gallery/visibility.js';

let posters;

/* ========================================================================
   Boot
   ======================================================================== */

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  store.set('theme', theme);
  try { localStorage.setItem('ninaivu.theme', JSON.stringify(theme)); } catch { /* private */ }
}

window.applyTheme = applyTheme;
window.addEventListener('storage', (event) => {
  if (event.key === 'mv.theme' || event.key === 'ninaivu.theme') {
    try {
      const next = JSON.parse(event.newValue);
      if (next && ['system', 'light', 'dark'].includes(next)) {
        document.documentElement.dataset.theme = next;
        store.set('theme', next);
      }
    } catch {}
  }
});

let gate;
let profileSheet;

async function init() {
  // Enter in a field does what the button beside it does, everywhere.
  enterPressesTheButton();
  const initialTheme = store.get('theme', null)
    || (() => {
      try {
        const raw = localStorage.getItem('ninaivu.theme');
        return raw ? JSON.parse(raw) : null;
      } catch { return null; }
    })()
    || 'system';
  applyTheme(initialTheme);

  // Before the first paint anybody will notice: the page is marked up in
  // English and translated in place, so doing this after the gallery drew
  // would show a flicker of one language turning into another.
  await i18n.start();

  setGrid(new Grid($('#scroller'), $('#grid')));
  posters = new PosterMaker({
    onMade: (item) => {
      const swatch = item.color ? item.color[1] + item.color[3] + item.color[5] : '';
      grid.gotThumb(item.id, item.thumb_v, swatch);
      if (viewer?.cache?.has(item.id)) Object.assign(viewer.cache.get(item.id), item);
    },
  });
  grid.setMode(store.get('layout', 'justified'));
  grid.setZoom(store.get('zoom', 2));
  $('#zoom').value = String(grid.zoom);
  showLayout(grid.mode);

  setViewer(new Viewer($('#viewer')));
  viewer.toast = toast;
  viewer.onChange = () => { reload(); };

  wireChrome();
  wireGrid();
  wireViewer();
  wireKeyboard();
  initPalette();
  wireDialogs();

  gate = new Gate($('#gate'), {
    toast,
    onSignedIn: async (user) => {
      sessionRestored();
      forgetCachedShell();
      forgetPerson();
      // Signing in can change the name: this person may keep their own.
      try { applyHomeName(await accountsApi.state()); } catch { /* keep the house name */ }
      start(user);
    },
  });

  // The same thing that happens to the console happens here: a profile is
  // disabled or signed out from the console, and the tablet on the kitchen
  // wall keeps showing a gallery it can no longer load. Send it back to the
  // picker rather than leaving somebody tapping a dead screen.
  onUnauthorized(async () => {
    let authState = {};
    try {
      authState = await accountsApi.state();
    } catch {
      authState = {};
    }
    bootDone();
    forgetPerson();
    gate.show(authState, authState.setup_required ? 'setup' : 'picker');
  });
  profileSheet = new ProfileSheet($('#profile-sheet'), {
    toast,
    albumName: (id) => (state.albums || []).find((a) => a.id === id)?.name || '',
    onChange: async (user) => {
      state.user = user;
      renderIdentity();
      try { applyHomeName(await accountsApi.state()); } catch { /* leave it */ }
    },
  });

  wireAccounts();

  // Handy from the console, and what the performance suite measures against.
  window.__mv = { grid, viewer, state, reload, api };

  let auth;
  try {
    auth = await accountsApi.state();
  } catch {
    setServerOffline();
    return;
  }

  await finishStartup(auth);
}

/**
 * Put away everything the last person was looking at.
 *
 * Called whenever who is viewing changes — a session that ended under an open
 * photograph, somebody new picked on the sign-in screen, a guest tapping "Just
 * looking". The viewer used to stay open behind the gate with its photograph,
 * its GPS details and its filmstrip, and the arrow keys still moved through
 * the last person's photographs.
 */
function forgetPerson() {
  if (viewer?.isOpen) viewer.close();
  viewer?.cache?.clear();
  if (viewer?.filmstrip) viewer.filmstrip.innerHTML = '';
  if (viewer?.infoList) viewer.infoList.innerHTML = '';
  for (const overlay of document.querySelectorAll('.modal, .sheet')) overlay.hidden = true;
  dropResults();
  grid?.setData([], { resetScroll: true });
}

/** Everything that depends on knowing who the viewer is. */
/* What this viewer calls the home.

   The server has already resolved it — their own name if they set one, the
   household's otherwise — so there is no fallback chain duplicated here to
   drift out of step with the server's. */
export function applyHomeName(authState) {
  const given = (authState && (authState.home_name || authState.house_name)) || '';
  // "Ninaivu" is what the server answers when nobody has named the home: that
  // is the product's own name, not somebody's choice, so it is written in the
  // language the page is in (நினைவு), not shown as the English the server sent.
  state.homeNameGiven = Boolean(given) && given !== 'Ninaivu';
  const name = state.homeNameGiven ? given : i18n.t('Ninaivu');
  // The canonical form, which renderLibraryName compares against.
  state.homeName = state.homeNameGiven ? given : 'Ninaivu';

  // textContent, never innerHTML: this is something a family member typed.
  const brand = document.querySelector('.brand span');
  if (brand) brand.textContent = name;
  document.title = name;

  const frameName = document.querySelector('#kiosk-home');
  if (frameName) frameName.textContent = name;
  renderLibraryName();

  // The profile sheet shows the household name as its placeholder, so an
  // empty box reads as "using the household name" rather than "unset".
  // Ninaivu's own name (nobody named the home) is left out, so the sheet
  // writes it in the page's language (நினைவு) rather than "Ninaivu".
  const house = (authState && authState.house_name) || '';
  if (profileSheet) profileSheet.houseName = house === 'Ninaivu' ? '' : house;
}

// Nobody has named this home, so what the tab and the frame say is Ninaivu's
// own name, and it is written in the language the page is in. (A name somebody
// typed is left exactly as typed.) `state.homeName` is left alone on purpose:
// it is how the library box tells "named" from "not named".
i18n.onChange(() => {
  if (state.homeNameGiven) return;
  const own = i18n.t('Ninaivu');
  document.title = own;
  const frameName = document.querySelector('#kiosk-home');
  if (frameName) frameName.textContent = own;
});

export async function finishStartup(auth) {
  markStarted();
  applyHomeName(auth);
  registerServiceWorker();
  setupIOSInstallPrompt();
  setupAndroidInstallPrompt();
  watchSearchHint();
  setupNetworkStatusListeners();

  // First run has to happen on the admin console.
  if (auth.setup_required) {
    bootDone();
    gate.show(auth, 'setup');
    return;
  }
  // Nobody signed in yet: ask who's watching before loading anything.
  if (!auth.signed_in) {
    bootDone();
    gate.show(auth, 'picker');
    return;
  }
  await start(auth.user);
}

async function start(user) {
  state.user = user || { role: 'guest', anonymous: true, can: {} };
  // The language follows the person: what they chose once, on any device,
  // wins over what this browser asks for. A device with no choice saved
  // for this person keeps the browser's language until they pick one.
  if (state.user.language && state.user.language !== i18n.language()
      && i18n.LANGUAGES.some((l) => l.code === state.user.language)) {
    await i18n.use(state.user.language);
    showLanguage();
  }
  // Must be set before anything below fires a request: a guest hits a
  // family-gated 401 on the very first round of calls (see api.js).
  setAnonymousViewer(state.user.anonymous === true);
  renderIdentity();
  bootStep('signed-in');
  try {
    await refreshStatus();
  } catch (error) {
    console.error('status failed', error);
  }
  bootStep('status');
  // Permissions and the gallery must render even if one call above failed,
  // otherwise a single bad response leaves an empty, feature-less page.
  applyPermissions();
  // The counts are deliberately not awaited: they are the only call here
  // whose cost grows with the library, and nothing on screen needs them to
  // show a photograph. They fill the sidebar in when they arrive.
  refreshCounts();
  await Promise.all([reload(), refreshFacets()]);
  bootStep('photographs');
  bootDone();
  openAskedFor();

  if (state.user.must_change) {
    profileSheet.open(state.user);
    toast(i18n.t('Choose your own password to finish setting up.'));
  }
}

/* A photograph named in the address (`/?photo=123`, the console's "Open in
   gallery" beside each copy it lists) opens in the viewer once the gallery is
   up: among the photographs around it when it is in the grid, else alone.
   The server answers only for someone who may see it. */
async function openAskedFor() {
  const params = new URLSearchParams(window.location.search);
  const id = Number(params.get('photo'));
  if (!Number.isSafeInteger(id) || id <= 0) return;
  params.delete('photo');
  const rest = params.toString();
  history.replaceState(history.state, '', `${window.location.pathname}${rest ? `?${rest}` : ''}${window.location.hash}`);
  const index = grid.ids.indexOf(id);
  if (index >= 0) {
    viewer.open(grid.ids, index, grid.thumbedIds());
    return;
  }
  try {
    await viewer.fetchItem(id);
  } catch {
    toast(i18n.t('That photo is not in the library any more.'));
    return;
  }
  viewer.open([id], 0);
}

/* ========================================================================
   Identity and permissions
   ======================================================================== */

export function renderIdentity() {
  const user = state.user || {};
  const button = $('#profile-btn');
  button.innerHTML = '';
  const anonymous = user.anonymous !== false;

  // Shown to everybody: the menu behind it holds the language, the theme
  // and help, which somebody who has not signed in needs as much as anyone.
  button.hidden = false;
  $('#signin-btn').hidden = !anonymous;
  // Its sibling. Both ship hidden in the markup; this one had no line, so the
  // control existed, carried a handler, and could never be clicked.
  $('#switch-btn').hidden = anonymous;
  $('#profile-open').hidden = anonymous;

  const head = $('#profile-menu-head');
  head.textContent = '';
  head.hidden = anonymous;
  if (!anonymous) {
    button.appendChild(avatarNode(user, 30));
    button.title = `${user.name} · ${i18n.role(user.role_label)}`;
    button.setAttribute('aria-label', `${i18n.t('Profile and settings')} — ${user.name}`);
    const name = document.createElement('strong');
    name.textContent = user.name;
    const role = document.createElement('span');
    role.textContent = i18n.role(user.role_label);
    head.append(avatarNode(user, 36), name, role);
  } else {
    // Nobody to draw, so the outline of a person where the face would be.
    button.innerHTML = '<svg class="profile-anon" viewBox="0 0 24 24" aria-hidden="true">'
      + '<circle cx="12" cy="8" r="4"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/></svg>';
    button.title = i18n.t('Profile and settings');
    button.setAttribute('aria-label', i18n.t('Profile and settings'));
  }
}

/**
 * Show only what this role can actually use. The server refuses the rest
 * regardless; this keeps the interface from offering dead ends.
 */
function applyPermissions() {
  const can = state.user?.can || {};
  document.body.dataset.role = state.user?.role || 'guest';

  document.querySelectorAll('.admin-only').forEach((node) => {
    node.hidden = !can.set_visibility;
  });
  grid.showVisibility = !!can.set_visibility;
  // Library and people management live on the admin console; from here we
  // only offer the link to it.
  const consoleLink = $('#console-link');
  if (consoleLink) {
    consoleLink.hidden = !can.manage_library;
    // Ninaivu Lite serves the console on the same port, at /admin.
    consoleLink.href = '/admin';
  }

  // Favourites and downloads are family-and-above.
  $('[data-view="favorites"]').hidden = !can.favorite;
  $('#sel-fav').hidden = !can.favorite;
  $('#sel-unfav').hidden = !can.favorite;
  $('#sel-download').hidden = !can.download;
  viewer.canDownload = !!can.download;
  viewer.canSave = !!can.save_edits;
  viewer.canRotate = !!can.set_visibility;
  // Anyone signed in may make a photograph their own profile picture.
  viewer.canAvatar = !!state.user && !state.user.anonymous;
  // Video posters made by this browser: family and administrators only.
  posters.enabled = !!can.favorite;
  $('#v-fav').hidden = !can.favorite;
  $('#v-download').hidden = !can.download;
  // Albums and share links are for family members and admins.
  const albums = $('#albums-block');
  if (albums) albums.hidden = !can.favorite;
  $('#sel-album').hidden = !can.favorite;
  const viewerAlbum = $('#v-album');
  if (viewerAlbum) viewerAlbum.hidden = !can.favorite;
  const viewerShare = $('#v-share');
  if (viewerShare) viewerShare.hidden = !can.favorite;

  // Guests get a plain gallery: no scan strip, no jargon.
  if (!can.manage_library) $('#scan-strip').hidden = true;
}

/* ========================================================================
   Chrome
   ======================================================================== */

function wireChrome() {
  $('#sidebar-toggle').onclick = () => {
    const shell = document.querySelector('.shell');
    const button = $('#sidebar-toggle');
    // Two different classes carry "open" depending on layout — mobile draws
    // the sidebar as an overlay that defaults to closed, desktop draws it
    // inline and defaults to open — so the two branches invert what the
    // toggled class means. aria-expanded has to follow whichever one just
    // fired, not the other.
    if (window.innerWidth <= 900) {
      const expanded = shell.classList.toggle('mobile-open');
      button.setAttribute('aria-expanded', String(expanded));
    } else {
      const collapsed = shell.classList.toggle('collapsed');
      button.setAttribute('aria-expanded', String(!collapsed));
    }
  };

  document.querySelector('.shell')?.addEventListener('click', (event) => {
    const shell = document.querySelector('.shell');
    if (!shell?.classList.contains('mobile-open')) return;
    // A view picked in the drawer (a filter, an album, a year, a folder) is
    // what the person wants to see: the drawer gets out of its way, as a
    // tap beside it does. Other controls in it (+ album, sort) keep it open.
    const picked = event.target.closest('.side-item, .album-card, #year-list .chip, #folder-list button');
    if (!picked && (event.target.closest('.sidebar') || event.target.closest('#sidebar-toggle'))) return;
    shell.classList.remove('mobile-open');
    $('#sidebar-toggle')?.setAttribute('aria-expanded', 'false');
  });

  $('#theme-btn').onclick = cycleTheme;
  // One button, two languages: a picker would be more than this needs. If a
  // third is ever added this becomes a menu, and the label already says which
  // one you are about to get.
  const langButton = $('#lang-btn');
  if (langButton) langButton.onclick = async () => {
    const codes = i18n.LANGUAGES.map((l) => l.code);
    const next = codes[(codes.indexOf(i18n.language()) + 1) % codes.length];
    await i18n.use(next);
    showLanguage();
    rememberLanguage(next);
  };
  showLanguage();
  document.addEventListener('ninaivu:language', (event) => {
    showLanguage();
    rememberLanguage(event.detail);
  });
  $('#help-btn').onclick = () => ($('#help-modal').hidden = false);
  $('#about-btn').onclick = () => ($('#about-modal').hidden = false);
  $('#offline-retry-btn')?.addEventListener('click', () => checkConnection(true));

  // The topbar i18n.t("More") menu: everything that doesn't fit next to search and
  // profile/sign-out on a narrow screen (see the responsive CSS). Inert
  // above that width — #more-btn stays hidden there, so this never fires.
  const moreBtn = $('#more-btn');
  const moreMenu = $('#topbar-more');
  moreBtn.onclick = (event) => {
    event.stopPropagation();
    const open = moreMenu.classList.toggle('open');
    moreBtn.setAttribute('aria-expanded', String(open));
  };
  // A button inside the menu is a one-shot action (upload, map, a layout
  // choice, theme, help) — close the menu once it's picked rather than
  // leaving it sitting open over the gallery. The zoom slider isn't a
  // <button>, so dragging it doesn't trigger this.
  moreMenu.addEventListener('click', (event) => {
    if (event.target.closest('button')) {
      moreMenu.classList.remove('open');
      moreBtn.setAttribute('aria-expanded', 'false');
    }
  });
  document.addEventListener('click', (event) => {
    if (!moreMenu.classList.contains('open')) return;
    if (moreMenu.contains(event.target) || event.target === moreBtn) return;
    moreMenu.classList.remove('open');
    moreBtn.setAttribute('aria-expanded', 'false');
  });
  $('#help-close').onclick = () => ($('#help-modal').hidden = true);
  $('#about-close').onclick = () => ($('#about-modal').hidden = true);

  wireMenu($('#view-btn'), $('#view-menu'));
  wireMenu($('#profile-btn'), $('#profile-menu'));

  document.querySelectorAll('[data-layout]').forEach((button) => {
    button.onclick = () => setLayout(button.dataset.layout);
  });

  $('#zoom').oninput = (event) => {
    grid.setZoom(Number(event.target.value));
    store.set('zoom', grid.zoom);
  };

  document.querySelectorAll('[data-view]').forEach((button) => {
    button.onclick = async () => {
      state.view = button.dataset.view;
      document.querySelectorAll('[data-view]').forEach(
        (b) => b.classList.toggle('active', b === button),
      );
      document.querySelector('.shell').classList.remove('mobile-open');
      reload({ resetScroll: true });
    };
  });

  $('#sort').onchange = (event) => {
    state.filters.sort = event.target.value;
    reload({ resetScroll: true });
  };

  $('#clear-filters').onclick = clearFilters;

  wireSearch();
  wireSelection();
  wireScrubber();
  setJump(wireJump({
    grid,
    filters: currentFilters,
    hasMore: () => paging.next != null,
    loadMore: () => loadMoreIfNear({ force: true }),
  }));
  setOnThisDay(wireOnThisDay({
    wanted: () => state.view === 'all' && !anyFilter(),
    open: (ids, index, thumbed) => viewer.open(ids, index, thumbed),
    seeAll: (day) => {
      state.filters.onThisDay = day;
      reload({ resetScroll: true });
    },
  }));
  wireVisibility();
  wireSharing();
  wireAlbums();

  document.querySelectorAll('.modal').forEach((modal) => {
    modal.addEventListener('click', (event) => {
      if (event.target === modal) modal.hidden = true;
    });
  });
}

export function setLayout(mode) {
  if (!MODES.includes(mode)) return;
  grid.setMode(mode);
  store.set('layout', mode);
  showLayout(mode);
  buildScrubber();
}

/** Mark the layout in use, and put its icon on the view button. */
function showLayout(mode) {
  let current = null;
  document.querySelectorAll('[data-layout]').forEach((button) => {
    const on = button.dataset.layout === mode;
    button.classList.toggle('active', on);
    button.setAttribute('aria-pressed', String(on));
    if (on) current = button;
  });
  const icon = $('#view-btn-icon');
  const svg = current?.querySelector('svg');
  if (icon && svg) icon.replaceChildren(svg.cloneNode(true));
}


/** Save the language on the profile, so every device of theirs follows. */
async function rememberLanguage(code) {
  if (!state.user || state.user.anonymous || !state.user.id) return;
  try {
    state.user = await accountsApi.updateMe({ language: code });
  } catch { /* this device still remembers it */ }
}

/** The label on the language button: what you get if you press it. */
function showLanguage() {
  const label = $('#lang-label');
  if (!label) return;
  const codes = i18n.LANGUAGES.map((l) => l.code);
  const next = i18n.LANGUAGES[(codes.indexOf(i18n.language()) + 1) % codes.length];
  // One letter, not the word: the full name stays in the tooltip and the
  // accessible name, in its own language.
  label.textContent = next.letter;
  label.lang = next.code;             // so "அ" is drawn and read as Tamil
  const button = $('#lang-btn');
  if (button) {
    button.title = next.name;
    button.setAttribute('aria-label', next.name);
  }
}

export function cycleTheme() {
  const order = ['system', 'light', 'dark'];
  const current = document.documentElement.dataset.theme || 'system';
  applyTheme(order[(order.indexOf(current) + 1) % order.length]);
  toast(i18n.t('Theme: {theme}', { theme: document.documentElement.dataset.theme }));
}
window.cycleTheme = cycleTheme;

/* -- accounts ----------------------------------------------------------- */

function wireAccounts() {
  $('#profile-open').onclick = () => profileSheet.open(state.user);
  $('#signin-btn').onclick = async () => {
    gate.show(await accountsApi.state(), 'picker');
  };
  $('#switch-btn').onclick = async () => {
    forgetCachedShell();
    await accountsApi.logout();
    window.location.reload();
  };
  $('#profile-sheet').addEventListener('click', (event) => {
    if (event.target === $('#profile-sheet')) $('#profile-sheet').hidden = true;
  });
}

/* ========================================================================
   Grid / viewer wiring
   ======================================================================== */

function wireGrid() {
  grid.addEventListener('open', (event) => {
    viewer.open(grid.ids, event.detail.index, grid.thumbedIds());
  });

  grid.addEventListener('selection', (event) => {
    const count = event.detail.ids.length;
    // The bar comes and goes above the grid. On a phone it is two lines tall,
    // and the long press that brings it would otherwise slide the photograph
    // out from under the finger: the grid scrolls by what the bar took.
    const scroller = $('#scroller');
    const before = scroller.getBoundingClientRect().top;
    $('#selection-bar').hidden = count === 0;
    $('#sel-count').textContent = i18n.t('{count} selected', { count });
    const moved = scroller.getBoundingClientRect().top - before;
    if (moved) scroller.scrollTop += moved;
  });

  grid.addEventListener('scroll', (event) => {
    positionScrubber(event.detail.ratio);
    loadMoreIfNear({ top: event.detail.top, view: event.detail.height });
    labelScrubber(event.detail.section);
  });

  grid.addEventListener('layout', () => buildScrubber());
  grid.addEventListener('poster', (event) => posters.offer(event.detail.id));
}

function wireViewer() {
  viewer.addEventListener('change', () => syncViewerVisibility());
  viewer.addEventListener('poster-source', (event) => posters.fromElement(event.detail.id, event.detail.video));
  viewer.addEventListener('avatar', async (event) => {
    try {
      state.user = await accountsApi.setAvatar(event.detail.item.id);
      renderIdentity();
      toast(i18n.t('Your profile picture is set.'));
    } catch (exc) {
      toast(exc.message, true);
    }
  });

  viewer.addEventListener('mutated', async (event) => {
    const { id, favorite, rotation } = event.detail;
    // A turned photograph has a new shape and a new thumbnail: the grid is
    // laid out again rather than patched.
    if (rotation !== undefined) { reload(); return; }
    if (favorite !== undefined) {
      const cell = grid.layout.cells.find((c) => c.id === id);
      if (cell) {
        cell.flags = favorite ? cell.flags | 1 : cell.flags & ~1;
        grid.refreshCell(id);
      }
    }
    refreshStatus();
  });

  // "Photos taken nearby" in a photograph's details: the grid, filtered to
  // within a kilometre of where it was taken.
  viewer.addEventListener('nearby', (event) => {
    const { item } = event.detail;
    viewer.close();
    state.filters.near = item.id;
    state.filters.nearName = item.name;
    reload({ resetScroll: true });
  });

  viewer.addEventListener('jump', async (event) => {
    const index = grid.indexOfId(event.detail.id);
    if (index >= 0) {
      viewer.goTo(index);
    } else {
      viewer.ids = [event.detail.id, ...viewer.ids];
      viewer.goTo(0);
    }
  });

  // The grid under an open photo fetches nothing ahead: the photo needs the connection.
  viewer.addEventListener('open', () => grid.hold('viewer', true));
  viewer.addEventListener('close', (event) => {
    grid.hold('viewer', false);
    const index = grid.indexOfId(event.detail.id);
    if (index >= 0) {
      grid.cursor = index;
      grid.scrollToIndex(index);
      grid.render();
    }
  });
}

/* ======================================================================== */

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

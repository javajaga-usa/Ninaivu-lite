/** Loading the gallery: the status, the counts, the facets and the photographs in pieces. */

import { api } from '../api.js';
import { mergeSegments } from '../grid.js';
import * as i18n from '../i18n.js';
import { renderIdentity } from '../app.js';
import { loadAlbums } from './albums.js';
import { isNetworkFailure, serverIsOffline, setServerOffline } from './connection.js';
import { clearFilters, renderFilterBar, renderFolders, renderYears } from './filters.js';
import { kickActivity, noteCountsAsked } from './progress.js';
import { buildScrubber } from './scrubber.js';
import { $, grid, onThisDay, state } from './state.js';
import { toast } from './toast.js';

let searchController = null;
/**
 * Where the gallery's result set continues. A library too large for one
 * request arrives in pieces: `next` is the offset of the next piece, or null
 * when everything is held. Replaced wholesale by every reload, so a piece that
 * arrives for a query no longer on screen can tell it is stale.
 */
export let paging = { next: null, filters: null, signal: null, loading: false };
/** Items per piece as the grid is scrolled; about a quarter of a second each. */
const GALLERY_PAGE = 25000;
/** The most one request may ask for (the server's own cap). */
const GALLERY_MAX_PAGE = 200000;

/** Drop the result set and any piece of it still on its way. */
export function dropResults() {
  searchController?.abort();
  paging = { next: null, filters: null, signal: null, loading: false };
  showLoadMoreFailed(null);
}

/**
 * Say when a library folder is not plugged in.
 *
 * Photographs on an unplugged drive stay in the index, so the gallery is
 * neither empty nor wrong — it is short, which looks exactly like photographs
 * having gone missing. Ninaivu knew which folder was away and said nothing;
 * this is the saying.
 */
export function showAwayLibraries(status) {
  const banner = $('#away-banner');
  if (!banner) return;
  const away = Number(status?.libraries_away || 0);
  banner.hidden = !away;
  if (!away) return;
  const paths = status.libraries_away_paths || [];
  const text = away === 1
    ? i18n.t('One of your library folders is not connected. Its photographs are still listed but cannot be opened until it is back.')
    : i18n.t('{count} of your library folders are not connected. Their photographs are still listed but cannot be opened until they are back.',
      { count: away });
  // The paths only come back for an admin, and are the useful part when they do.
  $('#away-banner-text').textContent = paths.length
    ? `${text} (${paths.join(', ')})` : text;
}


export async function refreshStatus() {
  noteCountsAsked();
  try {
    state.status = await api.status();
    state.statusFailed = false;
  } catch (err) {
    // Remembered so the gallery can tell "the status call failed" from "no
    // library folder yet": with no status, it used to say the latter.
    state.statusFailed = true;
    if (isNetworkFailure(err)) {
      setServerOffline();
      return;
    }
    toast(i18n.t('Cannot reach the server.'), true);
    return;
  }
  showAwayLibraries(state.status);
  const { stats, user } = state.status;
  if (user) {
    state.user = user;
    renderIdentity();
  }

  renderLibraryName();
  // The quick status call leaves the counts out (they are a whole-library
  // count); after a favourite or a bulk change they are asked for on their
  // own, so the sidebar's numbers follow the change instead of the next reload.
  if (stats) renderCounts(stats); else refreshCounts();

  // The strip is the activity poll's to draw — /api/status carries the scan
  // but knows nothing about the other jobs, and half a list is worse than a
  // list that arrives a moment later. This starts that loop, or restarts it
  // after the tab was hidden or the server went away and came back.
  kickActivity();
}

/* The library card's name.

   The server sends a folder's name ("library") or, to an admin, its whole
   path, which is a fact about a disk rather than a name for anybody's
   photographs. The card says what the home is called when somebody has named
   it, and otherwise the folder's own name written as a name ("Library"); the
   path itself stays in the tooltip. */
export function renderLibraryName() {
  const box = $('#root-path');
  if (!box || !state.status) return;
  const { root, root_label: label } = state.status;
  const raw = label || root || '';
  // "Ninaivu" is what the server answers when nobody has named the home.
  const named = state.homeName && state.homeName !== 'Ninaivu' ? state.homeName : '';
  box.textContent = raw ? (named || libraryName(raw)) : i18n.t('No folder selected');
  box.title = root || label || '';
  box.classList.toggle('is-name', Boolean(raw));
}

/** "library" -> "Library", "/srv/family_photos" -> "Family Photos"; the two
 *  labels the server writes as sentences are translated rather than cased. */
export function libraryName(raw) {
  const several = /^(\d+) library folders$/.exec(raw);
  if (several) return i18n.t('{count} library folders', { count: several[1] });
  if (raw === 'Your library') return i18n.t('Your library');
  const last = raw.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || raw;
  return last.replace(/[_]+/g, ' ').replace(/(^|\s)(\p{Ll})/gu,
    (_, space, letter) => space + letter.toUpperCase());
}

/* The sidebar's numbers.
 *
 * They arrive after the photographs now, because counting a library is the
 * one piece of work here that grows with it: on 196,174 items with a cold
 * cache it took twenty-three seconds, and the gallery used to wait for all
 * of it before asking for a single picture. Nothing here is needed to draw
 * one, so it is fetched alongside and filled in when it lands.
 */
export function renderCounts(stats) {
  if (!stats || typeof stats.count !== 'number') return;
  state.lastCounts = stats;
  $('#root-meta').textContent =
    `${i18n.items(stats.count)} · ${humanBytes(stats.bytes)}`;
  $('#count-all').textContent = stats.count.toLocaleString();
  $('#count-fav').textContent = stats.favorites || '';
  $('#count-pic').textContent = stats.pictures || '';
  $('#count-vid').textContent = stats.videos || '';
  $('#count-hidden').textContent = stats.hidden || '';
  $('#count-public').textContent = stats.public || '';
}

export async function refreshCounts() {
  try {
    const body = await api.counts();
    if (body?.stats) {
      state.status = { ...(state.status || {}), stats: body.stats };
      renderCounts(body.stats);
    }
  } catch { /* the numbers are a nicety; the photographs are not */ }
}

export async function refreshFacets() {
  if (!state.status?.has_library) return;
  try {
    state.facets = await api.facets();
  } catch { return; }
  renderYears();
  renderFolders();
  loadAlbums();
}

export function currentFilters() {
  const filters = { ...state.filters };
  if (state.view === 'favorites') filters.favorites = true;
  if (state.view === 'pictures') filters.kinds = ['picture'];
  if (state.view === 'videos') filters.kinds = ['video'];
  if (state.view === 'hidden') filters.visibility = 'hidden';
  if (state.view === 'public') filters.visibility = 'public';
  return filters;
}

/**
 * `resetScroll: true` is for callers whose change means the *query* is
 * different — a search, a filter, a sort order, a different view — where
 * whatever the grid was scrolled to almost certainly is not in the new
 * results at all. Left false (the default), the grid keeps its scroll
 * position, which is what an in-place refresh wants: a bulk edit, a
 * background rescan, a viewer mutation syncing back. See Grid.setData().
 */
export async function reload({ resetScroll = false } = {}) {
  if (!state.status && state.statusFailed) {
    if (!serverIsOffline) {
      showEmpty(i18n.t('Could not load media'), i18n.t('Cannot reach the server.'), { retry: true });
    }
    return;
  }
  if (!state.status?.has_library) {
    showEmpty(
      i18n.t('No library folder yet'),
      state.user?.can?.manage_library
        ? i18n.t('Choose a folder and Ninaivu will index it in the background.')
        : i18n.t('An admin has not set up the library yet.'),
    );
    return;
  }
  setLoading(true);
  searchController?.abort();
  searchController = new AbortController();
  const signal = searchController.signal;
  const filters = currentFilters();
  // An in-place refresh must hand back as much as was already held, or the
  // grid shrinks under a person scrolled deep into it and loses their place.
  const held = resetScroll ? 0 : grid.layout.cells.length;
  paging = { next: null, filters, signal, loading: false };

  try {
    const data = await api.segments(filters, signal, {
      limit: Math.min(GALLERY_MAX_PAGE, Math.max(GALLERY_PAGE, held)),
    });
    const segments = data.segments;
    let { next_offset: next } = data;
    while (next != null && next < held) {
      const more = await api.segments(filters, signal, {
        limit: Math.min(GALLERY_MAX_PAGE, held - next), offset: next,
      });
      mergeSegments(segments, more.segments);
      next = more.next_offset;
    }
    state.semantic = false;
    paging.next = next;
    grid.setData(segments, { resetScroll });
    buildScrubber();
    showTruncation({ ...data, next_offset: next });

    if (!data.total && state.user?.anonymous && !anyFilter()) {
      // A guest, with nothing marked public: there is no filter to clear, and
      // "This filter has no items yet" sent them looking for one.
      showEmpty(i18n.t('Nothing shared with guests yet'),
        i18n.t('Sign in to see the family’s photographs.'), { signIn: true });
    } else if (!data.total) {
      showEmpty(
        state.filters.q ? i18n.t('No matches') : i18n.t('Nothing to show'),
        state.filters.q
          ? i18n.t('Nothing matched “{q}”. Try fewer words, or part of a file or folder name.', { q: state.filters.q })
          : i18n.t('This filter has no items yet.'),
      );
    } else {
      $('#empty').hidden = true;
    }
  } catch (error) {
    if (error.name !== 'AbortError') {
      if (isNetworkFailure(error)) {
        setServerOffline();
      } else {
        toast(error.message || i18n.t('Could not load media'), true);
      }
    }
  } finally {
    setLoading(false);
  }
  renderFilterBar();
  onThisDay?.refresh();
  loadMoreIfNear();
}

/**
 * Fetch the next piece once the scroll is within a few screens of the end.
 * `force` fetches it now wherever the scroll is (the "Jump to" button going
 * to a month further down), waiting for a piece already on its way. True
 * when a piece came.
 */
export async function loadMoreIfNear({ force = false, top = null, view: height = null } = {}) {
  const page = paging;
  if (page !== loadMoreFailed.page) showLoadMoreFailed(null);
  if (page.next == null || !page.signal || page.signal.aborted) return false;
  if (page.loading) {
    if (!force) return false;
    await page.loading.catch(() => {});
    return page === paging;
  }
  // After a failure, wait out the back-off rather than asking again on every
  // scroll frame; the Retry row (or the timer) clears it.
  if (!force && page.retryAt && Date.now() < page.retryAt) return false;
  // From the grid's own frame when it says (reading them from the page
  // after the tiles were put on it lays the page out again, every frame).
  const view = (height ?? grid.scroller.clientHeight) || 1;
  const remaining = grid.layout.height - ((top ?? grid.scroller.scrollTop) + view);
  // As far ahead as the grid is fetching pictures: a fast scroll asks sooner.
  if (!force && remaining > Math.max(view * 3, grid.lookahead + view)) return false;

  try {
    page.loading = api.segments(page.filters, page.signal, {
      limit: GALLERY_PAGE, offset: page.next,
    });
    const data = await page.loading;
    if (page !== paging) return false;     // a reload replaced this result set
    page.next = data.next_offset;
    page.retryAt = 0;
    page.retryDelay = 0;
    showLoadMoreFailed(null);
    grid.appendData(data.segments);
    showTruncation(data);
  } catch (error) {
    if (error.name === 'AbortError' || page !== paging) return false;
    if (isNetworkFailure(error)) setServerOffline();
    else toast(error.message || i18n.t('Could not load more media'), true);
    // One failed piece used to end the gallery for the session (`next` was
    // set to null). It is kept, and asked for again after a growing pause, or
    // at once from the Retry row at the foot of the grid.
    page.retryDelay = Math.min(LOAD_MORE_MAX_DELAY, (page.retryDelay || 1000) * 2);
    page.retryAt = Date.now() + page.retryDelay;
    showLoadMoreFailed(page);
    setTimeout(() => { if (page === paging) loadMoreIfNear(); }, page.retryDelay);
    return false;
  } finally {
    page.loading = null;
  }
  // A short piece may still leave the screen unfilled.
  if (page === paging) loadMoreIfNear();
  return true;
}

/** The longest pause between automatic retries of a failed piece. */
export const LOAD_MORE_MAX_DELAY = 60000;
export const loadMoreFailed = { page: null };

/** The "Couldn't load more · Retry" row under the grid; null hides it. */
export function showLoadMoreFailed(page) {
  loadMoreFailed.page = page;
  const row = $('#load-more-failed');
  if (!row) return;
  row.hidden = !page;
  const retry = $('#load-more-retry');
  if (retry && !retry.onclick) {
    retry.onclick = () => {
      const current = loadMoreFailed.page;
      if (!current || current !== paging) return;
      current.retryAt = 0;
      loadMoreIfNear();
    };
  }
}

// The grid lays out at most one request's worth of items. A library larger
// than that used to end the scroll with no sign anything was missing, which on
// a million photographs reads exactly like losing the older nine-tenths.
export function showTruncation(data) {
  const notice = $('#truncated-notice');
  if (!notice) return;
  // A result set that continues is not cut short — it is still arriving.
  if (!data.truncated || data.next_offset != null) {
    notice.hidden = true;
    return;
  }
  const which = { date_desc: i18n.key('Showing the newest {shown} of {total}. Pick a year, a folder or a search to see the rest.'),
    date_asc: i18n.key('Showing the oldest {shown} of {total}. Pick a year, a folder or a search to see the rest.') }[state.filters.sort || 'date_desc']
    || i18n.key('Showing the first {shown} of {total}. Pick a year, a folder or a search to see the rest.');
  notice.textContent = i18n.t(which, {
    shown: Number(data.returned).toLocaleString(i18n.locale()), total: i18n.items(data.total) });
  notice.hidden = false;
}

export function setLoading(on) {
  state.loading = on;
  $('#skeleton').classList.toggle('on', on && !grid.layout.cells.length);
  if (on && !grid.layout.cells.length) {
    $('#skeleton').innerHTML = '<i></i>'.repeat(18);
    if (!serverIsOffline) $('#empty').hidden = true;
  }
}

/** Whether anything narrows the gallery: search, a chip, a kind, favourites. */
export function anyFilter() {
  const f = state.filters;
  return Boolean(f.q || f.folder || f.camera || f.from || f.to
    || f.album || f.favorites || f.kinds?.length || f.onThisDay || f.near);
}

export function showEmpty(title, text, { signIn = false, retry = false } = {}) {
  const empty = $('#empty');
  empty.classList.remove('offline');
  empty.hidden = false;
  $('#empty-title').textContent = title;
  $('#empty-text').textContent = text;
  const action = $('#empty-action');
  const needsFolder = !state.status?.has_library;
  const canFix = state.user?.can?.manage_library;
  if (signIn) {
    action.hidden = false;
    action.textContent = i18n.t('Sign in');
    action.onclick = () => $('#signin-btn').click();
  } else if (retry) {
    action.hidden = false;
    action.textContent = i18n.t('Try again');
    action.onclick = async () => {
      await refreshStatus();
      if (state.status) await Promise.all([reload(), refreshFacets()]);
      else reload();
    };
  } else if (needsFolder) {
    // Setting the library up happens on the console — send them there.
    action.hidden = !canFix;
    action.textContent = i18n.t('Open the admin console');
    action.onclick = () => { window.open('/admin', '_blank'); };
  } else {
    action.hidden = false;
    action.textContent = i18n.t('Clear filters');
    action.onclick = clearFilters;
  }
}

export function humanBytes(n) {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let value = n;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return `${i === 0 ? value : value.toFixed(1)} ${units[i]}`;
}

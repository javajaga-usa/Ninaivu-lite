/**
 * Each view remembers its place, as a phone's photo app does: All media, an
 * album, a folder, a search. Going back to it, reloading the page or opening
 * the app again lands on the same photograph at the top of the screen. Kept
 * on this device only (the last PLACES_MAX views), per person.
 * (From Ninaivu 1.10.10.)
 */

import { grid, state, store } from './state.js';

const PLACES_KEY = 'places';
const PLACES_MAX = 40;

/** The key of the view on screen, as reload() last loaded it. */
let shown = null;
let timer = 0;
/** The first gallery of a visit returns to its place too. */
let due = true;

function placeKey(filters) {
  return JSON.stringify([state.user?.id ?? 0, state.view, filters]);
}

/** Note where the screen is in the view on screen. */
export function rememberPlace() {
  clearTimeout(timer);
  if (!shown || !grid?.layout?.cells?.length) return;
  const at = grid.anchor();
  if (!at) return;
  const key = shown;
  const places = store.get(PLACES_KEY, []).filter((p) => p && p.k !== key);
  places.push({ k: key, id: at.id, offset: at.offset });
  store.set(PLACES_KEY, places.slice(-PLACES_MAX));
}

/**
 * The grid has just been given *filters*' results. A view opened anew
 * (`fresh`: a different view, filter or search) or the first of a visit goes
 * back to where it was left; an in-place refresh keeps its scroll as it is.
 */
export function placeLoaded(filters, fresh) {
  const key = placeKey(filters);
  shown = key;
  if (!fresh && !due) return;
  // Nothing laid out yet (a hidden page): try again with the next load.
  if (!grid.layout.cells.length) return;
  due = false;
  const place = store.get(PLACES_KEY, []).find((p) => p && p.k === key);
  if (place && place.id != null) grid.scrollToAnchor(place);
}

/** A different view is about to replace the one on screen: its place is
 *  noted now, while the grid still shows it. */
export function leavingView() {
  rememberPlace();
  shown = null;
}

/** Someone else is using the page now: their places, not the last person's. */
export function forgetShown() {
  rememberPlace();             // under the last person's own key
  shown = null;
  due = true;
}

export function wirePlaces() {
  grid.addEventListener('scroll', () => {
    clearTimeout(timer);
    timer = setTimeout(rememberPlace, 500);
  });
  // Leaving the page, or the phone putting it away, keeps the place too.
  window.addEventListener('pagehide', rememberPlace);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') rememberPlace();
  });
}

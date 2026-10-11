/** What the gallery's modules share: the page's state, and the grid and viewer. */

export const $ = (sel) => document.querySelector(sel);
export const store = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem(`mv.${key}`);
      return raw === null ? fallback : JSON.parse(raw);
    } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(`mv.${key}`, JSON.stringify(value)); } catch { /* private mode */ }
  },
};

export const state = {
  user: null,
  view: 'all',
  filters: { q: '', kinds: [], favorites: false,
             folder: '', camera: '', from: '', to: '', album: 0, sort: 'date_desc',
             onThisDay: '', near: 0, nearName: '' },
  albums: [],
  status: null,
  facets: null,
  semantic: false,
  loading: false,
};

/* The grid and the viewer are made once, by init() in app.js. They are live
   bindings: a module that imports them sees them as soon as they are made. */
export let grid;
export let viewer;
export function setGrid(value) { grid = value; }
export function setViewer(value) { viewer = value; }

//: The "Jump to" button over the grid (jump.js).
export let jump = null;
//: The "On this day" row over the timeline (on-this-day.js).
export let onThisDay = null;
export function setJump(value) { jump = value; }
export function setOnThisDay(value) { onThisDay = value; }

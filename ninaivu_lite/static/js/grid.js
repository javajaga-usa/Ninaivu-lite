/**
 * Virtualised gallery grid.
 *
 * Only the cells intersecting the viewport (plus an overscan margin) exist in
 * the DOM. Nodes are recycled through a pool, positions are applied with
 * `transform`, and scroll handling is coalesced into a single rAF per frame,
 * so scrolling a 100k-item library costs the same as scrolling 200 items.
 */

import { computeLayout, visibleRange, sectionAt, targetSize } from './layout.js';
import { thumbUrl } from './api.js';
import * as i18n from './i18n.js';

const OVERSCAN = 900;      // px rendered above and below the viewport
const POOL_LIMIT = 400;
/** How long a thumbnail may take before the tile admits to loading. */
const SHIMMER_DELAY = 120;

// Scrolling fast. The band of tiles kept ready grows ahead of the scroll with
// its speed, so a flick finds its pictures already asked for, and shrinks
// behind it, where nothing is going to be looked at.
/** How far ahead, in time, the band reaches: speed × this. */
const LOOKAHEAD_MS = 450;
/** …but never more than this many screens. */
const MAX_AHEAD_SCREENS = 3;
/** The band left behind a fast scroll. */
const TRAILING = 300;
/** Faster than this (px/ms: 5,000 px a second) is flying past rather than
 *  looking: the band behind it shrinks and less is kept ready that way. */
const FLING = 5;
/** No scroll for this long and it has stopped: sharpen, and look ahead. */
const SETTLE_MS = 140;
/** A picture arriving within this of its tile being made is shown at once,
 *  with no fade: it was already here, or as good as. Only a tile that waited
 *  fades its picture in. */
const INSTANT_MS = 90;
/** Screens fetched ahead of where a scroll stopped, in its direction. */
const PREFETCH_SCREENS = 1.5;

// Thumbnails fetched ahead of the scroll while it moves, beyond the tiles
// already on the page: as far as the scroll will travel in the next few
// seconds at its present speed, so a faster scroll looks further ahead and a
// slow or stopped one less. A little is kept ready the other way too, so
// turning back finds pictures waiting.
/** How far ahead, in time, the pictures are fetched: speed × this. */
const PRELOAD_SECONDS = 2.5;
/** …never less than this many screens ahead, even standing still… */
const PRELOAD_MIN_SCREENS = 1.5;
/** …nor more than this many, however fast. */
const PRELOAD_MAX_SCREENS = 12;
/** The other way, in screens: less while flying past. */
const PRELOAD_BEHIND_SCREENS = 0.75;
const PRELOAD_BEHIND_FAST = 0.25;
/** At most this many pictures waiting to be fetched ahead. */
const PRELOAD_MAX = 160;
/** How often the list ahead is worked out again while scrolling. */
const PRELOAD_EVERY_MS = 120;
/** Faster than this (px/ms), two frames running, is a jump — the scrollbar
 *  dragged, End pressed — not a scroll: nothing on the way will be looked at,
 *  so nothing is fetched until it lands. The scrubber says so itself. */
const JUMP = 25;
/** Tiles off the screen put on the page in one frame; the rest follow in the
 *  next, so a fast scroll on a slow computer does not stall a frame. */
const MOUNT_BUDGET = 24;

const KIND_NAMES = ['picture', 'video', 'audio'];

const LONG_PRESS_MS = 450;

const ICON = {
  video: '<svg viewBox="0 0 24 24"><path d="m8 5 11 7-11 7Z" fill="currentColor" stroke="none"/></svg>',
  audio: '<svg viewBox="0 0 24 24"><path d="M9 18V6l11-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/></svg>',
  // Only ever a glyph: a photograph with no picture to show, which is almost
  // always a damaged file.
  picture: '<svg viewBox="0 0 24 24"><rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m4 17 5-5 4 4 2.5-2.5L20 18"/></svg>',
  heart: '<svg viewBox="0 0 24 24"><path d="M12 20.5 4.5 13a4.6 4.6 0 0 1 6.5-6.5l1 1 1-1A4.6 4.6 0 0 1 19.5 13Z"/></svg>',
  globe: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M4 12h16M12 4a14 14 0 0 1 0 16 14 14 0 0 1 0-16"/></svg>',
  eyeOff: '<svg viewBox="0 0 24 24"><path d="M10.6 5.2A9.7 9.7 0 0 1 12 5c5 0 9 4.5 9 7a11 11 0 0 1-2.2 3.4M6.3 6.6C3.9 8.2 3 10.6 3 12c0 2.5 4 7 9 7 1.6 0 3-.4 4.3-1.1M3 3l18 18"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="m5 12 5 5 9-9"/></svg>',
  // A Live Photo: a still that moves (its short video is played in the viewer).
  live: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="2.5" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="5.5"/><circle cx="12" cy="12" r="9" stroke-dasharray="1.6 2.4"/></svg>',
};

/**
 * Thumbnails fetched ahead of the scroll into the browser's cache, a few at a
 * time and at low priority, so a tile mounted there later has nothing to wait
 * for. Changing what is wanted cancels what no longer is.
 */
class Prefetcher {
  /** `idle`: whether the page is ready for more; asked before each start. */
  constructor(limit = 4, idle = () => true) {
    this.limit = limit;
    this.idle = idle;
    this.queue = [];
    this.active = new Map();     // url -> Image in flight
    this.done = new Set();
  }

  /** `keep`: pictures the page now shows itself — a download already under
   *  way for one is left to finish rather than started again from nothing. */
  want(urls, keep = null) {
    const wanted = new Set(urls);
    for (const [url, image] of this.active) {
      if (!wanted.has(url) && !keep?.has(url)) {
        this.active.delete(url);
        image.removeAttribute('src');      // stops the download
      }
    }
    if (this.done.size > 4000) this.done.clear();
    this.queue = urls.filter((url) => !this.active.has(url) && !this.done.has(url));
    this.pump();
  }

  pump() {
    while (this.active.size < this.limit && this.queue.length && this.idle()) {
      const url = this.queue.shift();
      const image = new Image();
      image.decoding = 'async';
      if ('fetchPriority' in image) image.fetchPriority = 'low';
      const finish = () => {
        if (this.active.get(url) !== image) return;
        this.active.delete(url);
        this.done.add(url);
        this.pump();
      };
      image.onload = finish;
      image.onerror = finish;
      this.active.set(url, image);
      image.src = url;
    }
  }
}

/** The sign for an item's kind, standing in for a picture it does not have. */
function glyphFor(kind) {
  const glyph = document.createElement('div');
  glyph.className = 'glyph';
  glyph.innerHTML = ICON[kind] || ICON.audio;
  return glyph;
}

function formatDuration(seconds) {
  if (!seconds) return '';
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}

function formatSection(key, count) {
  if (key === 'match') return { title: i18n.t('Best matches'), sub: i18n.t('{count} results', { count }) };
  if (key === 'unknown') return { title: i18n.t('Undated'), sub: i18n.items(count) };
  const date = new Date(`${key}T00:00:00`);
  if (Number.isNaN(date.getTime())) return { title: key, sub: i18n.items(count) };
  const now = new Date();
  const sameYear = date.getFullYear() === now.getFullYear();
  return {
    title: date.toLocaleDateString(i18n.locale(), {
      weekday: 'short',
      day: 'numeric',
      month: 'long',
      ...(sameYear ? {} : { year: 'numeric' }),
    }),
    sub: i18n.items(count),
  };
}

export class Grid extends EventTarget {
  constructor(scroller, container) {
    super();
    this.scroller = scroller;
    this.container = container;
    this.segments = [];
    this.layout = { cells: [], headers: [], height: 0, buckets: new Map(), bucketSize: 600 };
    this.mode = 'justified';
    this.zoom = 2;
    this.width = 0;
    this.mounted = new Map();     // cell index -> node
    this.mountedHeads = new Map();
    this.pool = [];
    this.parked = new Map();      // thumbnail URL -> parked node still showing it
    this.headPool = [];
    this.selection = new Set();
    this.selecting = false;
    this.showVisibility = false;   // admins only; set by the app shell
    this.cursor = -1;
    this.lastAnchor = -1;
    // What was selected before the current Shift run began. Held so that
    // shrinking a Shift+Arrow range gives back what the range swallowed,
    // instead of leaving it selected the way an add-only range would.
    // Null means no run is in progress.
    this.rangeBase = null;
    this.frame = null;
    // How fast, and which way, the scroll is going (px per ms; down is +).
    this.velocity = 0;
    this.direction = 1;
    this.lastTop = 0;
    this.lastTime = 0;
    this.fast = false;
    this.settleTimer = null;
    this.prefetcher = new Prefetcher(4, () => !this.tilesLoading());
    this.scrolling = false;
    // The pointer, for telling a real move of the mouse from the one a
    // browser makes up when the page scrolls under it.
    this.pointerAt = null;
    this.lastPreload = 0;
    // Frames running the scroll has gone faster than JUMP; a jump asked for.
    this.overJump = 0;
    this.jumpAsked = false;
    // How far ahead of the screen, in px, pictures are being fetched (the app
    // asks for the next piece of the gallery before the scroll reaches it).
    this.lookahead = 0;
    this.topUp = null;
    // The scroll position and screen height, while a frame's work is done,
    // and as the last frame left them.
    this.frameViewport = null;
    this.lastViewport = null;
    // Why nothing is being fetched ahead right now (the viewer covers the
    // grid, the page is in the background): while any holds, nothing is.
    this.holds = new Set();
    this.onVisibility = () => this.hold('hidden', document.hidden);
    document.addEventListener('visibilitychange', this.onVisibility);

    this.onScroll = this.onScroll.bind(this);
    this.scroller.addEventListener('scroll', this.onScroll, { passive: true });

    this.resizeObserver = new ResizeObserver(() => this.measure());
    this.resizeObserver.observe(this.scroller);

    this.container.addEventListener('click', (event) => this.onClick(event));
    this.container.addEventListener('dblclick', (event) => this.onClick(event, true));
    // A phone has no hover to show the pick mark and no Ctrl to hold: a long
    // press on a tile starts selecting, as it does in every photo app.
    this.container.addEventListener('pointerdown', (event) => {
      this.onPressStart(event);
      if (event.pointerType === 'mouse') this.hoverBack();
    });
    // The hover look comes back when the mouse moves, not when the scroll
    // stops. Brought back at the stop, it came and went between every notch
    // of a mouse wheel: the tile under the pointer lit up, zoomed and showed
    // its tick, then dropped it again as the next notch scrolled — a flicker.
    this.container.addEventListener('pointermove', (event) => {
      this.onPressMove(event);
      if (event.pointerType !== 'mouse') return;
      const was = this.pointerAt;
      this.pointerAt = { x: event.clientX, y: event.clientY };
      if (!this.container.classList.contains('no-hover') || !was) return;
      if (Math.abs(event.clientX - was.x) + Math.abs(event.clientY - was.y) < 2) return;
      this.hoverBack();
    });
    // On the window: the tile under the finger may have been redrawn (and
    // its node moved) by the time the finger lifts.
    this.onLift = () => this.onPressEnd(true);
    window.addEventListener('pointerup', this.onLift);
    window.addEventListener('pointercancel', this.onLift);
    // That click lands wherever the finger is when it lifts, and the selection
    // bar that has just appeared moves what is above the grid (On this day)
    // under it: it is stopped anywhere on the page, not only on a tile.
    this.onAnyClick = (event) => {
      if (!this.swallowing()) return;
      this.swallowUntil = 0;
      event.preventDefault();
      event.stopPropagation();
    };
    window.addEventListener('click', this.onAnyClick, true);
    this.container.addEventListener('contextmenu', (event) => {
      if (this.press || this.swallowing()) event.preventDefault();
    });
  }

  /** The mouse moved: tiles answer it again. */
  hoverBack() {
    if (this.scrolling) return;
    this.container.classList.remove('no-hover');
  }

  /** Whether any tile on the screen is still waiting for its picture. While
   *  one is, nothing is fetched ahead of it: the browser has only so many
   *  connections, and the pictures being looked at come first. */
  tilesLoading() {
    // Asked from a picture's load event, between frames: the screen as the
    // last frame saw it will do, rather than a layout of the page per load.
    const { top, height } = this.frameViewport || this.lastViewport || { top: 0, height: 0 };
    for (const node of this.mounted.values()) {
      const img = node.img;
      if (!img || img.hidden || !img.getAttribute('src') || img.complete) continue;
      const cell = this.layout.cells[Number(node.dataset.n)];
      if (cell && cell.y + cell.h > top && cell.y < top + height) return true;
    }
    return false;
  }

  /* ---------------------------------------------------------------- */

  /**
   * `resetScroll` distinguishes a genuinely new result set (a search, a
   * filter, a sort, a different view) from an in-place refresh of the one
   * already on screen (an edit, a background rescan finishing). The former
   * wants the viewport back at the top — nothing scrolled to is likely to
   * still be there once the query itself changed, so `relayout`'s scroll-
   * anchor preservation (below) just leaves the new results sitting above a
   * scroll position that means nothing for them, invisible until scrolled to
   * by hand. The latter wants exactly the opposite: don't move the person's
   * place in the grid just because a photo they are looking at got a new
   * favourite flag.
   */
  setData(segments, { resetScroll = false } = {}) {
    this.segments = segments || [];
    const hadSelection = this.selection.size > 0;
    this.selection.clear();
    this.cursor = -1;
    this.rangeBase = null;
    if (resetScroll) this.scroller.scrollTop = 0;
    // What was being fetched ahead belongs to the last result set.
    this.prefetcher.want([]);
    this.relayout(!resetScroll);
    // Say so, or the selection bar goes on offering to act on photos that are
    // no longer selected.
    if (hadSelection) this.emitSelection();
  }

  /**
   * Add the next piece of the same result set to the end.
   *
   * Unlike setData this keeps the selection, the keyboard cursor and the
   * scroll position: nothing already on screen moves, because every cell that
   * was laid out keeps its index and the new ones only ever follow it.
   */
  appendData(segments) {
    const added = mergeSegments(this.segments, segments);
    if (added) this.relayout(true);
    return added;
  }

  setMode(mode) {
    if (this.mode === mode) return;
    this.mode = mode;
    this.relayout(true);
  }

  setZoom(zoom) {
    const next = Math.max(0, Math.min(4, zoom));
    if (this.zoom === next) return;
    this.zoom = next;
    this.relayout(true);
  }

  /** Usable width = scroller content box minus its horizontal padding. */
  contentWidth() {
    const style = getComputedStyle(this.scroller);
    const pad = parseFloat(style.paddingLeft || 0) + parseFloat(style.paddingRight || 0);
    return Math.max(0, this.scroller.clientWidth - pad);
  }

  measure() {
    const width = this.contentWidth();
    if (Math.abs(width - this.width) < 2) return;
    this.width = width;
    this.relayout(true);
  }

  /**
   * How wide a packed day's header must be for its count and "Select all" to
   * share the second line whole, in the language on screen. The Tamil
   * "Select all" alone is about 140 px, and a 170 px header cut the count
   * down to "1…". Measured once per language.
   */
  packedHeadWidth() {
    const lang = i18n.language();
    if (this.packedHeadFor === lang) return this.packedHead;
    const probe = this.createHeader();
    probe.classList.add('packed');
    probe.style.cssText = 'visibility:hidden;width:auto;left:-10000px;top:0';
    probe.querySelector('.sub').textContent = formatSection('2000-01-01', 88).sub;
    this.container.appendChild(probe);
    const sub = probe.querySelector('.sub').scrollWidth;
    const button = probe.querySelector('[data-select-section]').getBoundingClientRect().width;
    probe.remove();
    // Two px of padding each side and the 6 px column gap.
    this.packedHead = sub && button ? Math.ceil(sub + button + 10) : 0;
    this.packedHeadFor = lang;
    return this.packedHead;
  }

  /** Rewrite the headings on screen in the language just chosen. */
  relabel() {
    for (const node of this.mountedHeads.values()) {
      const head = node.dataset.head;
      const seg = this.segments?.find((one) => one.key === head);
      const count = seg ? (seg.count ?? seg.items?.length) : undefined;
      const { title, sub } = formatSection(head, count ?? 0);
      node.querySelector('.title').textContent = title;
      if (node.classList.contains('packed')) node.querySelector('.title').title = title;
      if (count != null) node.querySelector('.sub').textContent = sub;
      const select = node.querySelector('[data-select-section]');
      if (select) select.textContent = i18n.t('Select all');
    }
    // A language whose count and "Select all" need wider packed headers.
    const before = this.packedHead;
    this.packedHeadFor = null;
    if (this.width && this.packedHeadWidth() !== before) this.relayout(true);
  }

  relayout(preserveAnchor = false) {
    if (!this.width) this.width = this.contentWidth();
    if (!this.width) return;

    let anchorId = null;
    let anchorOffset = 0;
    if (preserveAnchor && this.layout.cells.length) {
      const top = this.scroller.scrollTop;
      const found = this.layout.cells.find((c) => c.y + c.h > top);
      if (found) {
        anchorId = found.id;
        anchorOffset = found.y - top;
      }
    }

    this.layout = computeLayout(this.segments, {
      width: this.width,
      mode: this.mode,
      zoom: this.zoom,
      gap: this.mode === 'film' ? 2 : 6,
      packMin: this.packedHeadWidth(),
    });
    this.container.style.height = `${this.layout.height}px`;

    this.unmountAll();

    if (anchorId != null) {
      const target = this.layout.cells.find((c) => c.id === anchorId);
      if (target) this.scroller.scrollTop = Math.max(0, target.y - anchorOffset);
    }
    this.render();
    this.trimPool();
    this.dispatchEvent(new CustomEvent('layout', { detail: this.layout }));
  }

  /** Back to the pool's size, letting the longest-parked nodes go. */
  trimPool() {
    if (this.pool.length <= POOL_LIMIT) return;
    const dropped = new Set(this.pool.splice(0, this.pool.length - POOL_LIMIT));
    for (const [url, node] of this.parked) if (dropped.has(node)) this.parked.delete(url);
  }

  onScroll() {
    if (this.frame) return;
    this.frame = requestAnimationFrame((frameTime) => {
      this.frame = null;
      const detail = this.withViewport(() => {
        this.measureSpeed(frameTime);
        this.render();
        this.preload();
        const { top, height } = this.viewport();
        return {
          top,
          height,
          section: sectionAt(this.layout, top + 40),
          ratio: top / Math.max(1, this.layout.height - height),
        };
      });
      this.dispatchEvent(new CustomEvent('scroll', { detail }));
    });
  }

  /* ---------------------------------------------------------------- */

  /**
   * Speed and direction from this frame and the last, smoothed. *now* is the
   * frame's own time, when the scroll position it shows was taken: the clock
   * read inside the callback is late by whatever ran before it in that frame,
   * so on a busy computer two frames' worth of scrolling could be divided by
   * a few milliseconds and a steady scroll look like a jump (and fetch
   * nothing ahead).
   */
  measureSpeed(now = performance.now()) {
    const { top } = this.viewport();
    const elapsed = now - this.lastTime;
    if (this.lastTime && elapsed > 0 && elapsed < 250) {
      const speed = (top - this.lastTop) / elapsed;
      this.velocity = this.velocity * 0.4 + speed * 0.6;
      // A jump is going on when it was asked for (the scrubber, scrollToRatio)
      // or the scroll has gone faster than any finger for two frames running:
      // one long frame on a slow computer is not a jump.
      this.overJump = Math.abs(speed) > JUMP ? this.overJump + 1 : 0;
    } else {
      this.velocity = 0;
      this.overJump = 0;
    }
    const direction = this.velocity < 0 ? -1 : this.velocity > 0 ? 1 : this.direction;
    // Turned round: what was fetched ahead is now behind.
    const turned = direction !== this.direction;
    this.direction = direction;
    this.lastTop = top;
    this.lastTime = now;
    if (turned) this.lastPreload = 0;
    this.fast = Math.abs(this.velocity) > FLING;
    if (!this.scrolling) {
      this.scrolling = true;
      this.container.classList.add('is-scrolling', 'no-hover');
    }
    clearTimeout(this.settleTimer);
    this.settleTimer = setTimeout(() => this.settle(), SETTLE_MS);
  }

  /** The band of tiles kept ready: longer ahead of a fast scroll, shorter behind. */
  band() {
    const { top, height: view } = this.viewport();
    const speed = Math.abs(this.velocity);
    const ahead = Math.min(OVERSCAN + speed * LOOKAHEAD_MS, OVERSCAN + view * MAX_AHEAD_SCREENS);
    const behind = this.fast ? TRAILING : OVERSCAN;
    return this.direction >= 0
      ? [top - behind, top + view + ahead]
      : [top - ahead, top + view + behind];
  }

  /**
   * The scroll has stopped. Tiles that took the small thumbnail while it went
   * on get the sharp one — fetched on the side and put in only once decoded,
   * so nothing blinks (see sharpen) — and the next screen and a half in the
   * direction it was going is fetched, so carrying on finds it ready. The
   * hover look stays off until the mouse itself moves (see hoverBack).
   */
  settle() {
    this.velocity = 0;
    this.lastTime = 0;
    this.overJump = 0;
    this.jumpAsked = false;
    this.fast = false;
    this.scrolling = false;
    this.container.classList.remove('is-scrolling');
    this.render();
    this.preload(true);
  }

  /**
   * Fetch, into the browser's cache, the thumbnails the scroll is about to
   * reach: beyond the tiles already on the page, as far as it will go in the
   * next PRELOAD_SECONDS at its present speed, nearest first, and a little
   * the other way. Run every PRELOAD_EVERY_MS while scrolling, and once when
   * it stops (`now`), when the sharp pictures are fetched in place of the
   * small ones a fast scroll gets.
   */
  preload(now = false) {
    if (this.holds.size) return;
    const clock = performance.now();
    if (!now && clock - this.lastPreload < PRELOAD_EVERY_MS) return;
    this.lastPreload = clock;
    this.withViewport(() => this.preloadNow());
  }

  preloadNow() {
    const { top, height: view } = this.viewport();
    const speed = Math.abs(this.velocity);
    if (!view || this.jumpAsked || this.overJump >= 2) {
      this.lookahead = 0;
      this.prefetcher.want([], this.shownUrls());
      return;
    }
    const bottom = top + view;
    const [bandTop, bandBottom] = this.band();
    const down = this.direction >= 0;
    // Measured from the screen's edge (where the scroll will have travelled),
    // but always some way past the tiles already on the page.
    const onPage = down ? bandBottom - bottom : top - bandTop;
    // A phone that asked to save data gets half the reach.
    const thrift = navigator.connection?.saveData ? 0.5 : 1;
    const ahead = Math.max(
      onPage + thrift * view * (this.scrolling ? PRELOAD_MIN_SCREENS : PREFETCH_SCREENS),
      thrift * Math.min(view * PRELOAD_MAX_SCREENS, speed * PRELOAD_SECONDS * 1000));
    const behind = thrift * view * (this.fast ? PRELOAD_BEHIND_FAST : PRELOAD_BEHIND_SCREENS);
    this.lookahead = ahead;

    const ranges = down
      ? [[bandBottom, bottom + ahead, bottom, 1], [bandTop - behind, bandTop, top, 3]]
      : [[top - ahead, bandTop, top, 1], [bandBottom, bandBottom + behind, bottom, 3]];
    const picks = [];
    for (const [from, to, edge, weight] of ranges) {
      if (to <= from) continue;
      for (const cell of visibleRange(this.layout, from, to).cells) {
        if (this.mounted.has(cell.n)) continue;
        const url = this.cellThumbUrl(cell, this.scrolling);
        // The far side of the band ahead, or the near side behind: the
        // distance the scroll has to travel to reach it. Behind counts for
        // more, since the scroll is going the other way.
        if (url) picks.push([Math.abs((cell.y + cell.h / 2) - edge) * weight, url]);
      }
    }
    picks.sort((a, b) => a[0] - b[0]);
    const urls = picks.slice(0, PRELOAD_MAX).map((pick) => pick[1]);
    // Tiles on screen still waiting for their picture come first: while any
    // is, nothing ahead is started (the prefetcher asks tilesLoading), and
    // the tiles' own load events start it again.
    this.prefetcher.want(urls, this.shownUrls());
  }

  /**
   * Stop fetching ahead for *reason* (`on`), or let it carry on. The grid
   * under an open photo, or on a page in the background, is not going to be
   * scrolled: what it went on fetching there took the slow connection the
   * photo being opened needed (on a phone on mobile data, a megabyte of
   * thumbnails ahead of the photo).
   */
  hold(reason, on) {
    if (on === this.holds.has(reason)) return;
    if (on) {
      this.holds.add(reason);
      this.lookahead = 0;
      this.prefetcher.want([], this.shownUrls());
    } else {
      this.holds.delete(reason);
      if (!this.holds.size) this.preload(true);
    }
  }

  /** The pictures the tiles on the page are showing or fetching. */
  shownUrls() {
    const urls = new Set();
    for (const node of this.mounted.values()) {
      const src = node.img?.getAttribute('src');
      if (src) urls.add(src);
    }
    return urls;
  }

  onScreenNode(node) {
    const cell = this.layout.cells[Number(node.dataset.n)];
    return !!cell && this.onScreen(cell);
  }

  onScreen(cell) {
    const { top, height } = this.viewport();
    return cell.y + cell.h > top && cell.y < top + height;
  }

  /**
   * Where the scroll is and how tall the screen is: read once a frame (see
   * withViewport). Read from the page after a tile was put on it, they make
   * the browser lay the page out again there and then; asked for every tile
   * of a frame, that was most of the time a scroll took on a slow phone
   * (1.1 s of 2.3 s of script measured, CPU slowed four times).
   */
  viewport() {
    return this.frameViewport
      || { top: this.scroller.scrollTop, height: this.scroller.clientHeight };
  }

  withViewport(work) {
    if (this.frameViewport) return work();
    this.frameViewport = { top: this.scroller.scrollTop, height: this.scroller.clientHeight };
    this.lastViewport = this.frameViewport;
    try {
      return work();
    } finally {
      this.frameViewport = null;
    }
  }

  /**
   * The sharp picture for a tile that took the small one while the scroll
   * went on — once it has stopped, and only for tiles near the screen, so the
   * ones being looked at are not queued behind ones a screen further on.
   *
   * Fetched and decoded on the side first, and only then put in the tile.
   * Pointing a tile's <img> at a picture the browser does not have yet blanks
   * it in Safari — WebKit drops the old picture the moment the address
   * changes, where Chrome keeps it until the new one is ready — so after
   * every brisk scroll the whole screen blinked and came back sharp. A
   * picture already decoded is drawn in the same frame, in every browser.
   */
  sharpen(node, cell) {
    if (this.scrolling || !node.blurry) return;
    const img = node.img;
    const full = this.cellThumbUrl(cell);
    if (!full || !img || img.hidden || img.getAttribute('src') === full) {
      node.blurry = false;
      return;
    }
    const { top, height: view } = this.viewport();
    if (cell.y + cell.h < top - view / 2 || cell.y > top + view * 1.5) return;
    this.swapIn(node, cell, full);
    node.blurry = false;
  }

  /** Fetch and decode `url` on the side, then show it in the tile — which
   *  keeps the picture it has until then. See sharpen. */
  swapIn(node, cell, url) {
    if (node.pendingSrc === url) return;
    node.pendingSrc = url;
    const img = node.img;
    const sharp = new Image();
    sharp.decoding = 'async';
    if ('fetchPriority' in sharp) sharp.fetchPriority = this.onScreen(cell) ? 'high' : 'auto';
    const swap = () => {
      if (node.pendingSrc !== url) return;            // recycled, or wanted elsewhere
      node.pendingSrc = null;
      if (img.hidden || node.dataset.id !== String(cell.id) || !this.mounted.has(cell.n)) return;
      img.src = url;
    };
    sharp.onload = () => {
      const decoded = sharp.decode ? sharp.decode() : Promise.resolve();
      decoded.then(swap, swap);
    };
    sharp.onerror = () => { if (node.pendingSrc === url) node.pendingSrc = null; };
    sharp.src = url;
  }

  render() {
    this.withViewport(() => this.renderNow());
  }

  renderNow() {
    const [top, bottom] = this.band();
    const { cells, headers } = visibleRange(this.layout, top, bottom);

    const wanted = new Set();
    let budget = MOUNT_BUDGET;
    let deferred = false;
    for (const cell of cells) {
      wanted.add(cell.n);
      let node = this.mounted.get(cell.n);
      if (!node) {
        // On screen always; further out, only so many a frame.
        if (budget <= 0 && !this.onScreen(cell)) {
          deferred = true;
          continue;
        }
        budget -= 1;
        node = this.takeCell(cell);
        this.mounted.set(cell.n, node);
        this.container.appendChild(node);
        this.paintCell(node, cell);
      } else {
        // Already mounted, and where the layout put it (a new layout mounts
        // everything afresh): re-sync only the state that can change without
        // the cell being remounted (selection, keyboard cursor), and only when
        // it has — every frame touching every tile's style was work a slow
        // computer felt while scrolling.
        this.syncCellState(node, cell);
        this.sharpen(node, cell);
      }
    }
    if (deferred && !this.topUp) {
      this.topUp = requestAnimationFrame(() => {
        this.topUp = null;
        this.render();
      });
    }
    for (const [index, node] of this.mounted) {
      if (!wanted.has(index)) {
        this.mounted.delete(index);
        this.releaseCell(node);
      }
    }

    const wantedHeads = new Set();
    for (const head of headers) {
      // Small days share a row (see layout.js), so a header is known by where
      // it sits across as well as down.
      const place = `${head.y}:${head.x || 0}`;
      wantedHeads.add(place);
      let node = this.mountedHeads.get(place);
      if (!node) {
        node = this.headPool.pop() || this.createHeader();
        this.mountedHeads.set(place, node);
        this.container.appendChild(node);
        const { title, sub } = formatSection(head.key, head.count);
        node.querySelector('.title').textContent = title;
        node.querySelector('.sub').textContent = sub;
        // A pooled header may have been made in the language used before.
        node.querySelector('[data-select-section]').textContent = i18n.t('Select all');
        node.dataset.head = head.key;
        node.dataset.firstCell = String(head.firstCell);
        // A packed day's header is only as wide as its own photographs; a
        // pooled node may come back from one, so both ways are set.
        const packed = head.w != null;
        node.classList.toggle('packed', packed);
        node.style.width = packed ? `${head.w}px` : '';
        node.querySelector('.title').title = packed ? title : '';
        node.style.transform = `translate(${head.x || 0}px,${head.y}px)`;
      }
    }
    for (const [key, node] of this.mountedHeads) {
      if (!wantedHeads.has(key)) {
        this.mountedHeads.delete(key);
        node.remove();
        if (this.headPool.length < 40) this.headPool.push(node);
      }
    }
  }

  /* -- node lifecycle ------------------------------------------------ */

  createCell() {
    const node = document.createElement('div');
    node.className = 'cell';
    node.setAttribute('role', 'gridcell');
    node.tabIndex = -1;
    const img = document.createElement('img');
    node.img = img;
    img.decoding = 'async';
    img.draggable = false;
    img.alt = '';
    img.addEventListener('load', () => {
      clearTimeout(node.shimmerTimer);
      delete img.dataset.retried;
      // Arrived almost at once: no fade (see INSTANT_MS).
      img.classList.toggle('instant',
        !!node.paintedAt && performance.now() - node.paintedAt < INSTANT_MS);
      img.classList.add('ready');
      node.classList.remove('loading');
      this.prefetcher.pump();
    });
    img.addEventListener('error', () => {
      clearTimeout(node.shimmerTimer);
      node.classList.remove('loading');
      this.prefetcher.pump();
      const src = img.getAttribute('src');
      if (!src || node.querySelector('.glyph')) return;
      // A server busy making other tiles says so rather than keep this one
      // waiting (it has only so many threads, and other phones to answer):
      // one more try a moment later, before the tile gives up.
      if (img.dataset.retried !== src) {
        img.dataset.retried = src;
        clearTimeout(node.retryTimer);
        node.retryTimer = setTimeout(() => {
          if (img.getAttribute('src') !== src || img.naturalWidth) return;
          img.removeAttribute('src');
          img.src = src;
        }, 1500 + Math.random() * 1500);
        return;
      }
      // A thumbnail that will not load shows the sign for its kind, as an item
      // with none does. An empty square reads as the page still loading, or
      // the file being broken, and neither is usually true.
      img.hidden = true;
      node.classList.add('audio-cell');
      node.appendChild(glyphFor(node.dataset.kind));
    });
    node.appendChild(img);

    const pick = document.createElement('button');
    pick.className = 'pick';
    pick.type = 'button';
    pick.setAttribute('aria-label', i18n.t('Select'));
    pick.innerHTML = ICON.check;
    node.appendChild(pick);
    return node;
  }

  createHeader() {
    const node = document.createElement('div');
    node.className = 'section-head';
    node.innerHTML = '<span class="title"></span><span class="sub"></span>'
      + `<button type="button" data-select-section>${ICON.check}<span class="label"></span></button>`;
    // A narrow day shows only the tick (style.css), so the button carries its
    // name for a screen reader and a pointer either way.
    const button = node.querySelector('[data-select-section]');
    button.querySelector('.label').textContent = i18n.t('Select all');
    button.setAttribute('aria-label', i18n.t('Select all'));
    button.title = i18n.t('Select all');
    return node;
  }

  /** The smallest derivative that still covers the size this cell is drawn at —
   *  or, with `small`, the smallest there is, for a tile only flying past. */
  cellThumbUrl(cell, small = false) {
    if ((cell.flags & 2) === 0) return null;
    const need = Math.max(cell.w, cell.h) * (window.devicePixelRatio || 1);
    return thumbUrl(cell.id, !small && need > 300 ? 640 : 256, cell.v);
  }

  /**
   * Prefer the parked node that is still holding this very picture.
   *
   * Recycling any node and pointing it at a new URL means the browser decodes
   * the image again, and the tile spends a frame or two on the placeholder
   * even when the bytes are in cache — which is the flicker you see scrolling
   * back up through a library. A node that never stopped holding the right
   * picture has nothing to decode and nothing to wait for.
   */
  takeCell(cell) {
    // The sharp picture if a parked node holds it, else the small one: a
    // tile holding either shows it at once and is sharpened on the side
    // (paintCell), where a fresh node would show its colour first.
    const urls = [this.cellThumbUrl(cell), this.cellThumbUrl(cell, true)];
    for (const url of urls) {
      const kept = url && this.parked.get(url);
      if (kept) {
        this.parked.delete(url);
        const at = this.pool.indexOf(kept);
        if (at >= 0) this.pool.splice(at, 1);
        return kept;
      }
    }
    const node = this.pool.pop();
    if (!node) return this.createCell();
    const src = node.querySelector('img')?.getAttribute('src');
    if (src) this.parked.delete(src);
    return node;
  }

  releaseCell(node, keep = this.pool.length < POOL_LIMIT) {
    node.remove();
    clearTimeout(node.shimmerTimer);
    node.pendingSrc = null;
    if (keep) {
      node.className = 'cell';
      const img = node.querySelector('img');
      if (img) {
        // `ready` goes, because this node may be handed to a different
        // picture next; the src stays, because it may just as easily be
        // handed back to this one.
        img.classList.remove('ready');
        const src = img.getAttribute('src');
        // Still downloading: stop it. On a fast scroll these are the tiles
        // already gone past, and the browser fetches only a few at a time —
        // left running, they hold up the ones now coming on screen.
        if (src && !img.complete) img.removeAttribute('src');
        else if (src) this.parked.set(src, node);
      }
      this.pool.push(node);
    }
  }

  unmountAll() {
    // Every node is kept, whatever the pool's size: a relayout mounts most
    // of them again at once, each with the picture it already holds, and a
    // node dropped here came back as a tile on its colour, fading its own
    // picture in again. The pool is trimmed after the render (trimPool).
    for (const node of this.mounted.values()) this.releaseCell(node, true);
    // Anything not parked above is gone; the map must not outlive the pool.
    for (const [url, node] of this.parked) {
      if (!this.pool.includes(node)) this.parked.delete(url);
    }
    this.mounted.clear();
    for (const node of this.mountedHeads.values()) {
      node.remove();
      if (this.headPool.length < 40) this.headPool.push(node);
    }
    this.mountedHeads.clear();
  }

  syncCellState(node, cell, force = false) {
    const selected = this.selection.has(cell.id);
    const cursor = this.cursor === cell.n;
    if (!force && node.isSelected === selected && node.isCursor === cursor) return;
    node.isSelected = selected;
    node.isCursor = cursor;
    node.classList.toggle('selected', selected);
    node.classList.toggle('cursor', cursor);
    node.setAttribute('aria-selected', String(selected));
  }

  positionCell(node, cell) {
    // A plain translate, not translate3d: a tile does not move while the
    // grid scrolls (the scroller moves it), so a layer of its own bought
    // nothing, and hundreds of them — one GPU texture per tile — had an
    // iPhone dropping and redrawing tiles as it ran short of memory.
    node.style.transform = `translate(${cell.x}px,${cell.y}px)`;
    node.style.width = `${cell.w}px`;
    node.style.height = `${cell.h}px`;
  }

  paintCell(node, cell) {
    this.positionCell(node, cell);
    node.dataset.id = String(cell.id);
    node.dataset.n = String(cell.n);

    const kind = KIND_NAMES[cell.kind] || 'picture';
    node.dataset.kind = kind;
    const hasThumb = (cell.flags & 2) !== 0;
    const favorite = (cell.flags & 1) !== 0;

    this.syncCellState(node, cell, true);
    node.blurry = false;
    node.classList.toggle('audio-cell', !hasThumb);
    // A video with no picture: the app may have this browser make one.
    if (!hasThumb && kind === 'video') {
      this.dispatchEvent(new CustomEvent('poster', { detail: { id: cell.id } }));
    }

    // Clear previous badges without touching the img/pick nodes.
    node.querySelectorAll('.badge, .glyph, .label').forEach((n) => n.remove());

    const img = node.querySelector('img');
    // Its own colour until the picture arrives: a fast scroll shows a mosaic
    // of the right colours rather than a wall of grey.
    node.style.backgroundColor = hasThumb && cell.swatch ? `#${cell.swatch}` : '';
    if (hasThumb) {
      img.hidden = false;
      // Already holding the sharp one: keep it. Made while the scroll goes
      // on: the small one, a quarter of the bytes and usually fetched ahead
      // already, sharpened once the scroll stops (see settle) — the way
      // Photos shows its small pictures while the finger is moving. On a
      // phone every tile wants the sharp one (a Retina screen makes a small
      // tile a large picture), so a scroll fetching sharp ones outran the
      // network at any pace and met its tiles still empty.
      const full = this.cellThumbUrl(cell);
      const current = img.getAttribute('src');
      const url = current === full || !this.scrolling ? full : this.cellThumbUrl(cell, true);
      // Holding this very picture, sharp or small, and decoded: it stays on
      // screen, and a sharper one comes in on the side (swapIn). Pointing the
      // tile straight at the new address dropped the tile to its colour and
      // faded the picture back in — in Safari, which blanks an image the
      // moment its address changes, even when the browser had it already.
      const holds = (current === full || current === this.cellThumbUrl(cell, true))
        && img.complete && img.naturalWidth > 0;
      node.blurry = (holds ? current : url) !== full;
      // On screen first. Not "low" for the rest: the band ahead is where the
      // scroll is going, and a low hint let the browser hold those back until
      // they arrived on screen still empty.
      img.fetchPriority = this.onScreen(cell) ? 'high' : 'auto';

      // A recycled node usually comes back holding the very picture it is
      // being asked for again: scrolling down a row and back up is the
      // ordinary case, and the pool hands out the most recently released node
      // first. Clearing `ready` and re-assigning the same src there dropped
      // the tile to a shimmer and faded in a picture the browser already had
      // decoded — the flicker while scrolling was ours, not the network's.
      //
      // A thumbnail URL carries a version that changes whenever the bytes
      // change, so an image that is already here and decoded is by definition
      // the right one. Only a different picture, or one that failed, is worth
      // fetching again.
      if (holds) {
        if (current !== url) this.sharpen(node, cell);
      } else if (current !== url || (img.complete && !img.naturalWidth)) {
        img.src = url;
      }
      // A picture that is already here and decoded is shown at once.
      const shown = holds || (img.complete && img.naturalWidth > 0);
      img.classList.toggle('ready', shown);
      img.classList.remove('instant');
      node.paintedAt = shown ? 0 : performance.now();
      node.classList.remove('loading');
      clearTimeout(node.shimmerTimer);

      // Otherwise, wait before admitting to a wait. A thumbnail the browser
      // has cached is decoded within a frame or two, and a shimmer that
      // appears and disappears inside a tenth of a second is not information —
      // it is the flicker. Only a load that is genuinely slow gets to say so.
      // A tile showing its colour is already saying a picture is coming.
      if (!shown && !cell.swatch) {
        node.shimmerTimer = setTimeout(() => {
          if (!img.complete) node.classList.add('loading');
        }, SHIMMER_DELAY);
      }
    } else {
      img.hidden = true;
      img.removeAttribute('src');
      node.classList.remove('loading');
      node.appendChild(glyphFor(kind));
    }

    if (kind !== 'picture') {
      const badge = document.createElement('span');
      badge.className = 'badge kind';
      badge.innerHTML = ICON[kind];
      node.appendChild(badge);
    }
    // A Live Photo (flag 4): its still, which moves in the viewer.
    if (kind === 'picture' && (cell.flags & 4)) {
      const badge = document.createElement('span');
      badge.className = 'badge live';
      badge.title = i18n.t('Live Photo');
      badge.innerHTML = ICON.live;
      node.appendChild(badge);
    }
    if (cell.dur) {
      const badge = document.createElement('span');
      badge.className = 'badge dur';
      badge.textContent = formatDuration(cell.dur);
      node.appendChild(badge);
    }
    if (favorite) {
      const badge = document.createElement('span');
      badge.className = 'badge fav';
      badge.innerHTML = ICON.heart;
      node.appendChild(badge);
    }

    // Visibility, for the admins who set it (flags 64 = public, 128 = hidden).
    if (this.showVisibility && (cell.flags & 192)) {
      const badge = document.createElement('span');
      const isPublic = (cell.flags & 64) !== 0;
      badge.className = `badge vis ${isPublic ? 'is-public' : 'is-hidden'}`;
      badge.title = isPublic ? i18n.t('Visible to everyone') : i18n.t('Hidden — admins only');
      badge.innerHTML = isPublic ? ICON.globe : ICON.eyeOff;
      node.appendChild(badge);
    }
  }

  /** A thumbnail that exists now (a video's poster, just made): remember it
   *  in the data and redraw the tile. */
  gotThumb(id, version, swatch) {
    for (const segment of this.segments || []) {
      for (const item of segment.items) {
        if (item[0] !== id) continue;
        item[3] |= 2;
        item[5] = version || 0;
        item[6] = swatch || '';
      }
    }
    for (const cell of this.layout.cells) {
      if (cell.id !== id) continue;
      cell.flags |= 2;
      cell.v = version || 0;
      cell.swatch = swatch || '';
    }
    this.refreshCell(id);
  }

  refreshCell(id) {
    for (const [index, node] of this.mounted) {
      const cell = this.layout.cells[index];
      if (cell && cell.id === id) this.paintCell(node, cell);
    }
  }

  /* -- interaction ---------------------------------------------------- */

  onPressStart(event) {
    if (event.pointerType !== 'touch') return;
    const node = event.target.closest('.cell');
    if (!node) return;
    this.onPressEnd();
    const id = Number(node.dataset.id);
    const index = Number(node.dataset.n);
    this.press = {
      x: event.clientX, y: event.clientY,
      timer: setTimeout(() => {
        this.press = null;
        // The click some browsers send when the finger lifts. Chrome on
        // Android sends none after a long press, so the swallow ends shortly
        // after the lift (onPressEnd) instead of eating the next real tap.
        this.swallowUntil = Infinity;
        if (this.selection.has(id)) this.selection.delete(id);
        else this.selection.add(id);
        this.lastAnchor = index;
        this.cursor = index;
        try { navigator.vibrate?.(12); } catch { /* not every phone */ }
        this.emitSelection();
        this.render();
      }, LONG_PRESS_MS),
    };
  }

  onPressMove(event) {
    if (!this.press) return;
    if (Math.abs(event.clientX - this.press.x) > 8 || Math.abs(event.clientY - this.press.y) > 8) {
      this.onPressEnd();                    // a scroll, not a press
    }
  }

  /** A click now is the one that ends a long press. */
  swallowing() {
    return performance.now() < (this.swallowUntil || 0);
  }

  onPressEnd(lifted = false) {
    if (lifted && this.swallowUntil === Infinity) this.swallowUntil = performance.now() + 400;
    if (!this.press) return;
    clearTimeout(this.press.timer);
    this.press = null;
  }

  onClick(event, isDouble = false) {
    if (this.swallowing()) {
      this.swallowUntil = 0;
      return;
    }
    const sectionButton = event.target.closest('[data-select-section]');
    if (sectionButton) {
      const head = sectionButton.parentElement;
      const key = head.dataset.head;
      const ids = (this.segments.find((s) => s.key === key)?.items || [])
        .map((item) => item[0]);
      const allSelected = ids.every((id) => this.selection.has(id));
      ids.forEach((id) => (allSelected ? this.selection.delete(id) : this.selection.add(id)));
      this.emitSelection();
      this.render();
      return;
    }

    const node = event.target.closest('.cell');
    if (!node) return;
    const id = Number(node.dataset.id);
    const index = Number(node.dataset.n);
    this.cursor = index;

    const viaPick = !!event.target.closest('.pick');
    const additive = event.metaKey || event.ctrlKey || viaPick;
    const ranged = event.shiftKey;

    if (ranged && this.lastAnchor >= 0) {
      // Shift-click and Shift+Arrow are the same gesture with different
      // hardware, so they share one run: clicking further out extends the
      // range, clicking back inside it shrinks it again.
      if (this.rangeBase === null) this.rangeBase = new Set(this.selection);
      this.applyRange();
      this.render();
      return;
    }

    this.rangeBase = null;

    if (additive || this.selecting) {
      if (this.selection.has(id)) this.selection.delete(id);
      else this.selection.add(id);
      this.lastAnchor = index;
      this.emitSelection();
      this.render();
      return;
    }

    this.lastAnchor = index;

    if (!isDouble) {
      this.dispatchEvent(new CustomEvent('open', { detail: { id, index } }));
    }
  }

  emitSelection() {
    this.selecting = this.selection.size > 0;
    this.container.classList.toggle('selecting', this.selecting);
    this.dispatchEvent(new CustomEvent('selection', {
      detail: { ids: [...this.selection] },
    }));
  }

  clearSelection() {
    this.selection.clear();
    this.rangeBase = null;
    this.emitSelection();
    this.render();
  }

  selectAll() {
    for (const cell of this.layout.cells) this.selection.add(cell.id);
    this.rangeBase = null;
    this.emitSelection();
    this.render();
  }

  /** Selection = what was there before this run, plus anchor…cursor. */
  applyRange() {
    const base = this.rangeBase || new Set();
    const [from, to] = [this.lastAnchor, this.cursor].sort((a, b) => a - b);
    this.selection.clear();
    for (const id of base) this.selection.add(id);
    for (let i = from; i <= to; i++) {
      const cell = this.layout.cells[i];
      if (cell) this.selection.add(cell.id);
    }
    this.emitSelection();
  }

  /* -- navigation ----------------------------------------------------- */

  get ids() {
    return this.layout.cells.map((c) => c.id);
  }

  /** Ids with a generated thumbnail (flag bit 2), for the viewer's filmstrip. */
  /** The ids that have a thumbnail, each to its version: the viewer's
   *  filmstrip asks for the current one, so a photograph turned since is not
   *  shown from the browser's year-long cache the old way up. */
  thumbedIds() {
    const versions = new Map();
    for (const cell of this.layout.cells) if (cell.flags & 2) versions.set(cell.id, cell.v || 0);
    return versions;
  }

  indexOfId(id) {
    return this.layout.cells.findIndex((c) => c.id === id);
  }

  /**
   * Move the keyboard cursor, and with Shift held, take the selection with it.
   *
   * The behaviour people already know from Explorer and Photos: Shift fixes an
   * anchor where the cursor was, and every Shift+Arrow after that selects the
   * run between the anchor and wherever the cursor has reached. Coming back
   * towards the anchor gives items up again — which is why the run remembers
   * what was selected before it started, rather than only ever adding.
   *
   * Up and down are worked out from where the tiles actually are, not from a
   * column count, because the justified and masonry layouts do not have one.
   */
  moveCursor(direction, { extend = false } = {}) {
    const cells = this.layout.cells;
    if (!cells.length) return;
    if (extend) {
      if (this.rangeBase === null) {
        this.lastAnchor = this.cursor < 0 ? 0 : this.cursor;
        this.rangeBase = new Set(this.selection);
      }
    } else {
      // A plain arrow ends the run but keeps what it selected: the next Shift
      // press starts a fresh range from here, as it does everywhere else.
      this.rangeBase = null;
      this.lastAnchor = this.cursor;
    }
    if (this.cursor < 0) {
      this.cursor = 0;
    } else if (direction === 'next') {
      this.cursor = Math.min(cells.length - 1, this.cursor + 1);
    } else if (direction === 'prev') {
      this.cursor = Math.max(0, this.cursor - 1);
    } else {
      const current = cells[this.cursor];
      const wantY = direction === 'down' ? current.y + current.h + 1 : current.y - 1;
      let best = this.cursor;
      let bestScore = Infinity;
      for (let i = 0; i < cells.length; i++) {
        const cell = cells[i];
        if (direction === 'down' ? cell.y < wantY : cell.y + cell.h > wantY + 1) continue;
        const score = Math.abs(cell.x - current.x) + Math.abs(cell.y - wantY) * 2;
        if (score < bestScore) {
          bestScore = score;
          best = i;
        }
      }
      this.cursor = best;
    }
    if (extend) this.applyRange();
    this.scrollToIndex(this.cursor);
    this.render();
  }

  scrollToIndex(index) {
    const cell = this.layout.cells[index];
    if (!cell) return;
    const top = this.scroller.scrollTop;
    const height = this.scroller.clientHeight;
    if (cell.y < top + 60) this.scroller.scrollTop = Math.max(0, cell.y - 70);
    else if (cell.y + cell.h > top + height) {
      this.scroller.scrollTop = cell.y + cell.h - height + 20;
    }
  }

  scrollToRatio(ratio) {
    const max = Math.max(0, this.layout.height - this.scroller.clientHeight);
    const before = this.scroller.scrollTop;
    this.scroller.scrollTop = max * Math.max(0, Math.min(1, ratio));
    // A jump until the scroll settles: nothing on the way will be looked at.
    if (this.scroller.scrollTop !== before) this.jumpAsked = true;
  }

  currentId() {
    return this.layout.cells[this.cursor]?.id ?? null;
  }

  destroy() {
    this.scroller.removeEventListener('scroll', this.onScroll);
    window.removeEventListener('pointerup', this.onLift);
    window.removeEventListener('pointercancel', this.onLift);
    window.removeEventListener('click', this.onAnyClick, true);
    this.resizeObserver.disconnect();
    clearTimeout(this.settleTimer);
    if (this.topUp) cancelAnimationFrame(this.topUp);
    document.removeEventListener('visibilitychange', this.onVisibility);
    this.prefetcher.want([]);
  }
}

/**
 * Append `incoming` segments to `segments` in place; returns items added.
 *
 * A day cut in two by a piece boundary is joined back into one section, and
 * ids already present are dropped, so a photo that slid across the boundary
 * while the library changed between requests is not shown twice.
 */
export function mergeSegments(segments, incoming) {
  const held = new Set();
  for (const segment of segments) for (const item of segment.items) held.add(item[0]);
  let added = 0;
  for (const segment of incoming || []) {
    const items = segment.items.filter((item) => !held.has(item[0]));
    if (!items.length) continue;
    added += items.length;
    for (const item of items) held.add(item[0]);
    const last = segments[segments.length - 1];
    // A loop, not push(...items): spreading tens of thousands of arguments
    // overflows the call stack in some engines.
    if (last && last.key === segment.key) for (const item of items) last.items.push(item);
    else segments.push({ key: segment.key, items });
  }
  return added;
}

export { formatDuration, formatSection };

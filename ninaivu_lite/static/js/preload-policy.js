/**
 * What the gallery fetches ahead of itself, how much, and how many at once.
 *
 * Kept apart from the grid (grid.js) so the rules are plain functions that a
 * test can call. Ported from Ninaivu's adaptive preloader (1.10.10).
 *
 *  - connectionProfile: how much this device and link should fetch ahead.
 *  - predictionMs: how far ahead, in time, a scroll at a given speed is
 *    looked: further the slower it goes, nothing during a fling.
 *  - interleaveDirectional: nearest first both ways, two ahead for one behind.
 *  - AdaptivePreloader: fetches thumbnails into the browser's cache, a few
 *    at a time, deduplicated, each one stoppable.
 */

/** Faster than this (px/ms: 5,000 px a second) is a fling: flying past. */
export const FLING_PX_MS = 5;

/**
 * How much to fetch ahead on this device and link. A link that asked to save
 * data (Data Saver) or is 2G gets a screen and a half on and nothing behind,
 * one picture at a time; a device short of memory (2 GB or less) gets less.
 */
export function connectionProfile({
  connection = globalThis.navigator?.connection,
  deviceMemory = globalThis.navigator?.deviceMemory,
  touch = typeof globalThis.matchMedia === 'function'
    ? globalThis.matchMedia('(hover: none)').matches : false,
} = {}) {
  const effective = connection?.effectiveType || '';
  const lean = Boolean(connection?.saveData) || effective === '2g' || effective === 'slow-2g';
  const lowMemory = Number(deviceMemory || 4) <= 2;

  if (lean) {
    return { lean: true, touch, lowMemory, aheadScreens: 1.5, behindScreens: 0, concurrency: 1 };
  }
  let aheadScreens = touch ? 5 : 8;
  let behindScreens = touch ? 2 : 3;
  let concurrency = touch ? 2 : 4;
  if (lowMemory) {
    aheadScreens *= 0.6;
    behindScreens *= 0.5;
    concurrency = Math.min(concurrency, 2);
  }
  return { lean: false, touch, lowMemory, aheadScreens, behindScreens, concurrency };
}

/** How far ahead, in time, a scroll at *speed* (px/ms) is fetched: a slow
 *  read is looked at furthest ahead, a fling not at all (nothing on the way
 *  will be looked at, and what is queued would only hold up where it lands). */
export function predictionMs(speed) {
  const value = Math.abs(speed);
  if (value >= FLING_PX_MS) return 0;
  if (value < 0.4) return 1400;
  if (value < 1.5) return 900;
  if (value < 3) return 600;
  return 450;
}

/** *ahead* and *behind*, nearest first, *aheadRatio* ahead for each behind. */
export function interleaveDirectional(ahead, behind, aheadRatio = 2) {
  const result = [];
  let i = 0;
  let j = 0;
  while (i < ahead.length || j < behind.length) {
    for (let n = 0; n < aheadRatio && i < ahead.length; n += 1) result.push(ahead[i++]);
    if (j < behind.length) result.push(behind[j++]);
  }
  return result;
}

/** More at once when one connection carries them all (HTTP/2 or 3, behind a
 *  proxy of the household's own): there, more in flight hides the round trip.
 *  Ninaivu Lite itself speaks HTTP/1.1, and keeps the profile's number. */
export function nextHopConcurrency(base) {
  if (!globalThis.performance?.getEntriesByType) return base;
  const resources = globalThis.performance.getEntriesByType('resource');
  for (let i = resources.length - 1; i >= Math.max(0, resources.length - 50); i -= 1) {
    const protocol = resources[i]?.nextHopProtocol || '';
    if (protocol === 'h2' || protocol === 'h3') return Math.min(8, base + 4);
  }
  return base;
}

function idleSchedule(callback) {
  if (typeof globalThis.requestIdleCallback === 'function') {
    return globalThis.requestIdleCallback(callback, { timeout: 800 });
  }
  return globalThis.setTimeout(callback, 150);
}

function idleCancel(handle) {
  if (typeof globalThis.cancelIdleCallback === 'function') globalThis.cancelIdleCallback(handle);
  else globalThis.clearTimeout(handle);
}

/** A request this young is called off when no longer wanted; an older one is
 *  let finish, being most of the way there. */
const YOUNG_MS = 300;

/**
 * Thumbnails fetched ahead into the browser's cache (they are kept for a
 * year, at an address that changes with the picture, so a tile asking for one
 * later is answered from the cache). fetch() rather than an Image: nothing is
 * decoded until a tile shows it, and each one can be stopped.
 */
export class AdaptivePreloader {
  /** `idle`: whether the page is ready for more, asked before each start
   *  (the grid says no while a tile on screen still waits for its picture). */
  constructor({ idle = () => true, profile = () => connectionProfile() } = {}) {
    this.idle = idle;
    this.profile = profile;
    this.queue = [];
    this.active = new Map();     // url -> { controller, startedAt, priority }
    this.done = new Map();       // url -> when it arrived
    this.idleHandle = null;
    this.metrics = { requested: 0, completed: 0, cancelled: 0, failed: 0 };
  }

  has(url) {
    return Boolean(url) && this.done.has(url);
  }

  clearOldDone() {
    if (this.done.size <= 5000) return;
    const keepAfter = performance.now() - 10 * 60 * 1000;
    for (const [url, at] of this.done) {
      if (at < keepAfter) this.done.delete(url);
      if (this.done.size <= 3500) break;
    }
    // Still too many (all recent): the oldest go.
    while (this.done.size > 5000) this.done.delete(this.done.keys().next().value);
  }

  /**
   * Fetch *urls*, in that order, in place of whatever was waiting.
   *
   * What is already on its way carries on, unless `cancel`: then a request
   * no longer wanted (and not in `keep`) is stopped while it is young, or
   * whatever its age with `cancel: 'all'` (a photo opened over the grid, a
   * new set of results). `idleOnly` starts them only once the page is idle.
   * A Set in place of the options is `keep`, with `cancel: true`.
   */
  want(urls, options = {}) {
    const opts = options instanceof Set ? { cancel: true, keep: options } : options;
    const { cancel = false, keep = null, idleOnly = false, priority = 'warm' } = opts;
    const unique = [...new Set(urls.filter(Boolean))];
    const wanted = new Set(unique);

    if (cancel) {
      const now = performance.now();
      for (const [url, entry] of this.active) {
        if (wanted.has(url) || keep?.has(url)) continue;
        if (cancel !== 'all' && now - entry.startedAt > YOUNG_MS) continue;
        entry.controller.abort();
        this.active.delete(url);
        this.metrics.cancelled += 1;
      }
    }

    this.clearOldDone();
    this.queue = unique
      .filter((url) => !this.active.has(url) && !this.done.has(url))
      .map((url) => ({ url, priority }));

    if (this.idleHandle != null) {
      idleCancel(this.idleHandle);
      this.idleHandle = null;
    }
    if (idleOnly && this.queue.length) {
      this.idleHandle = idleSchedule(() => {
        this.idleHandle = null;
        this.pump();
      });
    } else {
      this.pump();
    }
  }

  pump() {
    if (this.idleHandle != null || !this.queue.length || !this.idle()) return;
    const limit = nextHopConcurrency(this.profile().concurrency);
    while (this.active.size < limit && this.queue.length && this.idle()) {
      const { url, priority } = this.queue.shift();
      if (this.done.has(url) || this.active.has(url)) continue;
      this.start(url, priority);
    }
  }

  start(url, priority) {
    const controller = new AbortController();
    const entry = { controller, startedAt: performance.now(), priority };
    this.active.set(url, entry);
    this.metrics.requested += 1;
    const init = { signal: controller.signal, credentials: 'same-origin', cache: 'force-cache' };
    // A hint where the browser takes one: behind what the screen is showing.
    try { init.priority = 'low'; } catch { /* older engines ignore it */ }
    fetch(url, init).then(async (response) => {
      if (!response.ok) throw new Error(`thumbnail preload ${response.status}`);
      // Read whole, so the cache keeps a complete answer to reuse.
      await response.blob();
      this.done.set(url, performance.now());
      this.metrics.completed += 1;
    }).catch((error) => {
      if (error?.name !== 'AbortError') this.metrics.failed += 1;
    }).finally(() => {
      if (this.active.get(url) === entry) this.active.delete(url);
      this.pump();
    });
  }

  destroy() {
    if (this.idleHandle != null) idleCancel(this.idleHandle);
    this.idleHandle = null;
    this.queue = [];
    for (const entry of this.active.values()) entry.controller.abort();
    this.active.clear();
  }
}

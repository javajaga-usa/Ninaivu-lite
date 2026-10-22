/** Indexing the library: the rescan buttons, the scan's bar and the strip of running jobs. */

import { SCAN_COUNTS, timeLeft } from '../api.js';
import { renderActivity, subscribeActivity } from '../activity.js';
import * as i18n from '../i18n.js';
import { refresh, showTab } from '../admin.js';
import { $, adminApi, json, toast } from './shared.js';

export async function runScan(full) {
  try {
    await adminApi.rescan(full);
    toast(full ? i18n.t('Full re-index started.') : i18n.t('Scanning for changes…'));
    watchActivity();
  } catch (exc) { toast(exc.message, true); }
}

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
    // M85: while the folders are walked nothing is known of the total, and
    // the bar said "Indexing 12,000 / 0" for as long as that took.
    const walking = scan.status === 'walking';
    const counted = counter
      ? counter(scan.tagged.toLocaleString(), scan.tag_total.toLocaleString())
      : scan.message || (walking
        ? `${i18n.t('Looking for files')} · ${(scan.processed || 0).toLocaleString()}`
        : i18n.t('Indexing {done} / {total}',
          { done: scan.processed.toLocaleString(), total: scan.total.toLocaleString() }));
    const left = timeLeft(scan.eta);
    const line = left ? `${counted} · ${left}` : counted;
    const text = scan.folder ? `${line} · ${scan.folder}` : line;
    const fill = $('#scan-fill');
    if (fill) fill.style.width = `${percent ?? 0}%`;
    const strapline = $('#scan-text');
    if (strapline) strapline.textContent = text;
  }
  const phase = `${scan.status}:${scan.added}:${scan.removed}:${scan.unreadable || 0}`;
  const ended = scan.status === 'done' || scan.status === 'error';
  if (lastPhase === null && ended) {
    // A scan that ended before the console opened: the page has just read
    // what it found. Refreshing on the first tick read the counts, People
    // and Albums twice on every open. (From Ninaivu 2.1.0.)
    lastPhase = phase;
    if (scan.unreadable) toast(i18n.t('Some folders could not be read, so their photos were kept as they were. Check that this computer may read them, then rescan.'), true);
    return;
  }
  if (phase !== lastPhase && ended) {
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
export function watchActivity() {
  stopActivity?.();
  stopActivity = subscribeActivity(onActivity);
}

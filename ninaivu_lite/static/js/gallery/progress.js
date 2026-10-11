/** The strip that says what Ninaivu is doing, kept up to date by asking. */

import { fetchActivity, renderActivity } from '../activity.js';
import * as i18n from '../i18n.js';
import { isNetworkFailure, setServerOffline } from './connection.js';
import { refreshCounts, refreshFacets, refreshStatus, reload } from './data.js';
import { $, state } from './state.js';
import { toast } from './toast.js';

let lastPhase = null;
let progressPoll = null;
let countsAskedAt = 0;

/** refreshStatus has just asked for the counts too. */
export function noteCountsAsked() { countsAskedAt = Date.now(); }

// The item counts in the sidebar climb as a scan adds files, but counting them
// means counting the library, so while a scan runs they are brought up to date
// on this slower cadence and the strip itself asks only for the counters.
const COUNTS_EVERY_MS = 30000;

// Coming back to the tab is the moment to catch up on what happened while it
// was in the background, and to restart the loop that visibility stopped.
document.addEventListener('visibilitychange', () => {
  // Only once the gallery has started: behind the sign-in gate there is
  // nothing to refresh, and a session that has ended must not keep asking.
  if (!document.hidden && state.status) refreshStatus();
});

// Ask now, and let the answer set the next tick's cadence.
export function kickActivity() {
  clearTimeout(progressPoll);
  pollProgress();
}

async function pollProgress() {
  let activity;
  try {
    activity = await fetchActivity();
  } catch (err) {
    // Offline has its own reconnect loop, and it ends in refreshStatus(),
    // which starts this one again.
    if (isNetworkFailure(err)) {
      setServerOffline();
      return;
    }
    progressPoll = setTimeout(pollProgress, 10000);
    return;
  }
  onActivity(activity);
}

// Everything Ninaivu is doing, not only the scan.
//
// The strip used to be one bar, and it was the library scan. Anything else
// that held the disk — a consolidation, a cloud upload, a storage check, a
// straightening pass — ran with nothing on screen to say so, and a machine
// with its fan up had no answer here for why. Each of those is now a line.
function onActivity(activity) {
  if (!activity) return;
  const scan = activity.scan;
  const strip = $('#scan-strip');
  // Guests never see it: the strip is hidden for them on sign-in, and a poll
  // that arrives afterwards must not put it back. The server sends them an
  // empty list anyway — which drives are being read is not their business —
  // and this is the same rule stated where the strip is drawn.
  const allowed = Boolean(state.user?.can?.manage_library);
  const running = Boolean(activity.running);
  strip.hidden = !running || !allowed;

  // Not clickable here. The console page that owns each job is not somewhere
  // the family app can send anybody, so the strip says what is happening and
  // leaves stopping it to the console.
  renderActivity($('#job-rows'), allowed ? activity.jobs : []);

  // Keep the strip moving.
  //
  // Progress is pushed over an event stream on the console, but that endpoint
  // is an admin route and does not exist on this port at all — so here the
  // only way to see work advance is to ask.
  //
  // Two cadences: quick while something is running, slow otherwise so a job
  // started from the console still turns up here within a few seconds. Both
  // stop when the tab is not being looked at, because a household leaves this
  // open on five devices and none of them should be asking about a library
  // nobody is watching; coming back to the tab starts them again.
  //
  // Each tick asks for counters only. It used to ask /api/status, which counts
  // the whole library — every two seconds, for the length of the scan, against
  // the database the scan was writing to.
  clearTimeout(progressPoll);
  if (!document.hidden) {
    progressPoll = setTimeout(pollProgress, running ? 2000 : 10000);
    if (running && Date.now() - countsAskedAt > COUNTS_EVERY_MS) {
    countsAskedAt = Date.now();
    refreshCounts();          // the numbers climb as a scan adds files
  }
  }

  if (!scan) return;
  // Refresh the gallery when a phase completes, not on every tick.
  const phase = `${scan.status}:${scan.added}:${scan.removed}`;
  // A scan that had already finished when this page opened is old news: the
  // page has just loaded what it found. Every visit used to say "116 added,
  // 0 removed." again, hours later, and load the gallery a second time.
  if (lastPhase === null) {
    lastPhase = phase;
    if (scan.status === 'done' || scan.status === 'error') return;
  }
  if (phase !== lastPhase && (scan.status === 'done' || scan.status === 'error')) {
    lastPhase = phase;
    refreshStatus();
    refreshFacets();
    if (!state.loading) reload();
    if (scan.status === 'done' && (scan.added || scan.removed)) {
      toast(i18n.t('{added} added, {removed} removed.', { added: scan.added.toLocaleString(), removed: scan.removed.toLocaleString() }));
    }
  }
}

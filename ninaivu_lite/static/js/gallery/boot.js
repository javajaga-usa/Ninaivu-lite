/** The cover shown while the gallery first loads, and its progress bar. */

import * as i18n from '../i18n.js';
import { $ } from './state.js';

/* The first load ---------------------------------------------------------
 *
 * Real steps, not a bar that fills on a timer. Each one moves when the thing
 * it names has actually happened, so the percentage is a fact rather than an
 * animation — and if a step never arrives, the bar stops where it stopped and
 * says what it is waiting for, which is the truth.
 *
 * The weights are roughly what each step costs on a large library, so the bar
 * does not race to 90% and then sit there.
 */
// The third item is a key, not a sentence: `i18n.t` is called where the line
// is put on screen, because this array is built as the file loads and the
// locale has not been fetched yet — translating here would freeze all four in
// English, and these are the first words anybody sees.
const BOOT_STEPS = [
  ['signed-in', 15, i18n.key('Checking who you are')],
  ['status', 30, i18n.key('Opening your library')],
  ['photographs', 85, i18n.key('Finding your photographs')],
  ['painted', 100, i18n.key('Almost there')],
];
let bootAt = 0;

export function bootStep(name) {
  const box = $('#boot');
  if (!box || box.hidden) return;
  const step = BOOT_STEPS.find((s) => s[0] === name);
  if (!step || step[1] <= bootAt) return;
  bootAt = step[1];
  const fill = $('#boot-fill');
  if (fill) fill.style.width = `${bootAt}%`;
  const percent = $('#boot-percent');
  if (percent) percent.textContent = `${bootAt}%`;
  const text = $('#boot-step');
  // The next step's name, because what it says it is doing should be the
  // thing it is waiting on, not the thing it has just finished.
  const next = BOOT_STEPS[BOOT_STEPS.indexOf(step) + 1];
  if (text && next) text.textContent = i18n.t(next[2]);
}

// Whatever goes wrong, the cover comes off within this long. Every path that
// ends in something worth looking at calls bootDone itself; this is for the
// path nobody thought of, because an overlay that outlives its page is a
// worse bug than the empty page it replaced.
setTimeout(() => bootDone(), 30000);

export function bootDone() {
  const box = $('#boot');
  if (!box || box.hidden) return;
  bootStep('painted');
  box.classList.add('done');
  // Left in the DOM for the length of the fade, then taken out so it can
  // never swallow a click.
  setTimeout(() => { box.hidden = true; }, 400);
}

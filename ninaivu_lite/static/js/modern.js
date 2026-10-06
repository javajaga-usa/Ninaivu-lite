/*
 * Says "this browser can run Ninaivu", by running at all.
 *
 * A module, written with the newest syntax the pages use (`||=`, `?.`,
 * `??`): a browser that cannot parse it never sets the flag, and
 * old-browser.js then shows its message instead of a loading screen that
 * never ends. The APIs every page relies on are checked too.
 */
const ok = { modules: true };
ok.syntax ||= Boolean(globalThis?.document ?? null);
ok.apis = typeof Element.prototype.replaceChildren === 'function'
  && typeof String.prototype.matchAll === 'function';
if (ok.syntax && ok.apis) window.__ninaivuCanRun = true;

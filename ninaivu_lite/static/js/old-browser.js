/*
 * "This browser is too old", where otherwise there would be nothing.
 *
 * The pages are ES modules using syntax from 2020 (Safari 14, Chrome 85). A
 * browser older than that skips them, or fails to parse them, silently: the
 * family app sat on its loading screen for ever and the share page said
 * "Loading…" for ever. modern.js sets a flag when it runs; once the page has
 * loaded, no flag means the message in the page (#too-old) is shown.
 *
 * Deliberately old-fashioned JavaScript (ES3: var, function, no arrows), so
 * the browser it is for can run it. A classic script, not inline, because the
 * pages' security policy allows scripts from files only.
 */
(function () {
  var shown = false;
  function check() {
    if (shown || window.__ninaivuCanRun) return;
    var note = document.getElementById('too-old');
    if (!note) return;
    shown = true;
    note.style.display = 'block';
    var boot = document.getElementById('boot');
    if (boot) boot.style.display = 'none';
  }
  // Modules run before the load event, so by then the flag is set or never
  // will be. No timer of its own: on a slow connection a timer would call a
  // perfectly good browser too old.
  if (window.addEventListener) {
    window.addEventListener('load', function () { setTimeout(check, 300); }, false);
  } else if (window.attachEvent) {
    window.attachEvent('onload', check);
  }
}());

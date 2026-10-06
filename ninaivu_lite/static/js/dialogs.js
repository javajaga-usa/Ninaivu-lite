/**
 * Dialogs that behave like dialogs.
 *
 * The viewer, the modals, the profile sheet, the command palette and the
 * sign-in screen all said `aria-modal`, but the page behind them stayed live:
 * Tab walked out of the dialog into the gallery underneath, a screen reader
 * read the sidebar, and closing one left the focus on nothing.
 *
 * So, for whichever of them is on top: the rest of the page is made `inert`
 * while it is open, the focus goes into it if it is not there already, and
 * when it closes the focus goes back to where it was before it opened.
 *
 * It watches the `hidden` attribute rather than asking every dialog to call
 * in, because that is how every one of them is shown and hidden already.
 */

//: Top-level elements that are a dialog over the page.
const OVERLAYS = '.viewer, .modal, .sheet, .gate, .palette';
//: The page behind them. Nothing else is touched: a toast made inert would
//: never be read out, a <dialog> opened with showModal() looks after itself,
//: and the field copyText() adds for a moment has to take the focus.
const PAGE = '.skip-link, .topbar, .shell, .ios-install-banner';
//: What the focus goes to first. A dialog with no field takes the focus
//: itself, so Tab starts at its first control, and Space or Enter does not
//: press a toolbar button the person never chose (in the viewer, Space is
//: the slideshow's).
const FIELDS = '[autofocus], input:not([type="hidden"]):not([type="range"]), select, textarea';

function focusable(node) {
  return !node.hidden && !node.disabled && !node.closest('[inert]')
    && node.getClientRects().length > 0;
}

export function wireDialogs(root = document.body) {
  const returnTo = new Map();

  const overlays = () => [...root.children].filter((el) => el.matches(OVERLAYS) && !el.hidden);

  const sync = () => {
    const open = overlays();
    const top = open[open.length - 1] || null;
    for (const el of root.children) {
      if (!el.matches(PAGE) && !el.matches(OVERLAYS)) continue;
      const wanted = Boolean(top) && el !== top;
      if (Boolean(el.inert) !== wanted) {
        el.inert = wanted;
        // An older browser without `inert` still hears that this is hidden.
        if (!('inert' in HTMLElement.prototype)) el.toggleAttribute('aria-hidden', wanted);
      }
    }
  };

  const focusInto = (dialog) => {
    // After the dialog's own code has had its turn: several put the focus
    // on their first field themselves, and know better which one.
    setTimeout(() => {
      if (dialog.hidden || dialog.contains(document.activeElement)) return;
      for (const candidate of dialog.querySelectorAll(FIELDS)) {
        if (!focusable(candidate)) continue;
        candidate.focus();
        if (dialog.contains(document.activeElement)) return;
      }
      if (!dialog.hasAttribute('tabindex')) dialog.setAttribute('tabindex', '-1');
      dialog.focus({ preventScroll: true });
    }, 80);
  };

  new MutationObserver((records) => {
    let changed = false;
    for (const record of records) {
      const el = record.target;
      if (record.type === 'attributes') {
        if (el.parentElement !== root || !el.matches(OVERLAYS)) continue;
        changed = true;
        if (!el.hidden) {
          const from = document.activeElement;
          if (!returnTo.has(el) && from && from !== document.body && !el.contains(from)) {
            returnTo.set(el, from);
          }
          focusInto(el);
        } else {
          const back = returnTo.get(el);
          returnTo.delete(el);
          const lost = !document.activeElement || document.activeElement === document.body
            || el.contains(document.activeElement);
          if (back && lost) {
            sync();
            if (back.isConnected && !back.closest('[inert]')) back.focus();
          }
        }
      } else if (el === root) {
        changed = true;               // a palette added to the page
      }
    }
    if (changed) sync();
  }).observe(root, { attributes: true, attributeFilter: ['hidden'], subtree: true, childList: true });

  sync();
}

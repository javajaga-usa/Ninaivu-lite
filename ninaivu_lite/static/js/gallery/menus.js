/** The topbar's small drop-down menus. */

//: The topbar's drop-down menus (view, profile), so Escape can close
//: whichever is open.
const menus = [];

/**
 * A button that opens a small menu under it.
 *
 * The same behaviour as the "More" menus: the button toggles it, a click
 * outside or on an item closes it, Escape closes it and gives focus back to
 * the button. Opened from the keyboard, focus goes to the first item and the
 * arrow keys move between items. An item marked `data-keep-open` (the theme
 * and the language, which somebody may press twice to get where they want)
 * leaves the menu open.
 */
export function wireMenu(button, menu) {
  if (!button || !menu) return;
  const items = () => [...menu.querySelectorAll('button')]
    .filter((item) => !item.hidden && item.offsetParent !== null);
  const close = (returnFocus = false) => {
    if (!menu.classList.contains('open')) return false;
    const hadFocus = menu.contains(document.activeElement);
    menu.classList.remove('open');
    button.setAttribute('aria-expanded', 'false');
    if (returnFocus && hadFocus) button.focus();
    return true;
  };
  menus.push(close);
  button.addEventListener('click', (event) => {
    event.stopPropagation();
    const open = !menu.classList.contains('open');
    menus.forEach((other) => other !== close && other());
    menu.classList.toggle('open', open);
    button.setAttribute('aria-expanded', String(open));
    if (open && event.detail === 0) items()[0]?.focus();
  });
  menu.addEventListener('click', (event) => {
    const item = event.target.closest('button');
    if (item && !item.hasAttribute('data-keep-open')) close();
  });
  menu.addEventListener('keydown', (event) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    event.preventDefault();
    event.stopPropagation();          // not the gallery's own arrow keys
    const list = items();
    const at = list.indexOf(document.activeElement);
    const next = at < 0 ? 0 : (at + (event.key === 'ArrowDown' ? 1 : -1) + list.length) % list.length;
    list[next]?.focus();
  });
  document.addEventListener('click', (event) => {
    if (menu.contains(event.target) || button.contains(event.target)) return;
    close();
  });
}

/** Close whichever topbar menu is open. True if one was. */
export function closeMenus() {
  return menus.map((close) => close(true)).some(Boolean);
}

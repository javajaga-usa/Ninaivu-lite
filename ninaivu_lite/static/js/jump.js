/**
 * "Jump to": a button over the gallery that names the month on screen and
 * opens a list of the years and months the gallery, as filtered right now,
 * actually has photographs in. Picking one scrolls the grid there, first
 * fetching more of it when that month is further down than has arrived.
 *
 * The list comes from /api/months, which the server remembers until the
 * index changes, so opening it again costs nothing. Shown only for the two
 * date orders, where months are the grid's own order.
 */

import { api } from './api.js';
import * as i18n from './i18n.js';
import { sectionAt } from './layout.js';

const DATE_SORTS = new Set(['date_desc', 'date_asc']);
const UNDATED = 'unknown';

/**
 * `grid` is the gallery's Grid; `filters()` the query the grid was loaded
 * with; `hasMore()` whether more of it is still to come and `loadMore()`
 * fetches the next piece now (false when nothing came).
 */
export function wireJump({ grid, filters, hasMore, loadMore }) {
  const button = document.getElementById('jump-btn');
  const label = document.getElementById('jump-label');
  const panel = document.getElementById('jump-panel');
  const body = document.getElementById('jump-body');
  if (!button || !panel || !body) return { open() {}, sync() {} };

  let current = null;        // 'YYYY-MM' (or 'unknown') on screen now
  let asking = null;         // AbortController of the list being fetched
  let ticket = 0;            // the latest jump; an older one gives way

  const dated = (key) => /^\d{4}-\d{2}/.test(key || '');
  const monthOf = (key) => (dated(key) ? key.slice(0, 7) : key === UNDATED ? UNDATED : null);
  const sortOf = () => filters().sort || 'date_desc';
  const isOpen = () => !panel.hidden;

  function monthName(month, style) {
    const date = new Date(`${month}-01T00:00:00`);
    if (Number.isNaN(date.getTime())) return month;
    return new Intl.DateTimeFormat(i18n.locale(), style).format(date);
  }

  function describe(month) {
    if (!month) return i18n.t('Jump to');
    if (month === UNDATED) return i18n.t('Undated');
    return monthName(month, { month: 'short', year: 'numeric' });
  }

  /** The button, shown only where there is somewhere to go. */
  function sync() {
    const heads = grid.layout?.headers || [];
    let months = 0;
    let last = null;
    for (const head of heads) {
      const month = monthOf(head.key);
      if (month && month !== last) {
        months += 1;
        last = month;
        if (months > 1) break;
      }
    }
    const show = DATE_SORTS.has(sortOf()) && grid.layout?.cells?.length > 0
      && (months > 1 || hasMore());
    button.hidden = !show;
    if (!show && isOpen()) close();
    if (show) showCurrent(monthOf(sectionAt(grid.layout, grid.scroller.scrollTop + 40)?.key));
  }

  function showCurrent(month) {
    current = month;
    const text = describe(month);
    if (label.textContent !== text) label.textContent = text;
    const name = month
      ? i18n.t('Jump to a year or month. Showing {month}.', { month: text })
      : i18n.t('Jump to a year or month');
    button.setAttribute('aria-label', name);
    button.title = `${name} (D)`;
  }

  /* -- the list ----------------------------------------------------------- */

  function message(text) {
    const note = document.createElement('p');
    note.className = 'jump-note';
    note.textContent = text;
    body.replaceChildren(note);
  }

  function render({ months, undated }) {
    const ascending = sortOf() === 'date_asc';
    const list = ascending ? [...months].reverse() : months;
    const years = [];
    for (const entry of list) {
      const year = entry.month.slice(0, 4);
      let group = years[years.length - 1];
      if (!group || group.year !== year) {
        group = { year, count: 0, months: [] };
        years.push(group);
      }
      group.count += entry.count;
      group.months.push(entry);
    }
    const number = (n) => Number(n).toLocaleString(i18n.locale());
    const nodes = [];
    for (const group of years) {
      const section = document.createElement('section');
      section.className = 'jump-year';
      const head = document.createElement('button');
      head.type = 'button';
      head.className = 'jump-year-btn';
      head.dataset.jump = group.year;
      const name = document.createElement('span');
      name.textContent = group.year;
      const count = document.createElement('small');
      count.textContent = number(group.count);
      head.append(name, count);
      head.setAttribute('aria-label', `${group.year}, ${i18n.items(group.count)}`);
      const row = document.createElement('div');
      row.className = 'jump-months';
      for (const entry of group.months) {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'jump-month';
        chip.dataset.jump = entry.month;
        chip.textContent = monthName(entry.month, { month: 'short' });
        const full = `${monthName(entry.month, { month: 'long', year: 'numeric' })}, ${i18n.items(entry.count)}`;
        chip.title = full;
        chip.setAttribute('aria-label', full);
        if (entry.month === current) chip.setAttribute('aria-current', 'true');
        row.appendChild(chip);
      }
      if (current && current.startsWith(group.year)) head.classList.add('current');
      section.append(head, row);
      nodes.push(section);
    }
    if (undated) {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'jump-month jump-undated';
      chip.dataset.jump = UNDATED;
      chip.textContent = i18n.t('Undated');
      chip.title = i18n.items(undated);
      chip.setAttribute('aria-label', `${i18n.t('Undated')}, ${i18n.items(undated)}`);
      if (current === UNDATED) chip.setAttribute('aria-current', 'true');
      const section = document.createElement('section');
      section.className = 'jump-year';
      section.appendChild(chip);
      if (ascending) nodes.unshift(section); else nodes.push(section);
    }
    if (!nodes.length) message(i18n.t('Nothing to show'));
    else body.replaceChildren(...nodes);
  }

  async function open() {
    if (button.hidden) return;
    asking?.abort();
    asking = new AbortController();
    const signal = asking.signal;
    panel.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    message(i18n.t('Loading…'));
    panel.focus({ preventScroll: true });
    try {
      const data = await api.months(filters(), signal);
      if (signal.aborted || !isOpen()) return;
      render(data);
    } catch (error) {
      if (error.name === 'AbortError' || !isOpen()) return;
      message(i18n.t('Could not load the months. Try again.'));
      return;
    }
    // Where you are, ready for the arrow keys and in view.
    const here = body.querySelector('[aria-current="true"]')
      || body.querySelector('.jump-year-btn.current') || body.querySelector('[data-jump]');
    here?.focus({ preventScroll: true });
    here?.scrollIntoView({ block: 'center' });
  }

  function close(returnFocus = false) {
    if (!isOpen()) return false;
    asking?.abort();
    const hadFocus = panel.contains(document.activeElement);
    panel.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    if (returnFocus && hadFocus) button.focus();
    return true;
  }

  /* -- going there -------------------------------------------------------- */

  /** The first heading of that month (or year), in the grid's order. */
  function headingFor(target) {
    const heads = grid.layout?.headers || [];
    if (target === UNDATED) return heads.find((h) => h.key === UNDATED);
    return heads.find((h) => dated(h.key) && h.key.startsWith(target));
  }

  async function jumpTo(target, { keyboard = false } = {}) {
    close();
    const mine = ++ticket;
    let head = headingFor(target);
    if (!head && hasMore()) {
      button.classList.add('busy');
      button.setAttribute('aria-busy', 'true');
      try {
        while (!head && hasMore()) {
          const came = await loadMore();
          if (mine !== ticket) return;
          head = headingFor(target);
          if (!came) break;
        }
      } finally {
        if (mine === ticket) {
          button.classList.remove('busy');
          button.removeAttribute('aria-busy');
        }
      }
    }
    if (!head) return;
    grid.scroller.scrollTop = Math.max(0, head.y - 4);
    if (keyboard && head.firstCell != null) {
      // The arrow keys carry on from the first photograph there.
      grid.cursor = head.firstCell;
      grid.scroller.focus({ preventScroll: true });
    }
    showCurrent(monthOf(head.key));
    requestAnimationFrame(() => {
      grid.render();
      const node = grid.container.querySelector(`.section-head[data-head="${head.key}"]`);
      if (!node) return;
      node.classList.remove('jump-flash');
      void node.offsetWidth;          // start the flash again on a second jump
      node.classList.add('jump-flash');
      setTimeout(() => node.classList.remove('jump-flash'), 1200);
    });
  }

  /* -- wiring ------------------------------------------------------------- */

  button.addEventListener('click', (event) => {
    event.stopPropagation();
    if (isOpen()) close(true); else open();
  });
  document.getElementById('jump-close')?.addEventListener('click', () => close(true));

  body.addEventListener('click', (event) => {
    const target = event.target.closest('[data-jump]');
    if (target) jumpTo(target.dataset.jump, { keyboard: event.detail === 0 });
  });

  document.addEventListener('click', (event) => {
    if (isOpen() && !panel.contains(event.target) && !button.contains(event.target)) close();
  });

  panel.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close(true);
      return;
    }
    const moves = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: 'up', ArrowDown: 'down', Home: 'first', End: 'last' };
    const move = moves[event.key];
    if (move === undefined) return;
    event.preventDefault();
    event.stopPropagation();          // not the gallery's own arrow keys
    const list = [...body.querySelectorAll('[data-jump]')];
    if (!list.length) return;
    const at = list.indexOf(document.activeElement);
    let next;
    if (at < 0 || move === 'first') next = list[0];
    else if (move === 'last') next = list[list.length - 1];
    else if (typeof move === 'number') {
      // Right-to-left needs no special case: Tamil and English both read left to right.
      next = list[Math.max(0, Math.min(list.length - 1, at + move))];
    } else {
      next = nearestRow(list, list[at], move === 'down' ? 1 : -1) || list[at];
    }
    next.focus();
    next.scrollIntoView({ block: 'nearest' });
  });

  /** The button on the next row up or down that sits closest across. */
  function nearestRow(list, from, direction) {
    const box = from.getBoundingClientRect();
    const middle = box.left + box.width / 2;
    let row = null;
    let best = null;
    for (const item of list) {
      const r = item.getBoundingClientRect();
      const gap = direction > 0 ? r.top - box.bottom : box.top - r.bottom;
      if (gap < -2) continue;
      if (row === null || gap < row - 2) {
        row = gap;
        best = item;
      } else if (Math.abs(gap - row) <= 2) {
        const was = best.getBoundingClientRect();
        if (Math.abs(r.left + r.width / 2 - middle) < Math.abs(was.left + was.width / 2 - middle)) best = item;
      }
    }
    return best;
  }

  grid.addEventListener('layout', sync);
  grid.addEventListener('scroll', (event) => {
    if (button.hidden) return;
    const month = monthOf(event.detail.section?.key);
    if (month !== current) showCurrent(month);
  });
  i18n.onChange(() => {
    showCurrent(current);
    if (isOpen()) open();
  });
  sync();

  return { open: () => (isOpen() ? close(true) : open()), close, sync };
}

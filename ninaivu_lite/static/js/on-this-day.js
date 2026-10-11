/**
 * "On this day": a row over the timeline of the photographs taken on today's
 * date in the years before, newest year first. Shown on the plain timeline
 * only (no search, folder, year or album chosen), and only when there are
 * some. A tap opens one in the viewer; "See all" shows them all in the grid.
 * Closing the row hides it until tomorrow, on this device.
 *
 * Read from /api/segments with `on_this_day`, so what a person may see is
 * decided where it always is.
 */

import { api, thumbUrl } from './api.js';
import * as i18n from './i18n.js';

const MOST = 30;
const HIDDEN_KEY = 'ninaivu:on-this-day-hidden';

/** Today in the browser's own time, as the index's date_key spells a day. */
export function today(now = new Date()) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** "11 October 2025" (or its Tamil) for a YYYY-MM-DD day key. */
function spelled(key) {
  const [y, m, d] = key.split('-').map(Number);
  if (!y || !m || !d) return key;
  try {
    return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(i18n.locale(),
      { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
  } catch {
    return key;
  }
}

/**
 * `wanted()` says whether the timeline is plain enough to show the row;
 * `open(ids, index, thumbed)` opens the viewer; `seeAll(day)` filters the grid to them.
 */
export function wireOnThisDay({ wanted, open, seeAll }) {
  const row = document.getElementById('on-this-day');
  const list = document.getElementById('on-this-day-list');
  const title = document.getElementById('on-this-day-title');
  if (!row || !list) return { refresh() {} };
  let asking = null;
  let shownFor = null;

  document.getElementById('on-this-day-all')?.addEventListener('click', () => seeAll(today()));
  document.getElementById('on-this-day-close')?.addEventListener('click', () => {
    row.hidden = true;
    try { localStorage.setItem(HIDDEN_KEY, today()); } catch { /* private window */ }
  });

  function hiddenToday() {
    try { return localStorage.getItem(HIDDEN_KEY) === today(); } catch { return false; }
  }

  /** `again`: build the row anew even if it is up to date (a new language). */
  async function refresh(again = false) {
    const day = today();
    if (!wanted() || hiddenToday()) { row.hidden = true; return; }
    if (again) shownFor = null;
    if (shownFor === day && list.childElementCount) { row.hidden = false; return; }
    asking?.abort();
    asking = new AbortController();
    let data;
    try {
      data = await api.segments({ onThisDay: day, sort: 'date_desc' }, asking.signal, { limit: MOST });
    } catch {
      row.hidden = true;     // a row of extras: never an error of its own
      return;
    }
    const cells = (data.segments || []).flatMap((s) => s.items.map((item) => ({ item, key: s.key })));
    list.innerHTML = '';
    if (!cells.length || !wanted()) { row.hidden = true; return; }
    const years = new Set(cells.map((c) => c.key.slice(0, 4)));
    if (title) {
      title.textContent = years.size === 1
        ? i18n.t('On this day in {year}', { year: [...years][0] })
        : i18n.t('On this day');
    }
    const ids = cells.map((c) => c.item[0]);
    // As the grid hands the viewer: which have a thumbnail, at which version.
    const thumbed = new Map(cells.filter((c) => c.item[3] & 2).map((c) => [c.item[0], c.item[5] || 0]));
    cells.forEach(({ item, key }, index) => {
      const [id, , kind, flags, , version] = item;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'otd-tile';
      const ago = Number(day.slice(0, 4)) - Number(key.slice(0, 4));
      const label = ago === 1 ? i18n.t('1 year ago') : i18n.t('{count} years ago', { count: ago });
      const when = spelled(key);
      button.setAttribute('aria-label', `${label} · ${when}`);
      button.title = when;
      if (flags & 2) {
        const img = document.createElement('img');
        img.alt = '';
        img.loading = 'lazy';
        img.decoding = 'async';
        img.src = thumbUrl(id, 256, version);
        button.appendChild(img);
      }
      if (kind === 1) button.classList.add('video');
      const tag = document.createElement('span');
      tag.className = 'otd-year';
      tag.textContent = key.slice(0, 4);
      button.appendChild(tag);
      button.onclick = () => open(ids, index, thumbed);
      list.appendChild(button);
    });
    shownFor = day;
    row.hidden = false;
  }

  return { refresh };
}

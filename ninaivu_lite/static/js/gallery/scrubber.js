/** The rail of months beside the grid, and its marker. */

import * as i18n from '../i18n.js';
import { scrubberTicks, sectionAt } from '../layout.js';
import { $, grid } from './state.js';

/** The rail's height when the marks were placed: read once there, not on
 *  every scroll frame, where reading it would force a layout. */
let scrubberRail = 0;

export function buildScrubber() {
  const scrubber = $('#scrubber');
  const track = $('#scrubber-track');
  const headers = grid.layout.headers;
  track.innerHTML = '';
  if (headers.length < 3 || grid.layout.height < grid.scroller.clientHeight * 2) {
    scrubber.hidden = true;
    return;
  }
  scrubber.hidden = false;

  // One tick per month, and never two ticks closer than 26px, so the rail
  // stays readable however dense the library is. Months are named, years are
  // written out where they change (see scrubberTicks in layout.js).
  // Placed where the marker will be when the grid is scrolled to that month:
  // the marker goes by scroll position, not by height, and a mark placed by
  // height sat a screen's worth below the month it named near the end.
  const total = Math.max(1, grid.layout.height - grid.scroller.clientHeight);
  const railHeight = Math.max(1, scrubber.clientHeight || grid.scroller.clientHeight);
  scrubberRail = railHeight;
  const locale = i18n.locale();
  const monthName = new Intl.DateTimeFormat(locale, { month: 'short' });
  const yearName = new Intl.DateTimeFormat(locale, { year: 'numeric' });

  for (const tick of scrubberTicks(headers, total, railHeight, 26)) {
    const mark = document.createElement('span');
    // Kept clear of the rail's ends, where half the label was cut off.
    mark.style.top = `clamp(7px, ${tick.frac * 100}%, calc(100% - 7px))`;
    mark.dataset.frac = String(tick.frac);
    if (tick.month) {
      const date = new Date(`${tick.month}-01T00:00:00`);
      mark.textContent = (tick.year ? yearName : monthName).format(date);
      mark.classList.toggle('year', tick.year);
    } else {
      mark.textContent = tick.key === 'match' ? i18n.t('Top') : i18n.t('Undated');
    }
    track.appendChild(mark);
  }
  positionScrubber(grid.scroller.scrollTop /
    Math.max(1, grid.layout.height - grid.scroller.clientHeight));
  labelScrubber(sectionAt(grid.layout, grid.scroller.scrollTop + 40));
}

/** The marker's own label: the month and year it is on. */
let scrubberLabelled = '';

export function labelScrubber(section) {
  const label = $('#scrubber-thumb').querySelector('span');
  if (!section) return;
  // Called on every frame of a scroll: the label is made again only when the
  // month under it (or the language) changes.
  const said = `${section.key.slice(0, 7)}|${i18n.locale()}|${i18n.language()}`;
  if (said === scrubberLabelled && label.textContent) return;
  scrubberLabelled = said;
  if (section.key === 'match') label.textContent = i18n.t('Best matches');
  else if (!/^\d{4}-\d{2}/.test(section.key)) label.textContent = i18n.t('Undated');
  else {
    label.textContent = new Date(`${section.key.slice(0, 7)}-01T00:00:00`)
      .toLocaleDateString(i18n.locale(), { month: 'short', year: 'numeric' });
  }
}

export function positionScrubber(ratio) {
  const thumb = $('#scrubber-thumb');
  const at = Math.max(0, Math.min(1, ratio));
  // Clamped like the marks, so the marker's own label is never cut in half
  // at either end of the rail.
  thumb.style.top = `clamp(12px, ${at * 100}%, calc(100% - 12px))`;
  // The marker carries the month it is on; a mark it would sit on top of
  // gives way, rather than showing through half-covered.
  const rail = scrubberRail || 1;
  const thumbY = Math.max(12, Math.min(rail - 12, at * rail));
  for (const mark of $('#scrubber-track').children) {
    const y = Math.max(7, Math.min(rail - 7, Number(mark.dataset.frac) * rail));
    mark.classList.toggle('covered', Math.abs(y - thumbY) < 20);
  }
}

export function wireScrubber() {
  const scrubber = $('#scrubber');
  const thumb = $('#scrubber-thumb');
  let dragging = false;

  // The rail runs beside the grid itself, not beside the bars above it. Over
  // the selection bar or the On this day row, its marker sat at the top of
  // the page, half hidden behind the bar's blur, and the marks did not line
  // up with the grid they stand for.
  const scroller = $('#scroller');
  let railTop = -1;
  let railHeight = -1;
  const fit = () => {
    const top = scroller.offsetTop;
    const height = scroller.clientHeight;
    if (top === railTop && height === railHeight) return;
    railTop = top;
    railHeight = height;
    scrubber.style.top = `${top + 8}px`;
    if (grid?.layout && !scrubber.hidden) buildScrubber();
  };
  if (window.ResizeObserver) new ResizeObserver(fit).observe(scroller);
  fit();

  const seek = (clientY) => {
    const rect = scrubber.getBoundingClientRect();
    grid.scrollToRatio((clientY - rect.top) / rect.height);
  };

  thumb.addEventListener('pointerdown', (event) => {
    dragging = true;
    scrubber.classList.add('active');
    thumb.setPointerCapture(event.pointerId);
  });
  thumb.addEventListener('pointermove', (event) => dragging && seek(event.clientY));
  const release = () => {
    dragging = false;
    scrubber.classList.remove('active');
  };
  thumb.addEventListener('pointerup', release);
  thumb.addEventListener('pointercancel', release);
  scrubber.addEventListener('click', (event) => {
    if (event.target === thumb || thumb.contains(event.target)) return;
    seek(event.clientY);
  });
}

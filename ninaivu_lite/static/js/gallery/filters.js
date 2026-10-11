/** Search and its suggestions, the filter bar, and the year and folder lists. */

import { api } from '../api.js';
import * as i18n from '../i18n.js';
import { loadAlbums, renderAlbums } from './albums.js';
import { reload, renderCounts, renderLibraryName } from './data.js';
import { buildScrubber } from './scrubber.js';
import { openShareModal } from './sharing.js';
import { $, grid, onThisDay, state } from './state.js';
import { toast } from './toast.js';

let searchTimer = null;

/* The search box's hint, the longest that fits. A phone showed "Descri", and
   the desktop cut its example off mid-word; semantic search's hint was also
   English whatever the language. */
function setSearchHint() {
  const input = $('#search');
  if (!input) return;
  // Ninaivu Lite searches file and folder names (no date phrases, no AI).
  const hints = [i18n.t('Search')];
  const style = getComputedStyle(input);
  const room = input.clientWidth - parseFloat(style.paddingLeft || 0)
    - parseFloat(style.paddingRight || 0);
  setSearchHint.canvas ||= document.createElement('canvas');
  const measure = setSearchHint.canvas.getContext('2d');
  measure.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  input.placeholder = hints.find((hint) => measure.measureText(hint).width <= room)
    || hints[hints.length - 1];
}

export function watchSearchHint() {
  const input = $('#search');
  if (!input) return;
  setSearchHint();
  if (window.ResizeObserver) new ResizeObserver(() => setSearchHint()).observe(input);
  i18n.onChange(() => {
    setSearchHint();
    // What the page built itself, as opposed to what apply() redraws.
    renderAlbums();
    grid?.relabel?.();
    if (grid?.layout) buildScrubber();
    renderLibraryName();
    renderFolders();
    syncChips();
    // The filter chips and the On this day row are built here too.
    renderFilterBar();
    onThisDay?.refresh(true);
    // "6 items · 398.1 KB" is built here too, so it would keep the old language.
    if (state.lastCounts) renderCounts(state.lastCounts);
  });
}

/* -- search ------------------------------------------------------------- */

export function wireSearch() {
  const input = $('#search');
  const list = $('#suggestions');

  input.addEventListener('input', () => {
    const value = input.value.trim();
    $('#clear-search').hidden = !value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.filters.q = value;
      reload({ resetScroll: true });
    }, value ? 260 : 0);
    updateSuggestions(value);
  });

  input.addEventListener('keydown', (event) => {
    const options = [...list.querySelectorAll('li')];
    const active = list.querySelector('[aria-selected="true"]');
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (!options.length) return;
      event.preventDefault();
      const index = options.indexOf(active);
      const next = event.key === 'ArrowDown'
        ? Math.min(options.length - 1, index + 1)
        : Math.max(0, index - 1);
      options.forEach((li, i) => li.setAttribute('aria-selected', String(i === next)));
      // Said out loud: the focus stays in the box, so a screen reader hears
      // which suggestion is picked only through the box's active descendant.
      input.setAttribute('aria-activedescendant', options[next].id);
    } else if (event.key === 'Enter' && active) {
      active.click();
    } else if (event.key === 'Enter') {
      // A phone's Search key: search now, and take the suggestions and the
      // keyboard off the results (they covered most of a phone's screen).
      clearTimeout(searchTimer);
      list.hidden = true;
      state.filters.q = input.value.trim();
      reload({ resetScroll: true });
      if (window.matchMedia('(hover: none) and (pointer: coarse)').matches) input.blur();
    } else if (event.key === 'Escape') {
      list.hidden = true;
      input.blur();
    }
  });

  input.addEventListener('blur', () => setTimeout(() => (list.hidden = true), 140));
  // Whichever of the several places hides or shows the list, the box says so.
  new MutationObserver(() => {
    input.setAttribute('aria-expanded', String(!list.hidden));
    if (list.hidden) input.removeAttribute('aria-activedescendant');
  }).observe(list, { attributes: true, attributeFilter: ['hidden'] });
  $('#clear-search').onclick = () => {
    clearTimeout(searchTimer);
    input.value = '';
    $('#clear-search').hidden = true;
    state.filters.q = '';
    list.hidden = true;
    reload({ resetScroll: true });
  };
}

/** What a suggestion is, in the page's language (it was the raw "folder"). */
const SUGGESTION_KINDS = {
  folder: i18n.key('Folder'),
  camera: i18n.key('Camera'),
  concept: i18n.key('Search'),
};

async function updateSuggestions(value) {
  const list = $('#suggestions');
  if (!value || value.length < 2) {
    list.hidden = true;
    return;
  }
  let data;
  try {
    data = await api.suggest(value);
  } catch { return; }

  if ($('#search').value.trim() !== value) return;

  const entries = [
    ...(data.concepts || []).map((c) => ({ type: 'concept', value: c })),
    ...(data.suggestions || []),
  ].slice(0, 10);

  list.innerHTML = '';
  $('#search').removeAttribute('aria-activedescendant');
  if (!entries.length) {
    list.hidden = true;
    return;
  }
  for (const entry of entries) {
    const li = document.createElement('li');
    li.setAttribute('role', 'option');
    li.id = `suggestion-${list.childElementCount}`;
    li.setAttribute('aria-selected', 'false');
    const kind = document.createElement('span');
    kind.className = 'kind';
    kind.textContent = i18n.t(SUGGESTION_KINDS[entry.type] || entry.type);
    const label = document.createElement('span');
    label.textContent = entry.value;
    li.append(kind, label);
    if (entry.count) {
      const n = document.createElement('span');
      n.className = 'n';
      n.textContent = entry.count;
      li.appendChild(n);
    }
    li.onclick = () => {
      clearTimeout(searchTimer);
      list.hidden = true;
      if (entry.type === 'folder') {
        state.filters.folder = entry.value;
      } else if (entry.type === 'camera') {
        state.filters.camera = entry.value;
      } else {
        $('#search').value = entry.value;
        state.filters.q = entry.value;
      }
      reload({ resetScroll: true });
    };
    list.appendChild(li);
  }
  list.hidden = false;
}

/* -- filter bar --------------------------------------------------------- */

/** "11 October" (or its Tamil) for an On this day filter's YYYY-MM-DD: the
 *  chip read "10-11", which is neither language's way of writing a day. */
function dayOfYear(day) {
  const [y, m, d] = day.split('-').map(Number);
  try {
    return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(i18n.locale(),
      { day: 'numeric', month: 'long', timeZone: 'UTC' });
  } catch {
    return day.slice(5);
  }
}

export function renderFilterBar() {
  const bar = $('#active-filters');
  bar.innerHTML = '';
  const chips = [];
  if (state.filters.q) chips.push([i18n.t('Search'), state.filters.q, () => {
    state.filters.q = '';
    $('#search').value = '';
    $('#clear-search').hidden = true;
  }]);
  if (state.filters.folder) chips.push([i18n.t('Folder'), state.filters.folder, () => (state.filters.folder = '')]);
  if (state.filters.camera) chips.push([i18n.t('Camera'), state.filters.camera, () => (state.filters.camera = '')]);
  if (state.filters.from) chips.push([i18n.t('Year'), state.filters.from.slice(0, 4), () => {
    state.filters.from = '';
    state.filters.to = '';
  }]);
  if (state.filters.onThisDay) chips.push([i18n.t('On this day'), dayOfYear(state.filters.onThisDay), () => (state.filters.onThisDay = '')]);
  if (state.filters.near) chips.push([i18n.t('Taken nearby'), state.filters.nearName || i18n.t('Taken nearby'), () => {
    state.filters.near = 0;
    state.filters.nearName = '';
  }]);
  if (state.filters.album) {
    const album = (state.albums || []).find((a) => a.id === state.filters.album);
    chips.push([i18n.t('Album'), album?.name || i18n.t('Album'), () => (state.filters.album = 0)]);
  }

  for (const [label, value, clear] of chips) {
    const pill = document.createElement('span');
    pill.className = 'pill';
    const b = document.createElement('b');
    b.textContent = `${label}: `;
    const text = document.createElement('span');
    text.textContent = value;
    const button = document.createElement('button');
    button.type = 'button';
    button.setAttribute('aria-label', i18n.t('Remove {label} filter', { label }));
    button.innerHTML = '<svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>';
    button.onclick = () => {
      clear();
      syncChips();
      reload({ resetScroll: true });
    };
    pill.append(b, text, button);
    bar.appendChild(pill);
  }

  if (state.filters.album) {
    const album = (state.albums || []).find((a) => a.id === state.filters.album);
    if (album) {
      const shareBtn = document.createElement('button');
      shareBtn.className = 'btn ghost small';
      shareBtn.innerHTML = '<svg viewBox="0 0 24 24" style="width:13px;height:13px;margin-right:4px;vertical-align:-2px;"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>' + i18n.t('Share album');
      shareBtn.onclick = () => openShareModal({ type: 'album', id: album.id, name: album.name });
      bar.appendChild(shareBtn);

      const delBtn = document.createElement('button');
      delBtn.className = 'btn ghost small';
      delBtn.style.color = 'var(--danger)';
      delBtn.textContent = i18n.t('Delete album');
      delBtn.onclick = async () => {
        if (!confirm(i18n.t('Delete album “{name}”? Photographs inside will not be deleted.', { name: album.name }))) return;
        try {
          await api.deleteAlbum(album.id);
          toast(i18n.t('Album “{name}” deleted.', { name: album.name }));
          state.filters.album = 0;
          await loadAlbums();
          syncChips();
          reload({ resetScroll: true });
        } catch (err) {
          toast(i18n.t('Could not delete album: {reason}', { reason: err.message }), true);
        }
      };
      // Renaming: the album keeps its photographs, its links and its cover.
      const renameBtn = document.createElement('button');
      renameBtn.className = 'btn ghost small';
      renameBtn.textContent = i18n.t('Rename');
      renameBtn.onclick = async () => {
        const answer = prompt(i18n.t('New name for this album'), album.name);
        const name = (answer || '').trim();
        if (!name || name === album.name) return;
        try {
          await api.updateAlbum(album.id, { name });
          toast(i18n.t('Album renamed to “{name}”.', { name }));
          await loadAlbums();
          renderFilterBar();
        } catch (err) {
          toast(i18n.t('Could not rename album: {reason}', { reason: err.message }), true);
        }
      };
      bar.appendChild(renameBtn);
      bar.appendChild(delBtn);
    }
  }

  $('#filter-bar').hidden = chips.length === 0;
}

export function clearFilters() {
  clearTimeout(searchTimer);
  state.filters = { ...state.filters, q: '', folder: '', camera: '', from: '', to: '', album: 0,
    onThisDay: '', near: 0, nearName: '' };
  $('#suggestions').hidden = true;
  $('#search').value = '';
  $('#clear-search').hidden = true;
  syncChips();
  reload({ resetScroll: true });
}

export function syncChips() {
  document.querySelectorAll('#year-list .chip, #year-strip .chip').forEach((chip) => {
    chip.classList.toggle('active', chip.dataset.year === state.filters.from.slice(0, 4));
  });
  document.querySelectorAll('#folder-list button').forEach((button) => {
    button.classList.toggle('active', button.dataset.folder === state.filters.folder);
  });
  document.querySelectorAll('#album-list .album-card').forEach((button) => {
    button.classList.toggle('active',
      Number(button.dataset.album) === state.filters.album);
  });
  const removeBtn = $('#sel-album-remove');
  if (removeBtn) {
    removeBtn.hidden = !state.filters.album;
  }
}

export function renderYears() {
  const years = state.facets?.years || [];
  $('#years-block').hidden = years.length < 2;
  // The same chips twice: in the sidebar, and in the strip above the grid
  // that a phone shows in the sidebar's place.
  const strip = $('#year-strip');
  strip.hidden = years.length < 2;
  for (const box of [$('#year-list'), strip]) {
    box.innerHTML = '';
    for (const year of years) box.appendChild(yearChip(year, box === strip));
  }
  syncChips();
}

function yearChip(year, plain = false) {
  const chip = document.createElement('button');
  chip.className = 'chip';
  chip.type = 'button';
  chip.dataset.year = year.year;
  chip.textContent = year.year;
  if (!plain) {
    const n = document.createElement('span');
    n.className = 'n';
    n.textContent = year.count;
    chip.appendChild(n);
  }
  chip.onclick = () => {
    const active = state.filters.from.slice(0, 4) === year.year;
    state.filters.from = active ? '' : `${year.year}-01-01`;
    state.filters.to = active ? '' : `${year.year}-12-31`;
    syncChips();
    reload({ resetScroll: true });
  };
  return chip;
}

export function renderFolders() {
  const box = $('#folder-list');
  const folders = (state.facets?.folders || []).filter((f) => f.name);
  $('#folders-block').hidden = folders.length < 2;
  box.innerHTML = '';
  for (const folder of folders.slice(0, 40)) {
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.folder = folder.name;
    // The name shown is for reading; `dataset.folder` above, and the filter
    // below, keep the path exactly as the server gave it.
    const path = document.createElement('span');
    path.className = 'path';
    path.title = folder.name;
    const asDate = i18n.folderDate(folder.name);
    const cut = folder.name.replace(/\/+$/, '').lastIndexOf('/');
    if (asDate) {
      // "2025/05/11" is a day, and reads as one: "11 May 2025".
      path.textContent = asDate;
    } else if (cut > 0) {
      // A deep path: its last folder is the one that says what is in it, so
      // that is the part kept whole; the folders above it give way first.
      const parent = document.createElement('span');
      parent.className = 'parent';
      parent.textContent = folder.name.slice(0, cut + 1);
      const leaf = document.createElement('span');
      leaf.className = 'leaf';
      leaf.textContent = folder.name.slice(cut + 1).replace(/\/+$/, '');
      path.append(parent, leaf);
    } else {
      path.textContent = folder.name;
    }
    const n = document.createElement('span');
    n.className = 'n';
    n.textContent = folder.count;
    button.append(path, n);
    button.onclick = () => {
      state.filters.folder = state.filters.folder === folder.name ? '' : folder.name;
      syncChips();
      reload({ resetScroll: true });
    };
    box.appendChild(button);
  }
}

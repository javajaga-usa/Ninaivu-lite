/** The Duplicates page: the same file in more than one place, read-only. */

import { thumbUrl } from '../api.js';
import * as i18n from '../i18n.js';
import { $, adminApi, el } from './shared.js';

/* The same file in more than one place, as the scan found it: where each
   copy is, and a way to open it in the gallery. Read-only on purpose —
   Ninaivu Lite never moves or deletes a file, so there is no button here
   that could. A page of groups at a time. */
const copies = { page: 1, data: null, error: '' };

export async function loadDuplicates(page = copies.page) {
  try {
    copies.data = await adminApi.duplicates(page);
    copies.page = copies.data.page;
    copies.error = '';
  } catch (error) {
    copies.error = error.message;
  }
  renderDuplicates();
}

export function renderDuplicates() {
  const summary = $('#dup-summary');
  const list = $('#dup-list');
  const pager = $('#dup-pager');
  if (!summary || !list || !pager) return;
  summary.replaceChildren();
  list.replaceChildren();
  pager.replaceChildren();
  pager.hidden = true;
  if (copies.error) {
    summary.appendChild(el('p', 'hint', copies.error));
    return;
  }
  const data = copies.data;
  if (!data) return;

  if (data.total_groups) {
    summary.appendChild(el('p', 'dup-total', data.total_groups === 1
      ? i18n.t('1 photo or video has a copy elsewhere, {size} in the extra copy.',
        { size: data.extra_bytes_h })
      : i18n.t('{count} photos and videos have copies elsewhere, {size} in the extra copies.',
        { count: data.total_groups.toLocaleString(), size: data.extra_bytes_h })));
  } else if (!data.pending) {
    summary.appendChild(el('p', 'hint', i18n.t('No copies found: every photo and video in the library is in one place.')));
  }
  if (data.pending) {
    summary.appendChild(el('p', 'hint', i18n.t('Still checking {count} files that are the same size as another; this list grows as the scan reads them.',
      { count: data.pending.toLocaleString() })));
  }

  for (const group of data.groups) {
    const card = el('article', 'dup-group');
    const first = group.items[0];
    const picture = el('div', 'dup-thumb');
    if (first.has_thumb) {
      const img = document.createElement('img');
      img.src = thumbUrl(first.id, 256, first.thumb_v);
      img.alt = '';
      img.loading = 'lazy';
      picture.appendChild(img);
    }
    card.appendChild(picture);
    const body = el('div', 'dup-body');
    body.appendChild(el('h3', 'dup-head', i18n.t('{count} copies · {size} each',
      { count: group.count, size: group.size_h })));
    const rows = el('ul', 'dup-copies');
    for (const item of group.items) {
      const row = el('li', 'dup-copy');
      const where = el('div', 'dup-where');
      // The library folder by its name and the folders inside it: on a long
      // absolute path the part that tells two copies apart was cut off. The
      // whole path is on hover, and in the overview's library list.
      const library = item.library.split(/[\\/]/).filter(Boolean).pop() || item.library;
      const path = el('code', 'li-path', item.dir ? `${library}/${item.dir}` : library);
      path.title = item.folder;
      where.appendChild(path);
      where.appendChild(el('div', 'li-meta',
        [item.name, item.size_h, item.date].filter(Boolean).join(' · ')));
      row.appendChild(where);
      const open = el('a', 'btn small ghost', i18n.t('Open in gallery'));
      open.href = item.open;
      open.target = '_blank';
      open.rel = 'noopener';
      open.setAttribute('aria-label', i18n.t('Open {name} in the gallery', { name: item.name }));
      row.appendChild(open);
      rows.appendChild(row);
    }
    body.appendChild(rows);
    card.appendChild(body);
    list.appendChild(card);
  }

  if (data.pages > 1) {
    pager.hidden = false;
    const step = (label, page) => {
      const button = el('button', 'btn small ghost', label);
      button.type = 'button';
      button.disabled = page < 1 || page > data.pages;
      button.onclick = () => loadDuplicates(page);
      return button;
    };
    pager.append(
      step(i18n.t('Previous'), data.page - 1),
      el('span', 'hint', i18n.t('Page {page} of {pages}', { page: data.page, pages: data.pages })),
      step(i18n.t('Next'), data.page + 1),
    );
  }
}

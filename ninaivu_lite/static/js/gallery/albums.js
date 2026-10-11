/** Albums: the sidebar's list, and the dialog that makes one or adds to one. */

import { api, thumbUrl } from '../api.js';
import * as i18n from '../i18n.js';
import { reload } from './data.js';
import { syncChips } from './filters.js';
import { $, grid, state, viewer } from './state.js';
import { toast } from './toast.js';

let albumTargetIds = [];

export function renderAlbums() {
  const box = $('#album-list');
  const albums = state.albums || [];
  const block = $('#albums-block');
  if (block) block.hidden = !state.user?.can?.favorite;
  if (!box) return;
  box.innerHTML = '';
  if (albums.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'hint';
    empty.style.padding = '6px 8px';
    empty.style.fontSize = '12px';
    empty.textContent = i18n.t('No albums yet. Click + to create one.');
    box.appendChild(empty);
    return;
  }
  for (const album of albums) {
    const button = document.createElement('button');
    button.className = 'album-card';
    button.type = 'button';
    button.dataset.album = String(album.id);
    button.title = `${album.name} — ${i18n.items(album.n)}`;

    if (album.cover_id) {
      const img = document.createElement('img');
      img.src = thumbUrl(album.cover_id, 80, album.cover_v);
      img.alt = '';
      img.loading = 'lazy';
      button.appendChild(img);
    } else {
      const ph = document.createElement('div');
      ph.className = 'album-icon-ph';
      ph.innerHTML = '<svg viewBox="0 0 24 24"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18M9 21V9"/></svg>';
      button.appendChild(ph);
    }

    const text = document.createElement('span');
    text.className = 'album-text';
    const title = document.createElement('b');
    title.textContent = album.name;
    const n = document.createElement('span');
    n.className = 'n';
    n.textContent = i18n.items(album.n);
    text.append(title, n);
    button.appendChild(text);

    button.onclick = () => {
      state.filters.album = state.filters.album === album.id ? 0 : album.id;
      syncChips();
      reload({ resetScroll: true });
    };
    box.appendChild(button);
  }
  syncChips();
}

export async function loadAlbums() {
  // Albums are for family members and admins; a guest has none to load.
  if (!state.user?.can?.favorite) {
    state.albums = [];
    renderAlbums();
    return;
  }
  try {
    const data = await api.albums();
    state.albums = data.albums || [];
  } catch {
    state.albums = [];
  }
  renderAlbums();
}

export function openAlbumModal(ids = []) {
  albumTargetIds = ids;
  const modal = $('#album-modal');
  const title = $('#album-modal-title');
  const subtitle = $('#album-modal-subtitle');
  const input = $('#album-new-name');
  input.value = '';

  if (ids.length > 0) {
    title.textContent = ids.length === 1 ? i18n.t('Add 1 photo to album') : i18n.t('Add {count} photos to album', { count: ids.length });
    subtitle.textContent = i18n.t('Choose an album or enter a name to create a new one:');
  } else {
    title.textContent = i18n.t('Create new album');
    subtitle.textContent = i18n.t('Enter a name for the new album:');
  }

  const list = $('#album-modal-list');
  list.innerHTML = '';
  const albums = state.albums || [];
  if (albums.length === 0) {
    list.innerHTML = `<div class="hint" style="padding:8px;">${i18n.t('No existing albums yet.')}</div>`;
  } else {
    for (const album of albums) {
      const row = document.createElement('button');
      row.className = 'album-modal-item';
      row.type = 'button';

      if (album.cover_id) {
        const img = document.createElement('img');
        img.src = thumbUrl(album.cover_id, 64, album.cover_v);
        img.alt = '';
        row.appendChild(img);
      } else {
        const ph = document.createElement('div');
        ph.className = 'album-icon-ph';
        ph.innerHTML = '<svg viewBox="0 0 24 24"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18M9 21V9"/></svg>';
        row.appendChild(ph);
      }

      const text = document.createElement('div');
      text.className = 'album-text';
      const b = document.createElement('b');
      b.textContent = album.name;
      const n = document.createElement('span');
      n.className = 'n';
      n.textContent = i18n.items(album.n);
      text.append(b, n);
      row.appendChild(text);

      row.onclick = async () => {
        if (albumTargetIds.length > 0) {
          try {
            await api.albumAdd(album.id, albumTargetIds);
            toast(i18n.t('Added {items} to "{album}".', { items: i18n.items(albumTargetIds.length), album: album.name }));
            modal.hidden = true;
            grid.clearSelection();
            await loadAlbums();
            if (state.filters.album === album.id) reload();
          } catch (err) {
            toast(i18n.t('Could not add to album: {reason}', { reason: err.message }), true);
          }
        } else {
          modal.hidden = true;
          state.filters.album = album.id;
          syncChips();
          reload({ resetScroll: true });
        }
      };
      list.appendChild(row);
    }
  }

  modal.hidden = false;
  setTimeout(() => input.focus(), 50);
}

export function wireAlbums() {
  $('#new-album-btn')?.addEventListener('click', () => {
    openAlbumModal([]);
  });

  $('#album-create-btn')?.addEventListener('click', async () => {
    const input = $('#album-new-name');
    const name = input.value.trim();
    if (!name) {
      toast(i18n.t('Please enter an album name.'), true);
      input.focus();
      return;
    }
    const button = $('#album-create-btn');
    button.disabled = true;
    try {
      const created = await api.createAlbum(name, albumTargetIds);
      toast(albumTargetIds.length
        ? i18n.t('Album "{name}" made, with {items}.', { name, items: i18n.items(albumTargetIds.length) })
        : i18n.t('Album "{name}" made.', { name }));
      $('#album-modal').hidden = true;
      if (albumTargetIds.length) grid.clearSelection();
      await loadAlbums();
      state.filters.album = created.id;
      syncChips();
      reload({ resetScroll: true });
    } catch (err) {
      toast(i18n.t('Could not create album: {reason}', { reason: err.message }), true);
    } finally {
      button.disabled = false;
    }
  });

  $('#album-new-name')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      $('#album-create-btn')?.click();
    }
  });

  $('#album-modal-close')?.addEventListener('click', () => {
    $('#album-modal').hidden = true;
  });
  $('#album-modal-close-x')?.addEventListener('click', () => {
    $('#album-modal').hidden = true;
  });

  viewer.addEventListener('album', (e) => {
    if (e.detail?.item) {
      openAlbumModal([e.detail.item.id]);
    }
  });
}

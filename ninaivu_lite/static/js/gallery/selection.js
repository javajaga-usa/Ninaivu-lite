/** The selection bar: what can be done to several photographs at once. */

import { api } from '../api.js';
import * as i18n from '../i18n.js';
import { loadAlbums, openAlbumModal } from './albums.js';
import { refreshStatus, reload } from './data.js';
import { $, grid, state, viewer } from './state.js';
import { toast } from './toast.js';

export function wireSelection() {
  // DESK5: where the actions are one line that scrolls sideways (a phone, the
  // Pi's short screen), a mouse wheel scrolls it too, as a finger would.
  const actions = $('.sel-actions');
  actions?.addEventListener('wheel', (event) => {
    if (actions.scrollWidth <= actions.clientWidth) return;
    if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
    actions.scrollLeft += event.deltaY;
    event.preventDefault();
  }, { passive: false });
  $('#sel-cancel').onclick = () => grid.clearSelection();
  $('#sel-all').onclick = () => grid.selectAll();
  $('#sel-fav').onclick = () => bulk({ favorite: true });
  $('#sel-unfav').onclick = () => bulk({ favorite: false });
  $('#sel-download').onclick = () => {
    // One selection, one file. This used to fire up to twenty separate
    // downloads 320 ms apart, which browsers block after the first few and
    // which silently dropped everything past the twentieth anyway.
    const ids = [...grid.selection];
    if (!ids.length) return;

    if (ids.length === 1) {
      const link = document.createElement('a');
      link.href = `/api/download/${ids[0]}`;
      link.download = '';
      link.click();
      return;
    }

    const capped = ids.slice(0, 2000);
    if (capped.length < ids.length) {
      toast(i18n.t('Downloading the first {some} of {total}.', { some: capped.length, total: ids.length }));
    } else {
      toast(i18n.t('Preparing {count} photographs…', { count: capped.length }));
    }
    // The server streams the archive as it reads, so the browser starts
    // saving straight away rather than waiting on a spinner.
    window.location.href = `/api/download/zip?ids=${capped.join(',')}`;
  };
  $('#sel-album').onclick = () => {
    const ids = [...grid.selection];
    if (!ids.length) return;
    openAlbumModal(ids);
  };
  $('#sel-album-remove').onclick = async () => {
    const ids = [...grid.selection];
    if (!ids.length || !state.filters.album) return;
    try {
      await api.albumRemove(state.filters.album, ids);
      toast(i18n.t('Removed {items} from the album.', { items: i18n.items(ids.length) }));
      grid.clearSelection();
      await loadAlbums();
      reload();
    } catch (err) {
      toast(i18n.t('Could not remove items: {reason}', { reason: err.message }), true);
    }
  };
}

async function bulk(fields) {
  const ids = [...grid.selection];
  if (!ids.length) return;
  try {
    const result = await api.bulk(ids, fields);
    ids.forEach((id) => viewer.cache.delete(id));   // the viewer's copy is stale now
    // What the server changed, not what was asked: one it cannot see is not counted (A151).
    const updated = result && typeof result.updated === 'number' ? result.updated : ids.length;
    toast(updated === 1 ? i18n.t('Updated 1 item.') : i18n.t('Updated {count} items.', { count: updated }));
    grid.clearSelection();
    await refreshStatus();
    reload();
  } catch (error) {
    toast(error.message, true);
  }
}

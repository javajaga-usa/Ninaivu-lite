/** Who may see a photograph, set from the gallery by an admin. */

import { accountsApi } from '../accounts.js';
import { api } from '../api.js';
import * as i18n from '../i18n.js';
import { refreshStatus, reload } from './data.js';
import { $, grid, viewer } from './state.js';
import { toast } from './toast.js';

/**
 * Per-item visibility only. Folder rules and library settings live on the
 * admin console; this is the equivalent of tagging a photo you can already see.
 */
export function wireVisibility() {
  document.querySelectorAll('[data-sel-vis]').forEach((button) => {
    button.onclick = async () => {
      const ids = [...grid.selection];
      if (!ids.length) return;
      try {
        const result = await accountsApi.setVisibility(ids, button.dataset.selVis);
        ids.forEach((id) => viewer.cache.delete(id));
        toast(i18n.t('{count} now visible to {audience}.', { count: result.updated, audience: visLabel(button.dataset.selVis) }));
        grid.clearSelection();
        await refreshStatus();
        reload();
      } catch (exc) { toast(exc.message, true); }
    };
  });

  // Rotate on a selection: each turned a quarter from the way it is shown,
  // in the index only. The thumbnails are made again in the background.
  document.querySelectorAll('[data-sel-turn]').forEach((button) => {
    button.onclick = async () => {
      const ids = [...grid.selection];
      if (!ids.length || button.disabled) return;
      button.disabled = true;
      try {
        const result = await api.rotateMany(ids, Number(button.dataset.selTurn));
        ids.forEach((id) => viewer.cache.delete(id));
        toast(result.skipped
          ? i18n.t('Turned {count}; {skipped} could not be turned. The files are not changed.',
            { count: result.updated, skipped: result.skipped })
          : i18n.t('Turned {count}. The files are not changed.', { count: result.updated }));
        reload();
      } catch (exc) { toast(exc.message, true); }
      finally { button.disabled = false; }
    };
  });

  document.querySelectorAll('#v-visibility button').forEach((button) => {
    button.onclick = async () => {
      const item = viewer.item;
      if (!item) return;
      // Closing the viewer brings the gallery up to date. Marked before the
      // answer comes back, and the reload waits for it, so a viewer closed
      // in that moment neither skips the reload nor reads the old setting.
      // (From Ninaivu 1.12.0.)
      const change = accountsApi.setVisibility([item.id], button.dataset.vis);
      visibilitySaving = change.catch(() => {});
      visibilityChanged = true;
      try {
        await change;
        item.visibility = button.dataset.vis;
        syncViewerVisibility();
        // The tile behind the viewer shows it at once: its badge was left as
        // it was, and only a change made from a selection reloaded the
        // gallery. Whether the photograph still belongs in this view (Hidden,
        // Public) is settled when the viewer closes. (From Ninaivu 1.11.0.)
        showTileVisibility(item.id, button.dataset.vis);
        toast(i18n.t('Now visible to {audience}.', { audience: visLabel(button.dataset.vis) }));
        refreshStatus();
      } catch (exc) { toast(exc.message, true); }
    };
  });
}

/** Set since the viewer opened, so closing it brings the gallery up to date. */
let visibilityChanged = false;
/** The last change asked for from the viewer, settled or not. */
let visibilitySaving = Promise.resolve();

/** The visibility badge on one tile (flags 64 = public, 128 = hidden). */
function showTileVisibility(id, visibility) {
  const cell = grid.layout.cells.find((c) => c.id === id);
  if (!cell) return;
  cell.flags &= ~192;
  if (visibility === 'public') cell.flags |= 64;
  else if (visibility === 'hidden') cell.flags |= 128;
  grid.refreshCell(id);
}

/** The viewer has closed: a visibility changed in it is reflected in the view. */
export function viewerClosed() {
  if (!visibilityChanged) return;
  visibilityChanged = false;
  visibilitySaving.then(() => reload());
}

export function visLabel(visibility) {
  const words = { public: i18n.t('everyone'), family: i18n.t('family only'), hidden: i18n.t('admins only') };
  return words[visibility] || visibility;
}

// Named here, translated where shown: this table is built before any locale
// has loaded, so i18n.t() here would freeze the English (see i18n.key).
const VIS_REASONS = {
  hidden: i18n.key('Hidden because it was in a hidden file or folder on disk'),
  // The server calls a folder rule's choice "rule".
  rule: i18n.key('Follows the rule set on its folder'),
  item: i18n.key('Set on this item'),
  screen: i18n.key('Hidden because it looks like a screenshot, a document or a photo of a screen'),
  default: '',
};

export function syncViewerVisibility() {
  const picker = $('#v-visibility');
  if (!picker || picker.hidden) return;
  const current = viewer.item?.visibility || 'family';
  picker.querySelectorAll('button').forEach((button) => {
    button.classList.toggle('active', button.dataset.vis === current);
  });
  // Say where the setting came from. An admin looking at a hidden photo
  // otherwise has no way to tell whether they hid it or the filesystem did.
  const reason = VIS_REASONS[viewer.item?.visibility_source];
  picker.title = reason ? i18n.t(reason) : i18n.t('Who can see this');
  picker.dataset.source = viewer.item?.visibility_source || 'default';
}

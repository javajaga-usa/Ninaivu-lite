import {reportUnauthorized} from '../api.js';
import * as i18n from '../i18n.js';

/** Save the edited PNG beside its original in the library. Returns the new item. */
export async function saveLibraryCopy(assetId, blob) {
  if (!Number.isSafeInteger(assetId) || assetId < 1 || blob.type !== 'image/png')
    throw new Error(i18n.t('Choose a library photo and export a PNG copy.'));
  const response = await fetch(`/api/asset/${assetId}/edited-copy`, {
    method: 'POST', headers: {'Content-Type': 'image/png'}, body: blob,
  });
  if (response.status === 401) reportUnauthorized();
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(i18n.t(data.error || 'Could not save the copy ({status}).', {status: response.status}));
  if (!Number.isSafeInteger(data.id)) throw new Error(i18n.t('The save response was incomplete. Check your library before saving again.'));
  return data;
}

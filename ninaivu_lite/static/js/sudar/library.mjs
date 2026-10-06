import {reportUnauthorized} from '../api.js';
import * as i18n from '../i18n.js';

//: What the server writes a copy as (api_sudar.FORMATS).
const SAVED_TYPES = ['image/jpeg', 'image/png', 'image/webp'];

/**
 * Save the edited picture beside its original in the library, in the format it
 * was made in (a browser that cannot write WebP hands back a PNG, which is
 * then what is sent). Returns the new item.
 */
export async function saveLibraryCopy(assetId, blob) {
  if (!Number.isSafeInteger(assetId) || assetId < 1 || !SAVED_TYPES.includes(blob.type))
    throw new Error(i18n.t('Choose a library photo and export a PNG copy.'));
  const response = await fetch(`/api/asset/${assetId}/edited-copy`, {
    method: 'POST', headers: {'Content-Type': blob.type}, body: blob,
  });
  if (response.status === 401) reportUnauthorized();
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(i18n.t(data.error || 'Could not save the copy ({status}).', {status: response.status}));
  if (!Number.isSafeInteger(data.id)) throw new Error(i18n.t('The save response was incomplete. Check your library before saving again.'));
  return data;
}

/** The dialog that makes a share link for a photograph or an album. */

import { copyText } from '../accounts.js';
import { api } from '../api.js';
import * as i18n from '../i18n.js';
import { $, viewer } from './state.js';
import { toast } from './toast.js';

let shareTargetItem = null;

export function wireSharing() {
  viewer.addEventListener('share', (e) => {
    shareTargetItem = e.detail.item;
    openShareModal(shareTargetItem);
  });

  $('#share-close')?.addEventListener('click', () => {
    $('#share-modal').hidden = true;
  });

  $('#share-create-btn')?.addEventListener('click', async () => {
    const button = $('#share-create-btn');
    if (!shareTargetItem || button.disabled) return;
    const target = shareTargetItem;
    button.disabled = true;
    const expiry = Number($('#share-expiry').value || 30);
    const pwd = $('#share-password').value.trim() || null;
    const scope = target.type === 'album' ? 'album' : 'asset';
    try {
      const res = await api.createShare(scope, target.id, expiry, pwd);
      if (shareTargetItem !== target) return;
      const fullUrl = `${window.location.origin}${res.share_url}`;
      $('#share-url-text').value = fullUrl;
      $('#share-link-result').hidden = false;
      $('#share-options').hidden = true;
      $('#share-create-btn').hidden = true;
      toast(i18n.t('Share link generated!'));
    } catch (err) {
      toast(i18n.t('Could not create share link: {reason}', { reason: err.message }), true);
    } finally {
      button.disabled = false;
    }
  });

  $('#share-copy-btn')?.addEventListener('click', async () => {
    const input = $('#share-url-text');
    // Plain http on the home network has no navigator.clipboard; copyText
    // falls back to selecting the field and copying it the old way.
    if (await copyText(input.value, input)) {
      toast(i18n.t('Link copied to clipboard!'));
    } else {
      input.focus();
      input.select();
      toast(i18n.t('Could not copy automatically. Copy the selected link manually.'), true);
    }
  });
}

export function openShareModal(item) {
  shareTargetItem = item;
  $('#share-link-result').hidden = true;
  $('#share-options').hidden = false;
  $('#share-create-btn').hidden = false;
  $('#share-password').value = '';
  if (item?.type === 'album') {
    $('#share-title').textContent = i18n.t('Share album: {name}', { name: item.name });
  } else {
    $('#share-title').textContent = i18n.t('Share with family & friends');
  }
  $('#share-modal').hidden = false;
}

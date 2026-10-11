/** When the server cannot be reached: the banner, the empty page, and reconnecting. */

import { accountsApi } from '../accounts.js';
import * as i18n from '../i18n.js';
import { applyHomeName, finishStartup } from '../app.js';
import { bootDone } from './boot.js';
import { refreshFacets, refreshStatus, reload } from './data.js';
import { $, grid, state } from './state.js';
import { toast } from './toast.js';

let reconnectTimer = null;
let isCheckingConnection = false;
export let serverIsOffline = false;
let hasStarted = false;

/** Called by finishStartup in app.js: from now on a reconnect only refreshes. */
export function markStarted() { hasStarted = true; }

/** Opened on the tailnet address (Tailscale), away from home. */
export function onTailnet() {
  return location.hostname.endsWith('.ts.net');
}

export function isNetworkFailure(error) {
  if (!error) return false;
  if (error instanceof TypeError) return true;
  const msg = String(error.message || '').toLowerCase();
  return msg.includes('failed to fetch') ||
         msg.includes('networkerror') ||
         msg.includes('load failed') ||
         msg.includes('cannot reach') ||
         msg.includes('connection refused') ||
         msg.includes('504');
}

export function setServerOffline(reason = i18n.t('Cannot reach the Ninaivu server.')) {
  // Whatever else is true, the cover comes off: a boot overlay left over a
  // page that is telling somebody the server is unreachable hides the one
  // thing they need to read.
  bootDone();
  serverIsOffline = true;

  const banner = $('#offline-banner');
  if (banner) banner.hidden = false;
  const bannerText = $('#offline-banner-text');
  if (bannerText) bannerText.textContent = i18n.t('Can’t reach Ninaivu at {host}. Reconnecting automatically…', { host: location.host });

  if (!grid?.layout?.cells?.length) {
    const empty = $('#empty');
    if (empty) {
      empty.classList.add('offline');
      empty.hidden = false;
    }
    const skeleton = $('#skeleton');
    if (skeleton) skeleton.classList.remove('on');
    const title = $('#empty-title');
    // Worded as what is known, not as a diagnosis: a stopped server and a
    // certificate this browser does not trust fail identically from here, and
    // the offline copy of this page hides the browser's own certificate warning.
    if (title) title.textContent = i18n.t('Can’t reach Ninaivu');
    const text = $('#empty-text');
    // On the tailnet address the likeliest cause is neither: Tailscale is off on
    // this device, and a certificate is not the answer there (it is a public one).
    const away = onTailnet();
    if (text) text.textContent = away
      ? i18n.t('This device can’t reach Ninaivu at {host}. Away from home it is reached through Tailscale, so check that Tailscale is on and connected on this device.', { host: location.host })
      : i18n.t('This browser can’t reach Ninaivu at {host}. Either the Ninaivu server is stopped, or this browser doesn’t trust Ninaivu’s HTTPS certificate.', { host: location.host });
    const hint = $('#empty-hint');
    if (hint) {
      hint.hidden = false;
      hint.textContent = away
        ? i18n.t('Open the Tailscale app, connect, then retry. If Tailscale is connected, the Ninaivu server may be stopped.')
        : i18n.t('Check what the browser sees: a security warning means the certificate needs installing on this device; a “can’t reach this page” error means the server needs starting.');
    }
    const check = $('#empty-check');
    if (check) check.hidden = false;
    const action = $('#empty-action');
    if (action) {
      action.hidden = false;
      action.textContent = i18n.t('Retry connection');
      action.onclick = () => checkConnection(true);
    }
  }

  const rootPath = $('#root-path');
  if (rootPath && (rootPath.textContent === i18n.t('No folder selected') || !state.status?.has_library)) {
    rootPath.textContent = i18n.t('Server offline');
  }

  if (!reconnectTimer) {
    reconnectTimer = setInterval(() => checkConnection(false), 3000);
  }
}

export async function checkConnection(manual = false) {
  if (isCheckingConnection) return;
  isCheckingConnection = true;

  const retryBtn = $('#offline-retry-btn');
  const emptyAction = $('#empty-action');
  if (manual) {
    if (retryBtn) retryBtn.textContent = i18n.t('Checking…');
    if (emptyAction && $('#empty')?.classList.contains('offline')) emptyAction.textContent = i18n.t('Checking…');
  }

  try {
    const auth = await accountsApi.state();
    if (reconnectTimer) {
      clearInterval(reconnectTimer);
      reconnectTimer = null;
    }
    serverIsOffline = false;

    const banner = $('#offline-banner');
    if (banner) banner.hidden = true;
    const empty = $('#empty');
    if (empty?.classList.contains('offline')) {
      empty.classList.remove('offline');
      empty.hidden = true;
    }
    const hint = $('#empty-hint');
    if (hint) hint.hidden = true;
    const check = $('#empty-check');
    if (check) check.hidden = true;

    toast(i18n.t('Connected to Ninaivu server.'));

    if (!hasStarted) {
      await finishStartup(auth);
    } else {
      applyHomeName(auth);
      await refreshStatus();
      await Promise.all([reload(), refreshFacets()]);
    }
  } catch {
    if (manual) {
      toast(i18n.t('Server is still unreachable.'), true);
    }
  } finally {
    isCheckingConnection = false;
    if (retryBtn) retryBtn.textContent = i18n.t('Retry now');
    if (emptyAction && $('#empty')?.classList.contains('offline')) {
      emptyAction.textContent = i18n.t('Retry connection');
    }
  }
}

export function setupNetworkStatusListeners() {
  window.addEventListener('offline', () => {
    setServerOffline(i18n.t('You are offline.'));
  });
  window.addEventListener('online', () => {
    checkConnection(false);
  });
}

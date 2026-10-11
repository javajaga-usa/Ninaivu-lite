/** The offline shell, and the prompts to install the app on a phone or tablet. */

import * as i18n from '../i18n.js';

/* Install the offline shell.
 *
 * It caches the app itself and the thumbnails this viewer has already been
 * served — nothing else. Originals, listings and anything from the console go
 * to the network every time, because those differ per person and a shared
 * tablet must not hand the next person the last one's gallery.
 *
 * Failure here is silent on purpose: an unsupported browser, a private
 * window, or a page served over plain HTTP on a non-localhost address all
 * refuse registration, and none of them is a reason to bother anybody.
 */
export function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

/* Signing out wipes what was cached, so the next person to pick up the tablet
   starts clean rather than seeing a stranger's thumbnails. */
export function forgetCachedShell() {
  try {
    navigator.serviceWorker?.controller?.postMessage('ninaivu:forget');
  } catch { /* no worker, nothing to forget */ }
}

/* Android's browsers offer to install the app themselves, through this event;
   iOS never does, so it is told how (below). */
export function setupAndroidInstallPrompt() {
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    let dismissed = false;
    try { dismissed = Boolean(localStorage.getItem('ninaivu:install-dismissed')); } catch { /* private window */ }
    const banner = document.getElementById('ios-install-banner');
    const install = document.getElementById('install-btn');
    if (dismissed || !banner || !install) return;
    const title = document.getElementById('ios-install-title');
    const desc = document.getElementById('ios-install-desc');
    if (title) title.textContent = i18n.t('Install Ninaivu');
    if (desc) desc.textContent = i18n.t('Open it from your home screen, like an app.');
    install.hidden = false;
    install.onclick = async () => {
      banner.hidden = true;
      event.prompt();
      await event.userChoice.catch(() => null);
    };
    document.getElementById('ios-install-close')?.addEventListener('click', () => {
      banner.hidden = true;
      try { localStorage.setItem('ninaivu:install-dismissed', '1'); } catch { /* private window */ }
    });
    banner.hidden = false;
  });
}

export function setupIOSInstallPrompt() {
  // A desktop-class iPad says "MacIntel" and has a touchscreen. So does an
  // Android phone emulated on a Mac, which is not an iPad.
  const isIPad = /iPad/.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1
      && !/Android/.test(navigator.userAgent));
  const isIPhone = /iPhone|iPod/.test(navigator.userAgent);
  const isIOS = (isIPhone || isIPad) && !window.MSStream;
  const isStandalone = window.navigator.standalone === true || window.matchMedia('(display-mode: standalone)').matches;
  if (!isIOS || isStandalone) return;

  const dismissed = localStorage.getItem('ninaivu:ios-install-dismissed');
  if (dismissed) return;

  const banner = document.getElementById('ios-install-banner');
  const title = document.getElementById('ios-install-title');
  const desc = document.getElementById('ios-install-desc');
  const closeBtn = document.getElementById('ios-install-close');
  if (!banner) return;

  // The share glyph goes in as a substitution so the sentence around it is
  // translated whole, as the console's banner does; it was English only.
  const share = '<svg class="ios-share-glyph" viewBox="0 0 24 24"><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8M16 6l-4-4-4 4M12 2v13"/></svg>';
  if (isIPad) {
    if (title) title.textContent = i18n.t('Install Ninaivu on your iPad');
    if (desc) {
      desc.innerHTML = i18n.t('Tap {share} Share in Safari’s top toolbar, then select <strong>Add to Home Screen</strong> [+]', { share });
    }
  } else {
    if (title) title.textContent = i18n.t('Install Ninaivu on your iPhone');
    if (desc) {
      desc.innerHTML = i18n.t('Tap {share} Share below, then select <strong>Add to Home Screen</strong> [+]', { share });
    }
  }

  setTimeout(() => {
    banner.hidden = false;
  }, 2500);

  closeBtn?.addEventListener('click', () => {
    banner.hidden = true;
    localStorage.setItem('ninaivu:ios-install-dismissed', '1');
  });
}

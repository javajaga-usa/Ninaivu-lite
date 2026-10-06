/**
 * Offline shell for the Ninaivu Lite family app.
 *
 * WHAT THIS MAY CACHE, AND WHY THE LIST IS SHORT
 *
 * Only the app shell (the home page and /static/) is stored. This worker never
 * touches anything else:
 *
 *   /api/*              listings, thumbnails and originals differ per viewer
 *                       and recheck access on every request; cached, the next
 *                       person to pick up the tablet would be handed the last
 *                       person's gallery. They go straight to the network.
 *   /admin, /admin/*    the console is never offline and never cached.
 *   /share/*            a stranger's link, answered by the server each time.
 *
 * The caches are versioned. The server writes the version of the app's files
 * into CACHE_VERSION as it sends this file, so every upgrade is a new worker,
 * and its activation drops the old caches.
 */

const CACHE_VERSION = 'lite-1';  // replaced by the server: see pages.service_worker
/**
 * Thumbnails keep the version they were cached under. They are the expensive
 * thing to fetch again — thousands of them for a library scrolled through — and
 * a change to the app's scripts does not change a single one. The shell's version
 * moves when the scripts change, so that nobody is left with the old editor
 * asking a new worker to do something it no longer knows how to.
 */
const THUMB_VERSION = 'lite-1';
const SHELL_CACHE = `ninaivu-shell-${CACHE_VERSION}`;
const THUMB_CACHE = `ninaivu-thumbs-${THUMB_VERSION}`;

/**
 * Thumbnails are small (a 256px WEBP is tens of kilobytes) and a library is
 * scrolled through in long runs, so the cache has to be worth having: at 600
 * entries, and two sizes per picture, it held about three hundred photographs
 * and evicted the beginning of a scroll before the end of it.
 */
const MAX_THUMBS = 4000;

self.addEventListener('install', (event) => {
  // Nothing is pre-fetched. The shell is cached as it is actually used, so a
  // first visit costs one round trip per file and never downloads something
  // this install will not open.
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names
      .filter((name) => name.startsWith('ninaivu-')
        && name !== SHELL_CACHE && name !== THUMB_CACHE)
      .map((name) => caches.delete(name)));
    await self.clients.claim();
  })());
});

/** Signing out clears everything: the next person gets a clean device. */
self.addEventListener('message', (event) => {
  if (event.data === 'ninaivu:forget') {
    event.waitUntil((async () => {
      const names = await caches.keys();
      await Promise.all(names.filter((n) => n.startsWith('ninaivu-'))
        .map((n) => caches.delete(n)));
    })());
  }
});

async function trim(cacheName, limit) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  // Oldest first: Cache Storage preserves insertion order.
  for (let i = 0; i < keys.length - limit; i += 1) {
    await cache.delete(keys[i]);
  }
}

/** Serve from cache, and refresh in the background for next time. */
/** For content addressed by a version in its URL: ask the network once, ever. */
async function cacheFirst(request, cacheName, limit) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(request);
  if (hit) return hit;

  const response = await fetch(request).catch(() => null);
  // Only ever store a plain success. An opaque, partial or error response
  // cached here would be served back as though it were the real thing.
  if (response && response.status === 200 && response.type === 'basic') {
    await cache.put(request, response.clone());
    if (limit) trim(cacheName, limit);
  }
  return response || new Response('', { status: 504, statusText: 'Offline' });
}

/**
 * The app's files from the server whenever it answers, the cached copy only
 * when it does not. Only the entry script's URL carries a version; the modules
 * it imports, the translations and the editor do not, so a cache answering
 * first would hand a new app.js the old api.js after an upgrade, and the page
 * would never open. On the home network asking costs a 304.
 */
async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const response = await fetch(request).catch(() => null);
  // Only ever store a plain success. An opaque, partial or error response
  // cached here would be served back as though it were the real thing.
  if (response && response.status === 200 && response.type === 'basic') {
    await cache.put(request, response.clone());
    return response;
  }
  if (response && response.status !== 504) return response;
  const hit = await cache.match(request);
  if (hit) return hit;
  return response || new Response('', { status: 504, statusText: 'Offline' });
}

async function networkFirstNavigation(request, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const networkResponse = await fetch(request);
    if (networkResponse && networkResponse.status === 200 && networkResponse.type === 'basic') {
      cache.put(request, networkResponse.clone());
    }
    return networkResponse;
  } catch {
    const hit = await cache.match(request) || await cache.match('/');
    if (hit) return hit;
    // Both languages: the page's own choice lives in a script this worker
    // has not got, and an English-only page told a Tamil reader nothing.
    return new Response(
      '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Ninaivu — Offline · இணைப்பு இல்லை</title><style>body{background:#12161c;color:#e6e8eb;font-family:system-ui,-apple-system,"Noto Sans Tamil","Latha",sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:24px;box-sizing:border-box;text-align:center}.card{max-width:340px}h1{font-size:20px;margin-bottom:8px}p{color:#8b949e;font-size:14px;line-height:1.6}hr{border:0;border-top:1px solid #30363d;margin:20px 0}</style></head><body><div class="card">'
      + '<h1>Ninaivu is offline</h1><p>Check your Wi-Fi or network connection to reconnect to your library.</p>'
      + '<hr><div lang="ta"><h1>நினைவுடன் இணைப்பு இல்லை</h1><p>உங்கள் நூலகத்துடன் மீண்டும் இணைய, Wi-Fi அல்லது நெட்வொர்க் இணைப்பைச் சரிபார்க்கவும்.</p></div>'
      + '</div></body></html>',
      { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
    );
  }
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  let url;
  try {
    url = new URL(request.url);
  } catch {
    return;
  }
  if (url.origin !== self.location.origin) return;

  // The API and the admin console are never intercepted or cached: the
  // browser talks to the server directly, exactly as with no worker at all.
  const path = url.pathname;
  if (path.startsWith('/api/') || path === '/admin' || path.startsWith('/admin/')) return;

  // Range requests are how video seeking works; a cache must not answer one.
  if (request.headers.has('range')) return;

  // Only the home page is an offline shell. Navigating to media, shares or
  // an API URL must never store that response or substitute the home page.
  if (url.pathname === '/' && request.mode === 'navigate') {
    event.respondWith(networkFirstNavigation(request, SHELL_CACHE));
    return;
  }

  if (url.pathname.startsWith('/static/')) {
    event.respondWith(networkFirst(request, SHELL_CACHE));
    return;
  }
  // Everything else is left alone deliberately — see the note at the top.
});

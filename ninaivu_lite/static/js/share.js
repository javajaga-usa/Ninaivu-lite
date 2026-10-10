/* The share page is deliberately its own small thing rather than the family
   app in a different hat. Somebody opening this link has no account, no
   session and no business loading the gallery's machinery — and every URL it
   uses carries the token, because that token is the only authority here. */
import * as i18n from './i18n.js';
import { mediaRefusal } from './api.js';

const TOKEN = document.body.dataset.shareToken;
const main = document.getElementById('main');
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

function askForPassword(message) {
  main.replaceChildren();
  const form = el('form');
  form.appendChild(el('p', 'muted', i18n.t('This link is password protected.')));
  const input = document.createElement('input');
  input.type = 'password'; input.required = true;
  input.autocomplete = 'off'; input.placeholder = i18n.t('Password');
  input.setAttribute('aria-label', i18n.t('Share password'));
  const error = el('p', 'error', message || '');
  // M24: read out, and placed under the field rather than under the button:
  // on a phone held sideways the line below the button was off the screen.
  error.setAttribute('role', 'alert');
  const button = el('button', null, i18n.t('Open'));
  button.type = 'submit';
  form.append(input, error, button);
  form.onsubmit = async (event) => {
    event.preventDefault();
    button.disabled = true;
    try {
      const res = await fetch(`/api/share/${TOKEN}/unlock`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: input.value }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        error.textContent = body.error ? i18n.t(body.error) : i18n.t('That did not work.');
        button.scrollIntoView({ block: 'nearest' });
        return;
      }
      await load();
    } catch {
      error.textContent = i18n.t('Could not reach Ninaivu. Try opening the link again.');
    } finally { button.disabled = false; }
  };
  main.appendChild(form);
  input.focus();
}

function renderAlbum(data) {
  document.getElementById('title').textContent = data.album?.name || i18n.t('Shared photographs');
  document.getElementById('count').textContent = data.total === 1
    ? i18n.t('1 photograph')
    : i18n.t('{count} photographs', { count: data.total });
  const grid = el('div', 'grid');
  for (const item of data.items) {
    const link = document.createElement('a');
    link.href = item.view || item.src; link.target = '_blank'; link.rel = 'noopener';
    // A video turned or flipped in the gallery would play the file's own way
    // up on its own: it opens on this page instead, which plays it turned.
    if (item.kind === 'video' && (item.rotation || item.mirror)) link.href = `${location.pathname}#item=${item.id}`;
    const img = document.createElement('img');
    img.src = item.thumb; img.alt = ''; img.loading = 'lazy';
    // Fades in when it arrives; a photograph that fails stays a quiet tile.
    img.onload = () => img.classList.add('ready');
    link.appendChild(img);
    grid.appendChild(link);
  }
  main.replaceChildren(grid);
}

function renderOne(item) {
  document.getElementById('title').textContent = i18n.t('Shared photograph');
  const stage = el('div', 'single');
  const isVideo = (item.kind === 'video');
  const node = document.createElement(isVideo ? 'video' : 'img');
  node.src = item.view || item.src;
  node.onerror = async () => {
    if (!isVideo && item.thumb && node.getAttribute('src') !== item.thumb) {
      node.src = item.thumb;
    } else {
      // The server says why (a video it will not send with its location data
      // in it is a 415 with a sentence); a generic error hid that.
      const reason = await mediaRefusal(item.view || item.src);
      stage.appendChild(el('p', 'error', reason || i18n.t('This media could not be displayed.')));
    }
  };
  if (isVideo) {
    node.controls = true; node.playsInline = true;
    // A video turned or flipped by hand in the gallery plays that way here too
    // (a photograph's turn is already in its copy). A quarter turn is shrunk
    // to fit, as the gallery's viewer does.
    const rotation = Number(item.rotation || 0) % 360;
    if (rotation || item.mirror) {
      const fit = () => {
        let scale = 1;
        if (rotation % 180 && node.videoWidth && node.offsetWidth) {
          const contain = Math.min(node.offsetWidth / node.videoWidth, node.offsetHeight / node.videoHeight);
          scale = Math.min(node.offsetWidth / (node.videoHeight * contain),
            node.offsetHeight / (node.videoWidth * contain));
        }
        node.style.transform = `scale(${scale}) rotate(${rotation}deg)` + (item.mirror ? ' scaleX(-1)' : '');
      };
      node.addEventListener('loadedmetadata', fit);
      window.addEventListener('resize', fit);
      fit();
    }
  }
  else { node.alt = i18n.t('Shared photograph'); }
  stage.appendChild(node);
  main.replaceChildren(stage);
}

async function load() {
  try {
    const res = await fetch(`/api/share/${TOKEN}`, { headers: { Accept: 'application/json' } });
    if (res.status === 401) { askForPassword(); return; }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      main.replaceChildren(el('p', 'empty',
        data.error ? i18n.t(data.error) : i18n.t('This link is no longer available.')));
      return;
    }
    const one = Number(new URLSearchParams(location.hash.slice(1)).get('item'));
    const chosen = data.scope === 'album' && one ? data.items.find((item) => item.id === one) : null;
    if (chosen) renderOne(chosen);
    else if (data.scope === 'album') renderAlbum(data); else renderOne(data.item);
  } catch (err) {
    main.replaceChildren(el('p', 'empty', i18n.t('This link could not be opened.')));
  }
}
// Whatever the person opening this link reads. They have no account here and
// no stored preference, so this is their browser's answer — which is the right
// one: a link sent to somebody who reads Tamil opens in Tamil. A promise rather
// than a top-level await, which an iPad on iOS 14 cannot parse at all.
i18n.start().catch(() => {}).then(() => {
  const from = document.getElementById('from');
  if (from) {
    from.textContent = i18n.t('Shared from {name}',
      { name: document.body.dataset.appName || 'Ninaivu' });
  }
  load();
});

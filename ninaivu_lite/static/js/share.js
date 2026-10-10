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
  const isVideo = (item.kind === 'video');
  document.getElementById('title').textContent = i18n.t(isVideo ? 'Shared video' : 'Shared photograph');
  const stage = el('div', 'single');
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
      // A quarter turn: the video sits in a box the shape of the turned
      // picture, as large as the page allows, and is turned inside it. (Shrunk
      // to fit its own landscape box it was a third of a phone's width.)
      const box = el('div', 'turned-box');
      const fit = () => {
        const turn = `rotate(${rotation}deg)` + (item.mirror ? ' scaleX(-1)' : '');
        if (!(rotation % 180) || !node.videoWidth) {
          node.style.transform = turn;
          return;
        }
        // The height left under the header, less the play bar under it.
        const below = window.innerHeight - stage.getBoundingClientRect().top - 72;
        const room = { w: stage.clientWidth || window.innerWidth,
          h: Math.max(160, Math.min(window.innerHeight * 0.82, below)) };
        const width = Math.min(room.w, room.h * node.videoHeight / node.videoWidth);
        const height = width * node.videoWidth / node.videoHeight;
        box.style.width = `${width}px`; box.style.height = `${height}px`;
        node.style.width = `${height}px`; node.style.height = `${width}px`;
        node.style.transform = `translate(-50%, -50%) ${turn}`;
        box.classList.add('quarter');
      };
      node.addEventListener('loadedmetadata', fit);
      window.addEventListener('resize', fit);
      fit();
      // The browser's own controls turn (or mirror) with the picture.
      node.controls = false;
      box.appendChild(node);
      stage.append(box, turnedControls(node));
      main.replaceChildren(stage);
      fit();
      return;
    }
  }
  else { node.alt = i18n.t('Shared photograph'); }
  stage.appendChild(node);
  main.replaceChildren(stage);
}

const clock = (seconds) => {
  const s = Math.round(seconds || 0);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};

/** Play, pause and a position bar under a turned video, the right way up. */
function turnedControls(video) {
  const bar = el('div', 'turned-controls');
  const play = el('button');
  play.type = 'button';
  const seek = document.createElement('input');
  seek.type = 'range'; seek.min = '0'; seek.max = '1000'; seek.value = '0';
  seek.setAttribute('aria-label', i18n.t('Position in the video'));
  const time = el('span', 'turned-time');
  const paint = () => {
    const paused = video.paused || video.ended;
    play.innerHTML = paused
      ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7Z"/></svg>'
      : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z"/></svg>';
    play.setAttribute('aria-label', i18n.t(paused ? 'Play' : 'Pause'));
    play.title = play.getAttribute('aria-label');
    const length = video.duration || 0;
    if (length && document.activeElement !== seek) seek.value = String(Math.round(video.currentTime / length * 1000));
    time.textContent = length ? `${clock(video.currentTime)} / ${clock(length)}` : '';
  };
  play.onclick = () => (video.paused || video.ended ? video.play().catch(() => {}) : video.pause());
  seek.oninput = () => { if (video.duration) video.currentTime = Number(seek.value) / 1000 * video.duration; };
  for (const name of ['play', 'pause', 'timeupdate', 'loadedmetadata', 'ended']) video.addEventListener(name, paint);
  // Nothing to play: no controls under the reason why.
  video.addEventListener('error', () => { bar.hidden = true; });
  paint();
  bar.append(play, seek, time);
  return bar;
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

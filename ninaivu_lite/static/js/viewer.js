/**
 * Full-screen viewer: zoom/pan, swipe, slideshow, filmstrip and the EXIF
 * panel. Neighbouring items are prefetched so paging feels
 * instant.
 */

import { api, thumbUrl } from './api.js';
import { blurhashUrl } from './blurhash.js';
import * as i18n from './i18n.js';

const MAX_ZOOM = 8;

const escapeText = (value) => String(value ?? '');

function formatBytes(n) {
  if (!n) return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  let value = n;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return `${i === 0 ? value : value.toFixed(1)} ${units[i]}`;
}

/** Plain English for where a rotation came from. */
const ROTATION_WHY = {
  exif: i18n.key('from the camera'),
  faces: i18n.key('worked out from the people in it'),
  ai: i18n.key('worked out from the picture'),
  manual: i18n.key('you set this'),
};

function formatClock(seconds) {
  if (!seconds) return '';
  const s = Math.round(seconds);
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, '0')}`;
}

/**
 * How much to trust the date beside a photograph.
 *
 * Only `exif` is the camera's own answer. Everything else is Ninaivu working
 * it out from the folder it sits in or the timestamps on the file, and a
 * worked-out date that looks identical to a real one is how a photograph from
 * 2014 comes to sit under this month without anybody noticing.
 */
function dateCaveat(source) {
  switch (source) {
    case 'exif': return '';
    case 'container': return '';
    case 'takeout-json': return ' ' + i18n.t('(from Google Takeout)');
    case 'filename': return ' ' + i18n.t('(from the file name)');
    case 'path': return ' ' + i18n.t('(from the folder)');
    case 'created': return ' ' + i18n.t('(file created)');
    case 'mtime': return ' ' + i18n.t('(file modified)');
    default: return source ? ` (${source})` : '';
  }
}


export class Viewer extends EventTarget {
  constructor(root) {
    super();
    this.root = root;
    this.stage = root.querySelector('#viewer-stage');
    this.info = root.querySelector('#viewer-info');
    this.infoList = root.querySelector('#info-list');
    this.filmstrip = root.querySelector('#filmstrip');
    this.ids = [];
    this.index = -1;
    this.item = null;
    this.cache = new Map();
    this.slideshow = null;
    this.slideshowDelay = 4000;
    this.slideshowLoop = true;
    try {
      const settings = JSON.parse(localStorage.getItem('mv.slideshow') || '{}');
      if ([2000,4000,8000,15000].includes(settings.delay)) this.slideshowDelay = settings.delay;
      if (typeof settings.loop === 'boolean') this.slideshowLoop = settings.loop;
    } catch { /* Browser storage is optional. */ }

    this.transform = { scale: 1, x: 0, y: 0, rotate: 0 };
    this.pointers = new Map();
    this.pinchStart = null;

    /** Set by the app: whether this viewer may offer the download. */
    this.canDownload = false;
    /** Set by the app: whether Sudar may save a copy beside the original (admins). */
    this.canSave = false;
    /** Set by the app: whether a photograph may be turned by hand (admins). */
    this.canRotate = false;
    //: signed in: a photograph can become the person's own profile picture
    this.canAvatar = false;
    this.toast = null;
    this.onChange = null;

    this.wire();

    // A window that changes shape changes how much a turned photograph has to
    // shrink to keep fitting. Cheap, and only ever does anything while the
    // viewer is open on a quarter-turned picture.
    window.addEventListener('resize', () => {
      if (!this.root.hidden) this.applyTransform();
    });
  }

  /* ---------------------------------------------------------------- */

  wire() {
    const q = (sel) => this.root.querySelector(sel);
    q('#v-close').onclick = () => this.close();
    q('#v-prev').onclick = () => this.step(-1);
    q('#v-next').onclick = () => this.step(1);
    q('#v-zoom-in').onclick = () => this.zoomBy(1.5);
    q('#v-zoom-out').onclick = () => this.zoomBy(1 / 1.5);
    q('#v-info').onclick = () => this.toggleInfo();
    if (q('#v-info-close')) q('#v-info-close').onclick = () => this.toggleInfo(false);
    q('#v-rotate').onclick = () => this.rotate();
    q('#v-avatar').onclick = () => {
      this.dispatchEvent(new CustomEvent('avatar', { detail: { item: this.item } }));
    };
    // Sudar, the photo studio: loaded the first time it is opened, because
    // most visits never press it and its engine is the largest script here.
    q('#v-sudar').onclick = async () => {
      if (!this.canDownload || this.item?.kind !== 'picture') return;
      const item = { ...this.item, rotation: this.transform.rotate };
      this.stopSlideshow();
      if (this.isKiosk) this.toggleKiosk();
      try {
        const { openSudar } = await import('./sudar/sudar.js');
        openSudar({ item, returnFocus: q('#v-sudar'), canSave: this.canSave, onSaved: (copy) => {
          this.toast?.(i18n.t('Edited copy saved — original kept'));
          this.onChange?.(copy);
          this.open([copy.id], 0);
        } });
      } catch { this.toast?.(i18n.t('Sudar could not load. Please refresh and try again.')); }
    };
    // The button asks how to play before it plays; Space starts at once with
    // what was chosen last. While it is playing the button stops it.
    q('#v-slideshow').onclick = () => (this.slideshow ? this.stopSlideshow() : this.toggleSlidePop());
    const pop = q('#slide-pop');
    if (pop) {
      q('#v-slide-start').onclick = () => this.startSlideshow();
      q('#v-slide-cancel').onclick = () => this.toggleSlidePop(false);
      pop.addEventListener('keydown', (event) => {
        // Nothing typed in the question is a shortcut for the picture behind it.
        event.stopPropagation();
        if (event.key === 'Escape') { event.preventDefault(); this.toggleSlidePop(false, true); }
      });
      document.addEventListener('click', (event) => {
        if (pop.hidden || pop.contains(event.target) || event.target.closest('#v-slideshow')) return;
        this.toggleSlidePop(false);
      });
    }
    const delay = q('#v-slide-delay'), loop = q('#v-slide-loop');
    if (delay && loop) {
      delay.value = String(this.slideshowDelay);loop.checked = this.slideshowLoop;
      const update = () => {
        this.slideshowDelay = Number(delay.value);this.slideshowLoop = loop.checked;
        try { localStorage.setItem('mv.slideshow', JSON.stringify({delay:this.slideshowDelay,loop:this.slideshowLoop})); } catch {}
        if (this.slideshow) this.scheduleSlideshow();
      };
      delay.onchange = loop.onchange = update;
    }
    q('#v-fav').onclick = () => this.toggleFavorite();
    if (q('#v-kiosk')) q('#v-kiosk').onclick = () => this.toggleKiosk();
    if (q('#v-album')) q('#v-album').onclick = () => {
      if (!this.item) return;
      this.dispatchEvent(new CustomEvent('album', { detail: { item: this.item } }));
    };
    if (q('#v-share')) q('#v-share').onclick = () => {
      this.dispatchEvent(new CustomEvent('share', { detail: { item: this.item } }));
    };

    // The viewer's "More" menu, at every width: everything but the everyday
    // tools, each with its name. The same open / close-on-outside-click /
    // close-on-pick pattern as the topbar's menus, anchored under this
    // toolbar. Opening it from the keyboard puts focus on the first item, and
    // the arrow keys move between items rather than between photographs.
    const moreBtn = q('#v-more');
    const moreMenu = q('#viewer-tools-more');
    const menuItems = () => [...moreMenu.querySelectorAll('button')]
      .filter((button) => !button.hidden && !button.closest('[hidden]'));
    moreBtn.onclick = (event) => {
      event.stopPropagation();
      const open = moreMenu.classList.toggle('open');
      moreBtn.setAttribute('aria-expanded', String(open));
      // The slideshow question opens in the same corner; one at a time.
      if (open) this.toggleSlidePop(false);
      if (open && event.detail === 0) menuItems()[0]?.focus();
    };
    moreMenu.addEventListener('keydown', (event) => {
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      event.preventDefault();
      event.stopPropagation();
      const items = menuItems();
      const at = items.indexOf(document.activeElement);
      const next = at < 0 ? 0 : (at + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      items[next]?.focus();
    });
    // A button inside the menu is a one-shot action — close the menu once
    // it's picked rather than leaving it sitting open over the photo.
    moreMenu.addEventListener('click', (event) => {
      if (event.target.closest('button')) this.closeMoreMenu();
    });
    document.addEventListener('click', (event) => {
      if (!moreMenu.classList.contains('open')) return;
      if (moreMenu.contains(event.target) || event.target === moreBtn) return;
      this.closeMoreMenu();
    });

    this.stage.addEventListener('wheel', (event) => this.onWheel(event), { passive: false });
    this.stage.addEventListener('pointerdown', (event) => this.onPointerDown(event));
    this.stage.addEventListener('pointermove', (event) => this.onPointerMove(event));
    this.stage.addEventListener('pointerup', (event) => this.onPointerUp(event));
    this.stage.addEventListener('pointercancel', (event) => this.onPointerUp(event));
    this.root.addEventListener('click', (event) => {
      if (!this.isKiosk || event.target.closest('#v-kiosk')) return;
      event.preventDefault();
      event.stopPropagation();
      this.toggleKiosk();
    }, true);
    this.stage.addEventListener('dblclick', (event) => this.onDoubleClick(event));

    this.filmstrip.addEventListener('click', (event) => {
      const img = event.target.closest('img');
      if (img) this.goTo(Number(img.dataset.index));
    });
  }

  toggleKiosk() {
    this.stopSlideshow();
    this.isKiosk = !this.isKiosk;
    const hud = this.root.querySelector('#kiosk-hud');
    const topBar = this.root.querySelector('.viewer-top');
    const filmstrip = this.root.querySelector('#filmstrip');
    const navPrev = this.root.querySelector('#v-prev');
    const navNext = this.root.querySelector('#v-next');

    if (this.isKiosk) {
      if (hud) hud.hidden = false;
      if (topBar) topBar.style.opacity = '0';
      if (filmstrip) filmstrip.style.opacity = '0';
      if (navPrev) navPrev.style.opacity = '0';
      if (navNext) navNext.style.opacity = '0';
      if (this.media) this.media.classList.add('ken-burns');

      this.updateKioskClock();
      this.kioskClockTimer = setInterval(() => this.updateKioskClock(), 1000);
      this.kioskTimer = setInterval(() => this.goTo((this.index + 1) % this.ids.length), 8000);
      try { document.documentElement.requestFullscreen?.()?.catch(() => {}); } catch {}
      this.toast?.(i18n.t('Ambient Frame mode active — click anywhere or Esc to exit'));
    } else {
      if (hud) hud.hidden = true;
      if (topBar) topBar.style.opacity = '';
      if (filmstrip) filmstrip.style.opacity = '';
      if (navPrev) navPrev.style.opacity = '';
      if (navNext) navNext.style.opacity = '';
      if (this.media) this.media.classList.remove('ken-burns');

      clearInterval(this.kioskClockTimer);
      clearInterval(this.kioskTimer);
      this.kioskClockTimer = null;
      this.kioskTimer = null;
      try { if (document.fullscreenElement) document.exitFullscreen?.()?.catch(() => {}); } catch {}
    }
  }

  updateKioskClock() {
    const clock = this.root.querySelector('#kiosk-clock');
    const date = this.root.querySelector('#kiosk-date');
    if (!clock || !date) return;
    const now = new Date();
    clock.textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    date.textContent = now.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' });
  }

  async open(ids, index, thumbed = null) {
    this.ids = ids;
    this.thumbed = thumbed;
    this.root.hidden = false;
    document.body.style.overflow = 'hidden';
    await this.goTo(index);
    this.root.focus?.();
  }

  close() {
    this.previousIndex = null;
    if (this.isKiosk) this.toggleKiosk();
    this.stopSlideshow();
    this.toggleSlidePop(false);
    this.closeMoreMenu();
    this.root.hidden = true;
    this.stage.innerHTML = '';
    document.body.style.overflow = '';
    this.dispatchEvent(new CustomEvent('close', { detail: { id: this.item?.id } }));
  }

  /** Close the viewer's "More" menu, if it's open. Cheap and safe to call
   * unconditionally — leaving it open across a close or a swipe to the next
   * photo is the kind of thing that only shows up on someone's phone.
   * `returnFocus` is for Escape: focus goes back to the button that opened
   * it rather than being lost with the menu. */
  closeMoreMenu(returnFocus = false) {
    const menu = this.root.querySelector('#viewer-tools-more');
    const btn = this.root.querySelector('#v-more');
    const hadFocus = menu.contains(document.activeElement);
    menu.classList.remove('open');
    btn.setAttribute('aria-expanded', 'false');
    if (returnFocus && hadFocus) btn.focus();
  }

  get isOpen() {
    return !this.root.hidden;
  }

  step(delta) {
    const next = this.index + delta;
    if (next < 0 || next >= this.ids.length) return;
    this.goTo(next);
  }

  async fetchItem(id) {
    if (this.cache.has(id)) return this.cache.get(id);
    const item = await api.asset(id);
    if (this.cache.size > 300) this.cache.clear();
    this.cache.set(id, item);
    return item;
  }

  async goTo(index) {
    if (index < 0 || index >= this.ids.length) return;
    this.index = index;
    const id = this.ids[index];

    let item;
    try {
      item = await this.fetchItem(id);
    } catch {
      return;
    }
    if (!this.isOpen || this.ids[this.index] !== id) return; // user moved on while loading
    this.item = item;

    // Stage first: resetTransform needs the media element to exist, and
    // applying the photograph's own rotation to nothing was why a corrected
    // photograph still opened sideways in the viewer while looking right in
    // the grid.
    this.renderStage(item);
    this.resetTransform();
    this.renderChrome(item);
    if (this.slideshow) this.scheduleSlideshow();
    this.renderFilmstrip();
    if (!this.info.hidden) this.renderInfo(item);

    if (this.isKiosk) {
      const kTitle = this.root.querySelector('#kiosk-title');
      const kSub = this.root.querySelector('#kiosk-sub');
      if (kTitle) kTitle.textContent = item.caption || item.name || '';
      if (kSub) kSub.textContent = [item.date, item.city, item.country].filter(Boolean).join(' · ');
      if (this.media) this.media.classList.add('ken-burns');
    }

    // Warm the neighbours so paging is instant — more of them the way the
    // person is going, since that is where the next swipe lands. Stepping
    // backwards used to find only one photograph ready.
    let moved = this.previousIndex == null ? 0 : index - this.previousIndex;
    // The slideshow wrapping from the last photograph to the first is forwards.
    if (Math.abs(moved) > this.ids.length / 2) moved -= Math.sign(moved) * this.ids.length;
    const going = Math.sign(moved);
    this.previousIndex = index;
    const ahead = going < 0 ? [-1, -2, -3, 1] : going > 0 ? [1, 2, 3, -1] : [1, -1, 2];
    for (const offset of ahead) {
      const neighbour = this.ids[index + offset];
      if (neighbour != null && !this.cache.has(neighbour)) {
        this.fetchItem(neighbour).then((data) => {
          if (data?.kind === 'picture') new Image().src = data.view || data.src;
        }).catch(() => {});
      }
    }
    this.dispatchEvent(new CustomEvent('change', { detail: { id, index } }));
  }

  /* -- rendering ------------------------------------------------------ */

  renderStage(item) {
    this.stage.innerHTML = '';

    if (item.kind === 'video') {
      if (!item.playable) {
        // The browser cannot open this container: offer the file instead.
        this.renderUnsupported(item);
        return;
      }
      const video = document.createElement('video');
      video.src = item.src;
      video.controls = true;
      video.autoplay = true;
      video.playsInline = true;
      video.preload = 'metadata';
      video.onended = () => this.slideshow && this.advanceSlideshow();
      if (!item.has_thumb) {
        this.dispatchEvent(new CustomEvent('poster-source', { detail: { id: item.id, video } }));
      }
      this.media = video;
      this.stage.appendChild(video);
      return;
    }

    // A photograph the browser cannot decode is converted on first view, and
    // a 12-megapixel HEIC takes a few seconds the first time. The thumbnail
    // already exists and is a real picture rather than a blur, so it stands in
    // until the full copy arrives — after which the copy is cached and this
    // never happens again for that photograph.
    const placeholder = (!item.playable && item.thumb_v !== undefined)
      ? thumbUrl(item.id, 640, item.thumb_v)
      : blurhashUrl(item.blurhash);
    if (placeholder) {
      const ghost = document.createElement('img');
      ghost.className = 'placeholder';
      ghost.src = placeholder;
      ghost.alt = '';
      ghost.style.width = `${Math.min(item.width || 800, window.innerWidth * 0.92)}px`;
      ghost.style.aspectRatio = `${item.width || 4} / ${item.height || 3}`;
      this.stage.appendChild(ghost);
    }

    const img = document.createElement('img');
    img.alt = item.name;
    img.decoding = 'async';
    // `view` is the file itself for a JPEG and a converted copy for HEIC or
    // raw. Falling back to `src` keeps this working against an older server.
    img.src = item.view || item.src;
    img.onload = () => {
      this.stage.querySelector('.placeholder')?.remove();
      // The fit-after-turn factor is worked out from the picture's own
      // dimensions, and until it has loaded there are none — so a photograph
      // that opens already turned has to be measured again here, or it is
      // laid out at scale 1 and runs off the top and bottom of the screen.
      this.applyTransform();
    };
    img.onerror = () => {
      // The original could not be served — the file is gone from disk or
      // unreadable. Left alone, the blurhash placeholder would stay up
      // forever and read as an abstract picture that was never in the
      // library. Drop it and fall back to the generated preview instead.
      this.stage.querySelector('.placeholder')?.remove();
      img.remove();
      if (item.has_thumb) {
        const fallback = document.createElement('img');
        fallback.src = thumbUrl(item.id, 640, item.thumb_v);
        fallback.alt = item.name;
        this.media = fallback;
        this.stage.appendChild(fallback);
      }
      const note = document.createElement('div');
      note.className = 'badge';
      note.style.cssText = 'position:absolute;bottom:80px;left:50%;transform:translateX(-50%)';
      note.textContent = i18n.t('Original file is not available — showing the preview');
      this.stage.appendChild(note);
    };
    this.media = img;
    this.stage.appendChild(img);
  }

  renderUnsupported(item) {
    const box = document.createElement('div');
    box.className = 'audio-hero';
    const title = document.createElement('div');
    title.className = 'title';
    title.textContent = i18n.t('{name} cannot play in the browser', { name: item.name });
    const link = document.createElement('a');
    link.className = 'btn primary';
    link.href = item.download;
    link.textContent = i18n.t('Download to play');
    box.append(title, link);
    this.stage.appendChild(box);
  }

  renderChrome(item) {
    // The kind on the root: a phone keeps the arrows for a video, where a
    // swipe would start on the player.
    this.root.dataset.kind = item.kind;
    this.root.querySelector('#v-sudar').hidden = !this.canDownload || item.kind !== 'picture';
    this.root.querySelector('#v-rotate').hidden = !this.canRotate || item.kind !== 'picture';
    this.root.querySelector('#v-avatar').hidden = !this.canAvatar || item.kind !== 'picture';
    this.root.querySelector('#v-name').textContent = item.name;
    // On a phone the line has the width of the bar's left half: the size
    // (in Details anyway) is left off rather than wrapped onto a second line.
    const narrow = window.matchMedia('(max-width: 620px)').matches;
    const bits = [
      item.date,
      item.width ? `${item.width}×${item.height}` : '',
      narrow ? '' : item.size_h,
      item.duration ? formatClock(item.duration) : '',
    ].filter(Boolean);
    this.root.querySelector('#v-sub').textContent = bits.join('  ·  ');
    this.root.querySelector('#v-download').href = item.download;
    this.root.querySelector('#v-fav').classList.toggle('on', item.favorite);
  }

  renderFilmstrip() {
    const window_ = 24;
    const from = Math.max(0, this.index - window_);
    const to = Math.min(this.ids.length, this.index + window_);
    this.filmstrip.innerHTML = '';
    for (let i = from; i < to; i++) {
      const img = document.createElement('img');
      img.loading = 'lazy';
      img.decoding = 'async';
      img.alt = '';
      img.dataset.index = String(i);
      if (i === this.index) img.className = 'current';
      // Audio (and anything else without a derivative) has no thumbnail, so
      // don't request one; the error handler covers stale index entries.
      if (this.thumbed && !this.thumbed.has(this.ids[i])) {
        img.classList.add('no-thumb');
      } else {
        img.addEventListener('error', () => {
          img.removeAttribute('src');
          img.classList.add('no-thumb');
        }, { once: true });
        img.src = thumbUrl(this.ids[i], 256, this.cache.get(this.ids[i])?.thumb_v);
      }
      this.filmstrip.appendChild(img);
    }
    this.centerFilmstrip(true);
    // Thumbnails arrive at different widths, so what was centred drifts as
    // the ones before it load: centre again as each settles.
    let queued = false;
    const again = () => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => { queued = false; this.centerFilmstrip(false); });
    };
    for (const img of this.filmstrip.querySelectorAll('img')) {
      if (!img.complete) img.addEventListener('load', again, { once: true });
    }
  }

  /** Put the current frame in the middle of the strip, exactly. The strip has
   *  half its width of padding at each end (see .filmstrip), so even the first
   *  and last frame can sit in the middle. */
  centerFilmstrip(smooth) {
    const strip = this.filmstrip;
    const current = strip.querySelector('.current');
    if (!current) return;
    const left = current.offsetLeft + current.offsetWidth / 2 - strip.clientWidth / 2;
    strip.scrollTo({ left: Math.max(0, left), behavior: smooth ? 'smooth' : 'auto' });
  }

  toggleInfo(force) {
    this.info.hidden = force === undefined ? !this.info.hidden : !force;
    this.root.classList.toggle('info-open', !this.info.hidden);
    this.root.querySelector('#v-info').classList.toggle('on', !this.info.hidden);
    if (!this.info.hidden && this.item) this.renderInfo(this.item);
  }

  renderInfo(item) {
    const rows = [
      [i18n.t('Name'), item.name],
      [i18n.t('Folder'), item.folder || '—'],
      [i18n.t('Taken'), item.date + dateCaveat(item.date_source)],
      [i18n.t('Type'), `${item.kind} · ${item.ext.toUpperCase()}`],
      [i18n.t('Size'), item.size_h],
      item.width ? [i18n.t('Dimensions'), `${item.width} × ${item.height}`] : null,
      item.duration ? [i18n.t('Duration'), formatClock(item.duration)] : null,
      item.camera ? [i18n.t('Camera'), item.camera] : null,
      item.lens ? [i18n.t('Lens'), item.lens] : null,
      item.f_number ? [i18n.t('Aperture'), `ƒ/${item.f_number}`] : null,
      item.exposure ? [i18n.t('Shutter'), item.exposure] : null,
      item.iso ? ['ISO', item.iso] : null,
      item.focal_length ? [i18n.t('Focal length'), `${item.focal_length} mm`] : null,
      // Only when the photograph is shown turned, and saying who decided.
      item.rotation ? [i18n.t('Turned'), `${item.rotation}° · ${i18n.t(ROTATION_WHY[item.rotation_source])
        || item.rotation_source}`] : null,
    ].filter(Boolean);

    this.infoList.innerHTML = '';
    for (const [label, value] of rows) {
      const dt = document.createElement('dt');
      dt.textContent = label;
      const dd = document.createElement('dd');
      dd.textContent = escapeText(value);
      this.infoList.append(dt, dd);
    }
  }

  /* -- mutations ------------------------------------------------------- */

  async toggleFavorite() {
    if (!this.item || this.favoritePending) return;
    const item = this.item;
    const previous = item.favorite;
    const next = !previous;
    this.favoritePending = true;
    item.favorite = next;
    this.root.querySelector('#v-fav').classList.toggle('on', next);
    try {
      await api.update(item.id, { favorite: next });
      this.dispatchEvent(new CustomEvent('mutated', {
        detail: { id: item.id, favorite: next },
      }));
    } catch (error) {
      item.favorite = previous;
      if (this.item === item) this.root.querySelector('#v-fav').classList.toggle('on', !!previous);
      this.toast?.(i18n.t('Could not update favourite: {error}', { error: error.message }), true);
    } finally {
      this.favoritePending = false;
    }
  }

  /** A quarter turn more, kept in the index (an administrator's answer for
   *  a photograph the scan could not judge). The file is never changed. */
  async rotate() {
    const item = this.item;
    if (!item || item.kind !== 'picture' || this.rotatePending) return;
    this.rotatePending = true;
    const rotation = (Number(item.rotation || 0) + 90) % 360;
    try {
      const updated = await api.rotate(item.id, rotation);
      Object.assign(item, updated);
      this.cache.set(item.id, item);
      if (this.item === item) { this.resetTransform(); this.renderInfo(item); }
      this.dispatchEvent(new CustomEvent('mutated', { detail: { id: item.id, rotation } }));
    } catch (error) {
      this.toast?.(error.message, true);
    } finally {
      this.rotatePending = false;
    }
  }

  /* -- transform ------------------------------------------------------- */

  resetTransform() {
    // The turn starts where the photograph says it should, not at zero.
    // Thumbnails are baked upright by the scanner, but /api/file serves the
    // untouched original — so a photograph Ninaivu worked out the orientation
    // of would open sideways here while looking fine in the grid.
    this.transform = {
      scale: 1, x: 0, y: 0, rotate: Number(this.item?.rotation || 0) % 360,
    };
    this.applyTransform();
  }

  /**
   * How much to shrink a quarter-turned picture so it still fits.
   *
   * `object-fit: contain` fits the picture to the stage *before* the CSS
   * rotation happens, and a rotation does not re-run it — so a landscape
   * photograph turned on its side is laid out at its landscape size and then
   * spun, and its now-vertical long edge runs straight off the top and bottom
   * of the screen. This is the factor that puts it back inside.
   */
  fitScale() {
    const el = this.media;
    const turn = Math.abs(this.transform.rotate % 180);
    if (!el || turn !== 90) return 1;

    const boxW = el.offsetWidth;
    const boxH = el.offsetHeight;
    // The element fills the stage; what is *painted* inside it is the picture
    // fitted to that box by object-fit, and that is the thing being turned.
    // Measuring the element box instead gives the wrong factor entirely,
    // because the box is the shape of the window rather than the photograph.
    const natW = el.naturalWidth || el.videoWidth || boxW;
    const natH = el.naturalHeight || el.videoHeight || boxH;
    if (!boxW || !boxH || !natW || !natH) return 1;

    const contain = Math.min(boxW / natW, boxH / natH);
    const paintedW = natW * contain;
    const paintedH = natH * contain;
    // A quarter turn swaps which side has to fit which.
    // A portrait turned landscape can use MORE of the stage. Capping this
    // at 1 leaves it unnecessarily small even though there is room to fit.
    return Math.min(boxW / paintedH, boxH / paintedW);
  }

  applyTransform() {
    const el = this.media;
    if (!el) return;
    const { scale, x, y, rotate } = this.transform;
    const fitted = scale * this.fitScale();
    el.style.transform = `translate3d(${x}px, ${y}px, 0) scale(${fitted}) rotate(${rotate}deg)`;
    el.style.transition = this.pointers.size ? 'none' : 'transform .18s cubic-bezier(.22,.61,.36,1)';
    el.classList.toggle('zoomed', scale > 1.02);
    // Zooming out only makes sense once something has zoomed in — at 1:1
    // there is nowhere further out to go, so the button starts disabled and
    // wakes up the moment a zoom-in (wheel, pinch, double-click or the +
    // button) actually moves the scale past 1.
    const zoomOut = this.root.querySelector('#v-zoom-out');
    if (zoomOut) zoomOut.disabled = scale <= 1;
    const zoomIn = this.root.querySelector('#v-zoom-in');
    if (zoomIn) zoomIn.disabled = scale >= MAX_ZOOM;
  }

  zoomBy(factor, origin) {
    const previous = this.transform.scale;
    const next = Math.max(1, Math.min(MAX_ZOOM, previous * factor));
    if (next === previous) return;
    if (origin && this.media) {
      const rect = this.media.getBoundingClientRect();
      const dx = origin.x - (rect.left + rect.width / 2);
      const dy = origin.y - (rect.top + rect.height / 2);
      const ratio = next / previous;
      this.transform.x = this.transform.x - dx * (ratio - 1);
      this.transform.y = this.transform.y - dy * (ratio - 1);
    }
    this.transform.scale = next;
    if (next === 1) {
      this.transform.x = 0;
      this.transform.y = 0;
    }
    this.applyTransform();
  }

  onWheel(event) {
    if (event.ctrlKey || event.metaKey || this.transform.scale > 1.02) {
      event.preventDefault();
      this.zoomBy(event.deltaY < 0 ? 1.15 : 1 / 1.15, { x: event.clientX, y: event.clientY });
    }
  }

  onDoubleClick(event) {
    if (this.item?.kind !== 'picture') return;
    if (this.transform.scale > 1.02) this.resetTransform();
    else this.zoomBy(2.6, { x: event.clientX, y: event.clientY });
  }

  onPointerDown(event) {
    if (event.target.closest('video, audio, .filmstrip, .viewer-info')) return;
    this.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    this.stage.setPointerCapture?.(event.pointerId);
    this.dragStart = {
      x: event.clientX,
      y: event.clientY,
      tx: this.transform.x,
      ty: this.transform.y,
      t: performance.now(),
    };
    if (this.pointers.size === 2) {
      const [a, b] = [...this.pointers.values()];
      this.pinchStart = {
        distance: Math.hypot(a.x - b.x, a.y - b.y),
        scale: this.transform.scale,
      };
    }
  }

  onPointerMove(event) {
    if (!this.pointers.has(event.pointerId)) return;
    this.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });

    if (this.pointers.size === 2 && this.pinchStart) {
      const [a, b] = [...this.pointers.values()];
      const distance = Math.hypot(a.x - b.x, a.y - b.y);
      this.transform.scale = Math.max(
        1, Math.min(MAX_ZOOM, this.pinchStart.scale * (distance / this.pinchStart.distance)),
      );
      this.applyTransform();
      return;
    }

    if (this.transform.scale > 1.02 && this.dragStart) {
      this.transform.x = this.dragStart.tx + (event.clientX - this.dragStart.x);
      this.transform.y = this.dragStart.ty + (event.clientY - this.dragStart.y);
      this.media?.classList.add('panning');
      this.applyTransform();
    }
  }

  onPointerUp(event) {
    const start = this.dragStart;
    this.pointers.delete(event.pointerId);
    this.media?.classList.remove('panning');
    if (this.pointers.size < 2) this.pinchStart = null;
    if (!start) return;
    this.dragStart = null;

    const dx = event.clientX - start.x;
    const dy = event.clientY - start.y;
    const elapsed = performance.now() - start.t;

    // A quick horizontal flick pages, but only when not zoomed in.
    if (this.transform.scale <= 1.02 && Math.abs(dx) > 60
        && Math.abs(dx) > Math.abs(dy) * 1.6 && elapsed < 600) {
      this.step(dx < 0 ? 1 : -1);
      return;
    }
    this.applyTransform();
  }

  /* -- slideshow ------------------------------------------------------- */

  toggleSlideshow() {
    if (this.slideshow) this.stopSlideshow();
    else this.startSlideshow();
  }

  /** The question that comes before a slideshow: how long, and whether to loop. */
  toggleSlidePop(force, returnFocus = false) {
    const pop = this.root.querySelector('#slide-pop');
    if (!pop) return;
    const show = force === undefined ? pop.hidden : force;
    pop.hidden = !show;
    if (show) {
      this.closeMoreMenu();
      pop.querySelector('#v-slide-start').focus();
    } else if (returnFocus) {
      this.root.querySelector('#v-slideshow').focus();
    }
  }

  startSlideshow() {
    if (!this.ids.length) return;
    this.toggleSlidePop(false);
    if (this.isKiosk) this.toggleKiosk();
    this.stopSlideshow();
    this.root.querySelector('#v-slideshow').classList.add('on');
    this.root.querySelector('#v-slideshow').setAttribute('aria-pressed', 'true');
    this.root.querySelector('#v-slideshow').setAttribute('aria-label', i18n.t('Stop slideshow'));
    this.scheduleSlideshow();
    this.toast?.(i18n.t('Slideshow started.'));
  }

  scheduleSlideshow() {
    if (this.slideshow) clearInterval(this.slideshow);
    const tick = () => {
      if (document.hidden) return;
      const media = this.stage.querySelector('video, audio');
      if (media && !media.ended && !media.error) return;
      this.advanceSlideshow();
    };
    this.slideshow = setInterval(tick, this.slideshowDelay);
  }

  async advanceSlideshow() {
    if (!this.slideshow || this.slideAdvancing) return;
    if (this.index >= this.ids.length - 1 && !this.slideshowLoop) { this.stopSlideshow(); return; }
    this.slideAdvancing = true;
    try { await this.goTo((this.index + 1) % this.ids.length); }
    finally { this.slideAdvancing = false; }
  }

  stopSlideshow() {
    if (this.slideshow) clearInterval(this.slideshow);
    this.slideshow = null;
    this.root.querySelector('#v-slideshow').classList.remove('on');
    this.root.querySelector('#v-slideshow').setAttribute('aria-pressed', 'false');
    this.root.querySelector('#v-slideshow').setAttribute('aria-label', i18n.t('Start slideshow'));
  }

  /* -- keyboard -------------------------------------------------------- */

  handleKey(event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return false;
    const key = event.key.toLowerCase();
    if (this.isKiosk && key === 'escape') { this.toggleKiosk(); return true; }
    switch (key) {
      case 'escape': this.close(); return true;
      case 'arrowright': this.step(1); return true;
      case 'arrowleft': this.step(-1); return true;
      case 'home': this.goTo(0); return true;
      case 'end': this.goTo(this.ids.length - 1); return true;
      case 'f':
        if (!this.root.querySelector('#v-fav').hidden) this.toggleFavorite();
        return true;
      case 'i': this.toggleInfo(); return true;
      case 'r':
        if (!this.root.querySelector('#v-rotate').hidden) this.rotate();
        return true;
      case 'd':
        if (this.canDownload) this.root.querySelector('#v-download').click();
        return true;
      case '+': case '=': this.zoomBy(1.4); return true;
      case '-': this.zoomBy(1 / 1.4); return true;
      case '0': this.resetTransform(); return true;
      case 'k': this.toggleKiosk(); return true;
      case ' ': this.toggleSlideshow(); return true;
      default: return false;
    }
  }
}

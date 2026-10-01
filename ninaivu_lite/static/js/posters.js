/**
 * Preview pictures for videos, made by this browser.
 *
 * Without ffmpeg the server cannot open a video, but the browser that plays
 * it can. When a video with no picture comes on screen, the gallery loads the
 * first second of it in a hidden video element, draws one frame, and sends
 * that small picture to the server, which keeps it as the tile for everyone
 * from then on. One at a time, a few dozen per visit, only for signed-in
 * family and administrators (guests are never asked to work for the house),
 * and never twice for the same video. A video the browser cannot decode keeps
 * its plain tile, as before.
 *
 * The viewer feeds in too: a video somebody is watching is already decoded,
 * so its frame costs nothing.
 */

const LONGEST = 640;
const PER_VISIT = 60;
const SEEK_TO = 1;
const WAIT_MS = 20000;

export class PosterMaker {
  constructor({ onMade, enabled = false }) {
    this.onMade = onMade;
    this.enabled = enabled;
    this.queue = [];
    this.seen = new Set();
    this.busy = false;
    this.made = 0;
  }

  /** A video with no picture is on screen: make one, when its turn comes. */
  offer(id) {
    if (!this.enabled || this.seen.has(id) || this.made >= PER_VISIT) return;
    this.seen.add(id);
    this.queue.push(id);
    this.pump();
  }

  /** The viewer's own video element: a frame from it, once it has one. */
  fromElement(id, video) {
    if (!this.enabled || this.seen.has(id)) return;
    this.seen.add(id);
    const grab = () => {
      if (video.readyState < 2 || (video.currentTime < 0.4 && !video.ended)) return;
      video.removeEventListener('timeupdate', grab);
      this.send(id, frameOf(video)).catch(() => {});
    };
    video.addEventListener('timeupdate', grab);
  }

  async pump() {
    if (this.busy) return;
    this.busy = true;
    try {
      while (this.queue.length && this.made < PER_VISIT) {
        const id = this.queue.shift();
        try {
          const blob = await captureFrame(`/api/file/${id}`);
          await this.send(id, blob);
        } catch { /* a video this browser cannot open keeps its plain tile */ }
      }
    } finally {
      this.busy = false;
    }
  }

  async send(id, blob) {
    if (!blob) return;
    const response = await fetch(`/api/asset/${id}/poster`, {
      method: 'POST', headers: { 'Content-Type': blob.type }, body: blob, credentials: 'same-origin',
    });
    if (!response.ok) return;
    this.made += 1;
    const item = await response.json().catch(() => null);
    if (item) this.onMade?.(item);
  }
}

/** One frame of the video at `src`, about a second in, as a JPEG blob. */
function captureFrame(src) {
  return new Promise((resolve, reject) => {
    const video = document.createElement('video');
    video.muted = true;
    video.playsInline = true;
    video.preload = 'metadata';
    video.crossOrigin = 'use-credentials';
    const timer = setTimeout(() => finish(new Error('timed out')), WAIT_MS);
    const finish = (error, blob) => {
      clearTimeout(timer);
      video.removeAttribute('src');
      try { video.load(); } catch { /* releasing the decoder is best effort */ }
      if (error) reject(error); else resolve(blob);
    };
    video.onerror = () => finish(new Error('cannot decode'));
    video.onloadedmetadata = () => {
      const at = Math.min(SEEK_TO, Math.max(0, (video.duration || 0) / 3));
      video.onseeked = () => {
        frameOf(video).then((blob) => finish(null, blob), finish);
      };
      try { video.currentTime = at; } catch (error) { finish(error); }
    };
    video.src = src;
  });
}

/** The video's current frame, no longer than LONGEST on its long side. */
function frameOf(video) {
  return new Promise((resolve, reject) => {
    const w = video.videoWidth;
    const h = video.videoHeight;
    if (!w || !h) { reject(new Error('no frame')); return; }
    const scale = Math.min(1, LONGEST / Math.max(w, h));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(16, Math.round(w * scale));
    canvas.height = Math.max(16, Math.round(h * scale));
    try {
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('no picture'))), 'image/jpeg', 0.85);
    } catch (error) {
      reject(error);
    }
  });
}

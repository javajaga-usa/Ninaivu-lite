/** What the console's pages share: lookups, the server's answers, the state and the toast. */

import { reportUnauthorized } from '../api.js';
import * as i18n from '../i18n.js';

export const $ = (sel) => document.querySelector(sel);

/* Like $, but never null.
 *
 * admin.html is compiled once and cached by Flask, while this file is served
 * with no-cache and updates immediately, so between an edit and a restart the
 * browser runs new JavaScript against old markup. A bare
 * `$('#missing').onclick =` throws there and abandons the rest of the function
 * it is in — which is how adding a sign-out button silently killed the
 * storage-integrity button wired forty lines below it. Handlers bound to the
 * stub simply never fire.
 */
const DETACHED = document.createElement('div');
export const $$ = (sel) => document.querySelector(sel) || DETACHED;
export const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
};

export async function json(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      Accept: 'application/json',
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    // A 401 carrying `needs_password` is a request asking you to confirm
    // who you are before something irreversible — not a session that has
    // ended. See api.js: the same 401 means two different things, and
    // only one of them should raise the sign-in screen.
    if (response.status === 401 && !data?.needs_password) reportUnauthorized(url);
    throw Object.assign(new Error(i18n.t(data?.error || response.statusText)),
      { status: response.status, data });
  }
  return data;
}

export const adminApi = {
  overview: () => json('/api/admin/overview'),
  folders: () => json('/api/admin/folders'),
  preview: (query) => json(`/api/admin/preview?${new URLSearchParams(query)}`),
  settings: (body) => json('/api/admin/settings', { method: 'POST', body }),
  askAboutDrivesAgain: () => json('/api/admin/drives/ask-again', { method: 'POST' }),
  setFolderVisibility: (folder, visibility, confirm = false) =>
    json('/api/visibility/folder',
      { method: 'POST', body: { folder, visibility, confirm } }),
  undoVisibility: (batchId) =>
    json('/api/visibility/undo', { method: 'POST', body: { batch_id: batchId } }),
  visibilityHistory: () => json('/api/visibility/history'),
  browse: (path) => json(`/api/library/browse?path=${encodeURIComponent(path || '')}`),
  setRoot: (path) => json('/api/library/root', { method: 'POST', body: { path } }),
  rescan: (full) => json('/api/scan', { method: 'POST', body: { full } }),
  removeLibrary: (path, force) =>
    json(`/api/admin/libraries?path=${encodeURIComponent(path)}${force ? '&force=1' : ''}`,
      { method: 'DELETE' }),
  setActiveLibrary: (path) =>
    json('/api/admin/libraries/active', { method: 'POST', body: { path } }),
  moveLibrary: (path, to) =>
    json('/api/admin/libraries/move', { method: 'POST', body: { path, to } }),
  assignable: () => json('/api/admin/assignable'),
  backups: () => json('/api/admin/backups'),
  duplicates: (page) => json(`/api/admin/duplicates?page=${page}`),
};

export const state = {
  user: null, overview: null, people: [], roles: [], folders: [],
  libraries: [], assignable: [],
};

export function toast(message, isError = false) {
  // Nothing to say while the screen is locked: nobody is there to read it,
  // and requests refused behind the lock would pile their errors up here.
  if (document.body.classList.contains('screen-locked')) return;
  const node = el('div', `toast${isError ? ' error' : ''}`, message);
  $('#toasts').appendChild(node);
  setTimeout(() => {
    node.style.opacity = '0';
    setTimeout(() => node.remove(), 250);
  }, isError ? 5200 : 3200);
}

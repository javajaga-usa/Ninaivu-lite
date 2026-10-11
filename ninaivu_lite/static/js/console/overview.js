/** The Overview page: the cards, and the preview of what somebody else would see. */

import { thumbUrl } from '../api.js';
import * as i18n from '../i18n.js';
import { showTab } from '../admin.js';
import { $, adminApi, el, state } from './shared.js';

export let currentPreview = 'guest';

export function renderOverview() {
  const data = state.overview;
  const stats = data.stats || {};
  const cards = $('#overview-cards');
  cards.innerHTML = '';

  // Every card opens the page that changes what it counts. A number that
  // says "AI: off" and stops there tells you the problem and hides the fix.
  const card = (label, value, sub, tone, page) => {
    const node = el(page ? 'button' : 'div', `card${tone ? ` ${tone}` : ''}`);
    if (page) {
      node.type = 'button';
      node.onclick = () => showTab(page);
      // The page by the name the sidebar gives it, which is already in the
      // reader's language.
      const pageName = document.querySelector(`#tabs button[data-tab="${page}"] .tab-label`)?.textContent
        || page.replace('-', ' ');
      node.setAttribute('aria-label', i18n.t('{label}: {value}. Open {page}', { label, value, page: pageName }));
    }
    node.appendChild(el('div', 'card-label', label));
    node.appendChild(el('div', 'card-value', value));
    if (sub) node.appendChild(el('div', 'card-sub', sub));
    return node;
  };

  cards.append(
    card(i18n.t('In the library'), (stats.count || 0).toLocaleString(),
      `${counted(stats.pictures, PHOTOS)} · ${counted(stats.videos, VIDEOS)}`,
      '', 'library'),
    card(i18n.t('Public'), (stats.public || 0).toLocaleString(), i18n.t('visible to guests'),
      stats.public ? 'good' : '', 'visibility'),
    card(i18n.t('Hidden'), (stats.hidden || 0).toLocaleString(), i18n.t('admins only'),
      stats.hidden ? 'warn' : '', 'visibility'),
    card(i18n.t('Profiles'), String(data.people.total),
      Object.entries(data.people.by_role)
        .filter(([, n]) => n)
        .map(([role, n]) => (ROLE_COUNTS[role] ? i18n.t(ROLE_COUNTS[role], { count: n }) : `${n} ${role}`))
        .join(' · '), '', 'people'),
    // People, not sessions: one person on a phone, a tablet and two tabs is
    // one person signed in. `sessions` is the fallback for an older server.
    card(i18n.t('Signed in now'), String(data.people.signed_in ?? data.people.sessions),
      i18n.t('people with a live session'), '', 'people'),
  );
}

// How many of each role the Profiles card counts — "2 admin · 1 family",
// as the server names the roles.
const ROLE_COUNTS = {
  guest: i18n.key('{count} guest'),
  family: i18n.key('{count} family'),
  admin: i18n.key('{count} admin'),
};

// DESK7: "1 photo", not "1 photos"; and 12,345 with its separator.
const PHOTOS = [i18n.key('1 photo'), i18n.key('{count} photos')];
const VIDEOS = [i18n.key('1 video'), i18n.key('{count} videos')];
function counted(n, [one, many]) {
  const count = Number(n) || 0;
  return count === 1 ? i18n.t(one) : i18n.t(many, { count: count.toLocaleString(i18n.locale()) });
}

/* -- Preview ------------------------------------------------------------- */

function renderPreviewTabs(active) {
  const tabs = $('#preview-tabs');
  tabs.innerHTML = '';
  const options = [
    { key: 'guest', label: i18n.t('A guest') },
    { key: 'family', label: i18n.t('A family member') },
    ...state.people
      .filter((p) => p.role !== 'admin' && p.active)
      .map((p) => ({ key: `person:${p.id}`, label: p.name })),
  ];
  for (const option of options) {
    const button = el('button', `chip${option.key === active ? ' active' : ''}`,
      option.label);
    button.type = 'button';
    button.onclick = () => renderPreview(option.key);
    tabs.appendChild(button);
  }
}

export async function renderPreview(key) {
  currentPreview = key;
  renderPreviewTabs(key);
  const box = $('#preview');
  box.replaceChildren(el('p', 'hint', i18n.t('Checking…')));
  const query = key.startsWith('person:')
    ? { person: key.split(':')[1] }
    : { role: key };
  let data;
  try {
    data = await adminApi.preview(query);
  } catch (exc) {
    box.innerHTML = '';
    box.appendChild(el('p', 'hint', exc.message));
    return;
  }

  box.innerHTML = '';
  const summary = el('div', 'preview-summary');
  summary.appendChild(el('strong', null, i18n.items(data.total)));
  // A role's name (Guest, Family member) is the server's English: said in
  // the page's language. A person's name is theirs, as it is.
  const who = key.startsWith('person:') ? data.as : i18n.t(data.role_label || data.as);
  const detail = [i18n.t('as {who}', { who })];
  if (data.scope) detail.push(i18n.t('limited to {folder}', { folder: data.scope }));
  summary.appendChild(el('span', 'hint', detail.join(' · ')));
  box.appendChild(summary);

  if (!data.total) {
    // Before any folder is chosen there is nothing to publish yet.
    box.appendChild(el('p', 'empty-note', data.folders && !data.folders.length
      ? `${i18n.t('No library folder yet')}. ${i18n.t('Choose a folder and Ninaivu will index it in the background.')}`
      : i18n.t('Nothing at all. Publish a folder on the Visibility tab to let them see something.')));
    return;
  }

  const strip = el('div', 'preview-strip');
  for (const item of data.items.slice(0, 12)) {
    const tile = el('div', 'preview-tile');
    if (item.has_thumb) {
      const img = el('img');
      img.src = thumbUrl(item.id, 256, item.thumb_v);
      img.alt = item.name;
      img.loading = 'lazy';
      tile.appendChild(img);
    } else {
      tile.appendChild(el('span', 'preview-glyph', '♪'));
    }
    tile.title = `${item.name} — ${item.folder || i18n.t('root')}`;
    strip.appendChild(tile);
  }
  box.appendChild(strip);
}

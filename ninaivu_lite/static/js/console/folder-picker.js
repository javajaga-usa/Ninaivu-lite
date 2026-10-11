/** The one folder picker in the console. */

import * as i18n from '../i18n.js';
import { refresh } from '../admin.js';
import { watchActivity } from './scan.js';
import { $, adminApi, el, state, toast } from './shared.js';

let browsePath = '';
let browseSelectable = false;

/**
 * The one folder picker in the console.
 *
 * The Library tab uses it to add a library folder, and so does the first-day
 * walkthrough. `pick` lets a caller take over what happens on Use, and
 * `anyFolder` relaxes the library's selectability check for a caller that
 * does not need it.
 */
let picker = null;

/** A shortcut's name in the page's language (A259): the server said
 *  "Photos (current)", "Home" and "C: drive" in English on a Tamil page. A
 *  folder's own name is left as it is on the disk. */
export function openFolderPicker(options = {}) {
  picker = {
    start: options.start ?? (state.overview?.library?.root || ''),
    title: options.title || i18n.t('Choose a library folder'),
    cta: options.cta || i18n.t('Use this folder'),
    anyFolder: !!options.anyFolder,
    pick: options.pick || null,
  };
  $('#folder-modal').hidden = false;
  $('#fm-manual').value = '';
  $('#folder-modal h2').textContent = picker.title;
  $('#fm-apply').textContent = picker.cta;
  // Nothing is chosen until the listing comes or a path is typed: the last
  // picker's folder must not be what "Use this folder" hands back.
  browsePath = '';
  browseSelectable = false;
  $('#fm-path').textContent = '';
  refreshApply();
  return loadBrowse(picker.start);
}

/* The typed path and the browsed folder are two ways of answering the same
   question, so both have to feed the same decision. Without this the text box
   is inert: typing a perfectly good path leaves "Use this folder" greyed out
   whenever the tree happens to be sitting on a folder that cannot be picked. */
export function refreshApply() {
  const typed = $('#fm-manual').value.trim();
  const apply = $('#fm-apply');
  apply.disabled = !typed && !browseSelectable;
  apply.title = apply.disabled ? i18n.t('Pick a folder inside this one, or type a path') : '';
}

async function loadBrowse(path) {
  // Choosing from the tree is an answer, so it replaces whatever was typed
  // earlier — otherwise `typed || browsePath` submits the old string and the
  // folder the admin just clicked is silently ignored. Cleared now, as the
  // folder is chosen, not when its listing arrives (A257): a path typed
  // while a slow drive was being listed was wiped, and "Use this folder"
  // then took the folder the picker had opened at (often the home folder).
  $('#fm-manual').value = '';
  const list = $('#fm-dirs');
  const loading = el('p', 'hint', i18n.t('Loading…'));
  loading.style.padding = '12px';
  list.replaceChildren(loading);
  let data;
  try {
    data = await adminApi.browse(path);
  } catch (exc) {
    list.innerHTML = '';
    list.appendChild(el('p', 'gate-error', exc.message));
    return;
  }
  browsePath = data.path;
  browseSelectable = picker?.anyFolder ? true : data.selectable !== false;
  $('#fm-path').textContent = data.path;

  refreshApply();

  list.innerHTML = '';

  const folderIcon = '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/></svg>';
  const homeIcon = '<svg viewBox="0 0 24 24"><path d="M4 11 12 4l8 7v8a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1Z"/></svg>';

  // Shortcuts first: home, Pictures, and every drive on the machine, so
  // C:\Master is two clicks away instead of a climb from wherever we started.
  if (data.shortcuts?.length) {
    const bar = el('div', 'fm-shortcuts');
    for (const shortcut of data.shortcuts) {
      const chip = el('button', 'chip', shortcut.key
        ? i18n.t(shortcut.key, shortcut.vars) : (shortcut.name || shortcut.path));
      chip.type = 'button';
      chip.title = shortcut.path;
      chip.onclick = () => loadBrowse(shortcut.path);
      bar.appendChild(chip);
    }
    list.appendChild(bar);
  }

  if (data.parent) {
    const up = el('button');
    up.type = 'button';
    up.innerHTML = homeIcon;
    up.appendChild(el('span', null, i18n.t('.. (up one level)')));
    up.onclick = () => loadBrowse(data.parent);
    list.appendChild(up);
  }
  for (const dir of data.dirs) {
    const button = el('button');
    button.type = 'button';
    button.innerHTML = folderIcon;
    button.appendChild(el('span', null, dir.name));
    button.onclick = () => loadBrowse(dir.path);
    list.appendChild(button);
  }
  if (!data.dirs.length) {
    list.appendChild(el('p', 'hint',
      browseSelectable
        ? i18n.t('No subfolders here — use this folder, or go back up.')
        : i18n.t('No subfolders here.')));
  }
}

export async function applyRoot() {
  const typed = $('#fm-manual').value.trim();
  const path = typed || browsePath;
  if (!path) return;

  // A caller that supplied its own handler just wants the path back.
  if (picker?.pick) {
    const handler = picker.pick;
    $('#folder-modal').hidden = true;
    handler(path);
    return;
  }

  const button = $('#fm-apply');
  const label = button.textContent;
  button.disabled = true;
  button.textContent = i18n.t('Opening…');
  try {
    await adminApi.setRoot(path);
    $('#folder-modal').hidden = true;
    toast(i18n.t('Indexing the library…'));
    watchActivity();
    setTimeout(refresh, 1500);
  } catch (exc) {
    toast(exc.message, true);
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

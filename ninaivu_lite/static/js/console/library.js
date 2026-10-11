/** The Library settings and Settings pages: the library folders, the switches, the kept copies. */

import * as i18n from '../i18n.js';
import { refresh } from '../admin.js';
import { openFolderPicker } from './folder-picker.js';
import { $, $$, adminApi, el, state, toast } from './shared.js';

/* The copies Ninaivu Lite keeps by itself: how many, the newest, and a
   daily copy that failed, which used to go only to the log. */
async function renderBackups() {
  const kept = $('#backup-kept');
  const failure = $('#backup-failure');
  if (!kept || !failure) return;
  let data;
  try { data = await adminApi.backups(); } catch { return; }
  const list = data.backups || [];
  const newest = list.length ? new Date(list[0].at * 1000).toLocaleString(i18n.locale()) : '';
  if (list.length > 1) {
    kept.textContent = i18n.t('{count} copies are kept in {folder}. The newest is from {when}.', {
      count: list.length, folder: data.folder, when: newest,
    });
  } else if (list.length === 1) {
    kept.textContent = i18n.t('One copy is kept in {folder}, from {when}.', {
      folder: data.folder, when: newest,
    });
  } else {
    kept.textContent = i18n.t('No copy has been kept yet: one is made each day while Ninaivu Lite runs.');
  }
  failure.hidden = !data.failure;
  if (data.failure) {
    failure.textContent = i18n.t('The last daily copy failed ({when}): {why}', {
      when: new Date(data.failure.at * 1000).toLocaleString(i18n.locale()), why: data.failure.why,
    });
  }
}

export function renderLibraryFolders() {
  const list = $('#library-list');
  if (!list) return;
  const folders = state.overview?.library?.folders || [];
  list.innerHTML = '';

  if (!folders.length) {
    list.appendChild(el('p', 'hint', i18n.t('No folders yet — add one to get started.')));
    return;
  }

  for (const folder of folders) {
    const row = el('div', `library-item${folder.exists ? '' : ' missing'}`);

    const main = el('div', 'li-main');
    const title = el('div', 'li-name');
    title.appendChild(el('strong', null, folder.name));
    if (folder.active) title.appendChild(el('span', 'tag family', i18n.t('default')));
    if (!folder.exists) title.appendChild(el('span', 'tag hidden', i18n.t('missing')));
    main.appendChild(title);
    main.appendChild(el('code', 'li-path', folder.path));
    const bits = [i18n.items(folder.count)];
    if (folder.assigned) {
      bits.push(folder.assigned === 1 ? i18n.t('1 person assigned')
        : i18n.t('{count} people assigned', { count: folder.assigned }));
    }
    main.appendChild(el('div', 'li-meta', bits.join(' · ')));
    row.appendChild(main);

    const actions = el('div', 'li-actions');
    if (!folder.active) {
      const makeDefault = el('button', 'btn small ghost', i18n.t('Make default'));
      makeDefault.type = 'button';
      makeDefault.title = i18n.t('The folder the Visibility tab works on');
      makeDefault.onclick = async () => {
        try {
          await adminApi.setActiveLibrary(folder.path);
          await refresh();
        } catch (exc) { toast(exc.message, true); }
      };
      actions.appendChild(makeDefault);
    }
    if (!folder.exists) {
      // Photos on another drive letter, or a new computer after a restore:
      // point the folder there, keeping everything decided about them.
      const moved = el('button', 'btn small', i18n.t('Moved?'));
      moved.type = 'button';
      moved.title = i18n.t('The photographs are somewhere else now: choose where');
      moved.onclick = () => openFolderPicker({
        title: i18n.t('Where is {name} now?', { name: folder.name }),
        cta: i18n.t('It is here'),
        start: '',
        pick: async (to) => {
          try {
            await adminApi.moveLibrary(folder.path, to);
            toast(i18n.t('{name} now points to {path}. Everything set for its photos is kept.',
              { name: folder.name, path: to }));
            await refresh();
          } catch (exc) { toast(exc.message, true); }
        },
      });
      actions.appendChild(moved);
    }
    const remove = el('button', 'btn small ghost', i18n.t('Remove'));
    remove.type = 'button';
    remove.title = i18n.t('Stop indexing this folder. Your files are not touched.');
    remove.onclick = () => removeLibrary(folder);
    actions.appendChild(remove);
    row.appendChild(actions);

    list.appendChild(row);
  }
}

async function removeLibrary(folder) {
  const ask = folder.exists
    ? i18n.t('Take {name} out of the library? Its photos leave the gallery. Your files are not touched, and who sees what, favourites, albums and share links come back if you add the same folder again.', { name: folder.name })
    : i18n.t('{name} cannot be found. If its photos are somewhere else now, use "Moved?" instead. Take it out of the library anyway?', { name: folder.name });
  if (!confirm(ask)) return;
  try {
    await adminApi.removeLibrary(folder.path, false);
    toast(i18n.t('{name} is no longer indexed. Your files were not touched.', { name: folder.name }));
    await refresh();
  } catch (exc) {
    if (exc.status === 409) {
      // Someone is assigned to it — say who, and let the admin decide.
      if (!confirm(`${exc.message}\n\n${i18n.t('Remove it anyway? They will see nothing until you assign them another folder.')}`)) return;
      try {
        await adminApi.removeLibrary(folder.path, true);
        toast(i18n.t('{name} removed. The people assigned to it see nothing until you reassign them.', { name: folder.name }));
        await refresh();
      } catch (inner) { toast(inner.message, true); }
      return;
    }
    toast(exc.message, true);
  }
}

export function renderLibrary() {
  const data = state.overview;
  renderLibraryFolders();
  renderBackups();
  // The family app is at "/" on this same address.
  const box = $('#overview-library');
  box.innerHTML = '';
  const folderCount = (data.library.folders || []).length;
  // The first of each row says which it is, the second is what is shown.
  const rows = [
    ['folders', i18n.t('Library folders'), folderCount
      ? (folderCount === 1 ? i18n.t('1 folder') : i18n.t('{count} folders', { count: folderCount }))
      : i18n.t('None yet')],
    ['app', i18n.t('Family app'), data.app.home_url || '/'],
    ['guests', i18n.t('Guests without a login'), data.app.open_browsing ? i18n.t('Allowed') : i18n.t('Blocked')],
    ['watch', i18n.t('Watching for changes'), data.app.watch ? i18n.t('Yes') : i18n.t('No')],
  ];
  const list = el('dl', 'kv');
  for (const [which, key, value] of rows) {
    list.appendChild(el('dt', null, key));
    const dd = el('dd');
    if (which === 'app' && value && /^(https?:\/\/|\/)/.test(value)) {
      // The family app is on this same address now, at "/".
      const a = el('a', null, value.startsWith('/') ? `${location.origin}${value}` : value);
      a.href = value;
      dd.appendChild(a);
    } else {
      dd.textContent = value;
    }
    // The default folder's path on a line of its own, in the same type the
    // Settings page shows paths in: run on after the count it broke mid-word
    // wherever the column happened to end.
    if (which === 'folders' && folderCount && data.library.root) {
      dd.appendChild(el('code', 'kv-path', data.library.root));
    }
    list.appendChild(dd);
  }
  box.appendChild(list);

  // Settings. Each switch is rendered on the page of the thing it governs:
  // indexing on Library settings, and the home's name, its language and who
  // may look without signing in here on Settings.
  const settings = $$('#settings');
  const indexing = $('#library-switches') || settings;
  for (const holder of new Set([settings, indexing])) holder.innerHTML = '';

  // Which way up: the camera's tag always; faces only with OpenCV installed.
  const upright = el('p', 'hint setting-note');
  upright.textContent = data.capabilities?.opencv
    ? i18n.t('Sideways photographs are turned upright during the scan: by the camera\'s own tag, and for photographs without one, by the faces in them. Nothing is asked and no file is changed; Rotate in the viewer corrects any that are wrong.')
    : i18n.t('Sideways photographs are turned upright by the camera\'s own tag. To judge photographs without one by the faces in them, install OpenCV (requirements-straighten.txt) and restart. Rotate in the viewer corrects any by hand.');
  indexing.appendChild(upright);

  // What the household is called. This is the default everyone sees; family
  // members may keep their own name for it instead, which only they see.
  const nameBlock = el('div', 'setting-field');
  nameBlock.appendChild(el('label', null, i18n.t('Name for this home')));
  const nameRow = el('div', 'row');
  const nameInput = el('input', 'input');
  nameInput.value = data.app.house_name || '';
  nameInput.maxLength = 40;
  nameInput.placeholder = i18n.t('Ninaivu');
  nameInput.setAttribute('aria-label', i18n.t('Name for this home'));
  const nameSave = el('button', 'btn', i18n.t('Save'));
  nameSave.type = 'button';
  const saveHouseName = async () => {
    nameSave.disabled = true;
    try {
      const result = await adminApi.settings({ house_name: nameInput.value });
      state.overview.app.house_name = result.settings.house_name;
      nameInput.value = result.settings.house_name || '';
      toast(i18n.t('Everyone now sees “{name}”.', { name: result.settings.house_name_effective }));
    } catch (exc) {
      toast(exc.message, true);
    } finally {
      nameSave.disabled = false;
    }
  };
  nameSave.onclick = saveHouseName;
  nameInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveHouseName(); });
  nameRow.append(nameInput, nameSave);
  const versionLine = $('#app-version');
  if (versionLine) {
    versionLine.textContent = [
      data.app.version ? i18n.t('Ninaivu Lite {version}', { version: data.app.version }) : '',
      data.app.copyright || '',
      data.app.licence ? i18n.t('Licence: {licence}', { licence: data.app.licence }) : '',
    ].filter(Boolean).join(' · ');
  }
  nameBlock.appendChild(nameRow);
  nameBlock.appendChild(el('p', 'hint',
    i18n.t('Shown at the top of the family app and on the home screen icon. Family members can keep their own name for it instead — that one is private to them.')));
  settings.appendChild(nameBlock);

  // The language the family app and this console open in for somebody who
  // has not chosen one. Each person's own choice, on their profile, still
  // wins. The languages are named in themselves, as on every switch.
  const languageBlock = el('div', 'setting-field');
  languageBlock.appendChild(el('label', null, i18n.t('Default language')));
  const languageRow = el('div', 'row');
  const languagePick = el('select', 'input');
  languagePick.setAttribute('aria-label', i18n.t('Default language'));
  const defaultLanguage = data.app.default_language || 'en';
  for (const { code, name } of i18n.LANGUAGES) {
    const option = el('option', null, name);
    option.value = code;
    option.lang = code;
    option.selected = code === defaultLanguage;
    languagePick.appendChild(option);
  }
  languagePick.onchange = async () => {
    const chosen = languagePick.value;
    try {
      const result = await adminApi.settings({ language: chosen });
      const saved = result?.settings?.default_language ?? result?.settings?.language ?? chosen;
      state.overview.app.default_language = saved;
      toast(i18n.t('Saved.'));
    } catch (exc) {
      toast(exc.message, true);
      languagePick.value = state.overview.app.default_language || 'en';
    }
  };
  languageRow.appendChild(languagePick);
  languageBlock.appendChild(languageRow);
  languageBlock.appendChild(el('p', 'hint',
    i18n.t('What the family app and this console open in for anyone who has not picked a language of their own.')));
  settings.appendChild(languageBlock);

  const toggles = [
    [settings, 'open_browsing', i18n.t('Let visitors browse public media without signing in'),
      data.app.open_browsing],
    [indexing, 'watch', i18n.t('Watch the folder and index new files automatically'), data.app.watch],
    [settings, 'video_originals',
      i18n.t('Send videos to guests and share links as they are when their location cannot be removed'),
      data.app.video_originals],
  ];
  // Visitors kept out still leaves a Family profile without a PIN one tap
  // away for anyone on the home network, originals and locations included.
  const openFamily = el('p', 'hint warn');
  const showOpenFamily = () => {
    const names = state.overview.people?.open_family || [];
    openFamily.hidden = state.overview.app.open_browsing || !names.length;
    openFamily.textContent = i18n.t('Visitors must sign in, but anyone on your home network can still tap into these Family profiles, which have no PIN: {names}. Give them a PIN on the People page to keep them private.',
      { names: names.join(', ') });
  };
  for (const [where, key, label, value] of toggles) {
    const row = el('label', 'toggle');
    const input = el('input');
    input.type = 'checkbox';
    input.checked = Boolean(value);
    input.dataset.setting = key;
    input.onchange = async () => {
      try {
        const result = await adminApi.settings({ [key]: input.checked });
        toast(i18n.t('Saved.'));
        // What the server made of it, not what was sent.
        const saved = result?.settings?.[key];
        state.overview.app[key] = saved === undefined ? input.checked : saved;
        if (key === 'open_browsing') showOpenFamily();
      } catch (exc) {
        toast(exc.message, true);
        input.checked = !input.checked;
      }
    };
    row.append(input, el('span', null, label));
    where.appendChild(row);
    if (key === 'open_browsing') {
      showOpenFamily();
      where.appendChild(openFamily);
    }
  }

  // Drives and phones whose notice was answered with Don't ask again.
  const quiet = data.app.quiet_drives || 0;
  if (quiet) {
    const drivesBlock = el('div', 'setting-field');
    drivesBlock.appendChild(el('label', null, i18n.t('Plugged-in drives')));
    const drivesRow = el('div', 'row');
    drivesRow.appendChild(el('p', 'hint', i18n.t('Drives and phones set to Don’t ask again: {count}. Ask again shows their notice the next time they are plugged in.',
      { count: quiet.toLocaleString() })));
    const again = el('button', 'btn small', i18n.t('Ask again'));
    again.type = 'button';
    again.onclick = async () => {
      try {
        await adminApi.askAboutDrivesAgain();
      } catch (exc) {
        toast(exc.message, true);
        return;
      }
      state.overview.app.quiet_drives = 0;
      drivesBlock.remove();
      toast(i18n.t('Every drive and phone will be mentioned again when it is plugged in.'));
    };
    drivesRow.appendChild(again);
    drivesBlock.appendChild(drivesRow);
    settings.appendChild(drivesBlock);
  }
}

// Screenshots for the Ninaivu Lite user guide.
//
//   LANG=en BASE=http://127.0.0.1:8811 OUT=shots/en node shots.js [stage…]
//
// Needs a FRESH Ninaivu Lite (empty --data folder, no photo folder given) for
// stage "first", and the sample library at /home/family/Photos (make_library.py).
// Stages run in order; each later stage signs in again from saved state.
const { chromium, devices } = require('/opt/node22/lib/node_modules/playwright');
const fs = require('fs');
const path = require('path');

const LANG = process.env.LANG_UI || process.env.LANG_GUIDE || 'en';
const BASE = process.env.BASE || 'http://127.0.0.1:8811';
const OUT = path.resolve(process.env.OUT || `shots/${LANG}`);
const STATE = path.join(OUT, 'admin-state.json');
const LIB = '/home/family/Photos';
const OLD = '/home/family/Old backup 2011';
const GONE = '/home/family/Old laptop photos';
const TA = LANG === 'ta';
const ADMIN = { name: TA ? 'மீனா' : 'Meena', username: 'meena', password: 'kolam-rain-2026' };
const FAMILY = TA
  ? [['ராஜன்', '582914'], ['பிரியா', '730561'], ['அர்ஜுன்', ''], ['பாட்டி', '']]
  : [['Rajan', '582914'], ['Priya', '730561'], ['Arjun', ''], ['Paati', '']];
const GUEST = TA ? 'கவிதா' : 'Kavitha';
const VIEW = { width: 1280, height: 800 };
const SCALE = 1.5;
fs.mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let browser;

async function newPage(opts = {}) {
  const ctx = await browser.newContext({
    viewport: opts.viewport || VIEW, deviceScaleFactor: opts.scale || SCALE,
    storageState: opts.state === false ? undefined : (fs.existsSync(STATE) ? STATE : undefined),
    locale: TA ? 'ta-IN' : 'en-GB', colorScheme: 'light', reducedMotion: 'reduce',
    ...(opts.device || {}),
  });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => console.log('  pageerror:', e.message));
  return page;
}

async function shot(page, name, opts = {}) {
  await sleep(opts.wait ?? 500);
  const file = path.join(OUT, `${name}.png`);
  let clip = opts.clip;
  if (opts.tall) {
    const h = await page.evaluate(() => document.documentElement.scrollHeight);
    const vp = page.viewportSize();
    await page.setViewportSize({ width: vp.width, height: Math.min(h, opts.tall) });
    await page.evaluate(() => window.scrollTo(0, 0));
    await sleep(600);
  }
  if (opts.el) {
    await page.locator(opts.el).first().scrollIntoViewIfNeeded();
    await sleep(300);
    const b = await page.locator(opts.el).first().boundingBox();
    const pad = opts.pad ?? 20;
    const vp = page.viewportSize();
    const x = Math.max(0, b.x - pad), y = Math.max(0, b.y - pad);
    clip = { x, y, width: Math.min(vp.width - x, b.width + 2 * pad), height: Math.min((opts.full ? 1e5 : vp.height) - y, b.height + 2 * pad) };
  }
  await page.screenshot({ path: file, clip, fullPage: !!opts.full });
  console.log('  shot', name);
}

/** The gallery or console in the guide's language, whatever the profile says. */
async function useLanguage(page) {
  await page.evaluate(async (lang) => {
    try { localStorage.setItem('ninaivu.lang', lang); } catch {}
    try { await fetch('/api/me', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ language: lang }) }); } catch {}
  }, LANG);
}

async function api(page, url, method = 'GET', body) {
  return page.evaluate(async ({ url, method, body }) => {
    const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    let data = null; try { data = await r.json(); } catch {}
    return { status: r.status, data };
  }, { url, method, body });
}

/** Walk the console's folder picker to the folder that holds `target`. */
async function browseTo(page, target) {
  await page.waitForSelector('#fm-path:not(:empty)');
  for (let i = 0; i < 8; i++) {
    const up = page.locator('#fm-dirs > button').first();
    if (!(await page.locator('#fm-dirs > button:has(span:text-is(".. (up one level)")), #fm-dirs > button:has(span:text-is(".. (ஒரு நிலை மேலே)"))').count())) break;
    await page.locator('#fm-dirs > button').first().click();
    await sleep(300);
  }
  const parts = path.dirname(target).split('/').filter(Boolean);
  for (const part of parts) {
    await page.locator('#fm-dirs > button', { has: page.locator(`span:text-is("${part}")`) }).click();
    await sleep(300);
  }
}

const stages = {};

/* ---- 2 and 3: the first visit and the first-day walk-through ------------- */
stages.first = async () => {
  const page = await newPage({ state: false });
  await page.goto(BASE + '/');
  await page.waitForSelector('.gate-card');
  if (TA) { await page.click('.gate-lang[lang="ta"]'); await sleep(500); }
  await page.fill('input[name=name]', ADMIN.name);
  await page.fill('input[name=username]', ADMIN.username);
  await page.fill('input[name=password]', ADMIN.password);
  await shot(page, 'f02-setup', { el: '.gate-card', pad: 40 });
  await page.click('.gate-submit');
  await sleep(2500);
  await shot(page, 'f03-empty');
  await page.context().storageState({ path: STATE });

  // the console, with the walk-through
  await page.goto(BASE + '/admin');
  await page.waitForSelector('#first-day:not([hidden])', { timeout: 15000 });
  await sleep(800);
  await shot(page, 'f04a-step1', { el: '#first-day .modal-card', pad: 16 });
  await page.click('#fd-body .btn.primary');
  await page.waitForSelector('#folder-modal:not([hidden])');
  await browseTo(page, LIB);
  await shot(page, 'f04b-picker', { el: '#folder-modal .modal-card', pad: 16 });
  await page.click('#fm-dirs button:has-text("Photos")');
  await sleep(600);
  await page.click('#fm-apply');
  await sleep(2500);
  await shot(page, 'f05-chosen', { el: '#first-day .modal-card', pad: 16 });
  await page.click('#fd-next');
  await sleep(800);
  await page.fill('#fd-body form.row input', OLD);
  await page.press('#fd-body form.row input', 'Enter');
  await sleep(500);
  await shot(page, 'f06-import', { el: '#first-day .modal-card', pad: 16 });
  // the import itself runs from the Import page later; skip ahead
  await page.click('#fd-next');
  await sleep(800);
  for (const [name, pin] of FAMILY.slice(0, 2)) {
    await page.fill('#fd-body form.row input >> nth=0', name);
    await page.fill('#fd-body form.row input >> nth=1', pin);
    await page.click('#fd-body form.row button[type=submit]');
    await sleep(700);
  }
  await page.fill('#fd-body form.row input >> nth=0', FAMILY[2][0]);
  await page.fill('#fd-body form.row input >> nth=1', '1234');
  await page.click('#fd-body form.row button[type=submit]');
  await sleep(800);
  await shot(page, 'f07-people', { el: '#first-day .modal-card', pad: 16 });
  await page.fill('#fd-body form.row input >> nth=1', '');
  await page.click('#fd-body form.row button[type=submit]');
  await sleep(700);
  await page.click('#fd-next');
  await sleep(800);
  await shot(page, 'f08-tour', { el: '#first-day .modal-card', pad: 16 });
  await page.click('#fd-next');
  await sleep(1500);
  await page.close();
};

/* ---- 4: the console ------------------------------------------------------ */
async function consolePage(tab, viewport) {
  const page = await newPage({ viewport });
  await page.goto(BASE + '/admin');
  await page.waitForSelector('#tabs');
  await sleep(1200);
  if (tab) { await page.click(`#tabs [data-tab="${tab}"]`); await sleep(1200); }
  return page;
}

stages.people = async () => {
  const page = await consolePage(null);
  // the rest of the household, as an administrator would add them on People
  const r1 = await api(page, '/api/people', 'POST', { name: FAMILY[3][0], username: 'paati', role: 'family' });
  const r2 = await api(page, '/api/people', 'POST', { name: GUEST, username: 'kavitha', role: 'guest', password: 'guest-visit-77' });
  console.log('  people', r1.status, r2.status);
  await api(page, '/api/admin/settings', 'POST', { house_name: TA ? 'ராஜன் குடும்பம்' : 'The Rajan family' });
  await page.close();
};

stages.console = async () => {
  // signing in
  let page = await newPage({ state: false });
  await page.goto(BASE + '/admin');
  await page.waitForSelector('.gate-card');
  await page.fill('input[name=username]', ADMIN.username);
  await page.fill('input[name=password]', 'kolam-rain');
  await shot(page, 'c09-login', { el: '.gate-card', pad: 40 });
  await page.close();

  page = await consolePage(null, { width: 1280, height: 1000 });
  await shot(page, 'c10-overview', { wait: 1500 });
  await page.close();

  // A second library folder that is not where it was (a drive letter changed,
  // a new computer): Library settings then shows "Moved?" beside it. Real:
  // the folder is added, then taken off the disk; it is removed again after.
  page = await consolePage(null);
  fs.mkdirSync(GONE, { recursive: true });
  console.log('  add gone', (await api(page, '/api/library/root', 'POST', { path: GONE })).status);
  await api(page, '/api/admin/libraries/active', 'POST', { path: LIB });
  fs.rmSync(GONE, { recursive: true, force: true });
  await page.close();
  page = await consolePage('library', { width: 1280, height: 900 });
  await shot(page, 'c11-library', { wait: 1000, tall: 1300 });
  console.log('  remove gone', (await api(page, '/api/admin/libraries?path=' + encodeURIComponent(GONE), 'DELETE')).status);
  // Settings lists the copies kept: wait for the first daily one (two
  // minutes after the start) beside the one taken before the removal.
  for (let i = 0; i < 200; i++) {
    const r = await api(page, '/api/admin/backups');
    if (r.data && (r.data.backups || []).some((b) => !b.before)) break;
    await sleep(1000);
  }
  await page.close();

  page = await consolePage('people', { width: 1280, height: 900 });
  await shot(page, 'c13-people', { wait: 1000 });
  await page.click('#add-person-btn');
  await sleep(700);
  await shot(page, 'c14-add', { el: '#add-person-block', pad: 12 });
  await page.close();

  page = await consolePage('settings', { width: 1280, height: 900 });
  await shot(page, 'c17-settings', { tall: 2000, wait: 1000 });
  await page.close();
};

stages.import = async () => {
  const page = await consolePage('archive', { width: 1280, height: 900 });
  await page.fill('#ar-source-input', OLD);
  await page.click('#ar-add-source');
  await sleep(800);
  if (!(await page.inputValue('#ar-dest-input'))) await page.fill('#ar-dest-input', LIB + '/Ninaivu Archive');
  await page.click('#ar-start');
  for (let i = 0; i < 60; i++) {
    await sleep(1000);
    if (await page.locator('#ar-handoff:not([hidden])').count()) break;
  }
  await sleep(1500);
  await shot(page, 'c12-import', { tall: 2600 });
  await page.close();
};

function folderButton(page, name, vis) {
  return page.locator('#folder-tree .folder-row', { has: page.locator(`.folder-name > span:text-is("${name}")`) })
    .locator(`.vis-picker [data-vis="${vis}"]`);
}

stages.visibility = async () => {
  let page = await consolePage('visibility', { width: 1280, height: 900 });
  for (const [folder, v] of [['Trips', 'public'], ['Temples', 'public'], ['Family/2012', 'hidden'], ['Birthdays', 'hidden']]) {
    const r = await api(page, '/api/visibility/folder', 'POST', { folder, visibility: v, confirm: true });
    console.log('  vis', folder, r.status, r.data && r.data.error);
  }
  await page.close();
  page = await consolePage('visibility', { width: 1280, height: 900 });
  await page.click('#folder-tree .folder-row:has(.folder-name > span:text-is("Trips")) .folder-toggle');
  await sleep(600);
  await shot(page, 'c15-visibility', { tall: 2000 });
  await folderButton(page, 'Birthdays', 'public').click();
  await page.waitForSelector('#expose-modal:not([hidden])');
  await shot(page, 'c16a-confirm', { el: '#expose-modal .modal-card', pad: 16 });
  await page.click('#expose-confirm');
  await sleep(1500);
  await shot(page, 'c16b-undo', { wait: 300 });
  await page.click('#vis-undo-btn');
  await sleep(1200);
  await api(page, '/api/visibility/folder', 'POST', { folder: 'Birthdays', visibility: 'family', confirm: true });
  await page.close();
};

/* ---- 5: a drive or a phone plugged in: the notice at the top of the console
   (the drive list is played back) ---- */
async function mockDrives(page, drive, exportState) {
  await page.route('**/api/admin/drives', (route) => route.fulfill({ json: {
    drives: [{ ...drive, holds_library: false, pending: true }], export: exportState || {} } }));
  await page.route('**/api/admin/drives/answer', (route) => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/admin/drives/export', (route) => route.fulfill({ json: route.request().method() === 'POST'
    ? { ok: true, destination: 'E:\\Ninaivu Lite' }
    : { running: true, phase: 'copying', drive: drive.label, destination: 'E:\\Ninaivu Lite', total: 133, done: 52,
      copied: 52, skipped: 0, errors: 0, bytes_total: 9400000, bytes_done: 3700000, message: null } }));
}

stages.drives = async () => {
  const GB = 1024 ** 3;
  const stick = { id: 'demo-stick', path: 'E:\\', label: 'SANDISK', total: 64 * GB, free: 41.5 * GB, kind: 'drive', shell: false };
  let page = await newPage({ viewport: { width: 1280, height: 800 } });
  await mockDrives(page, stick);
  await page.goto(BASE + '/admin');
  await page.waitForSelector('.drive-notice:not([hidden])', { timeout: 15000 });
  await shot(page, 'd18-drive', { el: '.drive-notice', pad: 16 });
  await page.click('.drive-notice-actions .btn:not(.ghost)');
  await sleep(2500);
  await shot(page, 'd19a-export', { el: '.drive-notice', pad: 16 });
  await page.close();
  const phone = { id: 'demo-phone', path: '', label: 'Pixel 7', total: 0, free: 0, kind: 'phone', shell: true };
  page = await newPage({ viewport: { width: 1280, height: 800 } });
  await mockDrives(page, phone);
  await page.goto(BASE + '/admin');
  await page.waitForSelector('.drive-notice:not([hidden])', { timeout: 15000 });
  await shot(page, 'd19b-phone', { el: '.drive-notice', pad: 16 });
  await page.close();
};

/* ---- 6-10: the family gallery on a computer ----------------------------- */
async function galleryPage(viewport) {
  const page = await newPage({ viewport });
  await page.goto(BASE + '/');
  await page.waitForSelector('#grid .cell img.ready', { timeout: 20000 });
  await sleep(1500);
  return page;
}

stages.gallery = async () => {
  let page = await galleryPage();
  await shot(page, 'g20-timeline', { wait: 1500 });
  // search for a place
  await page.click('#search');
  await page.type('#search', 'Kanya', { delay: 60 });
  await sleep(900);
  await shot(page, 'g21a-search');
  await page.press('#search', 'Enter');
  await sleep(1500);
  await page.close();

  page = await galleryPage();
  await page.locator('#folder-list').scrollIntoViewIfNeeded();
  await page.click('#folder-list >> text=Festivals');
  await sleep(1500);
  await shot(page, 'g21b-folder');
  await page.close();

  // selecting a day and making an album
  page = await galleryPage();
  await page.click('#folder-list >> text=Trips');
  await sleep(1500);
  await page.locator('.section-head [data-select-section]').first().click();
  await sleep(800);
  await shot(page, 'g22-selected');
  await page.click('#sel-album');
  await page.waitForSelector('#album-modal:not([hidden])');
  await page.fill('#album-new-name', TA ? 'கன்னியாகுமரி பயணம்' : 'Kanyakumari trip');
  await shot(page, 'g23a-album-name', { el: '#album-modal .modal-card', pad: 16 });
  await page.click('#album-create-btn');
  await sleep(1500);
  if (await page.locator('#album-modal:not([hidden])').count()) await page.click('#album-modal-close');
  await sleep(500);
  await page.keyboard.press('Escape');
  await sleep(500);
  await page.locator('#album-list > *').first().click();
  await sleep(1500);
  await shot(page, 'g23b-album');
  await page.close();
};

async function openPhoto(page, folder, index = 0) {
  await page.click(`#folder-list >> text=${folder}`);
  await sleep(1500);
  await page.locator('#grid .cell:not([data-kind="video"])').nth(index).click();
  await page.waitForSelector('#viewer:not([hidden])');
  await sleep(1800);
}

stages.viewer = async () => {
  let page = await galleryPage();
  await openPhoto(page, 'Kanyakumari', 0);
  await page.click('#v-fav');
  await sleep(600);
  await page.mouse.move(640, 400);
  await shot(page, 'v25-viewer');
  await page.click('#v-info');
  await sleep(1000);
  await shot(page, 'v26-info');
  // a few more favourites, for Favourites
  await page.click('#v-info-close');
  for (let i = 0; i < 4; i++) { await page.keyboard.press('ArrowRight'); await sleep(700); if (i % 2 === 0) await page.click('#v-fav'); }
  await page.close();

  page = await galleryPage();
  await page.click('#sidebar >> text=' + (TA ? 'பிடித்தவை' : 'Favourites'));
  await sleep(1500);
  await shot(page, 'g24-favourites');
  await page.close();
};

stages.share = async () => {
  const page = await galleryPage();
  await openPhoto(page, 'Kanyakumari', 0);
  await page.click('#v-share');
  await page.waitForSelector('#share-modal:not([hidden])');
  await page.selectOption('#share-expiry', { index: 1 });
  await page.fill('#share-password', 'sea-view');
  await shot(page, 's27a-share', { el: '#share-modal .modal-card', pad: 16 });
  await page.click('#share-create-btn');
  await page.waitForSelector('#share-link-result:not([hidden])');
  await sleep(600);
  await shot(page, 's27b-link', { el: '#share-modal .modal-card', pad: 16 });
  const url = await page.inputValue('#share-url-text');
  fs.writeFileSync(path.join(OUT, 'share-url.txt'), url);
  await page.close();
  // what the visitor sees, on a phone
  const v = await newPage({ state: false, device: devices['Pixel 7'], viewport: devices['Pixel 7'].viewport });
  const local = url.replace(/^https?:\/\/[^/]+/, BASE);
  await v.goto(local);
  await sleep(1500);
  await v.fill('input[type=password]', 'sea-view');
  await shot(v, 's28a-visitor-pass');
  await v.keyboard.press('Enter');
  await sleep(2500);
  await shot(v, 's28b-visitor-photo');
  await v.close();
};

stages.sudar = async () => {
  const page = await galleryPage();
  await openPhoto(page, 'Ooty', 1);
  if (await page.locator('#v-more').isVisible()) { await page.click('#v-more'); await sleep(500); }
  await page.click('#v-sudar');
  await page.waitForSelector('.ap-stage:not([hidden])', { timeout: 20000 });
  await sleep(2500);
  await page.click('.ap-preset-card[data-preset="golden"]');
  await sleep(2500);
  await shot(page, 'z29-sudar');
  await page.click('.ap-mode-btn[data-tab="ai"]');
  await sleep(600);
  await page.fill('#ap-prompt', TA ? 'சற்று வெப்பமாக்கு, நிழல்களை உயர்த்து' : 'make it warmer and lift the shadows');
  await page.click('.ap-ask button[type=submit]');
  await sleep(3000);
  await shot(page, 'z30-ai');
  await page.close();
};

stages.profile = async () => {
  const page = await galleryPage();
  await page.click('#profile-btn');
  await sleep(700);
  await shot(page, 'p31a-menu', { clip: { x: 880, y: 0, width: 400, height: 420 } });
  await page.click('#profile-open');
  await page.waitForSelector('#profile-sheet:not([hidden])');
  await sleep(1000);
  await shot(page, 'p31b-profile');
  await page.close();
};

/* ---- 11: on a phone ------------------------------------------------------ */
const PHONE = { device: devices['Pixel 7'], viewport: devices['Pixel 7'].viewport, scale: 2.2, state: false };

stages.phone = async () => {
  let page = await newPage(PHONE);
  await page.goto(BASE + '/');
  await page.waitForSelector('.picker-tile');
  await sleep(1200);
  await shot(page, 'h32a-picker');
  await page.locator('.picker-tile', { hasText: FAMILY[1][0] }).click();
  await page.waitForSelector('.pin-input');
  await page.fill('.pin-input', FAMILY[1][1].slice(0, 4));
  await sleep(400);
  await shot(page, 'h32b-pin');
  await page.fill('.pin-input', FAMILY[1][1]);
  await page.keyboard.press('Enter');
  await page.waitForSelector('#grid .cell img.ready', { timeout: 20000 });
  await sleep(2000);
  await shot(page, 'h32c-timeline');
  await page.click('#sidebar-toggle');
  await sleep(1000);
  await shot(page, 'h33a-menu');
  await page.click('#sidebar-toggle', { force: true }).catch(() => {});
  await page.keyboard.press('Escape');
  await sleep(800);
  await page.locator('#grid .cell:not([data-kind="video"])').nth(2).click();
  await page.waitForSelector('#viewer:not([hidden])');
  await sleep(1800);
  await shot(page, 'h33b-viewer');
  await page.click('#v-more');
  await sleep(800);
  await shot(page, 'h33c-more');
  await page.close();

  page = await newPage(PHONE);
  await page.goto(BASE + '/');
  await page.waitForSelector('.picker-tile');
  await page.click('.guest-tile');
  await page.waitForSelector('#grid .cell img.ready', { timeout: 20000 });
  await sleep(2000);
  await shot(page, 'h34-looking');
  await page.close();
};

/* ---- run ------------------------------------------------------------------ */
(async () => {
  browser = await chromium.launch();
  const want = process.argv.slice(2);
  const ORDER = ['first', 'people', 'import', 'visibility', 'console', 'drives', 'gallery', 'viewer',
    'share', 'sudar', 'profile', 'phone'];
  for (const name of (want.length ? want : ORDER)) {
    console.log('stage', name);
    await stages[name]();
  }
  await browser.close();
})().catch(async (e) => { console.error(e); if (browser) await browser.close(); process.exit(1); });

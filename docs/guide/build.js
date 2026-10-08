// node build.js <lang> <out.pdf>   prints <lang>.html to an A4 PDF with Chromium.
// Printed twice: the first printing tells toc_pages.py which page each chapter
// starts on, and the second carries those numbers in the contents.
const { chromium } = require('/opt/node22/lib/node_modules/playwright');
const { execFileSync } = require('child_process');
const path = require('path');
const [lang, out] = process.argv.slice(2);
const PY = process.env.PY || '/tmp/claude-0/venv/bin/python';

async function print(browser, query) {
  const page = await browser.newPage();
  await page.goto('file://' + path.resolve(__dirname, `${lang}.html`) + query, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => document.documentElement.dataset.ready === '1');
  await page.evaluate(() => document.fonts.ready);
  const missing = await page.evaluate(() => [...document.images].filter((i) => !i.naturalWidth).map((i) => i.src));
  if (missing.length) { console.error('missing images:', missing); process.exit(1); }
  await page.pdf({
    path: out, format: 'A4', printBackground: true, preferCSSPageSize: true,
    displayHeaderFooter: false,
    tagged: true, outline: true,
  });
  await page.close();
}

(async () => {
  const browser = await chromium.launch();
  await print(browser, '');
  const pages = execFileSync(PY, [path.join(__dirname, 'toc_pages.py'), out]).toString().trim();
  if (!pages || pages.split(',').some((p) => !p)) { console.error('contents pages not found:', pages); process.exit(1); }
  await print(browser, '?pages=' + pages);
  await browser.close();
  console.log('wrote', out, 'chapters start on pages', pages);
})();

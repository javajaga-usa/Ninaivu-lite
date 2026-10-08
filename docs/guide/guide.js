// guide.js — runs in the page before it is printed (build.js waits for it).
// Frames each screenshot as a window or a phone, splits "3. Title" chapter headings
// into a number and a title, and fills the contents (page numbers come from
// build.js as ?pages=3,5,... after a first printing).
(function () {
  const ta = document.documentElement.lang === 'ta';
  const kicker = ta ? 'அத்தியாயம்' : 'Chapter';

  // screenshots
  document.querySelectorAll('figure img, .hero img').forEach((img) => {
    const phone = img.closest('figure.phone, .hero-phone');
    const wrap = document.createElement('span');
    wrap.className = phone ? 'phone-frame' : 'window';
    if (!phone) wrap.innerHTML = '<span class="bar"><i></i><i></i><i></i></span>';
    // a width given on the picture belongs to its frame
    if (img.style.width) { wrap.style.width = img.style.width; img.style.width = ''; }
    img.replaceWith(wrap);
    wrap.appendChild(img);
  });

  // chapter openers
  const chapters = [];
  document.querySelectorAll('h1.chapter').forEach((h, i) => {
    const m = h.textContent.trim().match(/^(\d+)\.\s*(.*)$/);
    const n = m ? m[1] : String(i + 1);
    const title = m ? m[2] : h.textContent.trim();
    h.id = 'ch-' + n;
    h.innerHTML = `<span class="num">${n.padStart(2, '0')}</span><span class="kicker">${kicker} ${n}</span><span class="title"></span>`;
    h.querySelector('.title').textContent = title;
    const subs = [];
    for (let el = h.nextElementSibling; el && !el.matches('h1.chapter, .backcover'); el = el.nextElementSibling) {
      if (el.tagName === 'H2') subs.push(el.textContent.trim());
    }
    chapters.push({ n, title, subs });
  });

  // contents
  const toc = document.getElementById('toc');
  if (toc) {
    const pages = (new URLSearchParams(location.search).get('pages') || '').split(',');
    chapters.forEach((c, i) => {
      const li = document.createElement('li');
      li.innerHTML = `<a href="#ch-${c.n}"><span class="n">${c.n.padStart(2, '0')}</span><span class="t"></span><span class="p">${pages[i] || ''}</span><span class="subs"></span></a>`;
      li.querySelector('.t').textContent = c.title;
      li.querySelector('.subs').textContent = c.subs.join('  ·  ');
      toc.appendChild(li);
    });
  }

  // callout icons
  const bulb = '<svg viewBox="0 0 24 24"><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.6 10.8c.6.5 1 1.2 1 2V16h5.2v-.2c0-.8.4-1.5 1-2A6 6 0 0 0 12 3Z"/></svg>';
  const info = '<svg viewBox="0 0 24 24"><path d="M12 11v6M12 7.5v.01"/></svg>';
  document.querySelectorAll('.tip, .note').forEach((b) => {
    const s = document.createElement('span');
    s.className = 'ico';
    s.innerHTML = b.classList.contains('tip') ? bulb : info;
    b.prepend(s);
    const lead = b.querySelector('b.lead');
    if (lead) lead.textContent = lead.textContent.replace(/[:：]\s*$/, '');
  });

  document.documentElement.dataset.ready = '1';
})();

/**
 * Sudar (சுடர், glow): the photo studio, as in Ninaivu, lighter.
 *
 * Ninaivu's `ai-playground/components/playground.js` without the tools that
 * need a model: what is left runs entirely in the browser. Light, colour,
 * detail and framing through the same develop engine as Ninaivu's studio;
 * looks; suggestions measured from the picture; "make it warmer" in plain
 * words through the built-in planner; clothing colour by brush; before and
 * after; undo and redo. The original is never changed: the result is
 * downloaded, or an administrator saves it as a copy beside the original.
 */

import {AIPhotoService} from './service.mjs';
import {defaults} from './adjustments.mjs';
import {decode, saveType} from './files.mjs';
import {History} from './history.mjs';
import {saveLibraryCopy} from './library.mjs';
import {isClothingColorRequest} from './recolor.mjs';
import {openRecolor} from './recolor-dialog.js';
import * as i18n from '../i18n.js';
import {errorText} from './messages.mjs';

export function openSudar({item=null, returnFocus=document.activeElement, canSave=false, onSaved=null}={}) {
  const existing=document.getElementById('ai-playground');
  // A closed dialog is removed a moment after its close event; opened again
  // inside that moment, it must not count as still open.
  if(existing){ if(existing.open) return; existing.remove(); }
  if(!document.getElementById('ai-playground-style')) {
    const link=document.createElement('link'); link.id='ai-playground-style';link.rel='stylesheet';link.href='/static/css/sudar.css';document.head.append(link);
  }
  const dialog=document.createElement('dialog'); dialog.id='ai-playground';dialog.setAttribute('aria-labelledby','ap-title');
  dialog.innerHTML=`
    <header class="ap-header">
      <div class="ap-brand">
        <span class="ap-brand-mark" aria-hidden="true"><svg class="ap-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z"/></svg></span>
        <h1 id="ap-title">${i18n.t('Sudar')} <span class="ap-private">${i18n.t('On-device AI')}</span></h1>
      </div>
      <div class="ap-bar">
        <div class="ap-cluster" role="group" aria-label="${i18n.t('History actions')}">
          <button type="button" class="btn" data-undo title="${i18n.t('Undo (Ctrl+Z)')}" disabled>${i18n.t('Undo')}</button>
          <button type="button" class="btn" data-redo title="${i18n.t('Redo (Ctrl+Y)')}" disabled>${i18n.t('Redo')}</button>
          <button type="button" class="btn" data-reset title="${i18n.t('Reset to original')}">${i18n.t('Reset all')}</button>
        </div>
        <div class="ap-cluster ap-view-modes" role="group" aria-label="${i18n.t('Preview mode')}">
          <button type="button" class="btn" data-view="compare" aria-pressed="true">${i18n.t('Compare')}</button>
          <button type="button" class="btn" data-view="edited" aria-pressed="false">${i18n.t('Edited')}</button>
          <button type="button" class="btn" data-view="original" aria-pressed="false">${i18n.t('Original')}</button>
        </div>
        <label class="ap-zoom" style="margin-left:auto; display:flex; align-items:center; gap:6px; font-size:11px; color:var(--ap-muted);">
          ${i18n.t('Zoom')}
          <select data-zoom style="padding:4px 8px; font-size:11px; background:var(--ap-card); border-radius:6px; color:inherit; border:1px solid var(--ap-line);">
            <option value="fit">${i18n.t('Fit')}</option>
            <option value="1">100%</option>
            <option value="1.5">150%</option>
          </select>
        </label>
        <button type="button" class="btn ap-theme-btn" id="ap-theme-btn" title="${i18n.t('Theme (T)')}" aria-label="${i18n.t('Toggle theme')}">
          <svg viewBox="0 0 24 24" class="ico-sun"><circle cx="12" cy="12" r="4.5"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/></svg>
          <svg viewBox="0 0 24 24" class="ico-moon"><path d="M20 14.5A8.5 8.5 0 1 1 10.2 4a7 7 0 0 0 9.8 10.5Z"/></svg>
        </button>
      </div>
      <button type="button" data-close aria-label="${i18n.t('Close Sudar')}"><svg class="ap-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg></button>
    </header>

    <div class="ap-layout">
      <section class="ap-workspace" aria-label="${i18n.t('Photo preview')}">
        <div class="ap-viewport">
          <div class="ap-empty">${i18n.t('Open a photo from the gallery to begin editing.')}</div>
          <div class="ap-stage" hidden>
            <img class="ap-edited" alt="${i18n.t('Edited preview')}" draggable="false">
            <img class="ap-original" alt="${i18n.t('Original photo')}" draggable="false">
            <div class="ap-divider" aria-hidden="true"><span class="ap-divider-handle">↔</span></div>
            <span class="ap-before">${i18n.t('Original')}</span>
            <span class="ap-after">${i18n.t('Edited')}</span>
          </div>
        </div>
        <label class="ap-floating-compare ap-compare" hidden>
          ${i18n.t('Before')}
          <input data-compare type="range" min="0" max="100" value="50" aria-label="${i18n.t('Before and after comparison position')}">
          ${i18n.t('After')}
        </label>
        <p class="ap-status" role="status" aria-live="polite">${i18n.t('Open a photo to begin.')}</p>
      </section>

      <aside class="ap-studio" aria-label="${i18n.t('Editing tools')}">
        <div class="ap-studio-nav" role="tablist">
          <button type="button" class="ap-mode-btn active" data-tab="adjustments"><svg class="ap-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></svg>${i18n.t('Adjust')}</button>
          <button type="button" class="ap-mode-btn" data-tab="ai"><svg class="ap-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M9.5 3.5 11.2 7.8 15.5 9.5 11.2 11.2 9.5 15.5 7.8 11.2 3.5 9.5 7.8 7.8Z"/><path d="M17.5 14 18.4 16.6 21 17.5 18.4 18.4 17.5 21 16.6 18.4 14 17.5 16.6 16.6Z"/></svg>${i18n.t('AI assist')}</button>
          <button type="button" class="ap-mode-btn" data-tab="magic"><svg class="ap-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M15 4V2M15 16v-2M8 9h2M20 9h2M17.8 11.8 19 13M17.8 6.2 19 5M3 21l9-9M12.2 6.2 11 5"/></svg>${i18n.t('Magic tools')}</button>
        </div>

        <div class="ap-tools-body">
          <!-- 1 · adjustments and looks -->
          <div data-panel="adjustments" style="display:flex; flex-direction:column; gap:16px;">
            <div>
              <div class="ap-group-title">${i18n.t('Creative looks')}</div>
              <div class="ap-presets-grid">
                <button type="button" class="ap-preset-card" data-preset="vivid"><strong>${i18n.t('Vivid')}</strong><span>${i18n.t('Punchy contrast & color')}</span></button>
                <button type="button" class="ap-preset-card" data-preset="golden"><strong>${i18n.t('Golden hour')}</strong><span>${i18n.t('Warm sunlit tone')}</span></button>
                <button type="button" class="ap-preset-card" data-preset="cinematic"><strong>${i18n.t('Cinematic')}</strong><span>${i18n.t('Moody contrast & shade')}</span></button>
                <button type="button" class="ap-preset-card" data-preset="bw"><strong>${i18n.t('Studio B&W')}</strong><span>${i18n.t('Rich monochrome')}</span></button>
                <button type="button" class="ap-preset-card" data-preset="bright"><strong>${i18n.t('Bright & clean')}</strong><span>${i18n.t('Lifted shadows & clarity')}</span></button>
                <button type="button" class="ap-preset-card" data-preset="vintage"><strong>${i18n.t('Vintage warm')}</strong><span>${i18n.t('Soft film warmth')}</span></button>
                <button type="button" class="ap-preset-card" data-preset="hdr"><strong>${i18n.t('HDR')}</strong><span>${i18n.t('Open shadows, held skies, crisp detail')}</span></button>
              </div>
            </div>

            <div class="ap-suggestions-section">
              <div class="ap-group-title">${i18n.t('Contextual AI suggestions')}</div>
              <div class="ap-suggestions">${i18n.t('Choose a photo to analyze.')}</div>
            </div>

            <fieldset disabled class="ap-panel ap-panel-sliders" style="border:0; padding:0; margin:0; display:flex; flex-direction:column; gap:16px; background:none;">
              <div>
                <div class="ap-group-title">${i18n.t('Light')}</div>
                <div class="ap-controls-light ap-controls"></div>
              </div>
              <div>
                <div class="ap-group-title">${i18n.t('Color')}</div>
                <div class="ap-controls-color"></div>
              </div>
              <div>
                <div class="ap-group-title">${i18n.t('Detail & optics')}</div>
                <div class="ap-controls-detail"></div>
              </div>
              <div>
                <div class="ap-group-title">${i18n.t('Composition')}</div>
                <div class="ap-controls-geometry"></div>
                <div style="display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:8px; font-size:11.5px; color:var(--ap-muted); margin-top:6px;">
                  <span style="color:var(--ap-muted);">${i18n.t('Crop framing')}</span>
                  <select data-crop style="font-size:11px; padding:4px 8px; background:var(--ap-card);">
                    <option value="original">${i18n.t('Original framing')}</option>
                    <option value="square">${i18n.t('Square · 1:1')}</option>
                    <option value="landscape">${i18n.t('Landscape · 16:9')}</option>
                    <option value="portrait">${i18n.t('Portrait · 4:5')}</option>
                    <option value="story">${i18n.t('Story · 9:16')}</option>
                  </select>
                </div>
                <p class="ap-crop-note" hidden>${i18n.t('Crop preview uses same framing. Reset to view full original.')}</p>
              </div>
            </fieldset>
          </div>

          <!-- 2 · ask in plain words -->
          <div data-panel="ai" hidden style="display:flex; flex-direction:column; gap:14px;">
            <form class="ap-ask">
              <h2 class="ap-ask-title">${i18n.t('Natural language editing')}</h2>
              <div class="ap-prompt-box">
                <textarea id="ap-prompt" rows="3" maxlength="2000" placeholder="${i18n.t('e.g. Lift shadows, soften highlights, warm white balance, crop to 4:5')}" disabled></textarea>
                <div class="ap-prompt-bar">
                  <span class="ap-prompt-shortcut">${i18n.t('Ctrl+Enter to plan')}</span>
                  <button class="btn primary" type="submit" disabled style="padding:6px 14px; font-size:12px;">${i18n.t('Plan edits')}</button>
                </div>
              </div>
              <small data-provider-note style="font-size:11px; color:var(--ap-dim); line-height:1.4;">${i18n.t('Combine adjustments and creative looks; review the plan before applying.')}</small>
              <div>
                <div class="ap-group-title">${i18n.t('Ideas')}</div>
                <div class="ap-prompt-ideas"></div>
              </div>
            </form>
            <section class="ap-plan" hidden aria-label="${i18n.t('Proposed AI edit')}">
              <h2>${i18n.t('Proposed AI edit')}</h2>
              <p data-plan-summary style="font-size:11.5px; color:var(--ap-text); margin:0;"></p>
              <ul data-plan-changes style="margin:4px 0; padding-left:18px;"></ul>
              <button class="btn primary" data-apply-plan style="width:100%;">${i18n.t('Apply planned edits')}</button>
            </section>
          </div>

          <!-- 3 · tools -->
          <div data-panel="magic" hidden style="display:flex; flex-direction:column; gap:12px;">
            <div class="ap-magic-card">
              <h3><svg class="ap-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 4h8l3 3-2 3-1-1v11H8V9L7 10 5 7Z"/><path d="M12 4a2 2 0 0 0 4 0"/></svg>${i18n.t('Clothing colour')}</h3>
              <p>${i18n.t('Brush over a dress, shirt or sari and choose a new colour. The folds and the weave stay; only the colour changes, and only where you painted.')}</p>
              <button class="btn" data-recolor style="align-self:flex-start;">${i18n.t('Open clothing colour')}</button>
            </div>
          </div>
        </div>

        <div class="ap-studio-footer">
          <button class="btn primary" data-save hidden>${i18n.t('Save copy to Ninaivu library')}</button>
          <p data-save-note hidden style="font-size:10.5px; color:var(--ap-dim); margin:0;">${i18n.t('Saved alongside your original on Ninaivu server.')}</p>
          <div class="ap-export-row">
            <select data-format aria-label="${i18n.t('Export format')}">
              <option value="image/png">${i18n.t('PNG · lossless')}</option>
              <option value="image/jpeg">${i18n.t('JPEG · standard')}</option>
              <option value="image/webp">${i18n.t('WebP · modern')}</option>
            </select>
            <button class="btn" data-export disabled>${i18n.t('Download image')}</button>
          </div>
          <div style="display:flex; justify-content:space-between; align-items:center; font-size:10.5px; color:var(--ap-dim); margin-top:2px;">
            <span class="ap-dimensions"></span>
            <span>${i18n.t('A download carries no metadata; a saved copy keeps the original\'s camera, exposure and place.')}</span>
          </div>
        </div>
      </aside>
    </div>`;

  document.body.append(dialog);
  const $=s=>dialog.querySelector(s), service=new AIPhotoService(), controller=new AbortController();

  dialog.querySelectorAll('.ap-mode-btn').forEach(btn => {
    btn.onclick = () => {
      dialog.querySelectorAll('.ap-mode-btn').forEach(b => b.classList.toggle('active', b === btn));
      dialog.querySelectorAll('[data-panel]').forEach(p => { p.hidden = p.dataset.panel !== btn.dataset.tab; });
    };
  });

  // Every idea is a sentence the built-in planner understands as it is.
  const prompts = $('.ap-prompt-ideas');
  for(const [title,prompt] of [
    [i18n.t('Natural light'),'Lift the shadows, reduce the highlights and more contrast'],
    [i18n.t('Golden hour'),'Golden hour and slightly warmer'],
    [i18n.t('Monochrome'),'Black and white and more contrast'],
    [i18n.t('Cinematic film'),'Cinematic and add a vignette'],
    [i18n.t('Bright & clean'),'Brighter, lift the shadows, reduce the highlights and add clarity'],
  ]) {
    const button=document.createElement('button');button.type='button';button.className='btn';button.textContent=title;
    button.onclick=()=>{if(busy)return;$('#ap-prompt').value=prompt;clearPlan();$('#ap-prompt').focus();};prompts.append(button);
  }

  let viewMode='compare';
  function updateCompareDivider(val){
    const divider=$('.ap-divider');
    if(divider){
      divider.style.left=`${val}%`;
      divider.hidden=viewMode!=='compare'||!previewReady||val<=0||val>=100;
    }
  }
  function setView(mode){
    viewMode=mode;
    dialog.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===mode)));
    $('.ap-original').hidden=mode==='edited';
    const compVal=Number($('[data-compare]').value);
    $('.ap-original').style.clipPath=mode==='original'?'none':`inset(0 ${100-compVal}% 0 0)`;
    $('.ap-compare').hidden=mode!=='compare'||!previewReady;
    $('.ap-before').hidden=mode==='edited';$('.ap-after').hidden=mode==='original';
    updateCompareDivider(compVal);
  }
  dialog.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>setView(b.dataset.view));

  const zoomSelect = $('[data-zoom]');
  zoomSelect.onchange = () => {
    const img = $('.ap-edited');
    if(zoomSelect.value === 'fit') { img.style.maxHeight = 'calc(97dvh - 160px)'; img.style.width = 'auto'; }
    else { img.style.maxHeight = 'none'; img.style.width = bitmap ? `${Math.round(bitmap.width * Number(zoomSelect.value))}px` : 'auto'; }
  };

  let bitmap=null, history=new History(defaults()), busy=false, closed=false, previewReady=false, urls=[];
  let sourceId=null, saving=false, savedState=JSON.stringify(defaults());
  let pendingPlan=null, analysis={};

  function clearPlan(){pendingPlan=null;$('.ap-plan').hidden=true;}
  const dirty=()=>JSON.stringify(history.current)!==savedState;
  const confirmDiscard=()=>!dirty()||window.confirm(i18n.t('Discard your unsaved photo edits and results?'));
  const status=message=>{$('.ap-status').textContent=message;};
  const releaseURLs=()=>{urls.forEach(URL.revokeObjectURL);urls=[];};

  function lock(value) {
    busy=value; dialog.setAttribute('aria-busy',String(value));
    $('.ap-panel-sliders').disabled=value||!bitmap;
    $('#ap-prompt').disabled=value||!bitmap;$('.ap-ask button[type=submit]').disabled=value||!bitmap;
    $('[data-apply-plan]').disabled=value||!pendingPlan;
    dialog.querySelectorAll('.ap-suggestions button').forEach(b=>b.disabled=value);
    dialog.querySelectorAll('.ap-prompt-ideas button').forEach(b=>b.disabled=value||!bitmap);
    dialog.querySelectorAll('.ap-preset-card').forEach(b=>b.disabled=value||!bitmap);
    $('[data-undo]').disabled=history.index===0; $('[data-redo]').disabled=history.index===history.items.length-1;
    $('[data-export]').disabled=!previewReady;
    $('[data-save]').hidden=!canSave||!sourceId;
    $('[data-save-note]').hidden=!canSave||!sourceId;
    $('[data-save]').disabled=!previewReady;
    // With no library to save to, downloading is the one thing left to do.
    $('[data-export]').classList.toggle('primary',$('[data-save]').hidden);
    $('[data-recolor]').disabled=value||!bitmap;
  }

  const sliderGroups = {
    light: [['exposure', i18n.t('Exposure')], ['contrast', i18n.t('Contrast')], ['shadows', i18n.t('Shadows')], ['highlights', i18n.t('Highlights')], ['dehaze', i18n.t('Dehaze')]],
    color: [['vibrance', i18n.t('Vibrance')], ['saturation', i18n.t('Saturation')], ['warmth', i18n.t('White balance')]],
    detail: [['clarity', i18n.t('Clarity')], ['sharpness', i18n.t('Sharpness')], ['noise', i18n.t('Noise reduction')], ['vignette', i18n.t('Vignette')]],
    geometry: [['angle', i18n.t('Straighten')]],
  };

  function formatValue(key, val) {
    const num = Number(val);
    if (num > 0 && key !== 'noise' && key !== 'sharpness' && key !== 'vignette') return `+${num}`;
    return String(num);
  }

  for(const [group, items] of Object.entries(sliderGroups)) {
    const container = $(`.ap-controls-${group}`);
    for(const [key, label] of items) {
      const row = document.createElement('label');
      row.className = 'ap-slider';
      row.title = i18n.t('Double-click to reset to 0');
      const title = document.createElement('span'); title.className = 'ap-slider-title'; title.textContent = label;
      const output = document.createElement('output'); output.dataset.value = key; output.textContent = '0';
      const input = document.createElement('input');
      input.type = 'range';
      input.min = ['noise','sharpness','vignette'].includes(key) ? 0 : key === 'angle' ? -10 : -100;
      input.max = key === 'angle' ? 10 : 100;
      input.step = key === 'angle' ? 0.5 : 1;
      input.value = 0;
      input.dataset.adjust = key;
      input.setAttribute('aria-label', label);
      input.id = `ap-${key}`;
      output.htmlFor = input.id;
      row.ondblclick = (e) => { e.preventDefault(); if(busy || !bitmap) return; input.value = 0; output.textContent = '0'; apply({[key]: 0}); };
      row.append(title, output, input);
      container.append(row);
      input.oninput = () => { output.textContent = formatValue(key, input.value); };
      input.onchange = () => apply({[key]: Number(input.value)});
    }
  }

  const presetDefinitions = {
    vivid: {contrast: 20, vibrance: 30, saturation: 8, clarity: 15, sharpness: 20, exposure: 5},
    golden: {warmth: 35, highlights: -15, shadows: 15, vignette: 10},
    cinematic: {contrast: 25, shadows: -15, highlights: -15, warmth: 10, vignette: 20},
    bw: {saturation: -100, contrast: 25, clarity: 20, sharpness: 25, exposure: 10},
    bright: {exposure: 20, shadows: 30, highlights: -25, clarity: 10, sharpness: 15},
    vintage: {warmth: 25, contrast: -10, dehaze: -15, vignette: 25, shadows: 10},
    hdr: {shadows: 35, highlights: -35, clarity: 25, vibrance: 15, dehaze: 10},
  };
  dialog.querySelectorAll('[data-preset]').forEach(btn => {
    btn.onclick = () => { const p = presetDefinitions[btn.dataset.preset]; if(p) apply(p); };
  });

  function sync() {
    const a = history.current;
    dialog.querySelectorAll('[data-adjust]').forEach(i => {
      i.value = a[i.dataset.adjust];
      const out = $(`[data-value="${i.dataset.adjust}"]`);
      if(out) out.textContent = formatValue(i.dataset.adjust, i.value);
    });
    $('[data-crop]').value = a.crop;
  }

  async function preview() {
    clearPlan();
    previewReady = false; lock(true); status(i18n.t('Applying adjustments…'));
    try {
      const a = history.current;
      const edited = await service.applyAdjustments(bitmap, a);
      const original = await service.applyAdjustments(bitmap, {...defaults(), crop: a.crop, angle: a.angle});
      if(closed) return;
      releaseURLs(); urls = [URL.createObjectURL(edited), URL.createObjectURL(original)];
      $('.ap-edited').src = urls[0]; $('.ap-original').src = urls[1];
      $('.ap-stage').hidden = false; $('.ap-compare').hidden = false;
      $('.ap-crop-note').hidden = a.crop === 'original' && a.angle === 0;
      previewReady = true; $('.ap-empty').hidden = true; setView(viewMode); sync(); status(i18n.t('Preview updated.'));
    } catch(error) { sync(); status(i18n.t('{reason} Preview could not update.', {reason: errorText(error, i18n.t)})); }
    finally { if(!closed) lock(false); }
  }

  async function apply(patch) {
    if(busy || !bitmap) return;
    const next = {...history.current, ...patch};
    if(JSON.stringify(next) === JSON.stringify(history.current)) return;
    history.push(next);
    await preview();
  }

  async function load(file, rotation=0, libraryId=null, mirror=false) {
    if(busy || !file || !confirmDiscard()) return; lock(true); status(i18n.t('Analyzing photograph…'));
    try {
      let next = await decode(file); if(closed){next.close(); return;}
      const angle = ((rotation % 360) + 360) % 360;
      if(angle || mirror) {
        const canvas = document.createElement('canvas');
        canvas.width = angle % 180 ? next.height : next.width; canvas.height = angle % 180 ? next.width : next.height;
        const ctx = canvas.getContext('2d'); ctx.translate(canvas.width/2, canvas.height/2); ctx.rotate(angle * Math.PI / 180); if(mirror) ctx.scale(-1, 1); ctx.drawImage(next, -next.width/2, -next.height/2);
        next.close(); next = await createImageBitmap(canvas);
        if(closed){next.close(); return;}
      }
      bitmap?.close(); bitmap = next; history = new History(defaults());
      sourceId = libraryId; savedState = JSON.stringify(defaults());
      $('.ap-dimensions').textContent = `${file.name} · ${bitmap.width} × ${bitmap.height}`;
      status(i18n.t('Generating contextual suggestions…'));
      analysis = service.analyzeImage(bitmap);
      const cards = service.getSuggestions(analysis); $('.ap-suggestions').replaceChildren();
      for(const card of cards) {
        const button = document.createElement('button'); button.className = 'ap-card'; button.type = 'button';
        const title = document.createElement('strong'), reason = document.createElement('span');
        title.textContent = i18n.t(card.title); reason.textContent = i18n.t(card.reason);
        button.append(title, reason); button.onclick = () => apply(card.patch); $('.ap-suggestions').append(button);
      }
      await preview();
    } catch(error) { status(errorText(error, i18n.t)); }
    finally { if(!closed) lock(false); }
  }

  $('[data-compare]').oninput = e => {
    const val = Number(e.target.value);
    $('.ap-original').style.clipPath = `inset(0 ${100-val}% 0 0)`;
    updateCompareDivider(val);
  };

  const stage = $('.ap-stage');
  let stageDragging = false;
  function updateStageCompare(e){
    const rect = $('.ap-edited').getBoundingClientRect();
    if(!rect.width) return;
    const pct = Math.max(0, Math.min(100, Math.round(((e.clientX - rect.left) / rect.width) * 100)));
    $('[data-compare]').value = String(pct);
    $('.ap-original').style.clipPath = `inset(0 ${100-pct}% 0 0)`;
    updateCompareDivider(pct);
  }
  // Dragging the divider must not also select: Safari ignores the CSS
  // user-select without its -webkit- prefix, and a mouse press left to its
  // default starts a selection that painted the edited (right) half blue as
  // the divider moved. The press is the divider's alone (M14).
  stage.onpointerdown = e => {
    if(viewMode !== 'compare' || (e.pointerType === 'mouse' && e.button !== 0)) return;
    e.preventDefault();
    document.getSelection()?.removeAllRanges();
    stageDragging = true; stage.setPointerCapture(e.pointerId); updateStageCompare(e);
  };
  stage.onpointermove = e => { if(stageDragging) updateStageCompare(e); };
  stage.onpointerup = stage.onpointercancel = e => { if(stageDragging){ stageDragging = false; try{stage.releasePointerCapture(e.pointerId);}catch{ /* already released */ } } };
  stage.onlostpointercapture = () => { stageDragging = false; };
  stage.ondragstart = stage.onselectstart = e => e.preventDefault();

  $('[data-crop]').onchange = e => apply({crop: e.target.value});

  /** A tool's finished picture becomes the photograph on the canvas, with the sliders at zero again. */
  async function takeOnto(next, message){
    if(closed || !next) return;
    bitmap?.close();
    bitmap = next;
    history = new History(defaults());
    savedState = JSON.stringify(defaults());
    await preview();
    status(message);
  }

  async function clothingColour(){
    if(busy || !bitmap) return;
    lock(true); status(i18n.t('Opening clothing colour…'));
    try{
      const blob = await service.applyAdjustments(bitmap, history.current, {maxSide: Infinity, type: 'image/png'});
      if(closed) return;
      const source = await createImageBitmap(blob);
      if(closed){source.close(); return;}
      openRecolor(source, {onApply: recoloured => takeOnto(recoloured, i18n.t('Clothing colour applied to the photo. Save a copy to keep it.'))});
      status(i18n.t('Brush over the clothing, choose a colour, then apply.'));
    } catch(error){ status(errorText(error, i18n.t)); }
    finally { if(!closed) lock(false); }
  }
  $('[data-recolor]').onclick = () => { if(!busy && bitmap) clothingColour(); };

  $('[data-reset]').onclick = () => apply(defaults());
  $('[data-undo]').onclick = () => { if(!busy){ history.undo(); preview(); } };
  $('[data-redo]').onclick = () => { if(!busy){ history.redo(); preview(); } };

  $('#ap-prompt').oninput = clearPlan;
  $('#ap-prompt').onkeydown = e => {
    if((e.ctrlKey || e.metaKey) && e.key === 'Enter'){ e.preventDefault(); $('.ap-ask').requestSubmit(); }
  };

  $('.ap-ask').onsubmit = async e => {
    e.preventDefault(); if(busy || !bitmap) return; clearPlan();
    const asked = $('#ap-prompt').value;
    if(isClothingColorRequest(asked)){ await clothingColour(); return; }
    lock(true); status(i18n.t('Planning edits…'));
    try {
      const plan = await service.planEdit(asked, history.current, analysis);
      if(closed) return; pendingPlan = plan;
      $('[data-plan-summary]').textContent = `${i18n.t(plan.provider)}: ${i18n.t(plan.summary)}`; $('[data-plan-changes]').replaceChildren();
      const labels = {exposure:i18n.t('Exposure'),contrast:i18n.t('Contrast'),saturation:i18n.t('Saturation'),vibrance:i18n.t('Vibrance'),warmth:i18n.t('White balance'),sharpness:i18n.t('Sharpness'),noise:i18n.t('Noise reduction'),shadows:i18n.t('Shadows'),highlights:i18n.t('Highlights'),clarity:i18n.t('Clarity'),dehaze:i18n.t('Dehaze'),vignette:i18n.t('Vignette'),angle:i18n.t('Straighten'),crop:i18n.t('Crop framing')};
      for(const [key, value] of Object.entries(plan.patch)) {
        const li = document.createElement('li'); li.textContent = `${labels[key]||key}: ${history.current[key]} → ${value}`; $('[data-plan-changes]').append(li);
      }
      $('.ap-plan').hidden = false; status(i18n.t('Plan ready. Review proposed changes.'));
    } catch(error){ if(!closed) status(errorText(error, i18n.t)); }
    finally { if(!closed) lock(false); }
  };
  $('[data-apply-plan]').onclick = () => { if(pendingPlan && !busy) apply(pendingPlan.patch); };

  $('[data-export]').onclick = async () => {
    if(busy || !bitmap || !previewReady) return; lock(true); status(i18n.t('Preparing full-resolution export…'));
    try {
      const blob = await service.applyAdjustments(bitmap, history.current, {maxSide: Infinity, type: $('[data-format]').value});
      if(closed) return;
      const url = URL.createObjectURL(blob), a = document.createElement('a'); a.href = url;
      a.download = `ninaivu-edited.${({'image/png':'png','image/jpeg':'jpg','image/webp':'webp'})[blob.type]||'png'}`;
      a.click(); setTimeout(() => URL.revokeObjectURL(url), 30000); savedState = JSON.stringify(history.current);
      status(i18n.t('Download ready. Metadata stripped.'));
    } catch(error){ status(errorText(error, i18n.t)); }
    finally { if(!closed) lock(false); }
  };

  $('[data-save]').onclick = async () => {
    if(busy || !sourceId || !canSave || !previewReady) return;
    saving = true; lock(true); status(i18n.t('Saving new library copy…'));
    try {
      const blob = await service.applyAdjustments(bitmap, history.current, {maxSide: Infinity, type: saveType(item)});
      const copy = await saveLibraryCopy(sourceId, blob);
      savedState = JSON.stringify(history.current); saving = false;
      dialog.close();
      onSaved?.(copy);
    } catch(error){ status(errorText(error, i18n.t)); }
    finally { saving = false; if(!closed) lock(false); }
  };

  function requestClose() {
    if(saving){ status(i18n.t('Please wait while your copy is saving.')); return; }
    if(confirmDiscard()) dialog.close();
  }
  $('[data-close]').onclick = requestClose;
  dialog.oncancel = e => { e.preventDefault(); requestClose(); };

  const cycleAppTheme = () => {
    if (typeof window.cycleTheme === 'function') { window.cycleTheme(); return; }
    const order = ['system', 'light', 'dark'];
    const cur = document.documentElement.dataset.theme || 'system';
    document.documentElement.dataset.theme = order[(order.indexOf(cur) + 1) % order.length];
  };
  $('#ap-theme-btn').onclick = cycleAppTheme;

  const handleKey = e => {
    if(!dialog.open || document.querySelector('.ap-recolor[open]')) return;
    const typing = e.target.matches('input:not([type=range]),textarea') || e.target.isContentEditable;
    if(!typing && e.key.toLowerCase() === 't' && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); cycleAppTheme(); return; }
    if(!(e.ctrlKey || e.metaKey) || e.altKey || typing) return;
    const key = e.key.toLowerCase();
    if(key !== 'z' && key !== 'y') return;
    e.preventDefault(); if(busy || !bitmap) return;
    if(key === 'y' || e.shiftKey) history.redo(); else history.undo(); preview();
  };

  document.addEventListener('keydown', handleKey);
  dialog.onclose = () => {
    closed = true;
    document.removeEventListener('keydown', handleKey);
    controller.abort(); bitmap?.close(); releaseURLs(); dialog.remove();
    if(returnFocus?.isConnected) returnFocus.focus();
  };
  dialog.showModal();

  if(item) {
    lock(true); status(i18n.t('Opening photograph…'));
    (async () => {
      try {
        const url = new URL(item.view || item.src, location.href);
        if(url.origin !== location.origin) throw new Error(i18n.t('This photo must come from your Ninaivu library.'));
        // A HEIC or TIFF is viewed through a 2560 px copy, and a copy saved
        // from that was smaller than its original. Whoever can save works on
        // the server's full-size conversion; the viewing copy stays the
        // fallback.
        let response = null;
        if(canSave && item.id && !item.playable) {
          response = await fetch(`/api/asset/${item.id}/edit-source`, {signal: controller.signal, credentials: 'same-origin'}).catch(() => null);
          if(response && !response.ok) response = null;
        }
        response ||= await fetch(url, {signal: controller.signal, credentials: 'same-origin'});
        if(!response.ok) throw new Error(i18n.t('Photo could not be loaded.'));
        const blob = await response.blob(); if(closed) return;
        lock(false); await load(new File([blob], item.name || i18n.t('Current photo'), {type: blob.type}), item.rotation || 0, item.id, !!item.mirror);
      } catch(error){ if(!closed){ lock(false); status(errorText(error, i18n.t)); } }
    })();
  }
}

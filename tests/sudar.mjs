/**
 * Sudar's model-free parts, without a browser: the built-in planner, the
 * adjustment model, the suggestions, the history and the clothing recolour.
 * Run with `node --test tests/sudar.mjs` (tests/test_sudar.py does, when Node
 * is installed).
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { interpret, planRequest } from '../ninaivu_lite/static/js/sudar/commands.mjs';
import { isClothingColorRequest, recolorPixels } from '../ninaivu_lite/static/js/sudar/recolor.mjs';
import { analyze, suggestions, defaults, validate } from '../ninaivu_lite/static/js/sudar/adjustments.mjs';
import { validateFile } from '../ninaivu_lite/static/js/sudar/files.mjs';
import { History } from '../ninaivu_lite/static/js/sudar/history.mjs';
import { recipeFor } from '../ninaivu_lite/static/js/sudar/processor.mjs';
import { develop } from '../ninaivu_lite/static/js/studio/develop.mjs';
import { RANGES } from '../ninaivu_lite/static/js/studio/recipe.mjs';

test('dark and bright images receive different contextual exposure suggestions', () => {
  const dark = suggestions(analyze(new Uint8ClampedArray([30, 30, 30, 255]), 1600, 1200));
  const bright = suggestions(analyze(new Uint8ClampedArray([230, 230, 230, 255]), 1600, 1200));
  assert.ok(dark.some((s) => s.title === 'Improve lighting'));
  assert.ok(!bright.some((s) => s.title === 'Improve lighting'));
  assert.ok(bright.some((s) => s.title === 'Fix exposure'));
});

test('plain words become adjustments, and anything else is refused whole', () => {
  assert.deepEqual(interpret('Make this photo brighter'), { exposure: 25 });
  assert.deepEqual(interpret('Make this suitable for LinkedIn'), { crop: 'square' });
  const p = planRequest('Lift the shadows, reduce highlights, make it slightly warmer, and crop to 4:5', { ...defaults(), warmth: 10 });
  assert.deepEqual(p.patch, { shadows: 30, highlights: -30, warmth: 20, crop: 'portrait' });
  assert.equal(planRequest('set contrast to 30 and increase contrast by 5').patch.contrast, 35);
  assert.equal(planRequest('black and white').patch.saturation, -100);
  for (const q of ['blur the background', 'remove the person', 'make my eyes blue', 'face swap', 'restore this old photo', 'increase contrast by 999']) {
    assert.throws(() => interpret(q), q);
  }
  const current = defaults();
  assert.throws(() => planRequest('brighten the shadows and remove the person', current));
  assert.deepEqual(current, defaults());
});

test('every idea Sudar offers is a sentence the planner understands', () => {
  for (const idea of [
    'Lift the shadows, reduce the highlights and more contrast',
    'Golden hour and slightly warmer',
    'Black and white and more contrast',
    'Cinematic and add a vignette',
    'Brighter, lift the shadows, reduce the highlights and add clarity',
  ]) assert.ok(Object.keys(planRequest(idea).patch).length > 0, idea);
});

test('a request the planner refuses names what Sudar can do, in English that is translated', () => {
  assert.throws(() => interpret('remove the background'), /Sudar adjusts light/);
  const error = (() => { try { interpret('make it sparkle'); } catch (e) { return e; } })();
  assert.ok(error.i18n, 'the message keeps its key for translation');
  assert.match(error.i18n[0], /explicit steps/);
});

test('the model rejects what it does not know, and the ranges hold', () => {
  assert.throws(() => validate({ exposure: Infinity }));
  assert.throws(() => validate({ crop: 'face' }));
  assert.throws(() => validate({ angle: 90 }));
  assert.throws(() => validate({ sparkle: 1 }));
  assert.deepEqual(validate({ vibrance: 20, crop: 'story' }), { ...defaults(), vibrance: 20, crop: 'story' });
});

test('type, empty files and excessive sizes are rejected', () => {
  for (const type of ['image/jpeg', 'image/png', 'image/webp']) validateFile({ type, size: 100 });
  for (const file of [{ type: 'image/svg+xml', size: 50 }, { type: 'image/png', size: 0 }, { type: 'image/png', size: 31 * 1024 * 1024 }]) {
    assert.throws(() => validateFile(file));
  }
});

test('history is bounded, redo is dropped by a new step, and reset is a step', () => {
  const h = new History(defaults());
  h.push({ ...defaults(), exposure: 20 }); h.undo(); assert.equal(h.current.exposure, 0);
  h.redo(); assert.equal(h.current.exposure, 20);
  h.undo(); h.push({ ...defaults(), contrast: 10 }); assert.equal(h.redo().exposure, 0);
  for (let i = 0; i < 40; i++) h.push({ ...defaults(), exposure: i });
  assert.equal(h.items.length, 30);
});

test('clothing colour requests open the brush, and the brush keeps the folds', () => {
  for (const text of ['Make her sari red', 'change the shirt colour to blue', 'recolor the dress']) assert.ok(isClothingColorRequest(text), text);
  for (const text of ['make it warmer', 'brighter']) assert.ok(!isClothingColorRequest(text), text);
  const source = new Uint8ClampedArray([200, 200, 200, 255, 100, 100, 100, 255]);
  const mask = new Uint8ClampedArray([0, 0, 0, 255, 0, 0, 0, 0]);
  const out = recolorPixels(source, mask, [255, 0, 0], 1);
  assert.deepEqual([...out.slice(4)], [100, 100, 100, 255], 'unpainted pixels are untouched');
  assert.ok(out[0] > out[1] && out[1] === out[2], 'the painted pixel took the colour');
});

test("Sudar's sliders are the engine's keys, within its ranges", () => {
  const recipe = recipeFor({ ...defaults(), exposure: 100, sharpness: 50, noise: 30 });
  for (const [key, value] of Object.entries(recipe)) {
    assert.ok(key in RANGES, key);
    const [lo, hi] = RANGES[key];
    assert.ok(value >= lo && value <= hi, `${key}=${value}`);
  }
});

test('a blank recipe gives the photograph back, and exposure brightens it', () => {
  const W = 8, H = 8, px = new Uint8ClampedArray(W * H * 4);
  for (let i = 0; i < px.length; i += 4) { px[i] = 90; px[i + 1] = 110; px[i + 2] = 130; px[i + 3] = 255; }
  const same = develop(px, W, H, {});
  assert.deepEqual([...same], [...px]);
  const brighter = develop(px, W, H, recipeFor({ ...defaults(), exposure: 40 }));
  assert.ok(brighter[0] > px[0] && brighter[1] > px[1] && brighter[2] > px[2]);
});

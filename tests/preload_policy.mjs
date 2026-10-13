/**
 * The rules for fetching ahead (static/js/preload-policy.js), without a
 * browser. Run with `node --test tests/preload_policy.mjs`
 * (tests/test_preload_native_feel.py does, when Node is installed).
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  AdaptivePreloader, connectionProfile, interleaveDirectional, predictionMs,
} from '../ninaivu_lite/static/js/preload-policy.js';

test('a lean link fetches a screen and a half ahead, one at a time, nothing behind', () => {
  for (const connection of [{ saveData: true }, { effectiveType: '2g' }, { effectiveType: 'slow-2g' }]) {
    const p = connectionProfile({ connection, deviceMemory: 8, touch: false });
    assert.equal(p.lean, true);
    assert.equal(p.aheadScreens, 1.5);
    assert.equal(p.behindScreens, 0);
    assert.equal(p.concurrency, 1);
  }
});

test('a computer fetches four at a time, a phone two, a small device less', () => {
  const desk = connectionProfile({ connection: {}, deviceMemory: 8, touch: false });
  const phone = connectionProfile({ connection: { effectiveType: '4g' }, deviceMemory: 4, touch: true });
  const small = connectionProfile({ connection: {}, deviceMemory: 2, touch: false });
  assert.equal(desk.concurrency, 4);
  assert.equal(phone.concurrency, 2);
  assert.equal(small.lowMemory, true);
  assert.ok(small.concurrency <= 2 && small.aheadScreens < desk.aheadScreens);
});

test('slower scrolls look further ahead in time, a fling not at all', () => {
  assert.equal(predictionMs(0.2), 1400);
  assert.equal(predictionMs(1), 900);
  assert.equal(predictionMs(-2), 600);
  assert.equal(predictionMs(4), 450);
  assert.equal(predictionMs(5), 0);
  assert.equal(predictionMs(-12), 0);
});

test('two ahead for every one behind, nearest first', () => {
  assert.deepEqual(interleaveDirectional([1, 2, 3, 4, 5], ['a', 'b', 'c']),
    [1, 2, 'a', 3, 4, 'b', 5, 'c']);
  assert.deepEqual(interleaveDirectional([], ['a']), ['a']);
});

/** A fetch that answers only when told to, and remembers what was stopped. */
function fakeFetch() {
  const calls = new Map();
  globalThis.fetch = (url, init) => new Promise((resolve, reject) => {
    const call = { url, init, aborted: false };
    calls.set(url, call);
    init.signal.addEventListener('abort', () => {
      call.aborted = true;
      reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
    });
    call.answer = () => resolve({ ok: true, blob: async () => new Uint8Array(1) });
  });
  return calls;
}

const tick = () => new Promise((r) => setTimeout(r, 0));
const profile = (concurrency = 2) => () => ({ concurrency, lean: false });

test('fetches into the cache a few at a time, each once', async () => {
  const calls = fakeFetch();
  const p = new AdaptivePreloader({ profile: profile(2) });
  p.want(['a', 'b', 'c', 'a']);
  assert.deepEqual([...calls.keys()], ['a', 'b']);
  assert.equal(calls.get('a').init.cache, 'force-cache');
  calls.get('a').answer();
  await tick(); await tick();
  assert.ok(p.has('a'));
  assert.deepEqual([...calls.keys()], ['a', 'b', 'c']);
  p.want(['a', 'c']);                      // done and on its way: neither asked again
  assert.equal(calls.size, 3);
});

test('what is on its way carries on unless called off, and an old one is let finish', async () => {
  const calls = fakeFetch();
  const p = new AdaptivePreloader({ profile: profile(3) });
  p.want(['a', 'b']);
  p.want(['c']);                           // no cancel: a and b carry on
  assert.ok(!calls.get('a').aborted && !calls.get('b').aborted);
  p.want(['c'], { cancel: true, keep: new Set(['b']) });   // young, unwanted, not kept: stopped
  assert.ok(calls.get('a').aborted && !calls.get('b').aborted);
  p.active.get('b').startedAt -= 1000;     // most of the way there
  p.want([], { cancel: true });
  assert.ok(!calls.get('b').aborted);
  p.want([], { cancel: 'all' });           // a photo opened: everything stops
  assert.ok(calls.get('b').aborted && calls.get('c').aborted);
  await tick();
  assert.equal(p.active.size, 0);
});

test('nothing starts while the page says it is busy', () => {
  const calls = fakeFetch();
  let idle = false;
  const p = new AdaptivePreloader({ idle: () => idle, profile: profile(2) });
  p.want(['a']);
  assert.equal(calls.size, 0);
  idle = true;
  p.pump();
  assert.equal(calls.size, 1);
});

test('a Set in place of the options is what to keep, with cancelling', () => {
  const calls = fakeFetch();
  const p = new AdaptivePreloader({ profile: profile(3) });
  p.want(['a', 'b']);
  p.want(['c'], new Set(['a']));
  assert.ok(!calls.get('a').aborted && calls.get('b').aborted);
});

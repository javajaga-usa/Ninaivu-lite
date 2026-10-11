// Correctness only (eslint:recommended): names that are not defined, code that
// cannot run as written. No rules about how the scripts are laid out.
import js from '@eslint/js';
import globals from 'globals';

export default [
  js.configs.recommended,
  {
    files: ['ninaivu_lite/static/**/*.{js,mjs}'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: globals.browser,
    },
    rules: {
      // A catch that quietly gives up (private mode, an old browser) is meant.
      'no-empty': ['error', { allowEmptyCatch: true }],
      // Said, not failed: an unused name is tidiness, not a fault.
      'no-unused-vars': ['warn', { args: 'none', caughtErrors: 'none' }],
      // New in @eslint/js 10's recommended set: a starting value later replaced
      // is tidiness too, not a fault.
      'no-useless-assignment': 'warn',
    },
  },
  // Sudar's drawing runs in a worker.
  {
    files: ['ninaivu_lite/static/js/sudar/worker.mjs'],
    languageOptions: { globals: globals.worker },
  },
  // Plain scripts, not modules: the service worker, and the old-browser note
  // (written for browsers that cannot read anything newer).
  {
    files: ['ninaivu_lite/static/sw.js'],
    languageOptions: { sourceType: 'script', globals: globals.serviceworker },
  },
  {
    files: ['ninaivu_lite/static/js/old-browser.js'],
    languageOptions: { ecmaVersion: 5, sourceType: 'script' },
  },
];

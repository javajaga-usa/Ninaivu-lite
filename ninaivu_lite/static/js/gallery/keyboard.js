/** The gallery's keys. */

import { cycleTheme, setLayout } from '../app.js';
import { closeMenus } from './menus.js';
import { $, grid, jump, store, viewer } from './state.js';

export function wireKeyboard() {
  document.addEventListener('keydown', (event) => {
    // While the sign-in screen is up, the gallery behind it is nobody's: no
    // theme key, no layout keys, no arrows through a photograph.
    if (!$('#gate').hidden) return;
    // A175: a modal <dialog> (Sudar, the recolour dialog) owns the keyboard.
    // Without this the viewer behind Sudar took the arrows, F, D and Escape
    // (and T cycled the theme twice).
    if (document.querySelector('dialog[open]')) return;
    const target = event.target;
    const typing = target.matches('input, textarea, select') || target.isContentEditable;
    const key = event.key.toLowerCase();
    if (!typing && key === 't' && !event.altKey && !event.ctrlKey && !event.metaKey) {
      cycleTheme();
      return;
    }
    if (event.defaultPrevented) return;
    if (event.key === 'Escape' && closeMenus()) return;
    // The sidebar drawer of a narrow window (a phone, a small Pi screen):
    // Escape shuts it like any other menu, and the focus goes back to its button.
    if (event.key === 'Escape' && document.querySelector('.shell.mobile-open')) {
      document.querySelector('.shell').classList.remove('mobile-open');
      $('#sidebar-toggle')?.setAttribute('aria-expanded', 'false');
      $('#sidebar-toggle')?.focus();
      return;
    }
    if (event.key === 'Escape' && $('#topbar-more').classList.contains('open')) {
      $('#topbar-more').classList.remove('open');
      $('#more-btn').setAttribute('aria-expanded', 'false');
      return;
    }
    if (event.key === 'Escape' && $('#viewer-tools-more').classList.contains('open')) {
      viewer.closeMoreMenu(true);
      return;
    }
    if ((event.key === 'Enter' || event.key === ' ') && target.closest('button, a, [role="button"]')) return;
    const overlay = [...document.querySelectorAll('.modal, .sheet')].reverse()
      .find((m) => !m.hidden);
    if (overlay) {
      if (event.key === 'Escape') { overlay.hidden = true; event.preventDefault(); }
      return;
    }

    if (viewer.isOpen) {
      // The position bar of a turned video is a range input: Escape still
      // leaves (or puts an unsaved turn back), as with the browser's own bar.
      if (typing && !(event.key === 'Escape' && target.matches('input[type="range"]'))) return;
      if (viewer.handleKey(event)) event.preventDefault();
      return;
    }

    if (event.key === '/' && !typing) {
      event.preventDefault();
      $('#search').focus();
      $('#search').select();
      return;
    }
    if (event.key === 'Escape') {
      const overlay = [...document.querySelectorAll('.modal, .sheet')]
        .find((m) => !m.hidden);
      if (overlay) { overlay.hidden = true; return; }
      if (grid.selection.size) { grid.clearSelection(); return; }
      if (typing) target.blur();
      return;
    }
    if (typing) return;
    if (event.altKey || ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() !== 'a')) return;

    if (key === '?') { $('#help-modal').hidden = false; return; }
    if (key === 't') { cycleTheme(); return; }
    if (key === 'j') { setLayout('justified'); return; }
    if (key === 'm') { setLayout('masonry'); return; }
    if (key === 'g') { setLayout('grid'); return; }
    if (key === 'f' && !event.metaKey && !event.ctrlKey) { setLayout('film'); return; }
    if (key === 'd') { jump?.open(); return; }
    if (key === 'a' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      grid.selectAll();
      return;
    }
    if (key === '+' || key === '=') {
      $('#zoom').value = String(grid.zoom + 1);
      grid.setZoom(grid.zoom + 1);
      store.set('zoom', grid.zoom);
      return;
    }
    if (key === '-') {
      $('#zoom').value = String(grid.zoom - 1);
      grid.setZoom(grid.zoom - 1);
      store.set('zoom', grid.zoom);
      return;
    }

    const moves = {
      arrowright: 'next', arrowleft: 'prev', arrowdown: 'down', arrowup: 'up',
    };
    if (moves[key]) {
      event.preventDefault();
      // Shift takes the selection along with the cursor, the way it does in
      // Explorer and in Photos.
      grid.moveCursor(moves[key], { extend: event.shiftKey });
      return;
    }
    if (event.key === 'Enter' && grid.cursor >= 0) {
      viewer.open(grid.ids, grid.cursor, grid.thumbedIds());
      return;
    }
    if (key === 'x' && grid.cursor >= 0) {
      const id = grid.currentId();
      if (grid.selection.has(id)) grid.selection.delete(id);
      else grid.selection.add(id);
      grid.emitSelection();
      grid.render();
    }
  });
}

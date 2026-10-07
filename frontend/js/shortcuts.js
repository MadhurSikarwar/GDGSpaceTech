// Keyboard shortcuts: "g" then a letter jumps to a page, "?" lists them all (Ctrl+K and "/" open the search, see palette.js).
// None of them fire while a field has the focus, and none of them take a modifier key, so the browser's own shortcuts stay.
export const GO = {
  h: ['#/', 'Home'], g: ['#/globe', 'Live globe'], d: ['#/dashboard', 'Dashboard'], c: ['#/catalog', 'Catalogue'],
  a: ['#/conjunctions', 'Close approaches'], l: ['#/demo', 'Demo lab'], i: ['#/insights', 'How it works'],
};
const WINDOW_MS = 1200;          // how long "g" waits for its second key

// the key to press for a destination, and what a second key means: pure, so it can be tested
export function destination(key, pressedAt, now = Date.now()) {
  if (!pressedAt || now - pressedAt > WINDOW_MS) return null;
  return GO[String(key).toLowerCase()] || null;
}

let overlay = null;
let returnTo = null;

const keycaps = (...keys) => keys.map((k) => `<kbd>${k}</kbd>`).join(' ');
const row = (keys, what) => `<div class="krow"><span>${what}</span><span>${keys}</span></div>`;

export function toggleKeys() {
  if (overlay) { closeKeys(); return; }
  returnTo = document.activeElement;
  const el = document.createElement('div');
  el.className = 'pal-scrim';
  el.innerHTML = `<div class="pal keys" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts" tabindex="-1">
    <div class="pal-head"><b class="keys-title">Keyboard shortcuts</b><kbd>Esc</kbd></div>
    <div class="keys-body">
      <h4>Anywhere</h4>
      ${row(`${keycaps('Ctrl', 'K')} or ${keycaps('/')}`, 'Search objects, pages and terms')}
      ${row(keycaps('?'), 'This list')}
      <h4>Go to</h4>
      ${Object.entries(GO).map(([k, [, label]]) => row(`${keycaps('g')} then ${keycaps(k)}`, label)).join('')}
      <h4>On the globe</h4>
      ${row('drag · scroll', 'Turn and zoom the Earth')}
      ${row('click a point', 'Select an object')}
      ${row('time slider', 'A day back or forward')}
    </div></div>`;
  overlay = el;
  document.body.appendChild(el);
  el.addEventListener('mousedown', (e) => { if (e.target === el) closeKeys(); });
  el.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.stopPropagation(); closeKeys(); } });
  el.offsetWidth;                         // a layout pass, so the transition into .on runs
  el.classList.add('on');
  el.querySelector('.keys').focus();
}

export function closeKeys() {
  if (!overlay) return;
  const gone = overlay;
  overlay = null;
  gone.classList.remove('on');
  setTimeout(() => gone.remove(), 180);
  if (returnTo && returnTo.isConnected) returnTo.focus({ preventScroll: true });
}

export function initShortcuts() {
  let pressedAt = 0;
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName) || e.target.isContentEditable) return;
    if (e.key === '?') { e.preventDefault(); toggleKeys(); return; }
    if (overlay || document.querySelector('.pal-scrim')) return;
    const to = destination(e.key, pressedAt);
    pressedAt = 0;
    if (to) { e.preventDefault(); location.hash = to[0]; return; }
    if (e.key === 'g') pressedAt = Date.now();
  });
}

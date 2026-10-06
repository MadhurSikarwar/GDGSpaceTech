// The landing page's instrument layer: corner brackets, a chapter rail that doubles as the scroll progress bar, and a
// telemetry column. Everything it shows is real: the camera numbers are the pose being drawn, the object count and the
// countdown come from /api/landing. It is decoration around the globe, so it takes no pointer events except on the
// chapter notches (which scroll to a chapter). A boot log in the corner reports the page's real loading steps.
import { fmt } from './ui.js';

const pad = (n) => String(n).padStart(2, '0');

// "T−03:12:08" before an event, "T+00:04:10" after it (days are shown once it is more than a day away).
export function countdown(ms) {
  const s = Math.floor(Math.abs(ms) / 1000);
  const days = Math.floor(s / 86400);
  const hms = `${pad(Math.floor((s % 86400) / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
  return `T${ms >= 0 ? '−' : '+'}${days ? `${days}d ` : ''}${hms}`;
}

export const latLabel = (v) => `${Math.abs(v).toFixed(1)}°${v >= 0 ? 'N' : 'S'}`;
export const lonLabel = (v) => {
  const w = ((v + 540) % 360) - 180;
  return `${Math.abs(w).toFixed(1)}°${w >= 0 ? 'E' : 'W'}`;
};

const utcMs = (iso) => new Date(iso.endsWith('Z') ? iso : `${iso}Z`).getTime();

// labels: one per chapter; objects: how many are tracked; next: the next close approach (or null);
// onJump(i): scroll to chapter i.
export function mountHud(root, { labels, objects, next, onJump }) {
  const el = document.createElement('div');
  el.className = 'hud';
  el.innerHTML = `
    <i class="hud-c tl"></i><i class="hud-c tr"></i><i class="hud-c bl"></i><i class="hud-c br"></i>
    <nav class="hud-rail" aria-label="Chapters"><span class="hud-line"><i class="hud-fill"></i></span>
      ${labels.map((l, i) => `<button type="button" class="hud-notch" data-i="${i}" aria-label="Chapter ${i}: ${l}"><span>${pad(i)} · ${l}</span></button>`).join('')}
    </nav>
    <dl class="hud-tel" aria-hidden="true">
      <div><dt>Range</dt><dd data-k="range">—</dd></div>
      <div><dt>Lat</dt><dd data-k="lat">—</dd></div>
      <div><dt>Lon</dt><dd data-k="lon">—</dd></div>
      <div class="rule"></div>
      <div><dt>Tracked</dt><dd>${fmt.int(objects)}</dd></div>
      <div><dt>Next close approach</dt><dd data-k="tca">—</dd><dd class="sub" data-k="sub"></dd></div>
    </dl>`;
  root.append(el);
  const cell = (k) => el.querySelector(`[data-k="${k}"]`);
  const notches = [...el.querySelectorAll('.hud-notch')];
  const fill = el.querySelector('.hud-fill');
  const shown = {};
  const put = (k, text) => { if (shown[k] !== text) { shown[k] = text; cell(k).textContent = text; } };
  const nextAt = next ? utcMs(next.time_of_closest_approach) : null;
  if (next) {
    put('sub', `${next.risk_level} · ${fmt.num(next.miss_distance_km, 1)} km`);
    cell('sub').dataset.risk = next.risk_level;
  }
  el.addEventListener('click', (e) => {
    const b = e.target.closest('.hud-notch');
    if (b) onJump(Number(b.dataset.i));
  });
  const tick = () => put('tca', nextAt ? countdown(nextAt - Date.now()) : '—');
  tick();
  return {
    el,
    tick,
    // the pose being drawn (range in metres, lat/lon in degrees) and the scroll position in chapters (0 .. n-1)
    update({ range, lat, lon, progress }) {
      put('range', `${fmt.int(range / 1000)} km`);
      put('lat', latLabel(lat));
      put('lon', lonLabel(lon));
      fill.style.transform = `scaleY(${Math.min(Math.max(progress / (labels.length - 1), 0), 1).toFixed(3)})`;
      const active = Math.round(progress);
      notches.forEach((n, i) => {
        const on = i === active;
        if (n.classList.contains('on') === on) return;
        n.classList.toggle('on', on);
        if (on) n.setAttribute('aria-current', 'step'); else n.removeAttribute('aria-current');
      });
    },
    hide(flag) { el.classList.toggle('off', flag); },
  };
}

// A short log of the page's real loading steps (each line turns on when that step actually finishes). It sits in
// the empty top-left of the hero, never covers anything and removes itself.
export function bootLog(root, steps) {
  const el = document.createElement('div');
  el.className = 'boot';
  el.setAttribute('aria-hidden', 'true');
  el.innerHTML = `<div class="b-head"><i></i><span>Establishing link</span></div>`
    + steps.map(([key, label]) => `<div class="b-row" data-k="${key}"><span>${label}</span><i></i><b>…</b></div>`).join('');
  root.append(el);
  let pending = steps.length;
  const head = el.querySelector('.b-head span');
  const leave = () => { el.classList.add('out'); setTimeout(() => el.remove(), 900); };
  const fallback = setTimeout(leave, 12000);
  return {
    done(key, value) {
      const row = el.querySelector(`[data-k="${key}"]`);
      if (!row || row.classList.contains('ok')) return;
      row.classList.add('ok');
      row.querySelector('b').textContent = value;
      if (--pending === 0) {
        head.textContent = 'Link established';
        el.classList.add('up');
        clearTimeout(fallback);
        setTimeout(leave, 2200);
      }
    },
  };
}

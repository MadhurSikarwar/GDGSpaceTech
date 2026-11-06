// Save on-screen pictures as files: SVG diagrams as .svg or .png (Database maps, watchlist timelines, the dashboard
// radar), Chart.js charts and the 3D globe as .png. The diagrams are styled by the site's stylesheet and its colour
// variables, which a saved file would not have, so the computed style of every element is written onto the copy, with
// the page background behind it. The web fonts the picture uses are embedded (fetched from Google Fonts, which the page
// loads them from anyway), so the file looks the same on a computer without them; offline it falls back to system
// fonts. Nothing is uploaded anywhere.
const PROPS = ['fill', 'fill-opacity', 'stroke', 'stroke-width', 'stroke-opacity', 'stroke-dasharray', 'stroke-linecap',
  'stroke-linejoin', 'opacity', 'font-family', 'font-size', 'font-weight', 'font-style', 'font-stretch', 'letter-spacing',
  'text-anchor', 'dominant-baseline', 'visibility', 'display', 'paint-order'];
const NS = 'http://www.w3.org/2000/svg';

function backgroundOf(el) {
  let host = el;
  let bg = 'rgba(0, 0, 0, 0)';
  while (host && /rgba\(0, 0, 0, 0\)|transparent/.test(bg)) { bg = getComputedStyle(host).backgroundColor; host = host.parentElement; }
  return /rgba\(0, 0, 0, 0\)|transparent/.test(bg) ? '#0b0f14' : bg;
}

// The copy with styles inlined, plus what it needs from the fonts. Synchronous: it reads the live page's styles.
function standalone(svg) {
  const copy = svg.cloneNode(true);
  const live = [svg, ...svg.querySelectorAll('*')];
  const dead = [copy, ...copy.querySelectorAll('*')];
  const fonts = new Map();             // family -> set of weights used
  live.forEach((node, i) => {
    const cs = getComputedStyle(node);
    const out = dead[i];
    out.setAttribute('style', PROPS.map((p) => `${p}:${cs.getPropertyValue(p)}`).join(';'));
    out.removeAttribute('class');
    out.removeAttribute('tabindex');
    if (node.tagName.toLowerCase() === 'text' || node.tagName.toLowerCase() === 'tspan') {
      const family = cs.fontFamily.split(',')[0].trim().replace(/^["']|["']$/g, '');
      if (!fonts.has(family)) fonts.set(family, new Set());
      fonts.get(family).add(Number(cs.fontWeight) || 400);
    }
  });
  const vb = svg.viewBox.baseVal;
  const w = vb && vb.width ? vb.width : svg.clientWidth;
  const h = vb && vb.height ? vb.height : svg.clientHeight;
  copy.setAttribute('xmlns', NS);
  copy.setAttribute('width', String(w));
  copy.setAttribute('height', String(h));
  copy.style.position = '';
  copy.style.left = '';
  const rect = document.createElementNS(NS, 'rect');
  rect.setAttribute('x', String(vb?.x || 0));
  rect.setAttribute('y', String(vb?.y || 0));
  rect.setAttribute('width', String(w));
  rect.setAttribute('height', String(h));
  rect.setAttribute('fill', backgroundOf(svg.parentElement));
  copy.insertBefore(rect, copy.firstChild);
  return { copy, w, h, fonts };
}

// ---- web fonts ---------------------------------------------------------------------------------------------------------
let fontCss = null;
const fontData = new Map();
const toBase64 = (buf) => {
  const bytes = new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
};

// @font-face blocks (with the font file inlined) for the families and weights the picture uses, Latin subset only.
export function pickFontFaces(css, fonts) {
  return (css.match(/@font-face\s*{[^}]*}/g) || []).filter((b) => {
    const family = /font-family:\s*['"]?([^;'"]+)/.exec(b)?.[1];
    if (!family || !fonts.has(family)) return false;
    const range = /unicode-range:\s*([^;]+)/.exec(b)?.[1];
    if (range && !/U\+0000-00FF/i.test(range)) return false;
    const stretch = /font-stretch:\s*([^;]+)/.exec(b)?.[1]?.trim();
    if (stretch && !/100%/.test(stretch) && !/^\d+%\s+\d+%$/.test(stretch)) return false;
    const [lo, hi = lo] = (/font-weight:\s*([^;]+)/.exec(b)?.[1] || '400').trim().split(/\s+/).map(Number);
    return [...fonts.get(family)].some((wt) => wt >= lo && wt <= hi);
  });
}

async function fontStyle(fonts) {
  if (!fonts.size) return '';
  const link = document.querySelector('link[href*="fonts.googleapis.com/css"]');
  if (!link) return '';
  try {
    fontCss ??= fetch(link.href).then((r) => (r.ok ? r.text() : ''));
    const blocks = pickFontFaces(await fontCss, fonts);
    const inlined = await Promise.all(blocks.map(async (b) => {
      const m = /url\((https:[^)]+)\)\s*format\(['"]?([\w-]+)/.exec(b);
      if (!m) return '';
      if (!fontData.has(m[1])) fontData.set(m[1], fetch(m[1]).then((r) => r.arrayBuffer()).then(toBase64));
      const mime = m[2] === 'woff2' ? 'font/woff2' : m[2] === 'woff' ? 'font/woff' : 'font/ttf';
      return b.replace(m[1], `data:${mime};base64,${await fontData.get(m[1])}`);
    }));
    return inlined.join('\n');
  } catch {
    return '';                        // offline: the file uses system fonts instead
  }
}

async function serialise(svg) {
  const { copy, w, h, fonts } = standalone(svg);
  const css = await fontStyle(fonts);
  if (css) {
    const style = document.createElementNS(NS, 'style');
    style.textContent = css;
    copy.insertBefore(style, copy.firstChild);
  }
  return { text: new XMLSerializer().serializeToString(copy), w, h };
}

// ---- saving ------------------------------------------------------------------------------------------------------------
function save(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

const canvasBlob = (canvas) => new Promise((resolve, reject) => {
  canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('PNG export failed'))), 'image/png');
});

export async function exportSvg(svg, filename) {
  const { text } = await serialise(svg);
  save(new Blob([text], { type: 'image/svg+xml' }), `${filename}.svg`);
}

// PNG at twice the drawing size (sharp in a printed report).
export async function exportPng(svg, filename, scale = 2) {
  const { text, w, h } = await serialise(svg);
  const img = new Image();
  const url = URL.createObjectURL(new Blob([text], { type: 'image/svg+xml' }));
  try {
    await new Promise((resolve, reject) => { img.onload = resolve; img.onerror = () => reject(new Error('PNG export failed')); img.src = url; });
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(w * scale);
    canvas.height = Math.round(h * scale);
    const ctx = canvas.getContext('2d');
    ctx.scale(scale, scale);
    ctx.drawImage(img, 0, 0, w, h);
    save(await canvasBlob(canvas), `${filename}.png`);
  } finally { URL.revokeObjectURL(url); }
}

// A <canvas> (a Chart.js chart, the globe) as PNG on its card's background: chart canvases are transparent.
export async function exportCanvasPng(canvas, filename) {
  const out = document.createElement('canvas');
  out.width = canvas.width;
  out.height = canvas.height;
  const ctx = out.getContext('2d');
  ctx.fillStyle = backgroundOf(canvas.parentElement);
  ctx.fillRect(0, 0, out.width, out.height);
  ctx.drawImage(canvas, 0, 0);
  save(await canvasBlob(out), `${filename}.png`);
}

export const slug = (text) => `orbitwatch-${String(text || 'picture').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'picture'}`;

// "Save image" on any card holding a chart or diagram (the button is added by charts.js and by the pages): the card's
// own title names the file.
export async function saveCardPicture(card) {
  const title = card.querySelector('.card-head h2, .card-head h3')?.textContent;
  const canvas = card.querySelector('canvas');
  const svg = card.querySelector('svg[viewBox]:not(.icon)');
  if (canvas) return exportCanvasPng(canvas, slug(title));
  if (svg) return exportPng(svg, slug(title));
  throw new Error('Nothing to save on this card');
}

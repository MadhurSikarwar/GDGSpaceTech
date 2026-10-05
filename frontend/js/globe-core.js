// Shared CesiumJS plumbing for the landing hero and the interactive globe page.
//   * a dark, desaturated Earth with a latitude/longitude graticule and real
//     sun lighting (the day/night terminator is where it is right now),
//   * the whole catalogue as a point cloud whose motion is interpolated
//     between two server-side SGP4 snapshots, so ~20k objects move smoothly.
import { get } from './api.js';

export const CESIUM_VERSION = '1.146.0';
const BASE = `https://cdn.jsdelivr.net/npm/cesium@${CESIUM_VERSION}/Build/Cesium/`;
export const TYPE_NAMES = ['Payload', 'Rocket Body', 'Debris', 'Unknown'];
// Categorical slots stepped for a dark surface (validated all-pairs).
export const TYPE_COLORS = ['#3987e5', '#d95926', '#199e70', '#8d8c86'];
export const RISK_COLORS = { LOW: '#0ca30c', MEDIUM: '#fab219', HIGH: '#ec835a', CRITICAL: '#d03b3b' };

let cesiumPromise;
export function loadCesium() {
  if (window.Cesium) return Promise.resolve(window.Cesium);
  if (!cesiumPromise) {
    cesiumPromise = new Promise((resolve, reject) => {
      window.CESIUM_BASE_URL = BASE;
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = `${BASE}Widgets/widgets.css`;
      document.head.appendChild(link);
      const s = document.createElement('script');
      s.src = `${BASE}Cesium.js`;
      s.onload = () => resolve(window.Cesium);
      s.onerror = () => { cesiumPromise = null; reject(new Error('Could not load CesiumJS from the CDN (needs an internet connection).')); };
      document.head.appendChild(s);
    });
  }
  return cesiumPromise;
}

export function createViewer(C, container, { widgets = true, interactive = true } = {}) {
  const viewer = new C.Viewer(container, {
    baseLayer: false, baseLayerPicker: false, geocoder: false, homeButton: widgets, sceneModePicker: false,
    navigationHelpButton: false, fullscreenButton: false, infoBox: false, selectionIndicator: false,
    animation: widgets, timeline: widgets, shouldAnimate: true, scene3DOnly: true,
  });
  const base = C.ImageryLayer.fromProviderAsync(
    C.TileMapServiceImageryProvider.fromUrl(C.buildModuleUrl('Assets/Textures/NaturalEarthII')));
  Object.assign(base, { brightness: 0.62, contrast: 1.2, saturation: 0.38, gamma: 1.05 });
  viewer.imageryLayers.add(base);
  viewer.imageryLayers.addImageryProvider(new C.GridImageryProvider({
    cells: 2, color: C.Color.fromCssColorString('#a9c8ea').withAlpha(0.13), glowWidth: 0,
    glowColor: C.Color.TRANSPARENT, backgroundColor: C.Color.TRANSPARENT,
  }));
  const { scene } = viewer;
  scene.globe.enableLighting = true;            // real sun position: the terminator is where it is now
  scene.globe.dynamicAtmosphereLighting = true;
  scene.globe.showGroundAtmosphere = true;
  scene.globe.baseColor = C.Color.fromCssColorString('#04070b');
  scene.backgroundColor = C.Color.BLACK;
  scene.fog.enabled = false;
  if (!interactive) {
    scene.screenSpaceCameraController.enableInputs = false;
    viewer.canvas.style.pointerEvents = 'none';
  }
  return viewer;
}

export class CatalogCloud {
  constructor(C, viewer, { span = 60, pixelSize = 2.5, onLoad } = {}) {
    this.C = C;
    this.viewer = viewer;
    this.span = span;
    this.pixelSize = pixelSize;
    this.onLoad = onLoad;
    this.points = viewer.scene.primitives.add(new C.PointPrimitiveCollection());
    this.colors = TYPE_COLORS.map((c) => C.Color.fromCssColorString(c));
    this.records = new Map();
    this.order = [];
    this.index = new Map();
    this.visible = new Set([0, 1, 2, 3]);
    this.counts = [0, 0, 0, 0];
    this.t0 = null;
    this.fetching = false;
    this.error = null;
    this.scratch = new C.Cartesian3();
    this.remove = viewer.scene.preUpdate.addEventListener((scene, time) => this.tick(time));
  }

  async load(time) {
    if (this.fetching) return;
    this.fetching = true;
    const C = this.C;
    try {
      const iso = C.JulianDate.toDate(time).toISOString();
      const d = await get(`/visual/positions?t=${encodeURIComponent(iso)}&span=${this.span}`);
      if (this.destroyed) return;
      this.p0 = Float64Array.from(d.pos);
      this.p1 = Float64Array.from(d.pos2 || d.pos);
      this.t0 = C.JulianDate.fromIso8601(d.t);
      this.counts.fill(0);
      this.order = new Array(d.ids.length);
      this.index = new Map();
      const seen = new Set();
      for (let i = 0; i < d.ids.length; i++) {
        const id = d.ids[i];
        const type = d.types[i];
        let rec = this.records.get(id);
        if (!rec) {
          const p = new C.Cartesian3(this.p0[3 * i] * 1000, this.p0[3 * i + 1] * 1000, this.p0[3 * i + 2] * 1000);
          rec = { id, type, point: this.points.add({ position: p, color: this.colors[type], pixelSize: this.pixelSize, id }) };
          this.records.set(id, rec);
        }
        rec.type = type;
        rec.live = true;
        rec.point.show = this.visible.has(type);
        this.order[i] = rec;
        this.index.set(id, i);
        this.counts[type] += 1;
        seen.add(id);
      }
      for (const rec of this.records.values()) {
        if (!seen.has(rec.id)) { rec.live = false; rec.point.show = false; }
      }
      this.error = null;
      if (this.onLoad) this.onLoad(this);
    } catch (err) {
      this.error = err;
    } finally {
      this.fetching = false;
    }
  }

  fraction(time) {
    const dt = this.C.JulianDate.secondsDifference(time, this.t0);
    return { dt, f: Math.min(Math.max(dt / this.span, 0), 1) };
  }

  tick(time) {
    if (!this.t0) { if (!this.fetching && !this.error) this.load(time); return; }
    const { dt, f } = this.fraction(time);
    if ((dt > this.span * 0.7 || dt < 0) && !this.fetching) this.load(time);
    for (let i = 0; i < this.order.length; i++) {
      const rec = this.order[i];
      if (!rec.point.show) continue;
      rec.point.position = this.interpolate(i, f, this.scratch);
    }
  }

  // A straight line between two snapshots cuts inside the curved orbit (tens of km
  // for LEO over a few minutes), so the radius is interpolated separately: the
  // direction comes from the chord, the distance from Earth's centre stays exact.
  interpolate(i, f, out) {
    const { p0, p1 } = this;
    const k = 3 * i;
    const x = p0[k] + (p1[k] - p0[k]) * f;
    const y = p0[k + 1] + (p1[k + 1] - p0[k + 1]) * f;
    const z = p0[k + 2] + (p1[k + 2] - p0[k + 2]) * f;
    const r0 = Math.hypot(p0[k], p0[k + 1], p0[k + 2]);
    const r1 = Math.hypot(p1[k], p1[k + 1], p1[k + 2]);
    const scale = ((r0 + (r1 - r0) * f) / (Math.hypot(x, y, z) || 1)) * 1000;
    out.x = x * scale;
    out.y = y * scale;
    out.z = z * scale;
    return out;
  }

  // Interpolated Earth-fixed position (metres) of one object at a time.
  positionOf(id, time) {
    const i = this.index.get(id);
    if (i === undefined || !this.t0) return null;
    return this.interpolate(i, this.fraction(time).f, new this.C.Cartesian3());
  }

  setTypeVisible(type, on) {
    if (on) this.visible.add(type); else this.visible.delete(type);
    for (const rec of this.records.values()) rec.point.show = rec.live && this.visible.has(rec.type);
  }

  highlight(id, on) {
    const rec = this.records.get(id);
    if (!rec) return null;
    rec.point.pixelSize = on ? 9 : this.pixelSize;
    rec.point.outlineColor = this.C.Color.WHITE;
    rec.point.outlineWidth = on ? 2 : 0;
    return rec;
  }

  destroy() {
    this.destroyed = true;
    this.remove();
    if (!this.viewer.isDestroyed()) this.viewer.scene.primitives.remove(this.points);
  }
}

// A ring drawn on a canvas, used as a pulsing billboard.
export function ringCanvas(color, size = 64, width = 3) {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d');
  g.strokeStyle = color;
  g.lineWidth = width;
  g.beginPath();
  g.arc(size / 2, size / 2, size / 2 - width, 0, Math.PI * 2);
  g.stroke();
  return c;
}

// Sampled position of one object over its next orbit(s), for a moving point plus its glowing track.
export async function sampledOrbit(C, norad, time, periods = 2) {
  const iso = C.JulianDate.toDate(time).toISOString();
  const o = await get(`/visual/orbit/${norad}?t=${encodeURIComponent(iso)}&periods=${periods}`);
  const start = C.JulianDate.fromIso8601(o.t);
  const prop = new C.SampledPositionProperty();
  prop.setInterpolationOptions({ interpolationDegree: 5, interpolationAlgorithm: C.LagrangePolynomialApproximation });
  o.offsets_s.forEach((s, i) => {
    const [x, y, z] = o.ecef_km[i];
    prop.addSample(C.JulianDate.addSeconds(start, s, new C.JulianDate()), new C.Cartesian3(x * 1000, y * 1000, z * 1000));
  });
  return { prop, start, periodS: o.period_min * 60, end: C.JulianDate.addSeconds(start, o.offsets_s[o.offsets_s.length - 1], new C.JulianDate()) };
}

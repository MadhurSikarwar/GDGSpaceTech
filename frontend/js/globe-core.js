// Shared CesiumJS plumbing for the landing hero and the interactive globe page.
//   * Earth: NASA GIBS Blue Marble by day, VIIRS Black Marble city lights on the night side, with real
//     sun lighting (the terminator is where it is right now); the bundled Natural Earth imagery is the
//     fallback when GIBS cannot be reached. A faint latitude/longitude graticule.
//   * the whole catalogue as a point cloud whose motion is interpolated between two server-side SGP4
//     snapshots, so tens of thousands of objects move smoothly; points scale with camera distance.
//   * filters by object type and orbital regime (from each object's radius).
import { get } from './api.js';

export const CESIUM_VERSION = '1.146.0';
const BASE = `https://cdn.jsdelivr.net/npm/cesium@${CESIUM_VERSION}/Build/Cesium/`;
export const TYPE_NAMES = ['Payload', 'Rocket Body', 'Debris', 'Unknown'];
// Categorical slots stepped for a dark surface (validated all-pairs).
export const TYPE_COLORS = ['#3987e5', '#d95926', '#199e70', '#8d8c86'];
export const RISK_COLORS = { LOW: '#0ca30c', MEDIUM: '#fab219', HIGH: '#ec835a', CRITICAL: '#d03b3b' };
export const ACCENT = '#3fb6cf';
export const SYN_COLOR = '#e3a33b';
export const REGIMES = [['LEO', 0, 2000], ['MEO', 2000, 35586], ['GEO', 35586, 35986], ['HEO+', 35986, Infinity]];
const EARTH_R = 6371.0;

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

// NASA GIBS in Web Mercator (GoogleMapsCompatible): its tile grid is the one Cesium's WMTS provider
// assumes. (The EPSG:4326 grid has 288-degree top-level tiles, which smeared Antarctica across the globe.)
// Web Mercator stops at +/-85 degrees; the bundled Natural Earth layer underneath fills the polar caps.
const GIBS = 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best';
function gibsLayer(C, layer, date, ext) {
  return new C.WebMapTileServiceImageryProvider({
    url: `${GIBS}/${layer}/default/${date}/GoogleMapsCompatible_Level8/{TileMatrix}/{TileRow}/{TileCol}.${ext}`,
    layer, style: 'default', format: `image/${ext === 'jpg' ? 'jpeg' : ext}`, tileMatrixSetID: 'GoogleMapsCompatible_Level8',
    maximumLevel: 8, tileWidth: 256, tileHeight: 256, tilingScheme: new C.WebMercatorTilingScheme(),
    credit: new C.Credit('NASA GIBS: Blue Marble, VIIRS Black Marble'),
  });
}

export function createViewer(C, container, { interactive = true, creditContainer } = {}) {
  // No clock dial, timeline or home button: OrbitWatch draws its own compact time and camera controls.
  const viewer = new C.Viewer(container, {
    baseLayer: false, baseLayerPicker: false, geocoder: false, homeButton: false, sceneModePicker: false,
    navigationHelpButton: false, fullscreenButton: false, infoBox: false, selectionIndicator: false,
    animation: false, timeline: false, shouldAnimate: true, scene3DOnly: true, requestRenderMode: false,
    msaaSamples: 4, creditContainer: creditContainer || undefined,
  });
  // fallback first: bundled imagery, always available with the CesiumJS files (and the polar caps)
  const fallback = C.ImageryLayer.fromProviderAsync(
    C.TileMapServiceImageryProvider.fromUrl(C.buildModuleUrl('Assets/Textures/NaturalEarthII')));
  Object.assign(fallback, { brightness: 0.85, contrast: 1.05, saturation: 0.75 });
  viewer.imageryLayers.add(fallback);
  try {
    const day = viewer.imageryLayers.addImageryProvider(gibsLayer(C, 'BlueMarble_ShadedRelief_Bathymetry', '2004-08-01', 'jpeg'));
    Object.assign(day, { brightness: 0.96, contrast: 1.06, saturation: 0.92, gamma: 1.0, dayAlpha: 1.0, nightAlpha: 0.0 });
    const night = viewer.imageryLayers.addImageryProvider(gibsLayer(C, 'VIIRS_Black_Marble', '2016-01-01', 'png'));
    Object.assign(night, { dayAlpha: 0.0, nightAlpha: 1.0, brightness: 1.5, contrast: 1.1, saturation: 0.75 });
    // if NASA GIBS is unreachable its tiles never arrive and the Natural Earth fallback underneath shows
  } catch { /* GIBS unavailable: fallback imagery only */ }
  viewer.imageryLayers.addImageryProvider(new C.GridImageryProvider({
    cells: 2, color: C.Color.fromCssColorString('#a9c8ea').withAlpha(0.055), glowWidth: 0,
    glowColor: C.Color.TRANSPARENT, backgroundColor: C.Color.TRANSPARENT,
  }));
  const { scene } = viewer;
  scene.globe.enableLighting = true;            // real sun position: the terminator is where it is now
  scene.globe.dynamicAtmosphereLighting = true;
  scene.globe.dynamicAtmosphereLightingFromSun = true;
  scene.globe.showGroundAtmosphere = true;
  // Cesium lights everything when the camera is closer than lightingFadeOutDistance (default 10,000 km):
  // keep the real day/night terminator and the city lights at every zoom level.
  scene.globe.lightingFadeOutDistance = 1.0e3;
  scene.globe.lightingFadeInDistance = 1.0e4;
  scene.globe.baseColor = C.Color.fromCssColorString('#020409');
  scene.backgroundColor = C.Color.BLACK;
  scene.fog.enabled = false;
  scene.highDynamicRange = false;
  scene.postProcessStages.fxaa.enabled = true;
  if (scene.skyAtmosphere) { scene.skyAtmosphere.brightnessShift = -0.05; scene.skyAtmosphere.saturationShift = -0.15; }
  if (!interactive) {
    scene.screenSpaceCameraController.enableInputs = false;
    viewer.canvas.style.pointerEvents = 'none';
  }
  return viewer;
}

// Where the Sun is overhead right now (low-precision solar position, ~0.1 degree): used to open the globe
// on the sunlit hemisphere with the day/night terminator in view, instead of a fixed longitude that is
// in darkness half the day.
export function subsolarPoint(date = new Date()) {
  const d = date.getTime() / 86400000 + 2440587.5 - 2451545.0;
  const g = ((357.529 + 0.98560028 * d) * Math.PI) / 180;
  const q = 280.459 + 0.98564736 * d;
  const L = ((q + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * Math.PI) / 180;
  const e = ((23.439 - 0.00000036 * d) * Math.PI) / 180;
  const dec = Math.asin(Math.sin(e) * Math.sin(L));
  const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L));
  let lon = ((ra - gmst(date)) * 180) / Math.PI;
  lon = ((lon + 540) % 360) - 180;
  return { lat: (dec * 180) / Math.PI, lon };
}

// A camera position over the sunlit side, `offset` degrees of longitude towards the evening terminator.
export function dayView(C, height = 24_000_000, offset = 38) {
  const sun = subsolarPoint();
  const lon = ((sun.lon + offset + 540) % 360) - 180;
  return C.Cartesian3.fromDegrees(lon, Math.max(-35, Math.min(35, sun.lat * 0.6 + 12)), height);
}

// Greenwich mean sidereal time (rad), IAU-82 with UT1 = UTC: the same formula as orbitwatch/orbital.py.
export function gmst(date) {
  const jd = date.getTime() / 86400000 + 2440587.5;
  const t = (jd - 2451545.0) / 36525.0;
  const sec = 67310.54841 + (876600.0 * 3600.0 + 8640184.812866) * t + 0.093104 * t * t - 6.2e-6 * t * t * t;
  const rad = ((sec / 240.0) * Math.PI) / 180.0;
  return ((rad % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
}

// A closed orbit: one period of TEME (inertial) positions, rotated into the Earth-fixed frame at each
// frame's time. The ellipse stays closed and always passes through the object (unlike an Earth-fixed
// trace, which does not meet itself because the Earth turns underneath during the orbit).
export function orbitRing(C, temeKm) {
  const pts = temeKm.concat([temeKm[0]]);
  const out = pts.map(() => new C.Cartesian3());
  return new C.CallbackProperty((time) => {
    const g = gmst(C.JulianDate.toDate(time));
    const c = Math.cos(g);
    const s = Math.sin(g);
    for (let i = 0; i < pts.length; i++) {
      const [x, y, z] = pts[i];
      out[i].x = (c * x + s * y) * 1000;
      out[i].y = (-s * x + c * y) * 1000;
      out[i].z = z * 1000;
    }
    return out;
  }, false);
}

const regimeOf = (altKm) => (altKm < 2000 ? 0 : altKm < 35586 ? 1 : altKm < 35986 ? 2 : 3);

export class CatalogCloud {
  constructor(C, viewer, { span = 60, pixelSize = 2.5, onLoad } = {}) {
    this.C = C;
    this.viewer = viewer;
    this.span = span;
    this.pixelSize = pixelSize;
    this.onLoad = onLoad;
    // translucent blending keeps the points round and anti-aliased; distance scaling gives depth
    this.points = viewer.scene.primitives.add(new C.PointPrimitiveCollection());
    this.colors = TYPE_COLORS.map((c) => C.Color.fromCssColorString(c).withAlpha(0.92));
    this.scale = new C.NearFarScalar(1.0e6, 1.5, 5.0e7, 0.75);
    this.records = new Map();
    this.order = [];
    this.index = new Map();
    this.visible = new Set([0, 1, 2, 3]);
    this.regimes = new Set([0, 1, 2, 3]);
    this.counts = [0, 0, 0, 0];
    this.regimeCounts = [0, 0, 0, 0];
    this.t0 = null;
    this.fetching = false;
    this.error = null;
    this.scratch = new C.Cartesian3();
    this.remove = viewer.scene.preUpdate.addEventListener((scene, time) => this.tick(time));
  }

  shown(rec) { return rec.live && this.visible.has(rec.type) && this.regimes.has(rec.regime); }

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
      this.regimeCounts.fill(0);
      this.order = new Array(d.ids.length);
      this.index = new Map();
      const seen = new Set();
      for (let i = 0; i < d.ids.length; i++) {
        const id = d.ids[i];
        const type = d.types[i];
        const alt = Math.hypot(this.p0[3 * i], this.p0[3 * i + 1], this.p0[3 * i + 2]) - EARTH_R;
        let rec = this.records.get(id);
        if (!rec) {
          const p = new C.Cartesian3(this.p0[3 * i] * 1000, this.p0[3 * i + 1] * 1000, this.p0[3 * i + 2] * 1000);
          rec = { id, type, point: this.points.add({ position: p, color: this.colors[type], pixelSize: this.pixelSize, id, scaleByDistance: this.scale }) };
          this.records.set(id, rec);
        }
        rec.type = type;
        rec.regime = regimeOf(alt);
        rec.live = true;
        rec.point.show = this.shown(rec);
        this.order[i] = rec;
        this.index.set(id, i);
        this.counts[type] += 1;
        this.regimeCounts[rec.regime] += 1;
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

  refresh() { for (const rec of this.records.values()) rec.point.show = this.shown(rec); }

  setTypeVisible(type, on) {
    if (on) this.visible.add(type); else this.visible.delete(type);
    this.refresh();
  }

  setRegimeVisible(regime, on) {
    if (on) this.regimes.add(regime); else this.regimes.delete(regime);
    this.refresh();
  }

  visibleCount() { let n = 0; for (const rec of this.order) if (rec && rec.point.show) n += 1; return n; }

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

// A diamond outline (close-approach markers) or a square outline (synthetic objects).
export function shapeCanvas(color, shape = 'diamond', size = 22, width = 2) {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d');
  g.strokeStyle = color;
  g.fillStyle = color;
  g.lineWidth = width;
  const m = size / 2;
  const r = m - width;
  g.beginPath();
  if (shape === 'diamond') { g.moveTo(m, m - r); g.lineTo(m + r, m); g.lineTo(m, m + r); g.lineTo(m - r, m); g.closePath(); }
  else { g.rect(width, width, size - 2 * width, size - 2 * width); }
  g.stroke();
  g.globalAlpha = 0.25;
  g.fill();
  return c;
}

// Sampled position of one object over its next orbit(s), for a moving point plus its track.
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
  return { prop, start, periodS: o.period_min * 60, teme: o.teme_km || null,
    end: C.JulianDate.addSeconds(start, o.offsets_s[o.offsets_s.length - 1], new C.JulianDate()) };
}

// A sampled Earth-fixed track (km arrays + second offsets from a start instant).
export function sampledTrack(C, start, offsets, ecefKm) {
  const prop = new C.SampledPositionProperty();
  prop.setInterpolationOptions({ interpolationDegree: 5, interpolationAlgorithm: C.LagrangePolynomialApproximation });
  offsets.forEach((s, i) => {
    const [x, y, z] = ecefKm[i];
    prop.addSample(C.JulianDate.addSeconds(start, s, new C.JulianDate()), new C.Cartesian3(x * 1000, y * 1000, z * 1000));
  });
  return prop;
}

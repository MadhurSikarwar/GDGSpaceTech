import * as THREE from 'three';
import { stationTemeKm, stationZenithTeme } from './groundstations.js';

// Scene scale: TEME kilometers -> scene units. Earth radius maps to 2.0 units.
const EARTH_RADIUS_KM = 6378.137;
const SCENE_EARTH_RADIUS = 2.0;
const KM_TO_SCENE = SCENE_EARTH_RADIUS / EARTH_RADIUS_KM;

const TYPE_COLOR = {
  SATELLITE: 0x35c9c1,
  DEBRIS: 0xD98324,
  ROCKET_BODY: 0x555555,
  SYNTHETIC_DEBRIS: 0xC45132,
  UNKNOWN: 0xCCCCCC,
};

function lerp(a, b, t) { return a + (b - a) * t; }
function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
function easeInOutCubic(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }
function lerpAngle(a, b, t) {
  const twoPi = Math.PI * 2;
  let diff = ((b - a + Math.PI) % twoPi + twoPi) % twoPi - Math.PI;
  return a + diff * t;
}

// The whole app (state.js, ConjunctionCandidate.primary_object/secondary_object,
// and GET /objects/{id}/trajectory) joins on catalog_id -- the NORAD number or
// SYNTHETIC-99999. `object_id` is an opaque per-row UUID the DB generates, so
// keying the instanced cloud by it made every click hand back an id that no
// other part of the app could resolve. Fixtures set both fields to the same
// value, which is why this only ever broke against the live backend.
function objectKey(o) {
  return o.catalog_id ?? o.object_id;
}

function kmToScene(pos) {
  // TEME z-axis (Earth's rotation axis) -> scene Y (three.js up axis).
  return new THREE.Vector3(pos.x * KM_TO_SCENE, pos.z * KM_TO_SCENE, pos.y * KM_TO_SCENE);
}

function buildEarthTexture() {
  const w = 1024, h = 512;
  const canvas = document.createElement('canvas');
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext('2d');

  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, '#0e1722');
  grad.addColorStop(0.5, '#0a1119');
  grad.addColorStop(1, '#0e1722');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);

  const img = ctx.getImageData(0, 0, w, h);
  for (let y = 0; y < h; y++) {
    const v = y / h;
    const lat = (0.5 - v) * Math.PI;
    for (let x = 0; x < w; x++) {
      const u = x / w;
      const lon = (u - 0.5) * Math.PI * 2;
      let n = 0;
      n += Math.sin(lon * 3.1 + lat * 2.2) * 0.5;
      n += Math.sin(lon * 7.3 - lat * 4.1 + 1.7) * 0.25;
      n += Math.sin(lon * 13.0 + lat * 9.0 + 4.2) * 0.15;
      n += Math.cos(lon * 1.7 - lat * 1.1) * 0.4;
      n /= 1.3;
      if (n > 0.15) {
        const idx = (y * w + x) * 4;
        const t = Math.min(1, (n - 0.15) / 0.5);
        img.data[idx] += 16 * t;
        img.data[idx + 1] += 27 * t;
        img.data[idx + 2] += 36 * t;
      }
    }
  }
  ctx.putImageData(img, 0, 0);

  ctx.strokeStyle = 'rgba(120,210,205,0.09)';
  ctx.lineWidth = 1;
  for (let lon = 0; lon <= 360; lon += 30) {
    const x = (lon / 360) * w;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  }
  for (let lat = -60; lat <= 60; lat += 30) {
    const y = h / 2 - (lat / 180) * h;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }
  ctx.strokeStyle = 'rgba(120,220,215,0.16)';
  ctx.beginPath(); ctx.moveTo(0, h / 2); ctx.lineTo(w, h / 2); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(w / 2, 0); ctx.lineTo(w / 2, h); ctx.stroke();

  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function buildStarfield() {
  const count = 12000;
  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);
  const tint = new THREE.Color();
  for (let i = 0; i < count; i++) {
    const r = 34 + Math.random() * 30;
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    positions[i * 3 + 1] = r * Math.cos(phi);
    positions[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
    const shade = 0.55 + Math.random() * 0.45;
    const hueDrift = Math.random();
    if (hueDrift > 0.92) tint.setRGB(0.75 * shade, 0.83 * shade, 1.0 * shade);
    else if (hueDrift > 0.85) tint.setRGB(1.0 * shade, 0.9 * shade, 0.75 * shade);
    else tint.setRGB(shade, shade, shade);
    colors[i * 3] = tint.r; colors[i * 3 + 1] = tint.g; colors[i * 3 + 2] = tint.b;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  const mat = new THREE.PointsMaterial({ size: 1.5, vertexColors: true, sizeAttenuation: false, transparent: true, opacity: 0.95 });
  return new THREE.Points(geo, mat);
}

// Real NASA-derived imagery from the three.js example asset set, pinned to a
// tag via jsDelivr so it never breaks even if upstream reorganizes examples.
const TEXTURE_BASE = 'https://cdn.jsdelivr.net/gh/mrdoob/three.js@r128/examples/textures/planets/';

function buildLatLongGrid(radius) {
  const group = new THREE.Group();
  const material = new THREE.LineBasicMaterial({
    color: 0x7ce9da,
    transparent: true,
    opacity: 0.10,
    toneMapped: false,
    depthTest: true,
    depthWrite: false,
  });
  const SEGMENTS = 96;
  for (let lat = -60; lat <= 60; lat += 30) {
    const phi = (lat * Math.PI) / 180;
    const r = radius * Math.cos(phi);
    const y = radius * Math.sin(phi);
    const pts = [];
    for (let i = 0; i <= SEGMENTS; i++) {
      const theta = (i / SEGMENTS) * Math.PI * 2;
      pts.push(new THREE.Vector3(r * Math.cos(theta), y, r * Math.sin(theta)));
    }
    group.add(new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pts), material));
  }
  for (let lon = 0; lon < 360; lon += 30) {
    const theta = (lon * Math.PI) / 180;
    const pts = [];
    for (let i = 0; i <= SEGMENTS; i++) {
      const phi = (i / SEGMENTS) * Math.PI - Math.PI / 2;
      pts.push(new THREE.Vector3(
        radius * Math.cos(phi) * Math.cos(theta),
        radius * Math.sin(phi),
        radius * Math.cos(phi) * Math.sin(theta)
      ));
    }
    group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), material));
  }
  return group;
}

const SUN_DIRECTION = new THREE.Vector3(1, 0.4, 0.6).normalize();

// Earth material: day/night blend (as before) plus two additions that were
// previously loaded... nowhere. The day texture alone is a flat photo decal
// with no lighting response of its own, which is exactly why it reads as
// soft/blurred no matter its resolution -- there's nothing on the surface
// that visually reacts to the sun direction. Two standard maps fix that
// without touching geometry or the render pipeline:
//   - normalTex adds tangent-space relief shading (terrain actually catches
//     light), via Christian Schuler's derivative-based tangent frame so no
//     precomputed tangent attributes are needed on the sphere geometry.
//   - specTex (bright over ocean, dark over land) masks a Blinn-Phong
//     highlight to water only, giving oceans real shine instead of flat
//     matte color.
// The night side also no longer drops to near-black city-lights-on-void: a
// faint wash of the (darkened) day texture keeps coastlines legible there,
// since a fully physically-dark hemisphere reads as "broken" in a UI far
// more than it reads as "realistic".
function dayNightMaterial(dayTex, nightTex, specTex, normalTex) {
  dayTex.colorSpace = THREE.SRGBColorSpace;
  return new THREE.ShaderMaterial({
    uniforms: {
      dayTexture: { value: dayTex },
      nightTexture: { value: nightTex },
      specularTexture: { value: specTex },
      normalTexture: { value: normalTex },
      sunDirection: { value: SUN_DIRECTION },
    },
    vertexShader: `
      varying vec2 vUv;
      varying vec3 vViewNormal;
      varying vec3 vViewPosition;
      void main() {
        vUv = uv;
        vViewNormal = normalize(normalMatrix * normal);
        vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
        vViewPosition = -mvPosition.xyz;
        gl_Position = projectionMatrix * mvPosition;
      }`,
    fragmentShader: `
      uniform sampler2D dayTexture;
      uniform sampler2D nightTexture;
      uniform sampler2D specularTexture;
      uniform sampler2D normalTexture;
      uniform vec3 sunDirection;
      varying vec2 vUv;
      varying vec3 vViewNormal;
      varying vec3 vViewPosition;

      // Tangent-space normal mapping without precomputed tangents (Schuler).
      // Works on any UV'd surface -- no geometry.computeTangents() needed.
      mat3 cotangentFrame(vec3 N, vec3 p, vec2 uv) {
        vec3 dp1 = dFdx(p);
        vec3 dp2 = dFdy(p);
        vec2 duv1 = dFdx(uv);
        vec2 duv2 = dFdy(uv);
        vec3 dp2perp = cross(dp2, N);
        vec3 dp1perp = cross(N, dp1);
        vec3 T = dp2perp * duv1.x + dp1perp * duv2.x;
        vec3 B = dp2perp * duv1.y + dp1perp * duv2.y;
        float invmax = inversesqrt(max(dot(T, T), dot(B, B)));
        return mat3(T * invmax, B * invmax, N);
      }

      void main() {
        vec3 N = normalize(vViewNormal);
        vec3 V = normalize(vViewPosition);
        vec3 sunView = normalize((viewMatrix * vec4(sunDirection, 0.0)).xyz);

        // Day/night terminator on the clean geometric normal -- keeps the
        // twilight line itself smooth; the bump map only adds local detail
        // within each side, not to where the line falls.
        float terminator = dot(N, sunView);
        float dayMix = smoothstep(-0.35, 0.25, terminator);

        vec3 mapN = texture2D(normalTexture, vUv).rgb * 2.0 - 1.0;
        mapN.xy *= 0.6; // restrained -- relief hint, not a cartoon bump
        mat3 TBN = cotangentFrame(N, -V, vUv);
        vec3 shadingNormal = normalize(TBN * mapN);

        vec3 dayColor = texture2D(dayTexture, vUv).rgb;
        float relief = clamp(dot(shadingNormal, sunView), 0.0, 1.0);
        dayColor *= mix(0.9, 1.06, relief);

        vec3 nightColor = texture2D(nightTexture, vUv).rgb * vec3(1.6, 1.4, 1.0) * 0.95;
        vec3 nightBase = nightColor + dayColor * 0.075;

        // Renderer has no tone mapping, so anything this pushes past 1.0
        // hard-clips to solid white -- keep the highlight tight (high power)
        // and low-intensity, a glint, not a wash of shine across the ocean.
        float specMask = texture2D(specularTexture, vUv).r;
        vec3 H = normalize(sunView + V);
        float specPower = pow(max(dot(shadingNormal, H), 0.0), 130.0);
        vec3 specular = vec3(0.8, 0.88, 1.0) * specPower * specMask * dayMix * 0.4;

        vec3 color = mix(nightBase, dayColor, dayMix) + specular;
        gl_FragColor = vec4(color, 1.0);
      }`,
    extensions: { derivatives: true },
    depthTest: true,
    depthWrite: true,
  });
}

function buildEarth(renderer) {
  const group = new THREE.Group();
  const geo = new THREE.SphereGeometry(SCENE_EARTH_RADIUS, 128, 128);

  // Procedural placeholder shown immediately; swapped for real imagery the
  // moment it finishes loading. Keeps the globe never blank, even offline.
  const earthMesh = new THREE.Mesh(geo, new THREE.MeshPhongMaterial({
    map: buildEarthTexture(),
    shininess: 6,
    specular: 0x1a2636,
    depthTest: true,
    depthWrite: true,
  }));
  earthMesh.renderOrder = 0;
  group.add(earthMesh);

  const maxAniso = renderer.capabilities.getMaxAnisotropy();
  const loader = new THREE.TextureLoader();
  const textures = {};
  const upgrade = () => {
    // Waits for all four maps so the swap is one clean cut to the fully-lit
    // material, never a visible half-upgraded frame (e.g. relief with no
    // specular yet).
    if (textures.day && textures.night && textures.spec && textures.normal) {
      earthMesh.material = dayNightMaterial(textures.day, textures.night, textures.spec, textures.normal);
      earthMesh.material.depthTest = true;
      earthMesh.material.depthWrite = true;
    }
  };
  const loadTex = (file, key, sRGB) => {
    loader.load(TEXTURE_BASE + file, (tex) => {
      tex.anisotropy = maxAniso; // sharper at the oblique/zoomed angles the globe is usually viewed at
      if (sRGB) tex.colorSpace = THREE.SRGBColorSpace;
      textures[key] = tex;
      upgrade();
    }, undefined, () => {});
  };
  // 4K day map (was 2K) -- the single biggest legibility win at typical zoom.
  loadTex('earth_atmos_4096.jpg', 'day', true);
  loadTex('earth_lights_2048.png', 'night', false);
  loadTex('earth_specular_2048.jpg', 'spec', false);
  loadTex('earth_normal_2048.jpg', 'normal', false);

  loader.load(TEXTURE_BASE + 'earth_clouds_2048.png', (tex) => {
    tex.anisotropy = maxAniso;
    const cloudsMesh = new THREE.Mesh(
      new THREE.SphereGeometry(SCENE_EARTH_RADIUS * 1.015, 96, 96),
      new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: 0.7, depthWrite: false, depthTest: true })
    );
    cloudsMesh.name = 'clouds';
    cloudsMesh.visible = pendingCloudsVisible; // respect a toggle clicked before this async load resolved
    group.add(cloudsMesh);
  }, undefined, () => {});

  group.add(buildLatLongGrid(SCENE_EARTH_RADIUS * 1.004));
  group.add(buildAtmosphere());

  // Earth's rotation axis
  const axisGeo = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(0, -SCENE_EARTH_RADIUS * 1.4, 0),
    new THREE.Vector3(0, SCENE_EARTH_RADIUS * 1.4, 0)
  ]);
  const axisMat = new THREE.LineDashedMaterial({
    color: 0x7ce9da,
    dashSize: 0.08,
    gapSize: 0.08,
    transparent: true,
    opacity: 0.4,
    toneMapped: false,
    depthTest: true,
    depthWrite: false,
  });
  const axisLine = new THREE.Line(axisGeo, axisMat);
  axisLine.computeLineDistances();
  group.add(axisLine);

  return group;
}

function buildAtmosphere() {
  const geo = new THREE.SphereGeometry(SCENE_EARTH_RADIUS * 1.045, 64, 64);
  const mat = new THREE.ShaderMaterial({
    vertexShader: `
      varying vec3 vNormal;
      void main() {
        vNormal = normalize( normalMatrix * normal );
        gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
      }`,
    fragmentShader: `
      varying vec3 vNormal;
      void main() {
        float intensity = pow( 0.68 - dot( vNormal, vec3( 0.0, 0.0, 1.0 ) ), 3.2 );
        gl_FragColor = vec4( 0.33, 0.89, 0.82, 1.0 ) * intensity;
      }`,
    side: THREE.BackSide,
    blending: THREE.AdditiveBlending,
    transparent: true,
    depthWrite: false,
    depthTest: true,
  });
  return new THREE.Mesh(geo, mat);
}

// ---- ground station sensor-coverage cones ----
// Apex at the station (narrow point), opening toward zenith at
// (90 - min_elevation_deg) half-angle from the axis. The 10-degree mask
// used by every configured station produces a genuinely wide, shallow dome
// (half-angle 80 deg) rather than a narrow searchlight beam -- that's
// physically correct for a low-elevation ground-station footprint, not a
// rendering bug; a low, restrained opacity plus additive blending keeps
// three overlapping domes legible instead of a solid wall of color.
const STATION_CONE_HEIGHT_KM = 2200; // comfortably past typical LEO altitudes
const STATION_COLOR = 0xD98324; // amber -- distinct from every object-type legend color

function buildStationCone(minElevationDeg) {
  const halfAngleRad = THREE.MathUtils.degToRad(90 - minElevationDeg);
  const heightScene = STATION_CONE_HEIGHT_KM * KM_TO_SCENE;
  const radiusScene = heightScene * Math.tan(halfAngleRad);
  const geo = new THREE.ConeGeometry(radiusScene, heightScene, 48, 1, true);
  // Default ConeGeometry centers itself with the apex at local +Y/2 and the
  // base at local -Y/2. Shift so the apex sits at the local origin -- the
  // cone's "opening direction" from there is local -Y -- so it can be
  // dropped exactly at the station's scene position and then oriented
  // toward true zenith with a single quaternion.
  geo.translate(0, -heightScene / 2, 0);
  const mat = new THREE.MeshBasicMaterial({
    color: STATION_COLOR,
    transparent: true,
    opacity: 0.07,
    blending: THREE.AdditiveBlending,
    side: THREE.DoubleSide,
    depthWrite: false,
    toneMapped: false,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.renderOrder = 1;
  mesh.raycast = () => {}; // decorative only, never steals a click from the object cloud
  return mesh;
}

const dummy = new THREE.Object3D();
const tmpColor = new THREE.Color();
let pendingCloudsVisible = true;

export function initGlobe(canvas) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(48, 1, 0.05, 200);

  scene.add(buildStarfield());

  const tiltGroup = new THREE.Group();
  tiltGroup.rotation.z = THREE.MathUtils.degToRad(23.5);
  scene.add(tiltGroup);

  const earthGroup = buildEarth(renderer);
  tiltGroup.add(earthGroup);

  scene.add(new THREE.AmbientLight(0x1c2836, 1.5));
  const sun = new THREE.DirectionalLight(0xeaf3ff, 1.7);
  sun.position.set(5, 2, 3);
  scene.add(sun);

  // ---- camera orbit control (custom: drag to orbit, wheel to zoom) ----
  const DEFAULT_CAM = { theta: 0.9, phi: 1.15, radius: 6.2 };
  const cam = { theta: DEFAULT_CAM.theta, phi: DEFAULT_CAM.phi, radius: DEFAULT_CAM.radius, targetTheta: DEFAULT_CAM.theta, targetPhi: DEFAULT_CAM.phi, targetRadius: DEFAULT_CAM.radius, targetOffsetY: 0, offsetY: 0 };
  const MIN_R = 2.6, MAX_R = 16;

  // Cinematic camera flights (Explore Mode) layer on top of the existing
  // drag/zoom/reset system: while a flight is active it drives cam.theta/
  // phi/radius directly with its own eased timing; any manual drag or
  // scroll cancels it immediately and hands control back to the user.
  let flight = null;

  function startFlight(toTheta, toPhi, toRadius, duration, onDone) {
    flight = {
      fromTheta: cam.theta, fromPhi: cam.phi, fromRadius: cam.radius,
      toTheta, toPhi, toRadius,
      start: performance.now(), duration, onDone,
    };
  }

  // Swing the camera onto the object's own bearing and stand off from it.
  //
  // `standoff` is the gap left between the camera and the object's orbital
  // shell, NOT an absolute radius -- so a LEO target and a GEO target both
  // end up framed at a comparable on-screen size. EXPLORE_MIN_RADIUS is the
  // floor that keeps Earth's limb inside a 48deg FOV: drop below it and the
  // camera ends up inside the debris shell, where nodes balloon into
  // faceted blobs and Earth degenerates into an unreadable dark wall.
  const EXPLORE_MIN_RADIUS = 4.8;

  function computeFraming(scenePos, standoff) {
    const dist = scenePos.length();
    if (dist < 1e-6) return { theta: cam.theta, phi: cam.phi, radius: cam.radius };
    const dir = scenePos.clone().divideScalar(dist);
    const theta = Math.atan2(dir.x, dir.z);
    const phi = Math.acos(clamp(dir.y, -1, 1));
    const radius = clamp(Math.max(dist + standoff, EXPLORE_MIN_RADIUS), MIN_R, MAX_R);
    return { theta, phi, radius };
  }

  function applyCamera() {
    camera.position.set(
      cam.radius * Math.sin(cam.phi) * Math.sin(cam.theta),
      cam.radius * Math.cos(cam.phi) + cam.offsetY,
      cam.radius * Math.sin(cam.phi) * Math.cos(cam.theta)
    );
    camera.lookAt(0, cam.offsetY, 0);
  }
  applyCamera();

  let dragging = false;
  let downPos = { x: 0, y: 0 };
  let lastPos = { x: 0, y: 0 };
  let interactedCb = null;

  canvas.addEventListener('pointerdown', (e) => {
    flight = null; // manual control always wins over a cinematic flight
    dragging = true;
    downPos = { x: e.clientX, y: e.clientY };
    lastPos = { x: e.clientX, y: e.clientY };
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener('pointermove', (e) => {
    if (!dragging) { handleHover(e); return; }
    const dx = e.clientX - lastPos.x, dy = e.clientY - lastPos.y;
    lastPos = { x: e.clientX, y: e.clientY };
    cam.targetTheta -= dx * 0.0055;
    cam.targetPhi = clamp(cam.targetPhi - dy * 0.0055, 0.18, Math.PI - 0.18);
    if (interactedCb) interactedCb();
  });
  window.addEventListener('pointerup', (e) => {
    if (!dragging) return;
    dragging = false;
    const dist = Math.hypot(e.clientX - downPos.x, e.clientY - downPos.y);
    if (dist < 4) handleClick(e);
  });
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    flight = null;
    cam.targetRadius = clamp(cam.targetRadius * (1 + Math.sign(e.deltaY) * 0.1), MIN_R, MAX_R);
    if (interactedCb) interactedCb();
  }, { passive: false });

  // ---- object layer (instanced point cloud, current-state positions) ----
  //
  // Two instanced draw calls, one index space:
  //   cloudMesh - the solid core. Small, opaque, and the only thing the
  //               raycaster ever sees, so picking behaviour is unchanged.
  //   glowMesh  - a camera-facing billboard per object, additively blended
  //               with a radial falloff, giving the core a halo without
  //               growing the node itself.
  //
  // Instance i refers to the same object in both meshes, and every write
  // goes through writeInstance() so the two can never drift apart.
  //
  // The core geometry is an icosahedron at detail 0 (20 tris) rather than
  // detail 2 (180 tris). At the few pixels these occupy on screen the two
  // are indistinguishable, but across a 10k+ catalog it is the difference
  // between ~1.96M and ~218k triangles per frame.
  const CORE_RADIUS = 0.019;
  const cloudGeo = new THREE.IcosahedronGeometry(CORE_RADIUS, 0);
  const cloudMat = new THREE.MeshBasicMaterial({ toneMapped: false });

  // Per-type glow treatment: halo radius in scene units + emissive strength.
  // Synthetic debris is deliberately the loudest (it is the injected threat);
  // satellites stay restrained so a full catalog does not turn into a wall
  // of neon.
  const GLOW = {
    SATELLITE:        { halo: 0.040, strength: 0.50 },
    DEBRIS:           { halo: 0.046, strength: 0.72 },
    ROCKET_BODY:      { halo: 0.044, strength: 0.58 },
    SYNTHETIC_DEBRIS: { halo: 0.070, strength: 1.25 },
    UNKNOWN:          { halo: 0.038, strength: 0.42 },
  };

  // Explore Mode background treatment -- subdued, deliberately not erased:
  // the surrounding traffic still has to be readable as context.
  const DIM = { coreScale: 0.45, coreColor: 0.16, haloScale: 0.4, glow: 0.06 };

  const glowGeo = new THREE.PlaneGeometry(1, 1);
  const glowMat = new THREE.ShaderMaterial({
    vertexShader: `
      varying vec2 vUv;
      varying vec3 vColor;
      void main() {
        vUv = uv;
        vColor = instanceColor;
        // Halo radius is carried as the instance matrix scale, so hiding an
        // instance (scale 0) collapses the quad and costs no fill.
        float s = length(instanceMatrix[0].xyz);
        vec4 mv = modelViewMatrix * instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0);
        mv.xy += (uv - 0.5) * 2.0 * s;   // billboard: expand in view space
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: `
      varying vec2 vUv;
      varying vec3 vColor;
      void main() {
        float d = length(vUv - 0.5) * 2.0;
        float a = pow(max(0.0, 1.0 - d), 2.6);
        if (a < 0.012) discard;          // skip the transparent corners
        gl_FragColor = vec4(vColor * a, a);
      }`,
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    toneMapped: false,
  });

  let cloudMesh = null;
  let glowMesh = null;
  let indexToId = [];
  let idToIndex = new Map();
  let objectsById = new Map();
  const hiddenIndices = new Set();
  const dimmedIds = new Set();

  // Single writer for both meshes. mode: 'normal' | 'hidden' | 'dimmed'.
  function writeInstance(i, obj, mode) {
    if (!cloudMesh || !glowMesh) return;
    const p = obj?.state ? kmToScene(obj.state.position_km) : new THREE.Vector3();
    const type = obj?.object_type;
    const g = GLOW[type] ?? GLOW.UNKNOWN;
    const baseHex = TYPE_COLOR[type] ?? TYPE_COLOR.UNKNOWN;

    const hidden = mode === 'hidden';
    const dimmed = mode === 'dimmed';
    // Dimming has to be aggressive to register: Explore also flies the camera
    // closer, so a mildly dimmed background just reads as "same view, nearer".
    // The halo is cut hardest -- 10k additive sprites accumulate into a teal
    // haze long after the individual cores have stopped being legible.
    const coreScale = hidden ? 0 : (dimmed ? DIM.coreScale : 1);
    const haloScale = hidden ? 0 : g.halo * (dimmed ? DIM.haloScale : 1);

    dummy.position.copy(p);
    dummy.scale.setScalar(coreScale);
    dummy.updateMatrix();
    cloudMesh.setMatrixAt(i, dummy.matrix);
    tmpColor.setHex(baseHex);
    if (dimmed) tmpColor.multiplyScalar(DIM.coreColor);
    cloudMesh.setColorAt(i, tmpColor);

    dummy.scale.setScalar(haloScale);
    dummy.updateMatrix();
    glowMesh.setMatrixAt(i, dummy.matrix);
    tmpColor.setHex(baseHex).multiplyScalar(g.strength * (dimmed ? DIM.glow : 1));
    glowMesh.setColorAt(i, tmpColor);
  }

  function flushInstances() {
    if (!cloudMesh || !glowMesh) return;
    cloudMesh.instanceMatrix.needsUpdate = true;
    glowMesh.instanceMatrix.needsUpdate = true;
    if (cloudMesh.instanceColor) cloudMesh.instanceColor.needsUpdate = true;
    if (glowMesh.instanceColor) glowMesh.instanceColor.needsUpdate = true;
  }

  function modeFor(objectId) {
    if (hiddenIndices.has(objectId)) return 'hidden';
    if (dimmedIds.has(objectId)) return 'dimmed';
    return 'normal';
  }

  function disposeCloud() {
    if (cloudMesh) { tiltGroup.remove(cloudMesh); cloudMesh.dispose(); cloudMesh = null; }
    if (glowMesh) { tiltGroup.remove(glowMesh); glowMesh.dispose(); glowMesh = null; }
  }

  function rebuildCloud(objects) {
    // Geometries and materials are module-level and reused across rebuilds;
    // only the per-instance buffers are recreated here.
    disposeCloud();
    indexToId = objects.map(objectKey);
    idToIndex = new Map(indexToId.map((id, i) => [id, i]));
    objectsById = new Map(objects.map((o) => [objectKey(o), o]));
    hiddenIndices.clear();
    dimmedIds.clear();
    if (!objects.length) return;

    cloudMesh = new THREE.InstancedMesh(cloudGeo, cloudMat, objects.length);
    glowMesh = new THREE.InstancedMesh(glowGeo, glowMat, objects.length);
    // The halo is expanded in the vertex shader, which the CPU-side bounding
    // sphere cannot know about, so skip culling rather than risk pop-out.
    // The cloud spans the whole scene either way.
    glowMesh.frustumCulled = false;
    // Picking stays entirely on the solid core.
    glowMesh.raycast = () => {};

    objects.forEach((o, i) => writeInstance(i, o, 'normal'));
    flushInstances();
    tiltGroup.add(glowMesh);
    tiltGroup.add(cloudMesh);
  }

  function setInstanceHidden(objectId, hidden) {
    const i = idToIndex.get(objectId);
    if (i === undefined || !cloudMesh) return;
    if (hidden) hiddenIndices.add(objectId); else hiddenIndices.delete(objectId);
    writeInstance(i, objectsById.get(objectId), modeFor(objectId));
    flushInstances();
  }

  // ---- Explore Mode relevance dimming: same per-instance write as above,
  // just with a partial scale/colour reduction instead of a full hide. ----
  function applyRelevance(objectId, dimmed) {
    const i = idToIndex.get(objectId);
    if (i === undefined || !cloudMesh) return;
    if (hiddenIndices.has(objectId)) return; // focused marker owns it; leave it
    if (dimmed) dimmedIds.add(objectId); else dimmedIds.delete(objectId);
    writeInstance(i, objectsById.get(objectId), modeFor(objectId));
  }

  function setRelevance(relevantIds) {
    for (const id of indexToId) applyRelevance(id, !relevantIds.has(id));
    flushInstances();
  }

  function clearRelevance() {
    if (!dimmedIds.size) return;
    for (const id of [...dimmedIds]) applyRelevance(id, false);
    flushInstances();
  }

  // ---- focused objects: bright marker + trajectory line, animated by sim time ----
  const focusGroup = new THREE.Group();
  tiltGroup.add(focusGroup);
  const focused = new Map(); // objectId -> { marker, line, trajectory, color, ring }

  function makeRingSprite(color) {
    // Rendered at 4x the display size (128px source for a ~0.16-unit sprite)
    // so the ring stays crisp under close zoom instead of pixelating.
    const c = document.createElement('canvas');
    c.width = 128; c.height = 128;
    const ctx = c.getContext('2d');
    ctx.strokeStyle = color; ctx.lineWidth = 8;
    ctx.beginPath(); ctx.arc(64, 64, 46, 0, Math.PI * 2); ctx.stroke();
    const tex = new THREE.CanvasTexture(c);
    tex.anisotropy = 4;
    const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, depthTest: true, toneMapped: false });
    const sprite = new THREE.Sprite(mat);
    sprite.renderOrder = 3;
    sprite.scale.set(0.22, 0.22, 1);
    return sprite;
  }

  function colorHexString(hex) { return '#' + hex.toString(16).padStart(6, '0'); }

  function markerMaterial(colorHex) {
    // A cheap fresnel-lit shader (same technique as the atmosphere glow)
    // instead of a flat unlit color, so the marker reads as a glowing 3D
    // orb rather than a flat-shaded disc at close zoom.
    return new THREE.ShaderMaterial({
      uniforms: { color: { value: new THREE.Color(colorHex) } },
      vertexShader: `
        varying vec3 vNormal;
        void main() {
          vNormal = normalize( normalMatrix * normal );
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }`,
      fragmentShader: `
        uniform vec3 color;
        varying vec3 vNormal;
        void main() {
          float rim = pow(1.0 - abs(dot(vNormal, vec3(0.0, 0.0, 1.0))), 2.0);
          gl_FragColor = vec4(mix(color * 0.95, vec3(1.0), rim * 0.65), 1.0);
        }`,
      depthTest: true,
      depthWrite: true,
    });
  }

  function focusObject(objectId, colorHex) {
    if (focused.has(objectId)) return focused.get(objectId);
    setInstanceHidden(objectId, true);
    const obj = objectsById.get(objectId);
    const markerGeo = new THREE.SphereGeometry(0.055, 32, 32);
    const markerMat = markerMaterial(colorHex);
    const marker = new THREE.Mesh(markerGeo, markerMat);
    marker.renderOrder = 3;
    marker.userData.objectId = objectId;
    if (obj?.state) marker.position.copy(kmToScene(obj.state.position_km));
    const ring = makeRingSprite(colorHexString(colorHex));
    ring.position.copy(marker.position);
    focusGroup.add(marker);
    focusGroup.add(ring);
    const entry = { marker, ring, line: null, trajectory: null, color: colorHex };
    focused.set(objectId, entry);
    return entry;
  }

  // Trajectories are re-selected constantly (every catalog click re-enters
  // this path), so every per-selection object is explicitly disposed rather
  // than just detached -- otherwise each click leaks a line buffer.
  function disposeTrajectoryVisuals(entry) {
    if (entry.line) {
      focusGroup.remove(entry.line);
      entry.line.geometry.dispose();
      entry.line.material.dispose();
      entry.line = null;
    }
    if (entry.arrows) {
      focusGroup.remove(entry.arrows);
      entry.arrows.dispose();
      entry.arrows.material.dispose();
      entry.arrows = null;
    }
  }

  function unfocusObject(objectId) {
    const entry = focused.get(objectId);
    if (!entry) return;
    focusGroup.remove(entry.marker);
    entry.marker.geometry.dispose();
    entry.marker.material.dispose();
    focusGroup.remove(entry.ring);
    entry.ring.material.map?.dispose();
    entry.ring.material.dispose();
    disposeTrajectoryVisuals(entry);
    setInstanceHidden(objectId, false);
    focused.delete(objectId);
    // A ruler pointing at a now-gone marker would read a stale/frozen
    // distance forever -- drop it rather than leave a dangling reference.
    if (ruler && (ruler.idA === objectId || ruler.idB === objectId)) clearDistanceRuler();
  }

  // ---- trajectory rendering: Earth occlusion, sphere clipping, and depth testing ----
  //
  // Requirements:
  // 1. A trajectory behind the Earth must be occluded naturally by the globe's depth buffer.
  // 2. A trajectory segment that dips below Earth's radius (r < SCENE_EARTH_RADIUS = 2.0,
  //    corresponding to 6378.137 km) must be clipped at the Earth surface and never render
  //    inside the Earth's interior.
  // 3. Complete mathematical trajectory remains intact in memory (entry.trajectory).
  // 4. Direction arrows and markers must respect depth testing and never render inside Earth.

  function clipSegmentSphere(p1, p2, radius) {
    const r1 = Math.hypot(p1.x, p1.y, p1.z);
    const r2 = Math.hypot(p2.x, p2.y, p2.z);
    const dx = p2.x - p1.x, dy = p2.y - p1.y, dz = p2.z - p1.z;
    const d2 = dx * dx + dy * dy + dz * dz;
    if (d2 < 1e-12) return [];

    const a = d2;
    const b = 2.0 * (p1.x * dx + p1.y * dy + p1.z * dz);
    const c = r1 * r1 - radius * radius;
    const disc = b * b - 4.0 * a * c;

    if (r1 < radius && r2 < radius && disc <= 0) return [];

    const roots = [];
    if (disc >= 0) {
      const sqrtDisc = Math.sqrt(disc);
      const t1 = (-b - sqrtDisc) / (2.0 * a);
      const t2 = (-b + sqrtDisc) / (2.0 * a);
      if (t1 > 0.0 && t1 < 1.0) roots.push(t1);
      if (t2 > 0.0 && t2 < 1.0) roots.push(t2);
      roots.sort((x, y) => x - y);
    }

    const ptAt = (t) => new THREE.Vector3(p1.x + t * dx, p1.y + t * dy, p1.z + t * dz);

    if (roots.length === 0) {
      return (r1 >= radius && r2 >= radius) ? [{ pStart: p1, pEnd: p2, t0: 0, t1: 1 }] : [];
    } else if (roots.length === 1) {
      const t = roots[0];
      const pCut = ptAt(t);
      return (r1 >= radius)
        ? [{ pStart: p1, pEnd: pCut, t0: 0, t1: t }]
        : [{ pStart: pCut, pEnd: p2, t0: t, t1: 1 }];
    } else {
      const [t1, t2] = roots;
      const pCut1 = ptAt(t1);
      const pCut2 = ptAt(t2);
      return (r1 >= radius)
        ? [{ pStart: p1, pEnd: pCut1, t0: 0, t1: t1 }, { pStart: pCut2, pEnd: p2, t0: t2, t1: 1 }]
        : [{ pStart: pCut1, pEnd: pCut2, t0: t1, t1: t2 }];
    }
  }

  function buildTrajectoryMaterial() {
    const uniforms = {
      opacity: { value: 1.0 },
      earthRadius: { value: SCENE_EARTH_RADIUS },
    };
    const mat = new THREE.ShaderMaterial({
      uniforms,
      vertexShader: `
        attribute vec3 color;
        varying vec3 vColor;
        varying vec3 vPos;
        void main() {
          vColor = color;
          vPos = position;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      fragmentShader: `
        uniform float opacity;
        uniform float earthRadius;
        varying vec3 vColor;
        varying vec3 vPos;
        void main() {
          // Hard GPU discard prevents any rasterized fragment from rendering inside Earth's radius
          if (length(vPos) < earthRadius - 0.0005) discard;
          gl_FragColor = vec4(vColor, opacity);
        }
      `,
      transparent: true,
      depthTest: true,
      depthWrite: false,
      toneMapped: false,
    });

    // Provide opacity getter/setter so pulseArrival animations work transparently
    Object.defineProperty(mat, 'opacity', {
      get() { return uniforms.opacity.value; },
      set(v) { uniforms.opacity.value = v; },
    });

    return mat;
  }

  // ---- direction of travel ----
  // The gradient along the trajectory line already encodes time progression,
  // but it does not say which way the object is going when playback is
  // paused. These are small cones sampled along the path and oriented down
  // the local tangent, brightening toward the future end of the track.
  // One instanced draw call of ~14 cones per focused object.
  const ARROW_COUNT = 14;
  const arrowGeo = new THREE.ConeGeometry(0.011, 0.032, 6);
  const CONE_AXIS = new THREE.Vector3(0, 1, 0); // ConeGeometry points +Y
  const arrowQuat = new THREE.Quaternion();
  const arrowTangent = new THREE.Vector3();

  function buildDirectionArrows(rawPts, baseColor) {
    if (rawPts.length < 4) return null;
    // Only place arrows on points that are above the Earth surface
    const pts = rawPts.filter((p) => p.length() >= SCENE_EARTH_RADIUS * 0.999);
    if (pts.length < 4) return null;

    const mat = new THREE.MeshBasicMaterial({
      transparent: true,
      opacity: 0.75,
      toneMapped: false,
      depthTest: true,
      depthWrite: false,
    });
    const mesh = new THREE.InstancedMesh(arrowGeo, mat, ARROW_COUNT);
    mesh.renderOrder = 2;
    mesh.frustumCulled = false;
    mesh.raycast = () => {};   // never steal a click from an object
    const dim = baseColor.clone().multiplyScalar(0.35);
    let validCount = 0;
    for (let a = 0; a < ARROW_COUNT; a++) {
      // skip the very ends so arrows don't sit on top of the marker
      const t = (a + 0.5) / ARROW_COUNT;
      const i = Math.min(pts.length - 2, Math.floor(t * (pts.length - 1)));
      // Skip placing an arrow across a clipped subterranean gap
      if (pts[i].distanceTo(pts[i + 1]) > 0.35) continue;
      arrowTangent.subVectors(pts[i + 1], pts[i]);
      if (arrowTangent.lengthSq() < 1e-12) continue;
      arrowTangent.normalize();
      arrowQuat.setFromUnitVectors(CONE_AXIS, arrowTangent);
      dummy.position.copy(pts[i]);
      dummy.quaternion.copy(arrowQuat);
      dummy.scale.setScalar(1);
      dummy.updateMatrix();
      mesh.setMatrixAt(validCount, dummy.matrix);
      mesh.setColorAt(validCount, tmpColor.copy(dim).lerp(baseColor, t));
      validCount++;
    }
    dummy.quaternion.identity();   // leave the shared dummy neutral
    mesh.count = validCount;
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    return mesh;
  }

  function setFocusTrajectory(objectId, trajectoryPoints, colorHex) {
    const entry = focusObject(objectId, colorHex);
    // Retain full mathematical trajectory in state for animation, scrub, and calculations
    entry.trajectory = trajectoryPoints;
    disposeTrajectoryVisuals(entry);

    const rawPts = trajectoryPoints.map((p) => kmToScene({ x: p.x, y: p.y, z: p.z }));
    const base = new THREE.Color(colorHex);
    const dim = base.clone().multiplyScalar(0.5);

    const posArray = [];
    const colorArray = [];
    const N = rawPts.length;

    // Geometric clipping against Earth sphere radius: only surface/space segments are drawn
    for (let i = 0; i < N - 1; i++) {
      const p1 = rawPts[i];
      const p2 = rawPts[i + 1];
      const visibleSegs = clipSegmentSphere(p1, p2, SCENE_EARTH_RADIUS);
      for (const seg of visibleSegs) {
        const globalT0 = (i + seg.t0) / (N - 1);
        const globalT1 = (i + seg.t1) / (N - 1);
        const c0 = dim.clone().lerp(base, 1 - globalT0 * 0.5);
        const c1 = dim.clone().lerp(base, 1 - globalT1 * 0.5);

        posArray.push(seg.pStart.x, seg.pStart.y, seg.pStart.z);
        posArray.push(seg.pEnd.x, seg.pEnd.y, seg.pEnd.z);
        colorArray.push(c0.r, c0.g, c0.b);
        colorArray.push(c1.r, c1.g, c1.b);
      }
    }

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(posArray, 3));
    geo.setAttribute('color', new THREE.Float32BufferAttribute(colorArray, 3));

    const mat = buildTrajectoryMaterial();
    entry.line = new THREE.LineSegments(geo, mat);
    entry.line.renderOrder = 2;
    focusGroup.add(entry.line);

    entry.arrows = buildDirectionArrows(rawPts, base);
    if (entry.arrows) focusGroup.add(entry.arrows);

    // A pulseArrival() call that arrived before this object's trajectory had
    // loaded is queued here rather than dropped -- this is exactly the path
    // that resolves it (see pulseArrival below).
    if (pendingArrivalPulse && pendingArrivalPulse.objectId === objectId) {
      entry.arrivalPulse = { start: performance.now(), duration: pendingArrivalPulse.duration };
      pendingArrivalPulse = null;
    }

    positionEntry(entry);
    updateRuler(); // this may be the entry the ruler was waiting on
  }

  // ---- ground station cones: fixed to Earth's true rotation, not the
  // cosmetic earthGroup spin -- see buildStationCone() and groundstations.js
  // for why. Parented to tiltGroup (the inertial frame satellites live in),
  // repositioned every time simMinutes changes via a real GMST computation
  // anchored to `simEpochMs` (wall-clock time this globe was initialized).
  const groundStationGroup = new THREE.Group();
  tiltGroup.add(groundStationGroup);
  const stationEntries = new Map(); // station_id -> { mesh, dot, config, active }
  const simEpochMs = Date.now();

  function setGroundStations(stations) {
    for (const [, entry] of stationEntries) {
      groundStationGroup.remove(entry.mesh);
      entry.mesh.geometry.dispose();
      entry.mesh.material.dispose();
      groundStationGroup.remove(entry.dot);
      entry.dot.geometry.dispose();
      entry.dot.material.dispose();
    }
    stationEntries.clear();

    for (const station of stations) {
      const mesh = buildStationCone(station.min_elevation_deg);
      const dot = new THREE.Mesh(
        new THREE.SphereGeometry(0.012, 12, 12),
        new THREE.MeshBasicMaterial({ color: STATION_COLOR, toneMapped: false })
      );
      dot.raycast = () => {};
      groundStationGroup.add(mesh);
      groundStationGroup.add(dot);
      stationEntries.set(station.station_id, { mesh, dot, config: station, active: false });
    }
    repositionGroundStations();
  }

  function repositionGroundStations() {
    if (!stationEntries.size) return;
    const date = new Date(simEpochMs + simMinutes * 60000);
    for (const [, entry] of stationEntries) {
      const scenePos = kmToScene(stationTemeKm(entry.config, date));
      const zenithScene = kmToScene(stationZenithTeme(entry.config, date)).normalize();
      entry.mesh.position.copy(scenePos);
      entry.dot.position.copy(scenePos);
      entry.mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, -1, 0), zenithScene);
    }
  }

  function setStationActive(stationId, active) {
    const entry = stationEntries.get(stationId);
    if (!entry || entry.active === active) return;
    entry.active = active;
    entry.mesh.material.opacity = active ? 0.30 : 0.07;
    entry.dot.material.color.setHex(active ? 0xfff3d6 : STATION_COLOR);
    entry.dot.scale.setScalar(active ? 1.8 : 1.0);
  }

  function clearStationActive() {
    for (const stationId of stationEntries.keys()) setStationActive(stationId, false);
  }

  let simMinutes = 0;

  function positionEntry(entry) {
    const traj = entry.trajectory;
    if (!traj || traj.length < 2) return;
    const stepMin = (new Date(traj[1].timestamp) - new Date(traj[0].timestamp)) / 60000 || 1;
    const idxF = clamp(simMinutes / stepMin, 0, traj.length - 1);
    const i0 = Math.floor(idxF), i1 = Math.min(traj.length - 1, i0 + 1), frac = idxF - i0;
    const p0 = traj[i0], p1 = traj[i1];
    const pos = kmToScene({
      x: lerp(p0.x, p1.x, frac),
      y: lerp(p0.y, p1.y, frac),
      z: lerp(p0.z, p1.z, frac),
    });
    entry.marker.position.copy(pos);
    entry.ring.position.copy(pos);
  }

  function applySimMinutes() {
    for (const [, entry] of focused) positionEntry(entry);
    updateRuler();
  }

  // ---- live distance ruler ----
  // A dashed connector + billboard km label between two focused objects,
  // kept in sync every time playback moves. Used whenever a conjunction --
  // real, freshly injected, or a post-maneuver ghost vs. the debris it was
  // meant to avoid -- is the thing on screen, so the operator watches an
  // actual live number instead of eyeballing how close two dots look.
  let ruler = null; // { idA, idB, line, label, lastText }
  let pendingArrivalPulse = null; // { objectId, duration } waiting on setFocusTrajectory

  function makeRulerLabelSprite() {
    const c = document.createElement('canvas');
    c.width = 256; c.height = 64;
    const tex = new THREE.CanvasTexture(c);
    tex.anisotropy = 4;
    const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, toneMapped: false });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.set(0.42, 0.105, 1);
    sprite.userData.canvas = c;
    sprite.userData.ctx = c.getContext('2d');
    return sprite;
  }

  function drawRulerLabel(sprite, text, colorHex) {
    const ctx = sprite.userData.ctx, c = sprite.userData.canvas;
    ctx.clearRect(0, 0, c.width, c.height);
    const pad = 8, r = 10;
    ctx.fillStyle = 'rgba(6,10,16,0.72)';
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(pad, pad, c.width - pad * 2, c.height - pad * 2, r);
    else ctx.rect(pad, pad, c.width - pad * 2, c.height - pad * 2);
    ctx.fill();
    ctx.strokeStyle = colorHexString(colorHex);
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.font = '500 30px "Roboto Mono", monospace';
    ctx.fillStyle = '#eaf3ff';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, c.width / 2, c.height / 2 + 1);
    sprite.material.map.needsUpdate = true;
  }

  function setDistanceRuler(idA, idB) {
    clearDistanceRuler();
    const geo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
    const mat = new THREE.LineDashedMaterial({ color: 0xeaf3ff, transparent: true, opacity: 0.55, dashSize: 0.045, gapSize: 0.03, toneMapped: false });
    const line = new THREE.Line(geo, mat);
    line.computeLineDistances();
    line.raycast = () => {}; // a thin ruler line should never steal a click
    focusGroup.add(line);
    const label = makeRulerLabelSprite();
    focusGroup.add(label);
    ruler = { idA, idB, line, label, lastText: null };
    updateRuler();
  }

  function clearDistanceRuler() {
    if (!ruler) return;
    focusGroup.remove(ruler.line);
    ruler.line.geometry.dispose();
    ruler.line.material.dispose();
    focusGroup.remove(ruler.label);
    ruler.label.material.map.dispose();
    ruler.label.material.dispose();
    ruler = null;
  }

  function updateRuler() {
    if (!ruler) return;
    const a = focused.get(ruler.idA), b = focused.get(ruler.idB);
    if (!a || !b) return; // one side's trajectory hasn't loaded yet -- next call catches it
    const posArr = ruler.line.geometry.attributes.position.array;
    posArr[0] = a.marker.position.x; posArr[1] = a.marker.position.y; posArr[2] = a.marker.position.z;
    posArr[3] = b.marker.position.x; posArr[4] = b.marker.position.y; posArr[5] = b.marker.position.z;
    ruler.line.geometry.attributes.position.needsUpdate = true;
    ruler.line.computeLineDistances();

    const distKm = a.marker.position.distanceTo(b.marker.position) / KM_TO_SCENE;
    const text = `${distKm >= 100 ? distKm.toFixed(0) : distKm.toFixed(1)} km`;
    ruler.label.position.copy(a.marker.position).add(b.marker.position).multiplyScalar(0.5);
    if (text !== ruler.lastText) {
      ruler.lastText = text;
      drawRulerLabel(ruler.label, text, distKm <= 10 ? 0xf0616e : 0x56e3d1);
    }
  }

  // ---- cinematic arrival pulse ----
  // A brief, hot intensification of a freshly-focused object's ring and
  // trajectory line -- the "threat just appeared" beat right after synthetic
  // debris injection. Purely a transient tweak of an already-existing
  // entry's own objects (see the ring/line animation in tick() below), so
  // there is nothing extra to leak or dispose once it fades.
  function pulseArrival(objectId, duration = 1600) {
    const entry = focused.get(objectId);
    if (entry) entry.arrivalPulse = { start: performance.now(), duration };
    else pendingArrivalPulse = { objectId, duration }; // trajectory not loaded yet; setFocusTrajectory resolves this
  }

  // ---- thruster burn flare ----
  // A brief expanding, fading flare at an object's current position, marking
  // the instant an approved avoidance maneuver's burn fires. Self-contained
  // and self-disposing (tracked in burnFlares, cleaned up from tick() once
  // its fade completes) rather than living on the focused entry, since it
  // must keep animating even if that entry is later unfocused.
  const burnFlares = [];
  function flashBurn(objectId) {
    const entry = focused.get(objectId);
    if (!entry) return;
    const flare = new THREE.Mesh(
      new THREE.SphereGeometry(0.03, 16, 16),
      new THREE.MeshBasicMaterial({ color: 0xfff3d6, transparent: true, toneMapped: false })
    );
    flare.position.copy(entry.marker.position);
    flare.raycast = () => {};
    flare.userData.burnStart = performance.now();
    focusGroup.add(flare);
    burnFlares.push(flare);
  }

  // ---- conjunction TCA marker ----
  let tcaMarker = null;
  function showConjunctionMarker(scenePos) {
    if (tcaMarker) focusGroup.remove(tcaMarker);
    const geo = new THREE.SphereGeometry(0.05, 24, 24);
    const mat = new THREE.MeshBasicMaterial({ color: 0xf0616e, transparent: true, toneMapped: false });
    tcaMarker = new THREE.Mesh(geo, mat);
    tcaMarker.position.copy(scenePos);
    tcaMarker.userData.pulse = true;
    focusGroup.add(tcaMarker);
  }
  function clearConjunctionMarker() {
    if (tcaMarker) { focusGroup.remove(tcaMarker); tcaMarker = null; }
  }

  // ---- raycasting ----
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  let selectCb = null;
  let hoverCb = null;

  function pointerToNdc(e) {
    const rect = canvas.getBoundingClientRect();
    ndc.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    ndc.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
  }

  function handleClick(e) {
    pointerToNdc(e);
    raycaster.setFromCamera(ndc, camera);
    const markerHits = raycaster.intersectObjects([...focused.values()].map((f) => f.marker));
    if (markerHits.length) {
      selectCb && selectCb(markerHits[0].object.userData.objectId);
      return;
    }
    if (cloudMesh) {
      const hits = raycaster.intersectObject(cloudMesh);
      if (hits.length && hits[0].instanceId !== undefined) {
        selectCb && selectCb(indexToId[hits[0].instanceId]);
        return;
      }
    }
  }

  function handleHover(e) {
    pointerToNdc(e);
    raycaster.setFromCamera(ndc, camera);
    let hit = false;
    if (cloudMesh) hit = raycaster.intersectObject(cloudMesh).length > 0;
    if (!hit) hit = raycaster.intersectObjects([...focused.values()].map((f) => f.marker)).length > 0;
    canvas.style.cursor = hit ? 'pointer' : 'grab';
    hoverCb && hoverCb(hit);
  }

  // ---- resize ----
  function resize() {
    const rect = canvas.parentElement.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    renderer.setSize(rect.width, rect.height, false);
    camera.aspect = rect.width / rect.height;
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(canvas.parentElement);
  resize();

  // ---- render loop ----
  let fpsCb = null;
  let frames = 0, fpsAccum = 0, lastTime = performance.now();
  let autoRotate = true;

  function tick(now) {
    requestAnimationFrame(tick);
    const dt = (now - lastTime) / 1000;
    lastTime = now;
    frames++; fpsAccum += dt;
    if (fpsAccum >= 0.5) {
      fpsCb && fpsCb(Math.round(frames / fpsAccum));
      frames = 0; fpsAccum = 0;
    }

    if (flight) {
      const ft = clamp((now - flight.start) / flight.duration, 0, 1);
      const fe = easeInOutCubic(ft);
      cam.theta = lerpAngle(flight.fromTheta, flight.toTheta, fe);
      cam.phi = lerp(flight.fromPhi, flight.toPhi, fe);
      cam.radius = lerp(flight.fromRadius, flight.toRadius, fe);
      cam.offsetY = lerp(flight.fromOffsetY !== undefined ? flight.fromOffsetY : cam.offsetY, flight.toOffsetY !== undefined ? flight.toOffsetY : cam.offsetY, fe);
      cam.targetTheta = cam.theta; cam.targetPhi = cam.phi; cam.targetRadius = cam.radius; cam.targetOffsetY = cam.offsetY;
      if (ft >= 1) { const cb = flight.onDone; flight = null; cb && cb(); }
    } else {
      cam.theta = lerp(cam.theta, cam.targetTheta, 0.14);
      cam.phi = lerp(cam.phi, cam.targetPhi, 0.14);
      cam.radius = lerp(cam.radius, cam.targetRadius, 0.14);
      cam.offsetY = lerp(cam.offsetY, cam.targetOffsetY, 0.14);
    }
    applyCamera();

    if (autoRotate) {
      earthGroup.rotation.y += dt * 0.006;
      const clouds = earthGroup.getObjectByName('clouds');
      if (clouds) clouds.rotation.y += dt * 0.0018;
    }

    const t = now / 1000;
    for (const [, entry] of focused) {
      const basePulse = 1 + 0.35 * (0.5 + 0.5 * Math.sin(t * 2.6));
      let ringScale = 0.16 * basePulse;
      let ringOpacity = 0.9 - 0.5 * (0.5 + 0.5 * Math.sin(t * 2.6));

      if (entry.arrivalPulse) {
        const ap = clamp((now - entry.arrivalPulse.start) / entry.arrivalPulse.duration, 0, 1);
        if (ap >= 1) {
          entry.arrivalPulse = null;
          if (entry.line) entry.line.material.opacity = 0.85; // restore baseline
        } else {
          const hot = Math.sin(ap * Math.PI); // 0 -> 1 -> 0 envelope
          ringScale *= 1 + hot * 1.8;
          ringOpacity = Math.min(1, ringOpacity + hot * 0.6);
          if (entry.line) entry.line.material.opacity = 0.85 + hot * 0.15;
        }
      }
      entry.ring.scale.set(ringScale, ringScale, 1);
      entry.ring.material.opacity = ringOpacity;
    }
    if (tcaMarker) {
      const pulse = 1 + 0.6 * (0.5 + 0.5 * Math.sin(t * 3.4));
      tcaMarker.scale.setScalar(pulse);
      tcaMarker.material.opacity = 1 - 0.6 * (0.5 + 0.5 * Math.sin(t * 3.4));
    }
    for (let i = burnFlares.length - 1; i >= 0; i--) {
      const f = burnFlares[i];
      const p = clamp((now - f.userData.burnStart) / 900, 0, 1);
      if (p >= 1) {
        focusGroup.remove(f); f.geometry.dispose(); f.material.dispose();
        burnFlares.splice(i, 1);
        continue;
      }
      f.scale.setScalar(1 + p * 5);
      f.material.opacity = (1 - p) * 0.9;
    }

    renderer.render(scene, camera);
  }
  requestAnimationFrame(tick);

  return {
    setObjects(objects) { rebuildCloud(objects); },
    focusTrajectory(objectId, trajectoryPoints, colorHex) { setFocusTrajectory(objectId, trajectoryPoints, colorHex); },
    unfocus(objectId) { unfocusObject(objectId); },
    unfocusAll() { for (const id of [...focused.keys()]) unfocusObject(id); },
    setSimMinutes(m) { simMinutes = m; applySimMinutes(); repositionGroundStations(); },
    showConjunctionAt(km) { showConjunctionMarker(kmToScene(km)); },
    clearConjunctionMarker,
    onSelect(cb) { selectCb = cb; },
    onHover(cb) { hoverCb = cb; },
    onFps(cb) { fpsCb = cb; },
    onInteracted(cb) { interactedCb = cb; },
    resetCamera() {
      flight = null;
      cam.targetTheta = DEFAULT_CAM.theta; cam.targetPhi = DEFAULT_CAM.phi; cam.targetRadius = DEFAULT_CAM.radius;
    },
    setAutoRotate(v) { autoRotate = v; },
    setCloudsVisible(v) {
      pendingCloudsVisible = v;
      const clouds = earthGroup.getObjectByName('clouds');
      if (clouds) clouds.visible = v;
    },
    focusCameraOn(scenePosA, scenePosB) {
      const mid = scenePosA.clone().add(scenePosB).multiplyScalar(0.5);
      const dist = Math.max(3.2, mid.length() * 1.4 + 1.4);
      cam.targetRadius = clamp(dist, MIN_R, MAX_R);
    },
    kmToScene,

    // ---- Explore Mode: cinematic framing + relevance dimming ----
    exploreObject(scenePos, opts = {}) {
      const f = computeFraming(scenePos, 3.2);
      startFlight(f.theta, f.phi, f.radius, opts.duration ?? 1800, opts.onDone);
    },
    exploreConjunction(scenePosA, scenePosB, opts = {}) {
      const mid = scenePosA.clone().add(scenePosB).multiplyScalar(0.5);
      const f = computeFraming(mid, 2.9);
      startFlight(f.theta, f.phi, f.radius, opts.duration ?? 2000, opts.onDone);
    },
    returnToGlobal(duration = 1500, onDone) {
      startFlight(DEFAULT_CAM.theta, DEFAULT_CAM.phi, DEFAULT_CAM.radius, duration, onDone);
    },
    setRelevance(relevantIds) { setRelevance(relevantIds); },
    clearRelevance() { clearRelevance(); },

    // ---- conjunction cinematics: live ruler, arrival pulse, burn flare ----
    setDistanceRuler(idA, idB) { setDistanceRuler(idA, idB); },
    clearDistanceRuler() { clearDistanceRuler(); },
    pulseArrival(objectId, duration) { pulseArrival(objectId, duration); },
    flashBurn(objectId) { flashBurn(objectId); },

    // ---- ground station coverage cones ----
    setGroundStations(stations) { setGroundStations(stations); },
    setGroundStationsVisible(visible) { groundStationGroup.visible = visible; },
    setStationActive(stationId, active) { setStationActive(stationId, active); },
    clearStationActive() { clearStationActive(); },
    setLandingMode(isActive) {
      cam.targetOffsetY = isActive ? 1.6 : 0;
      cam.targetRadius = isActive ? 5.0 : DEFAULT_CAM.radius;
      cam.targetTheta = isActive ? 0.3 : DEFAULT_CAM.theta;
      cam.targetPhi = isActive ? 1.4 : DEFAULT_CAM.phi;
      autoRotate = isActive;
    },
    enterMainFromLanding() {
      flight = {
        fromTheta: cam.theta, fromPhi: cam.phi, fromRadius: cam.radius, fromOffsetY: cam.offsetY,
        toTheta: DEFAULT_CAM.theta, toPhi: DEFAULT_CAM.phi, toRadius: DEFAULT_CAM.radius, toOffsetY: 0,
        start: performance.now(), duration: 2500, onDone: () => {}
      };
      autoRotate = false;
    }
  };
}

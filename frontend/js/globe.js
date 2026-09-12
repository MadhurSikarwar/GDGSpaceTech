import * as THREE from 'three';

// Scene scale: TEME kilometers -> scene units. Earth radius maps to 2.0 units.
const EARTH_RADIUS_KM = 6378.137;
const SCENE_EARTH_RADIUS = 2.0;
const KM_TO_SCENE = SCENE_EARTH_RADIUS / EARTH_RADIUS_KM;

const TYPE_COLOR = {
  SATELLITE: 0x56e3d1,
  DEBRIS: 0xf0a94e,
  ROCKET_BODY: 0xa99bf2,
  SYNTHETIC_DEBRIS: 0xf0616e,
  UNKNOWN: 0x8a95a8,
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
  const count = 3200;
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
  const mat = new THREE.PointsMaterial({ size: 0.055, vertexColors: true, sizeAttenuation: false, transparent: true, opacity: 0.85 });
  return new THREE.Points(geo, mat);
}

// Real NASA-derived imagery from the three.js example asset set, pinned to a
// tag via jsDelivr so it never breaks even if upstream reorganizes examples.
const TEXTURE_BASE = 'https://cdn.jsdelivr.net/gh/mrdoob/three.js@r128/examples/textures/planets/';

function buildLatLongGrid(radius) {
  const group = new THREE.Group();
  const material = new THREE.LineBasicMaterial({ color: 0x7ce9da, transparent: true, opacity: 0.10, toneMapped: false });
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

function dayNightMaterial(dayTex, nightTex) {
  dayTex.colorSpace = THREE.SRGBColorSpace;
  return new THREE.ShaderMaterial({
    uniforms: {
      dayTexture: { value: dayTex },
      nightTexture: { value: nightTex },
      sunDirection: { value: SUN_DIRECTION },
    },
    vertexShader: `
      varying vec2 vUv;
      varying vec3 vNormalW;
      void main() {
        vUv = uv;
        vNormalW = normalize( mat3(modelMatrix) * normal );
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }`,
    fragmentShader: `
      uniform sampler2D dayTexture;
      uniform sampler2D nightTexture;
      uniform vec3 sunDirection;
      varying vec2 vUv;
      varying vec3 vNormalW;
      void main() {
        float intensity = dot(vNormalW, normalize(sunDirection));
        float mixFactor = smoothstep(-0.2, 0.15, intensity);
        vec3 dayColor = texture2D(dayTexture, vUv).rgb;
        vec3 nightColor = texture2D(nightTexture, vUv).rgb * vec3(1.6, 1.4, 1.0) * 0.9;
        gl_FragColor = vec4(mix(nightColor, dayColor, mixFactor), 1.0);
      }`,
  });
}

function buildEarth() {
  const group = new THREE.Group();
  const geo = new THREE.SphereGeometry(SCENE_EARTH_RADIUS, 128, 128);

  // Procedural placeholder shown immediately; swapped for real imagery the
  // moment it finishes loading. Keeps the globe never blank, even offline.
  const earthMesh = new THREE.Mesh(geo, new THREE.MeshPhongMaterial({ map: buildEarthTexture(), shininess: 6, specular: 0x1a2636 }));
  group.add(earthMesh);

  const loader = new THREE.TextureLoader();
  const textures = {};
  const upgrade = () => {
    if (textures.day && textures.night) earthMesh.material = dayNightMaterial(textures.day, textures.night);
  };
  loader.load(TEXTURE_BASE + 'earth_atmos_2048.jpg', (tex) => { textures.day = tex; upgrade(); }, undefined, () => {});
  loader.load(TEXTURE_BASE + 'earth_lights_2048.png', (tex) => { textures.night = tex; upgrade(); }, undefined, () => {});
  loader.load(TEXTURE_BASE + 'earth_clouds_1024.png', (tex) => {
    const cloudsMesh = new THREE.Mesh(
      new THREE.SphereGeometry(SCENE_EARTH_RADIUS * 1.015, 96, 96),
      new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: 0.75, depthWrite: false })
    );
    cloudsMesh.name = 'clouds';
    cloudsMesh.visible = pendingCloudsVisible; // respect a toggle clicked before this async load resolved
    group.add(cloudsMesh);
  }, undefined, () => {});

  group.add(buildLatLongGrid(SCENE_EARTH_RADIUS * 1.004));
  group.add(buildAtmosphere());
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
  });
  return new THREE.Mesh(geo, mat);
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

  const earthGroup = buildEarth();
  scene.add(earthGroup);

  scene.add(new THREE.AmbientLight(0x1c2836, 1.5));
  const sun = new THREE.DirectionalLight(0xeaf3ff, 1.7);
  sun.position.set(5, 2, 3);
  scene.add(sun);

  // ---- camera orbit control (custom: drag to orbit, wheel to zoom) ----
  const DEFAULT_CAM = { theta: 0.9, phi: 1.15, radius: 6.2 };
  const cam = { theta: DEFAULT_CAM.theta, phi: DEFAULT_CAM.phi, radius: DEFAULT_CAM.radius, targetTheta: DEFAULT_CAM.theta, targetPhi: DEFAULT_CAM.phi, targetRadius: DEFAULT_CAM.radius };
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
      cam.radius * Math.cos(cam.phi),
      cam.radius * Math.sin(cam.phi) * Math.cos(cam.theta)
    );
    camera.lookAt(0, 0, 0);
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
    if (cloudMesh) { scene.remove(cloudMesh); cloudMesh.dispose(); cloudMesh = null; }
    if (glowMesh) { scene.remove(glowMesh); glowMesh.dispose(); glowMesh = null; }
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
    scene.add(glowMesh);
    scene.add(cloudMesh);
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
  scene.add(focusGroup);
  const focused = new Map(); // objectId -> { marker, line, trajectory, color, ring }

  function makeRingSprite(color) {
    // Rendered at 4x the display size (128px source for a ~0.16-unit sprite)
    // so the ring stays crisp under close zoom instead of pixelating.
    const c = document.createElement('canvas');
    c.width = 128; c.height = 128;
    const ctx = c.getContext('2d');
    ctx.strokeStyle = color; ctx.lineWidth = 6;
    ctx.beginPath(); ctx.arc(64, 64, 48, 0, Math.PI * 2); ctx.stroke();
    const tex = new THREE.CanvasTexture(c);
    tex.anisotropy = 4;
    const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, toneMapped: false });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.set(0.16, 0.16, 1);
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
          gl_FragColor = vec4(mix(color * 0.72, vec3(1.0), rim * 0.55), 1.0);
        }`,
    });
  }

  function focusObject(objectId, colorHex) {
    if (focused.has(objectId)) return focused.get(objectId);
    setInstanceHidden(objectId, true);
    const obj = objectsById.get(objectId);
    const markerGeo = new THREE.SphereGeometry(0.042, 32, 32);
    const markerMat = markerMaterial(colorHex);
    const marker = new THREE.Mesh(markerGeo, markerMat);
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

  function buildDirectionArrows(pts, baseColor) {
    if (pts.length < 4) return null;
    const mat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.75, toneMapped: false });
    const mesh = new THREE.InstancedMesh(arrowGeo, mat, ARROW_COUNT);
    mesh.frustumCulled = false;
    mesh.raycast = () => {};   // never steal a click from an object
    const dim = baseColor.clone().multiplyScalar(0.35);
    for (let a = 0; a < ARROW_COUNT; a++) {
      // skip the very ends so arrows don't sit on top of the marker
      const t = (a + 0.5) / ARROW_COUNT;
      const i = Math.min(pts.length - 2, Math.floor(t * (pts.length - 1)));
      arrowTangent.subVectors(pts[i + 1], pts[i]);
      if (arrowTangent.lengthSq() < 1e-12) continue;
      arrowTangent.normalize();
      arrowQuat.setFromUnitVectors(CONE_AXIS, arrowTangent);
      dummy.position.copy(pts[i]);
      dummy.quaternion.copy(arrowQuat);
      dummy.scale.setScalar(1);
      dummy.updateMatrix();
      mesh.setMatrixAt(a, dummy.matrix);
      mesh.setColorAt(a, tmpColor.copy(dim).lerp(baseColor, t));
    }
    dummy.quaternion.identity();   // leave the shared dummy neutral
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    return mesh;
  }

  function setFocusTrajectory(objectId, trajectoryPoints, colorHex) {
    const entry = focusObject(objectId, colorHex);
    entry.trajectory = trajectoryPoints;
    disposeTrajectoryVisuals(entry);

    const pts = trajectoryPoints.map((p) => kmToScene({ x: p.x, y: p.y, z: p.z }));
    const geo = new THREE.BufferGeometry().setFromPoints(pts);
    const colors = new Float32Array(pts.length * 3);
    const base = new THREE.Color(colorHex);
    const dim = base.clone().multiplyScalar(0.28);
    for (let i = 0; i < pts.length; i++) {
      const t = i / (pts.length - 1);
      const c = dim.clone().lerp(base, 1 - t * 0.8);
      colors[i * 3] = c.r; colors[i * 3 + 1] = c.g; colors[i * 3 + 2] = c.b;
    }
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    const mat = new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.85, toneMapped: false });
    entry.line = new THREE.Line(geo, mat);
    focusGroup.add(entry.line);
    entry.arrows = buildDirectionArrows(pts, base);
    if (entry.arrows) focusGroup.add(entry.arrows);
    positionEntry(entry);
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
      cam.targetTheta = cam.theta; cam.targetPhi = cam.phi; cam.targetRadius = cam.radius;
      if (ft >= 1) { const cb = flight.onDone; flight = null; cb && cb(); }
    } else {
      cam.theta = lerp(cam.theta, cam.targetTheta, 0.14);
      cam.phi = lerp(cam.phi, cam.targetPhi, 0.14);
      cam.radius = lerp(cam.radius, cam.targetRadius, 0.14);
    }
    applyCamera();

    if (autoRotate) {
      earthGroup.rotation.y += dt * 0.006;
      const clouds = earthGroup.getObjectByName('clouds');
      if (clouds) clouds.rotation.y += dt * 0.0018;
    }

    const t = now / 1000;
    for (const [, entry] of focused) {
      const pulse = 1 + 0.35 * (0.5 + 0.5 * Math.sin(t * 2.6));
      entry.ring.scale.set(0.16 * pulse, 0.16 * pulse, 1);
      entry.ring.material.opacity = 0.9 - 0.5 * (0.5 + 0.5 * Math.sin(t * 2.6));
    }
    if (tcaMarker) {
      const pulse = 1 + 0.6 * (0.5 + 0.5 * Math.sin(t * 3.4));
      tcaMarker.scale.setScalar(pulse);
      tcaMarker.material.opacity = 1 - 0.6 * (0.5 + 0.5 * Math.sin(t * 3.4));
    }

    renderer.render(scene, camera);
  }
  requestAnimationFrame(tick);

  return {
    setObjects(objects) { rebuildCloud(objects); },
    focusTrajectory(objectId, trajectoryPoints, colorHex) { setFocusTrajectory(objectId, trajectoryPoints, colorHex); },
    unfocus(objectId) { unfocusObject(objectId); },
    unfocusAll() { for (const id of [...focused.keys()]) unfocusObject(id); },
    setSimMinutes(m) { simMinutes = m; applySimMinutes(); },
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
  };
}

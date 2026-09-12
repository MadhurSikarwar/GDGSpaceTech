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

  function computeFraming(scenePos, extra) {
    const dist = scenePos.length();
    if (dist < 1e-6) return { theta: cam.theta, phi: cam.phi, radius: cam.radius };
    const dir = scenePos.clone().divideScalar(dist);
    const theta = Math.atan2(dir.x, dir.z);
    const phi = Math.acos(clamp(dir.y, -1, 1));
    const radius = clamp(dist + extra, 2.3, MAX_R);
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
  const cloudGeo = new THREE.IcosahedronGeometry(0.024, 2);
  const cloudMat = new THREE.MeshBasicMaterial({ toneMapped: false });
  let cloudMesh = null;
  let indexToId = [];
  let idToIndex = new Map();
  let objectsById = new Map();
  const hiddenIndices = new Set();

  function rebuildCloud(objects) {
    if (cloudMesh) { scene.remove(cloudMesh); cloudMesh.geometry.dispose?.(); }
    indexToId = objects.map((o) => o.object_id);
    idToIndex = new Map(indexToId.map((id, i) => [id, i]));
    objectsById = new Map(objects.map((o) => [o.object_id, o]));
    hiddenIndices.clear();
    if (!objects.length) { cloudMesh = null; return; }

    cloudMesh = new THREE.InstancedMesh(cloudGeo, cloudMat, objects.length);
    objects.forEach((o, i) => {
      const p = o.state ? kmToScene(o.state.position_km) : new THREE.Vector3();
      dummy.position.copy(p);
      dummy.scale.setScalar(1);
      dummy.updateMatrix();
      cloudMesh.setMatrixAt(i, dummy.matrix);
      cloudMesh.setColorAt(i, tmpColor.setHex(TYPE_COLOR[o.object_type] ?? TYPE_COLOR.UNKNOWN));
    });
    cloudMesh.instanceMatrix.needsUpdate = true;
    if (cloudMesh.instanceColor) cloudMesh.instanceColor.needsUpdate = true;
    scene.add(cloudMesh);
  }

  function setInstanceHidden(objectId, hidden) {
    const i = idToIndex.get(objectId);
    if (i === undefined || !cloudMesh) return;
    const obj = objectsById.get(objectId);
    const p = obj?.state ? kmToScene(obj.state.position_km) : new THREE.Vector3();
    dummy.position.copy(p);
    dummy.scale.setScalar(hidden ? 0 : 1);
    dummy.updateMatrix();
    cloudMesh.setMatrixAt(i, dummy.matrix);
    cloudMesh.instanceMatrix.needsUpdate = true;
    if (hidden) hiddenIndices.add(objectId); else hiddenIndices.delete(objectId);
  }

  // ---- Explore Mode relevance dimming: reuses the same per-instance
  // matrix/color update as setInstanceHidden above, just with a partial
  // scale/color reduction instead of a full hide. ----
  function applyRelevance(objectId, dimmed) {
    const i = idToIndex.get(objectId);
    if (i === undefined || !cloudMesh) return;
    if (hiddenIndices.has(objectId)) return; // already hidden as a focused marker; leave it
    const obj = objectsById.get(objectId);
    const p = obj?.state ? kmToScene(obj.state.position_km) : new THREE.Vector3();
    dummy.position.copy(p);
    dummy.scale.setScalar(dimmed ? 0.55 : 1);
    dummy.updateMatrix();
    cloudMesh.setMatrixAt(i, dummy.matrix);
    const baseHex = TYPE_COLOR[obj?.object_type] ?? TYPE_COLOR.UNKNOWN;
    tmpColor.setHex(baseHex);
    if (dimmed) tmpColor.multiplyScalar(0.32);
    cloudMesh.setColorAt(i, tmpColor);
    cloudMesh.instanceMatrix.needsUpdate = true;
    if (cloudMesh.instanceColor) cloudMesh.instanceColor.needsUpdate = true;
  }

  function setRelevance(relevantIds) {
    for (const id of indexToId) applyRelevance(id, !relevantIds.has(id));
  }

  function clearRelevance() {
    for (const id of indexToId) applyRelevance(id, false);
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

  function unfocusObject(objectId) {
    const entry = focused.get(objectId);
    if (!entry) return;
    focusGroup.remove(entry.marker);
    focusGroup.remove(entry.ring);
    if (entry.line) focusGroup.remove(entry.line);
    setInstanceHidden(objectId, false);
    focused.delete(objectId);
  }

  function setFocusTrajectory(objectId, trajectoryPoints, colorHex) {
    const entry = focusObject(objectId, colorHex);
    entry.trajectory = trajectoryPoints;
    if (entry.line) focusGroup.remove(entry.line);

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
      const f = computeFraming(scenePos, 1.1);
      startFlight(f.theta, f.phi, f.radius, opts.duration ?? 1800, opts.onDone);
    },
    exploreConjunction(scenePosA, scenePosB, opts = {}) {
      const mid = scenePosA.clone().add(scenePosB).multiplyScalar(0.5);
      const f = computeFraming(mid, 0.75);
      startFlight(f.theta, f.phi, f.radius, opts.duration ?? 2000, opts.onDone);
    },
    returnToGlobal(duration = 1500, onDone) {
      startFlight(DEFAULT_CAM.theta, DEFAULT_CAM.phi, DEFAULT_CAM.radius, duration, onDone);
    },
    setRelevance(relevantIds) { setRelevance(relevantIds); },
    clearRelevance() { clearRelevance(); },
  };
}

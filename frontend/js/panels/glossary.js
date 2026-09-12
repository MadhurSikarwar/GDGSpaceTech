import { icons } from '../icons.js';
import { escapeHtml } from '../utils.js';

const BOOK_SVG = (icons && icons.book)
  ? icons.book
  : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>`;

export const GLOSSARY_ITEMS = [
  {
    term: 'TCA',
    fullName: 'Time of Closest Approach',
    category: 'COLLISION',
    desc: 'The exact future UTC timestamp at which two orbiting objects will reach their minimum separation distance in 3D space.',
    detail: 'Calculated via SGP4 fine trajectory screening using adaptive time-stepping around the conjunction window.'
  },
  {
    term: 'Pc',
    fullName: 'Probability of Collision',
    category: 'COLLISION',
    desc: 'The mathematical likelihood (ranging from 0.0 to 1.0) that two objects will physically collide during a close encounter.',
    detail: 'Evaluated using the Foster (1992) 2D method by projecting 3D position covariance onto the encounter b-plane and integrating over the combined Hard-Body Radius (HBR) disk.'
  },
  {
    term: 'Foster 2D Method',
    fullName: 'Encounter Plane Covariance Integration',
    category: 'COLLISION',
    desc: 'The industry-standard analytical method for computing collision probability during short-duration orbital encounters.',
    detail: 'Transforms 3D position uncertainty ellipsoids of primary and secondary objects onto the 2D collision b-plane perpendicular to relative velocity.'
  },
  {
    term: 'HBR',
    fullName: 'Hard-Body Radius',
    category: 'COLLISION',
    desc: 'The combined physical collision sphere radius representing the spatial extent of both spacecraft and debris bodies.',
    detail: 'If the center-to-center miss distance at TCA falls within the combined HBR boundary (default 20 m), physical contact occurs.'
  },
  {
    term: 'Covariance Realism',
    fullName: 'Empirical Position Uncertainty Ellipsoid',
    category: 'COLLISION',
    desc: 'A 3D spatial uncertainty bubble representing where the space object actually resides versus where the TLE ephemeris predicts it.',
    detail: 'Decomposed into Radial, In-track, and Cross-track (RIC) axes. In-track error grows secularly with TLE data age due to unmodeled upper-atmospheric drag.'
  },
  {
    term: 'Synthetic Debris',
    fullName: 'Deterministic Conjunction Target',
    category: 'COLLISION',
    desc: 'Programmatically engineered debris objects injected with orbital parameters designed to cross an active satellite’s trajectory at a safe test altitude.',
    detail: 'Enables deterministic end-to-end testing and live mission demonstrations of the entire multi-agent screening and maneuver pipeline.'
  },
  {
    term: 'SGP4',
    fullName: 'Simplified General Perturbations 4',
    category: 'ORBITS',
    desc: 'The standard NASA/NORAD analytical mathematical propagator calculating satellite ephemeris positions and velocities from TLE orbital elements.',
    detail: 'Accounts for Earth oblateness (J2, J3, J4 zonal harmonics), atmospheric drag perturbations, and solar/lunar gravitational third-body effects.'
  },
  {
    term: 'TLE / OMM',
    fullName: 'Two-Line Element / Orbit Mean-Elements Message',
    category: 'ORBITS',
    desc: 'Standard data formats published by CelesTrak and Space-Track encoding a space object’s orbital elements at a specific epoch timestamp.',
    detail: 'OMM is the modern XML/JSON successor to classic 140-character Two-Line Element (TLE) ASCII sets.'
  },
  {
    term: 'TEME',
    fullName: 'True Equator, Mean Equinox Frame',
    category: 'ORBITS',
    desc: 'The Earth-Centered Inertial (ECI) coordinate frame natively produced by SGP4 propagators where Cartesian state vectors live.',
    detail: 'X-axis points towards the vernal equinox, Z-axis points along Earth’s true rotational axis, and Y completes the right-handed triad.'
  },
  {
    term: 'RIC / RSW',
    fullName: 'Radial, In-track, Cross-track Frame',
    category: 'ORBITS',
    desc: 'A spacecraft-centered local orbital coordinate system aligned with the satellite’s instantaneous motion.',
    detail: 'Radial (R) points outward from Earth center; In-track (I) points along velocity; Cross-track (C) points along orbital angular momentum normal to the orbit plane.'
  },
  {
    term: 'LEO / MEO / GEO',
    fullName: 'Orbital Altitude Regimes',
    category: 'ORBITS',
    desc: 'Low Earth Orbit (LEO, 160–2,000 km, dense debris regime), Medium Earth Orbit (MEO, GPS/navigation constellations), and Geostationary Earth Orbit (GEO, ~35,786 km).',
    detail: 'LEO spacecraft experience significant atmospheric drag requiring continuous space weather drag adjustments.'
  },
  {
    term: 'SSO',
    fullName: 'Sun-Synchronous Orbit',
    category: 'ORBITS',
    desc: 'A near-polar retrograded orbit whose orbital plane precesses around Earth at the same rate the Earth orbits the Sun.',
    detail: 'Maintains constant solar illumination angles, making it the most congested orbital regime for Earth observation and reconnaissance satellites.'
  },
  {
    term: 'Apogee & Perigee',
    fullName: 'Apsidal Orbital Extremes',
    category: 'ORBITS',
    desc: 'Perigee is the point of closest approach to Earth center (maximum orbital speed); Apogee is the point of greatest distance (minimum speed).',
    detail: 'Atmospheric drag perturbations are concentrated at perigee, secularly circularizing eccentric orbits by lowering apogee over time.'
  },
  {
    term: 'BSTAR (B*)',
    fullName: 'SGP4 Aerodynamic Drag Coefficient',
    category: 'ORBITS',
    desc: 'An empirical parameter in TLE sets representing the satellite’s ballistic coefficient multiplied by atmospheric density scale.',
    detail: 'Measured in units of 1/Earth-radii. High B* values signify rapid orbital energy loss and accelerated altitude decay.'
  },
  {
    term: 'CW Equations',
    fullName: 'Clohessy-Wiltshire (Hill-CW) Equations',
    category: 'MANEUVERS',
    desc: 'Linearized differential equations of relative orbital motion between two objects in circular or near-circular orbits.',
    detail: 'Used by the Maneuver Agent to analytically calculate how an impulsive delta-V burn at t0 alters the satellite’s relative position at TCA.'
  },
  {
    term: 'Delta-V (Δv)',
    fullName: 'Velocity Impulse Vector',
    category: 'MANEUVERS',
    desc: 'The change in spacecraft velocity (measured in m/s) required to modify its trajectory, directly proportional to onboard fuel expenditure.',
    detail: 'Decomposed in the RIC frame (Δvr, Δvi, Δvc) to evaluate fuel-optimal avoidance burns that provide safe separation while minimizing propellant usage.'
  },
  {
    term: 'SLSQP',
    fullName: 'Sequential Least Squares Programming',
    category: 'MANEUVERS',
    desc: 'A gradient-based numerical optimization algorithm implemented via SciPy to solve constrained non-linear minimization problems.',
    detail: 'Minimizes ||Δv|| subject to two hard constraints: predicted Pc ≤ 1e-4 and semi-major axis drift ≤ 5.0 km.'
  },
  {
    term: 'SMA Drift',
    fullName: 'Semi-Major Axis Drift Constraint',
    category: 'MANEUVERS',
    desc: 'A physical constraint ensuring that an avoidance maneuver does not permanently alter the satellite’s operational orbital slot.',
    detail: 'Bounding SMA change (|Δa| ≤ 5 km) guarantees the satellite can return to its operational constellation station without excessive fuel penalties.'
  },
  {
    term: 'Posigrade / Retrograde',
    fullName: 'Along-Track Burn Directions',
    category: 'MANEUVERS',
    desc: 'Posigrade thrust fires in the direction of orbital velocity (raising orbital altitude). Retrograde fires opposing velocity (lowering altitude).',
    detail: 'Along-track burns produce the largest miss-distance displacement at TCA per unit of delta-V expended.'
  },
  {
    term: 'Normal / Antinormal',
    fullName: 'Cross-Track Burn Directions',
    category: 'MANEUVERS',
    desc: 'Thrust vectors directed perpendicular to the orbital plane, altering orbital inclination without changing altitude.',
    detail: 'Effective for resolving conjunctions where the secondary object passes through the target’s orbital plane at high relative velocity.'
  },
  {
    term: 'Approval Gate',
    fullName: 'Human-in-the-Loop Mission Governance',
    category: 'MANEUVERS',
    desc: 'A mandatory safety operational gate ensuring that automated AI recommendations require explicit confirmation by a human flight director before simulation execution.',
    detail: 'Prevents unintended propulsion commanding, provides an active rejection banner, and allows switching between companion burn candidates.'
  },
  {
    term: 'NOAA SWPC',
    fullName: 'Space Weather Prediction Center',
    category: 'WEATHER',
    desc: 'The official US agency providing real-time solar radiation and geomagnetic activity observations.',
    detail: 'OrbitalGuard pulls live SWPC feeds to dynamically calculate upper atmospheric heating and LEO drag activity scalars.'
  },
  {
    term: 'F10.7 cm Flux',
    fullName: 'Solar Radio Flux at 2800 MHz',
    category: 'WEATHER',
    desc: 'A daily measurement of solar radio emissions at 10.7 cm wavelength, serving as the primary proxy for solar EUV upper-atmosphere heating.',
    detail: 'Elevated F10.7 increases thermospheric density, accelerating satellite orbital decay and expanding in-track positional uncertainty.'
  },
  {
    term: 'Kp & Ap Indices',
    fullName: 'Planetary Geomagnetic Activity Indices',
    category: 'WEATHER',
    desc: 'Geomagnetic disturbance scales measuring fluctuations in Earth’s magnetic field caused by solar wind and coronal mass ejections.',
    detail: 'Kp is quasi-logarithmic (0-9), while Ap is linear (0-400 nT). Running Ap directly scales OrbitalGuard’s thermospheric drag activity multiplier.'
  },
  {
    term: 'AOS / LOS',
    fullName: 'Acquisition / Loss of Signal',
    category: 'GROUND',
    desc: 'The exact UTC horizon crossing times when a satellite comes into line-of-sight view (AOS) and dips out of view (LOS) of a ground station.',
    detail: 'Computed topocentrically with WGS84 coordinates and a 10° minimum elevation mask for Svalbard, Fairbanks, and McMurdo stations.'
  },
  {
    term: 'Topocentric Mask',
    fullName: 'Minimum Horizon Elevation Angle',
    category: 'GROUND',
    desc: 'The minimum angle above the local horizon (standard 10°) required for reliable radio frequency communications.',
    detail: 'Filters out terrain clutter, atmospheric refraction distortions, and ground thermal noise during telemetry passes.'
  },
  {
    term: 'Polar Ground Network',
    fullName: 'High-Latitude Tracking Infrastructure',
    category: 'GROUND',
    desc: 'Strategic high-latitude ground stations (Svalbard SG3 at 78.2°N, Fairbanks AK1 at 64.9°N, McMurdo AQ2 at 77.8°S).',
    detail: 'High-latitude placement guarantees line-of-sight contact on nearly every 90-minute orbit for polar and sun-synchronous satellites.'
  }
];

let activeCategory = 'ALL';
let currentSearch = '';

export function initGlossary({ onToggleOtherDrawer } = {}) {
  const toggleBtn = document.getElementById('glossaryToggle');
  const drawer = document.getElementById('glossaryDrawer');
  const closeBtn = document.getElementById('glossaryClose');
  const searchInput = document.getElementById('glossarySearch');
  const catsContainer = document.getElementById('glossaryCats');

  const toggleIcon = document.getElementById('glossaryToggleIcon');
  if (toggleIcon) toggleIcon.innerHTML = BOOK_SVG;

  const headIcon = document.getElementById('glossaryHeadIcon');
  if (headIcon) headIcon.innerHTML = BOOK_SVG;

  const toggle = () => {
    const willOpen = !drawer.classList.contains('open');
    if (willOpen && onToggleOtherDrawer) onToggleOtherDrawer();
    drawer.classList.toggle('open', willOpen);
    if (willOpen && searchInput) {
      setTimeout(() => searchInput.focus(), 150);
    }
  };

  const close = () => {
    drawer.classList.remove('open');
  };

  if (toggleBtn) toggleBtn.addEventListener('click', toggle);
  if (closeBtn) closeBtn.addEventListener('click', close);

  // Close on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && drawer.classList.contains('open')) close();
  });

  // Click outside to dismiss
  document.addEventListener('click', (e) => {
    if (!drawer.classList.contains('open')) return;
    if (e.target.closest('#glossaryDrawer') || e.target.closest('#glossaryToggle') || e.target.closest('[data-glossary]')) {
      return;
    }
    close();
  });

  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      currentSearch = e.target.value.trim().toLowerCase();
      renderGlossary();
    });
  }

  if (catsContainer) {
    catsContainer.addEventListener('click', (e) => {
      const btn = e.target.closest('.g-cat');
      if (!btn) return;
      activeCategory = btn.dataset.cat;
      catsContainer.querySelectorAll('.g-cat').forEach((b) => b.classList.toggle('active', b === btn));
      renderGlossary();
    });
  }

  renderGlossary();

  return {
    toggle,
    open: () => {
      if (onToggleOtherDrawer) onToggleOtherDrawer();
      drawer.classList.add('open');
      if (searchInput) setTimeout(() => searchInput.focus(), 150);
    },
    close,
    search: (term) => {
      activeCategory = 'ALL';
      if (catsContainer) {
        catsContainer.querySelectorAll('.g-cat').forEach((b) => b.classList.toggle('active', b.dataset.cat === 'ALL'));
      }
      currentSearch = (term || '').toLowerCase();
      if (searchInput) searchInput.value = term || '';
      renderGlossary();
      if (onToggleOtherDrawer) onToggleOtherDrawer();
      drawer.classList.add('open');
      if (searchInput) setTimeout(() => searchInput.focus(), 150);
    }
  };
}

function renderGlossary() {
  const list = document.getElementById('glossaryList');
  if (!list) return;

  const filtered = GLOSSARY_ITEMS.filter((item) => {
    const matchesCat = activeCategory === 'ALL' || item.category === activeCategory;
    if (!matchesCat) return false;
    if (!currentSearch) return true;
    return (
      item.term.toLowerCase().includes(currentSearch) ||
      item.fullName.toLowerCase().includes(currentSearch) ||
      item.desc.toLowerCase().includes(currentSearch) ||
      item.detail.toLowerCase().includes(currentSearch)
    );
  });

  if (filtered.length === 0) {
    const searchIcon = (icons && icons.search) ? icons.search : '🔍';
    list.innerHTML = `
      <div class="glossary-empty">
        ${searchIcon}
        <p>No matching acronyms or terms found for "${escapeHtml(currentSearch)}".</p>
      </div>`;
    return;
  }

  list.innerHTML = filtered.map((item) => `
    <div class="glossary-card">
      <div class="gc-head">
        <div class="gc-title-wrap">
          <span class="gc-term">${escapeHtml(item.term)}</span>
          <span class="gc-fullname">${escapeHtml(item.fullName)}</span>
        </div>
        <span class="gc-badge cat-${item.category.toLowerCase()}">${escapeHtml(item.category)}</span>
      </div>
      <div class="gc-desc">${escapeHtml(item.desc)}</div>
      <div class="gc-detail">${escapeHtml(item.detail)}</div>
    </div>
  `).join('');
}

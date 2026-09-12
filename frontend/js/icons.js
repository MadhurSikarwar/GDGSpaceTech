// Small hand-rolled line-icon set (stroke-based, currentColor). No icon font/library.

const wrap = (body, vb = 24) =>
  `<svg viewBox="0 0 ${vb} ${vb}" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;

export const icons = {
  logo: wrap(`<circle cx="12" cy="12" r="2.1" fill="currentColor" stroke="none"/><ellipse cx="12" cy="12" rx="9.5" ry="4" transform="rotate(20 12 12)"/><ellipse cx="12" cy="12" rx="9.5" ry="4" transform="rotate(-35 12 12)"/><circle cx="20.3" cy="8.9" r="1" fill="currentColor" stroke="none"/>`),

  satellite: wrap(`<rect x="9.5" y="9.5" width="5" height="5" rx="0.6" transform="rotate(45 12 12)"/><path d="M4 8l3 3M20 8l-3 3M4 16l3-3M20 16l-3-3"/><path d="M12 2v2.4M12 19.6V22"/>`),
  debris: wrap(`<path d="M5 9l3-4 4 1 3-3 4 3-1 4 3 3-3 4 1 4-4-1-3 3-4-3 1-4-4-3z"/>`),
  rocketBody: wrap(`<path d="M12 2c2.2 2.4 3 5.4 3 9v6l-3 3-3-3v-6c0-3.6.8-6.6 3-9z"/><path d="M9 15l-3 1.5V20M15 15l3 1.5V20"/>`),
  unknown: wrap(`<circle cx="12" cy="12" r="9"/><path d="M9.3 9.3a2.7 2.7 0 1 1 3.9 2.4c-.9.5-1.2 1-1.2 2"/><circle cx="12" cy="16.6" r="0.15" fill="currentColor"/>`),

  search: wrap(`<circle cx="10.5" cy="10.5" r="6.5"/><path d="M20 20l-4.8-4.8"/>`),
  chevronRight: wrap(`<path d="M9 5l7 7-7 7"/>`),
  close: wrap(`<path d="M6 6l12 12M18 6L6 18"/>`),
  check: wrap(`<path d="M4 12l5.5 5.5L20 6"/>`),
  alertTriangle: wrap(`<path d="M12 3.5l9.5 16.5H2.5z"/><path d="M12 10v4.2"/><circle cx="12" cy="17" r="0.15" fill="currentColor"/>`),
  info: wrap(`<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5"/><circle cx="12" cy="7.6" r="0.2" fill="currentColor"/>`),
  inbox: wrap(`<path d="M3 12h5l2 3h4l2-3h5"/><path d="M5.5 5h13L21 12v6a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18v-6z"/>`),
  radar: wrap(`<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5.4"/><circle cx="12" cy="12" r="1.7"/><path d="M12 12L19 7"/>`),

  play: wrap(`<path d="M7 5l12 7-12 7z"/>`),
  pause: wrap(`<path d="M7 5v14M17 5v14"/>`),
  rewind: wrap(`<path d="M20 6v12M13 12l7-6v12z"/><path d="M13 12l-9-6v12z"/>`),
  target: wrap(`<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/><path d="M12 2v3.4M12 18.6V22M2 12h3.4M18.6 12H22"/>`),

  arrowUp: wrap(`<path d="M12 19V5M6 11l6-6 6 6"/>`),
  arrowDown: wrap(`<path d="M12 5v14M18 13l-6 6-6-6"/>`),
  arrowSide: wrap(`<path d="M5 12h14M13 6l6 6-6 6"/>`),

  link: wrap(`<path d="M9.5 14.5l5-5"/><path d="M8 16.5l-1.8 1.8a3 3 0 0 1-4.2-4.2L4 12M16 7.5l1.8-1.8a3 3 0 0 1 4.2 4.2L20 12"/>`),
  terminal: wrap(`<rect x="2.5" y="4" width="19" height="16" rx="1.3"/><path d="M6.5 9.5l3.5 2.5-3.5 2.5M13 15h4.5"/>`),
  liveDot: wrap(`<circle cx="12" cy="12" r="5" fill="currentColor" stroke="none"/>`),
  fly: wrap(`<path d="M12 3.5v17M4.5 12h15"/><circle cx="12" cy="12" r="7.5"/>`),
  reset: wrap(`<path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4v5h-5"/>`),
  flag: wrap(`<path d="M6 21V4"/><path d="M6 4.5h12l-3 3.75 3 3.75H6"/>`),
  book: wrap(`<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>`),
};

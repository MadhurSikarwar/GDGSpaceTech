import { del, get, post } from '../api.js';
import { updateAlertBadge } from '../app.js';
import { empty, errorBox, fmt, h, loading, objLink, riskBadge, table, toast, typeTag } from '../ui.js';

export async function render(root) {
  root.appendChild(h(`<div class="page-head"><div><div class="eyebrow">Alerts &amp; subscriptions</div><h1>My alerts</h1>
    <p>When the screening job records a new close approach involving an object you subscribe to, an alert appears here
    (and in your inbox, if you turn on e-mail alerts).</p></div><div class="row"><a class="btn" href="#/account">Notification settings</a></div></div>`));
  const grid = h(`<div class="stack"></div>`);
  root.appendChild(grid);

  const alertsCard = h(`<section class="card flush"><div class="card-head"><h2>Alerts</h2>
    <div class="row"><div class="view-toggle" role="group" aria-label="Which alerts">
      <button data-s="unacknowledged" class="active">Unacknowledged</button><button data-s="all">All</button></div>
      <button class="btn sm" id="ackAll">Acknowledge all</button></div></div><div class="body">${loading()}</div></section>`);
  const subsCard = h(`<section class="card flush"><div class="card-head"><h2>Subscriptions</h2>
    <form class="row" id="subForm"><input type="number" name="norad" min="1" placeholder="NORAD number" style="width:150px" required>
    <button class="btn sm primary">Subscribe</button></form></div><div class="body">${loading()}</div></section>`);
  grid.append(alertsCard, subsCard);

  let status = 'unacknowledged';
  async function loadAlerts() {
    const body = alertsCard.querySelector('.body');
    try {
      const { items } = await get(`/me/alerts?status=${status}`);
      body.innerHTML = '';
      if (!items.length) {
        body.innerHTML = empty(status === 'all' ? 'No alerts yet' : 'You are all caught up',
          ' Alerts appear when a subscribed object gets a new close approach.');
        return;
      }
      body.appendChild(table([
        { label: 'Alerted', render: (r) => `${fmt.dt(r.sent_on)}<div class="small muted">${fmt.rel(r.sent_on)}</div>` },
        { label: 'Approach', render: (r) => `${objLink(r.primary_norad, r.primary_name)} <span class="muted">vs</span> ${objLink(r.secondary_norad, r.secondary_name)}
           <div class="small muted">TCA ${fmt.dt(r.time_of_closest_approach)} (${fmt.rel(r.time_of_closest_approach)})</div>` },
        { label: 'Miss', num: true, render: (r) => fmt.km(r.miss_distance_km, 3) },
        { label: 'Risk', render: (r) => riskBadge(r.risk_level) },
        { label: '', render: (r) => (r.acknowledged ? `<span class="small muted">acknowledged ${fmt.rel(r.acknowledged_on)}</span>`
          : `<button class="btn sm" data-ack="${r.alert_id}">Acknowledge</button>`)
          + ` <a class="btn sm ghost" href="#/globe?event=${r.event_id}">3D</a>` },
      ], items));
    } catch (err) { body.innerHTML = errorBox(err); }
  }

  async function loadSubs() {
    const body = subsCard.querySelector('.body');
    try {
      const { items } = await get('/me/subscriptions');
      body.innerHTML = '';
      if (!items.length) {
        body.innerHTML = empty('No subscriptions', ' Open any object in the catalogue and choose “Subscribe to alerts”.');
        return;
      }
      body.appendChild(table([
        { label: 'Object', render: (r) => `${objLink(r.norad_id, r.name)}<div class="small mono muted">NORAD ${r.norad_id}</div>` },
        { label: 'Type', render: (r) => typeTag(r.object_type) },
        { label: 'Next approach', render: (r) => (r.next_approach ? `${fmt.dt(r.next_approach)}<div class="small muted">${fmt.rel(r.next_approach)}</div>` : '<span class="muted">none predicted</span>') },
        { label: 'Open alerts', num: true, render: (r) => fmt.int(r.unacknowledged) },
        { label: 'Since', render: (r) => fmt.date(r.subscribed_on) },
        { label: '', render: (r) => `<button class="btn sm danger" data-unsub="${r.norad_id}">Unsubscribe</button>` },
      ], items));
    } catch (err) { body.innerHTML = errorBox(err); }
  }

  alertsCard.addEventListener('click', async (e) => {
    const t = e.target.closest('[data-s]');
    if (t) {
      status = t.dataset.s;
      alertsCard.querySelectorAll('[data-s]').forEach((b) => b.classList.toggle('active', b === t));
      return loadAlerts();
    }
    const ack = e.target.closest('[data-ack]');
    if (ack) {
      ack.disabled = true;
      try { await post(`/me/alerts/${ack.dataset.ack}/ack`); await loadAlerts(); updateAlertBadge(); loadSubs(); } catch (err) { toast(err.message, 'error'); }
    }
  });
  alertsCard.querySelector('#ackAll').addEventListener('click', async () => {
    try {
      const r = await post('/me/alerts/ack-all');
      toast(`${r.acknowledged} alert${r.acknowledged === 1 ? '' : 's'} acknowledged`);
      loadAlerts(); loadSubs(); updateAlertBadge();
    } catch (err) { toast(err.message, 'error'); }
  });
  subsCard.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-unsub]');
    if (!b) return;
    try { await del(`/me/subscriptions/${b.dataset.unsub}`); toast('Unsubscribed'); loadSubs(); } catch (err) { toast(err.message, 'error'); }
  });
  subsCard.querySelector('#subForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const n = Number(e.target.norad.value);
    try { await post('/me/subscriptions', { norad_id: n }); e.target.reset(); toast(`Subscribed to NORAD ${n}`); loadSubs(); } catch (err) { toast(err.message, 'error'); }
  });
  loadAlerts();
  loadSubs();
}

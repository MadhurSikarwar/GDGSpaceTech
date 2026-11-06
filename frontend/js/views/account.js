// Account: notification preferences and password change.
import { del, get, post, put } from '../api.js';
import { deviceAlerts } from '../app.js';
import { deviceName } from '../logic.js';
import { errorBox, esc, fmt, h, hashQuery, loading, toast } from '../ui.js';

const RISKS = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];

export async function render(root, { app }) {
  const q = hashQuery();
  root.appendChild(h(`<div class="page-head"><div><div class="eyebrow">Account</div><h1>${esc(app.user.name)}</h1>
    <p><span class="mono">${esc(app.user.email)}</span> · ${esc(app.user.role_label)}</p></div></div>`));
  const grid = h(`<div class="acct-grid">
    <section class="card bezel" id="notif"><div class="card-head"><h2>Notifications</h2><span class="sub">close-approach alerts</span></div>${loading()}</section>
    <section class="card" id="devices"><div class="card-head"><h2>Notifications on your devices</h2><span class="sub">push · this browser</span></div>${loading()}</section>
    <section class="card" id="pw"><div class="card-head"><h2>Change password</h2><span class="sub">signs out your other sessions</span></div>
      <form class="stack" autocomplete="on" novalidate>
        <input type="email" name="username" autocomplete="username" value="${esc(app.user.email)}" hidden>
        <label class="field"><span>Current password</span><input type="password" name="current_password" autocomplete="current-password" required></label>
        <label class="field"><span>New password</span><input type="password" name="new_password" minlength="8" maxlength="72" autocomplete="new-password" required>
          <span class="hint">At least 8 characters (at most 72 bytes).</span></label>
        <label class="field"><span>Repeat new password</span><input type="password" name="repeat" autocomplete="new-password" required></label>
        <div class="form-error" aria-live="polite"></div>
        <div class="row"><button class="btn primary" type="submit">Change password</button>
          <a class="small" href="#/forgot">Forgot your password?</a></div>
      </form></section></div>`);
  root.appendChild(grid);
  if (q.tab === 'password') setTimeout(() => grid.querySelector('#pw input[name=current_password]').focus(), 50);

  // ---- notification preferences -------------------------------------------
  const notif = grid.querySelector('#notif');
  try {
    const n = await get('/me/notifications');
    notif.innerHTML = `<div class="card-head"><h2>Notifications</h2><span class="sub">close-approach alerts</span></div>
      <form class="stack" id="prefs">
        <p class="small muted" style="margin:0">Alerts for objects you follow always appear in the app (the bell). You can also receive them by e-mail at
          <span class="mono">${esc(n.email)}</span>.</p>
        ${n.email_delivery ? '' : '<div class="banner warn" style="margin:0"><div><strong>E-mail delivery is not configured on this server.</strong> Your choices are saved; e-mails are queued and sent once an administrator configures SMTP.</div></div>'}
        <label class="check"><input type="checkbox" name="email_alerts" ${n.prefs.email_alerts ? 'checked' : ''}> E-mail me new close-approach alerts</label>
        <label class="field"><span>Only for risk level and above</span><select name="min_risk_level">
          ${RISKS.map((r) => `<option ${n.prefs.min_risk_level === r ? 'selected' : ''}>${r}</option>`).join('')}</select></label>
        ${app.can('analyst') ? `<label class="check"><input type="checkbox" name="email_decisions" ${n.prefs.email_decisions ? 'checked' : ''}> E-mail me when a manoeuvre is approved or rejected</label>` : ''}
        <div class="form-ok" aria-live="polite"></div>
        <div class="row"><button class="btn primary" type="submit">Save preferences</button>
          ${app.can('admin') ? '<button class="btn" type="button" id="testMail">Send me a test e-mail</button>' : ''}</div>
      </form>`;
    const f = notif.querySelector('#prefs');
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const body = { email_alerts: f.elements.email_alerts.checked, min_risk_level: f.elements.min_risk_level.value };
      if (f.elements.email_decisions) body.email_decisions = f.elements.email_decisions.checked;
      try {
        await put('/me/notifications', body);
        f.querySelector('.form-ok').textContent = 'Saved.';
        setTimeout(() => { f.querySelector('.form-ok').textContent = ''; }, 2500);
      } catch (err) { toast(err.message, 'error'); }
    });
    notif.querySelector('#testMail')?.addEventListener('click', async () => {
      try {
        const r = await post('/admin/email/test');
        toast(r.email_delivery ? 'Test e-mail queued: check your inbox in a minute' : 'Queued, but SMTP is not configured: it will wait in the outbox');
      } catch (err) { toast(err.message, 'error'); }
    });
  } catch (err) { notif.innerHTML = errorBox(err); }

  paintDevices(grid.querySelector('#devices'));

  // ---- password -------------------------------------------------------------
  const form = grid.querySelector('#pw form');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const err = form.querySelector('.form-error');
    err.textContent = '';
    const d = Object.fromEntries(new FormData(form));
    if (d.new_password !== d.repeat) { err.textContent = 'The new passwords do not match.'; return; }
    if ((d.new_password || '').length < 8) { err.textContent = 'The new password must be at least 8 characters.'; return; }
    const btn = form.querySelector('button[type=submit]');
    btn.disabled = true;
    try {
      const r = await post('/auth/password', { current_password: d.current_password, new_password: d.new_password });
      form.reset();
      toast(r.message || 'Password changed');
    } catch (ex) { err.textContent = ex.message; } finally { btn.disabled = false; }
  });
}

// ---- notifications on this and other devices (Web Push, or in-tab where push is unavailable) --------------------------
async function paintDevices(card) {
  const head = '<div class="card-head"><h2>Notifications on your devices</h2><span class="sub">push · this browser</span></div>';
  let d;
  try { d = await get('/me/push'); } catch (err) { card.innerHTML = head + errorBox(err); return; }
  if (!card.isConnected) return;
  const mode = deviceAlerts.mode();
  const mine = await deviceAlerts.thisDeviceHash();
  const blocked = deviceAlerts.permission() === 'denied';
  const status = !deviceAlerts.supported() ? 'This browser cannot show notifications.'
    : blocked ? 'Notifications are blocked for this site. Allow them in the browser site settings, then reload this page.'
      : mode === 'push' ? '<strong>On for this browser.</strong> Alerts arrive even when OrbitWatch is closed.'
        : mode === 'tab' ? `<strong>On for this browser while an OrbitWatch tab is open.</strong> ${esc(deviceAlerts.fallbackReason() || 'Background push is not available here.')}
            Background push needs http://localhost, or HTTPS with a certificate the browser trusts, and a browser with a push service.`
          : 'Off for this browser.';
  const rows = d.devices.map((v) => `<tr><td>${esc(deviceName(v.user_agent || ''))}${v.endpoint_hash === mine ? ' <span class="tag accent">this browser</span>' : ''}
      <div class="small muted">added ${esc(fmt.rel(v.created_at))}</div></td>
    <td>${v.last_success_at ? esc(fmt.rel(v.last_success_at)) : '<span class="muted">not yet</span>'}</td>
    <td>${v.last_error && (!v.last_success_at || v.last_failure_at > v.last_success_at)
      ? `<span class="status-bad small">${esc(v.last_error)}</span><div class="small muted">${esc(fmt.rel(v.last_failure_at))}</div>` : '<span class="muted">—</span>'}</td>
    <td><button class="btn sm ghost" type="button" data-remove="${v.subscription_id}">Remove</button></td></tr>`).join('');
  card.innerHTML = `${head}<div class="stack">
    <p class="small" style="margin:0" aria-live="polite">${status}</p>
    <label class="field"><span>Notify me for risk level and above</span><select name="level">
      ${d.risk_levels.map((r) => `<option ${d.min_risk_level === r ? 'selected' : ''}>${r}</option>`).join('')}</select></label>
    <div class="row">
      ${mode ? '<button class="btn" type="button" data-act="off">Turn off for this browser</button>'
        : `<button class="btn primary" type="button" data-act="on" ${blocked || !deviceAlerts.supported() ? 'disabled' : ''}>Turn on for this browser</button>`}
      ${d.devices.length ? '<button class="btn" type="button" data-act="test">Send a test notification</button>' : ''}</div>
    <div class="form-ok" aria-live="polite"></div>
    ${d.devices.length ? `<div class="table-wrap"><table class="data devices"><thead><tr><th>Device</th><th>Last delivered</th><th>Last problem</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table></div>`
      : '<p class="small muted" style="margin:0">No device receives push notifications yet.</p>'}</div>`;
  const say = (text) => { card.querySelector('.form-ok').textContent = text; };
  card.querySelector('select[name=level]').addEventListener('change', async (e) => {
    try {
      await put('/me/push', { min_risk_level: e.target.value });
      deviceAlerts.setMinLevel(e.target.value);
      say(`Saved: ${e.target.value} and above.`);
    } catch (err) { toast(err.message, 'error'); }
  });
  card.onclick = async (e) => {           // replaced on every repaint, so handlers never pile up
    const b = e.target.closest('button');
    if (!b) return;
    b.disabled = true;
    try {
      if (b.dataset.act === 'on') {
        const r = await deviceAlerts.enable(d.public_key, card.querySelector('select[name=level]').value);
        toast(r.mode === 'push' ? 'Push notifications on for this browser' : r.mode === 'tab' ? 'Notifications on while OrbitWatch is open' : r.reason,
          r.mode ? 'info' : 'error');
      } else if (b.dataset.act === 'off') {
        await deviceAlerts.disable();
        toast('Notifications off for this browser');
      } else if (b.dataset.act === 'test') {
        const r = await post('/me/push/test');
        toast(r.message);
      } else if (b.dataset.remove) {
        await del(`/me/push/subscriptions/${b.dataset.remove}`);
        toast('Device removed');
      }
    } catch (err) { toast(err.message, 'error'); }
    paintDevices(card);
  };
}

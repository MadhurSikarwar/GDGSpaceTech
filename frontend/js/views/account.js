// Account: notification preferences and password change.
import { get, post, put } from '../api.js';
import { errorBox, esc, h, hashQuery, loading, toast } from '../ui.js';

const RISKS = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];

export async function render(root, { app }) {
  const q = hashQuery();
  root.appendChild(h(`<div class="page-head"><div><div class="eyebrow">Account</div><h1>${esc(app.user.name)}</h1>
    <p><span class="mono">${esc(app.user.email)}</span> · ${esc(app.user.role_label)}</p></div></div>`));
  const grid = h(`<div class="acct-grid">
    <section class="card bezel" id="notif"><div class="card-head"><h2>Notifications</h2><span class="sub">close-approach alerts</span></div>${loading()}</section>
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

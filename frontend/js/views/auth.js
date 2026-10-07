// Log in, create an account, forgot password, reset password.
import { get, post } from '../api.js';
import { orbitScene } from '../diagrams.js';
import { countdown } from '../hud.js';
import { esc, fmt, h, hashQuery, toast } from '../ui.js';

const COPY = {
  login: ['Log in', 'Follow satellites and get alerted about their close approaches.', 'Log in'],
  register: ['Create an account', 'New accounts are Public Viewers. An administrator can grant the Analyst role.', 'Create account'],
  forgot: ['Reset your password', 'Enter your account e-mail. If it has an account, we send a single-use link that expires in 30 minutes.', 'Send reset link'],
  reset: ['Choose a new password', 'Pick a new password for your account. Every other session is signed out.', 'Set new password'],
};

export async function render(root, { mode, app }) {
  const q = hashQuery();
  const next = q.next || '#/dashboard';
  const [title, lead, submit] = COPY[mode];
  const fields = {
    login: `<label class="field"><span>Email</span><input name="email" type="email" autocomplete="email" required></label>
      <label class="field"><span>Password</span><input name="password" type="password" maxlength="72" autocomplete="current-password" required></label>`,
    register: `<label class="field"><span>Name</span><input name="name" type="text" autocomplete="name" required maxlength="100"></label>
      <label class="field"><span>Email</span><input name="email" type="email" autocomplete="email" required></label>
      <label class="field"><span>Password</span><input name="password" type="password" minlength="8" maxlength="72" autocomplete="new-password" required>
        <span class="hint">At least 8 characters.</span></label>`,
    forgot: '<label class="field"><span>Email</span><input name="email" type="email" autocomplete="email" required></label>',
    reset: `<label class="field"><span>New password</span><input name="new_password" type="password" minlength="8" maxlength="72" autocomplete="new-password" required>
        <span class="hint">At least 8 characters.</span></label>
      <label class="field"><span>Repeat new password</span><input name="repeat" type="password" autocomplete="new-password" required></label>`,
  }[mode];
  const links = {
    login: '<a href="#/register">Create an account</a><a href="#/forgot">Forgot password?</a>',
    register: '<a href="#/login">Already registered? Log in</a>',
    forgot: '<a href="#/login">Back to log in</a>',
    reset: '<a href="#/login">Back to log in</a>',
  }[mode];
  root.appendChild(h(`<div class="auth-wrap"><div class="auth-split"><div class="card auth-card bezel">
    <div class="eyebrow">OrbitWatch</div>
    <h1>${title}</h1>
    <p class="muted">${esc(lead)}</p>
    ${mode === 'reset' && !q.token ? '<div class="error-box" style="margin-top:14px">This page needs the link from the reset e-mail.</div>' : ''}
    <form novalidate>${fields}
      <div class="form-error" aria-live="polite"></div><div class="form-ok" aria-live="polite"></div>
      <button class="btn primary lg" type="submit">${submit}</button>
      <div class="links small">${links}</div>
    </form></div>
    <aside class="auth-scene" aria-hidden="true">${orbitScene()}<div class="scene-stats" id="sceneStats"></div></aside></div></div>`));
  const form = root.querySelector('form');
  form.querySelector('input').focus();
  // The scene beside the form shows live numbers: what is tracked, what is ahead, and how long until the next close approach.
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) root.querySelectorAll('animateMotion').forEach((a) => a.remove());
  let nextAt = null;
  const tick = setInterval(() => {
    const el = root.querySelector('[data-tm]');
    if (el && nextAt) el.textContent = countdown(nextAt - Date.now());
  }, 1000);
  Promise.all([get('/stats'), get(`/conjunctions?page_size=1&when=upcoming&from=${encodeURIComponent(new Date().toISOString())}`)]).then(([s, c]) => {
    const e = c.items[0];
    nextAt = e ? Date.parse(e.time_of_closest_approach.endsWith('Z') ? e.time_of_closest_approach : `${e.time_of_closest_approach}Z`) : null;
    const box = root.querySelector('#sceneStats');
    if (!box) return;
    box.innerHTML = `<div><b>${fmt.int(s.counts.with_current_orbit)}</b><span>objects tracked</span></div><div><b>${fmt.int(c.total)}</b><span>close approaches ahead</span></div>`
      + (e ? `<div><b data-tm>${countdown(nextAt - Date.now())}</b><span>to the next · ${esc(e.risk_level.toLowerCase())}</span></div>` : '');
  }).catch(() => { /* the scene works without its numbers */ });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    const err = form.querySelector('.form-error');
    const okEl = form.querySelector('.form-ok');
    err.textContent = '';
    okEl.textContent = '';
    const btn = form.querySelector('button[type=submit]');
    btn.disabled = true;
    try {
      if (mode === 'login' || mode === 'register') {
        const res = await post(mode === 'login' ? '/auth/login' : '/auth/register', data);
        await app.refreshUser();
        toast(mode === 'login' ? `Welcome back, ${res.user.name}` : 'Account created');
        location.hash = next.startsWith('#') ? next : '#/dashboard';
      } else if (mode === 'forgot') {
        const res = await post('/auth/forgot', { email: data.email });
        okEl.textContent = res.message;
        form.reset();
      } else {
        if (data.new_password !== data.repeat) throw new Error('The passwords do not match.');
        const res = await post('/auth/reset', { token: q.token, new_password: data.new_password });
        toast(res.message || 'Password reset. Log in with your new password.');
        location.replace('#/login');   // replaces the history entry: the token does not stay in the history
      }
    } catch (ex) {
      err.textContent = ex.message;
    } finally {
      btn.disabled = false;
    }
  });
  return () => clearInterval(tick);
}

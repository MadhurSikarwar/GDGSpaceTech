import { post } from '../api.js';
import { h, hashQuery, toast } from '../ui.js';

export async function render(root, { mode, app }) {
  const isLogin = mode === 'login';
  const next = hashQuery().next || '#/dashboard';
  root.appendChild(h(`<div class="card auth-card">
    <div class="eyebrow">OrbitWatch</div>
    <h1>${isLogin ? 'Log in' : 'Create an account'}</h1>
    <p class="muted">${isLogin
      ? 'Subscribe to satellites and get alerted about their close approaches.'
      : 'New accounts are Public Viewers. An administrator can grant the Analyst role.'}</p>
    <form novalidate>
      ${isLogin ? '' : '<label class="field"><span>Name</span><input name="name" type="text" autocomplete="name" required maxlength="100"></label>'}
      <label class="field"><span>Email</span><input name="email" type="email" autocomplete="email" required></label>
      <label class="field"><span>Password</span><input name="password" type="password" minlength="8" maxlength="72"
        autocomplete="${isLogin ? 'current-password' : 'new-password'}" required></label>
      <div class="form-error" aria-live="polite"></div>
      <button class="btn primary" type="submit">${isLogin ? 'Log in' : 'Create account'}</button>
      <p class="small muted">${isLogin ? 'No account yet? <a href="#/register">Create one</a>'
        : 'Already registered? <a href="#/login">Log in</a>'}</p>
    </form></div>`));
  const form = root.querySelector('form');
  form.querySelector('input').focus();
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    const err = form.querySelector('.form-error');
    err.textContent = '';
    form.querySelector('button[type=submit]').disabled = true;
    try {
      const res = await post(isLogin ? '/auth/login' : '/auth/register', data);
      await app.refreshUser();
      toast(isLogin ? `Welcome back, ${res.user.name}` : 'Account created');
      location.hash = next.startsWith('#') ? next : '#/dashboard';
    } catch (ex) {
      err.textContent = ex.message;
    } finally {
      form.querySelector('button[type=submit]').disabled = false;
    }
  });
}

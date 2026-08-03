const $ = (id) => document.getElementById(id);
const T = (key, params) => (window.MWMI18N ? window.MWMI18N.t(key, params) : key);

$('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('login-error').hidden = true;
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: $('username').value, password: $('password').value }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || T('login_failed'));
    location.href = '/console';
  } catch (error) {
    $('login-error').textContent = error.message;
    $('login-error').hidden = false;
  }
});

const $ = (id) => document.getElementById(id);

if (sessionStorage.getItem('mwm_token')) {
  location.href = '/console';
}

$('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('login-error').hidden = true;
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: $('username').value, password: $('password').value }),
    });
    if (!response.ok) throw new Error('用户名或密码不正确');
    const { access_token } = await response.json();
    sessionStorage.setItem('mwm_token', access_token);
    location.href = '/console';
  } catch (error) {
    $('login-error').textContent = error.message;
    $('login-error').hidden = false;
  }
});

// Shared light / dark / system theming. Loaded in <head> so the theme applies
// before the body paints (no flash). Persists the preference in localStorage.
(function () {
  const KEY = 'mwm_theme';
  const media = window.matchMedia('(prefers-color-scheme: dark)');

  const preference = () => localStorage.getItem(KEY) || 'system';
  const resolve = (pref) => (pref === 'system' ? (media.matches ? 'dark' : 'light') : pref);

  function apply() {
    const pref = preference();
    document.documentElement.dataset.theme = resolve(pref);
    document.querySelectorAll('[data-theme-option]').forEach((el) => {
      const active = el.dataset.themeOption === pref;
      el.classList.toggle('active', active);
      el.setAttribute('aria-pressed', String(active));
    });
  }

  function wire() {
    document.querySelectorAll('[data-theme-option]').forEach((el) => {
      el.addEventListener('click', () => {
        localStorage.setItem(KEY, el.dataset.themeOption);
        apply();
      });
    });
    apply();
  }

  media.addEventListener('change', () => {
    if (preference() === 'system') apply();
  });

  window.MWMTheme = { apply, preference };
  apply(); // set <html data-theme> immediately to avoid a flash

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wire);
  } else {
    wire();
  }
})();

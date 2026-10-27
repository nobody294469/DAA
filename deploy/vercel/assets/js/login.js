const $ = (id) => document.getElementById(id);
const say = (t) => { $('msg').textContent = t; $('msg').hidden = !t; };
const next = (() => { const n = new URLSearchParams(location.search).get('next') || '/'; return n.startsWith('/') && !n.startsWith('//') ? n : '/'; })();

const cfg = await fetch('/api/auth').then((r) => r.json()).catch(() => ({}));
if (!cfg.enabled) location.replace('/');
else if (cfg.email) location.replace(next);
else {
  const init = () => {
    if (!window.google?.accounts?.id) return setTimeout(init, 150);
    google.accounts.id.initialize({
      client_id: cfg.client_id,
      callback: async ({ credential }) => {
        say('');
        const res = await fetch('/api/auth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ credential }) });
        if (res.ok) location.replace(next); else say((await res.json().catch(() => ({}))).error || 'Sign-in failed.');
      },
    });
    google.accounts.id.renderButton($('g-button'), { theme: 'outline', size: 'large', text: 'signin_with' });
  };
  init();
}

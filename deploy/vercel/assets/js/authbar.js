// Small "signed in as … · Sign out" tag, shown only when sign-in is configured and the visitor is signed in.
fetch('/api/auth').then((r) => r.json()).then((s) => {
  if (!s.enabled || !s.email) return;
  const bar = document.createElement('div');
  bar.style.cssText = 'position:fixed;right:12px;bottom:12px;z-index:50;font:500 12px Figtree,sans-serif;padding:6px 10px;border-radius:999px;background:var(--surface,#fff);color:var(--text,#111);border:1px solid var(--border,#ccc)';
  bar.append(document.createTextNode(`${s.email} · `));
  const out = document.createElement('a');
  out.href = '#'; out.textContent = 'Sign out';
  out.onclick = async (e) => { e.preventDefault(); await fetch('/api/auth', { method: 'DELETE' }); location.href = '/login.html'; };
  bar.append(out);
  document.body.append(bar);
}).catch(() => {});

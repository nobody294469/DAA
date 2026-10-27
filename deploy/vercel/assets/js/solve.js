import { mountShell, escapeHtml } from '/assets/js/core.js';

mountShell();
const $ = (id) => document.getElementById(id);
const b64 = (buf) => { let s = ''; const u = new Uint8Array(buf); for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode(...u.subarray(i, i + 0x8000)); return btoa(s); };

fetch('/api/solve').then((r) => r.json()).then((h) => {
  $('ai-status').textContent = h.ai === 'on' ? `AI model on (${h.model}) + offline rules` : 'AI model off: offline rules only. Add CLINDOC_ONLINE_API_KEY in the Vercel project settings to switch it on.';
}).catch(() => { $('ai-status').textContent = 'Solve service not reachable.'; });

$('run').addEventListener('click', async () => {
  const f = $('file').files[0];
  const body = f && f.name.toLowerCase().endsWith('.pdf') ? { pdf_b64: b64(await f.arrayBuffer()) } : { text: f ? await f.text() : $('note').value };
  $('busy').textContent = 'Working…'; $('out').innerHTML = '';
  try {
    const res = await fetch('/api/solve', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await res.json();
    if (!res.ok) throw new Error(d.error || 'Request failed');
    const groups = new Map();
    d.claims.forEach((c) => { const k = c.group || c.field_type + c.value; (groups.get(k) || groups.set(k, []).get(k)).push(c); });
    const rows = [...groups.values()].map((g) => {
      const name = g.find((c) => c.field_type === 'lab.test_name' || c.field_type === 'med.name');
      const rest = g.filter((c) => c !== name).map((c) => `<span class="mono">${escapeHtml(c.field_type)}</span> ${escapeHtml(c.value)}${c.unit_raw ? ' ' + escapeHtml(c.unit_raw) : ''}`).join('<br>');
      const ev = escapeHtml([...new Set(g.map((c) => c.evidence_text.replace(/\s+/g, ' ')))].join(' · '));
      return `<tr><td><strong>${escapeHtml(name ? name.value : g[0].field_type)}</strong></td><td>${rest}</td><td class="mono text-sm">${ev}</td><td>${g[0].source === 'ai' ? 'AI' : 'Rules'}</td></tr>`;
    }).join('');
    $('out').innerHTML = `<p class="text-sm"><strong>${groups.size}</strong> findings (${d.n_rules} by rules, ${d.n_ai} added by AI${d.ai === 'failed' ? '; AI call failed, showing rules only' : ''}).</p>` +
      (rows ? `<div style="overflow-x:auto"><table class="table"><thead><tr><th>Item</th><th>Values</th><th>Evidence in document</th><th>Source</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p>Nothing found.</p>');
  } catch (e) { $('out').innerHTML = `<p class="field__error">${escapeHtml(e.message)}</p>`; }
  $('busy').textContent = '';
});

import { mountShell, escapeHtml } from '/assets/js/core.js';

mountShell();
const $ = (id) => document.getElementById(id);
const b64 = (buf) => { let s = ''; const u = new Uint8Array(buf); for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode(...u.subarray(i, i + 0x8000)); return btoa(s); };
const MAX = 2900000; // a PDF is sent as base64 (+33%); the server accepts a 4,000,000-byte request
const EXAMPLE = 'Metoprolol 25 mg po BID for hypertension. Hemoglobin 9.2 g/dL (L). Blood pressure 118/76 mmHg, heart rate 72 bpm.';
const LABELS = { 'med.dose': 'Dose', 'med.route': 'Route', 'med.frequency': 'Frequency', 'lab.value': 'Value', 'lab.flag': 'Flag' };
const label = (t) => LABELS[t] || t;
let file = null;
let rowsData = [];

fetch('/api/solve').then((r) => { if (r.status === 401) { location.href = '/login.html?next=/solve.html'; return null; } return r.json(); }).then((h) => {
  if (!h) return;
  $('ai-status').textContent = h.ai === 'on' ? 'Smart extraction is on.' : 'Basic extraction is on.';
}).catch(() => { $('ai-status').textContent = 'Solve service not reachable.'; });

const showErr = (m) => { $('err').textContent = m || ''; $('err').hidden = !m; };
function setFile(f) {
  showErr('');
  if (f) {
    const n = f.name.toLowerCase();
    if (!n.endsWith('.pdf') && !n.endsWith('.txt')) return showErr('Please choose a PDF or a .txt file.');
    if (f.size > MAX) return showErr('That file is larger than 3 MB. Please choose a smaller one.');
  }
  file = f;
  $('fname').textContent = f ? f.name : ''; $('fname').hidden = $('remove').hidden = !f;
  $('file').value = '';
}
$('file').addEventListener('change', (e) => setFile(e.target.files[0] || null));
$('remove').addEventListener('click', () => setFile(null));
$('example').addEventListener('click', () => { setFile(null); $('note').value = EXAMPLE; $('note').focus(); });
const drop = $('drop');
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.style.outline = '2px dashed currentColor'; });
drop.addEventListener('dragleave', () => { drop.style.outline = ''; });
drop.addEventListener('drop', (e) => { e.preventDefault(); drop.style.outline = ''; if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); });
$('note').addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); run(); } });
$('run').addEventListener('click', run);

const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
function summary(groups) {
  const c = { med: 0, lab: 0, vital: 0 };
  groups.forEach((g) => { const p = g[0].field_type.split('.')[0]; if (p in c) c[p]++; });
  const parts = [[c.med, 'medicine'], [c.lab, 'lab result'], [c.vital, 'vital']].filter((x) => x[0]).map((x) => plural(x[0], x[1]));
  return `${plural(groups.length, 'finding')}${parts.length ? ': ' + parts.join(', ') : ''}`;
}

async function run() {
  if ($('run').disabled) return;
  showErr(''); $('out').innerHTML = '';
  const text = $('note').value.trim();
  if (!file && !text) return showErr('Add a note or choose a PDF first.');
  $('run').disabled = true; $('busy').textContent = 'Reading your document…';
  try {
    const body = file && file.name.toLowerCase().endsWith('.pdf') ? { pdf_b64: b64(await file.arrayBuffer()) } : { text: file ? await file.text() : text };
    let res, d;
    try {
      res = await fetch('/api/solve', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      d = await res.json().catch(() => ({}));
    } catch (_) { throw new Error('Could not reach the service. Check your connection and try again.'); }
    if (res.status === 401) { location.href = '/login.html?next=/solve.html'; return; }
    if (res.status === 413) throw new Error('That document is too large. Please use one under 3 MB.');
    if (res.status === 429) throw new Error('Too many requests. Please wait a few minutes and try again.');
    if (!res.ok) throw new Error(d.error || 'Something went wrong. Please try again.');
    const groups = new Map();
    (Array.isArray(d.claims) ? d.claims : []).forEach((c) => { const k = c.group || c.field_type + c.value; (groups.get(k) || groups.set(k, []).get(k)).push(c); });
    if (!groups.size) throw new Error(file && file.name.toLowerCase().endsWith('.pdf') ? 'Nothing found. If this PDF is a scan or photo, it has no text to read. Try a text PDF or paste the note.' : 'Nothing found in this text.');
    rowsData = [['Item', 'Field', 'Value', 'Evidence', 'Source']];
    const rows = [...groups.values()].map((g) => {
      const name = g.find((c) => c.field_type === 'lab.test_name' || c.field_type === 'med.name');
      const item = name ? name.value : g[0].field_type;
      const rest = g.filter((c) => c !== name);
      const ev = [...new Set(g.map((c) => String(c.evidence_text ?? '').replace(/\s+/g, ' ')))].join(' · ');
      const src = g[0].source === 'ai' ? 'Smart' : 'Rules';
      rest.forEach((c) => rowsData.push([item, label(c.field_type), c.value + (c.unit_raw ? ' ' + c.unit_raw : ''), ev, src]));
      if (!rest.length) rowsData.push([item, '', '', ev, src]);
      const vals = rest.map((c) => `<strong>${escapeHtml(label(c.field_type))}</strong> ${escapeHtml(c.value)}${c.unit_raw ? ' ' + escapeHtml(c.unit_raw) : ''}`).join('<br>');
      return `<tr><td><strong>${escapeHtml(item)}</strong></td><td>${vals}</td><td class="mono text-sm">${escapeHtml(ev)}</td><td>${src}</td></tr>`;
    }).join('');
    $('out').innerHTML = `<p class="text-sm"><strong>${escapeHtml(summary([...groups.values()]))}</strong>${d.ai === 'failed' ? ' (smart extraction was unavailable, showing basic results)' : ''}.</p>` +
      '<div style="display:flex;gap:8px;margin-block:8px"><button class="btn btn--secondary" id="copy" type="button">Copy</button><button class="btn btn--secondary" id="csv" type="button">Download CSV</button></div>' +
      `<div style="overflow-x:auto"><table class="table"><thead><tr><th>Item</th><th>Values</th><th>Evidence in document</th><th>Source</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    $('copy').addEventListener('click', async (e) => {
      try { await navigator.clipboard.writeText(rowsData.map((r) => r.map((x) => String(x).replace(/\t/g, ' ')).join('\t')).join('\n')); e.target.textContent = 'Copied'; } catch (_) { e.target.textContent = 'Copy failed'; }
      setTimeout(() => { e.target.textContent = 'Copy'; }, 1500);
    });
    $('csv').addEventListener('click', () => {
      // a leading = + - @ would run as a formula when the file is opened in a spreadsheet
      const csv = rowsData.map((r) => r.map((x) => `"${String(x).replace(/^[=+\-@\t\r]/, "'$&").replace(/"/g, '""')}"`).join(',')).join('\r\n');
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv' })); a.download = 'findings.csv'; a.click(); URL.revokeObjectURL(a.href);
    });
  } catch (e) { showErr(e.message); }
  $('run').disabled = false; $('busy').textContent = '';
}

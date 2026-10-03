/**
 * Explorer page: replay of the frozen V8 cascade (prompt V1) on the V3 OOD split.
 * Authority: HTTP API contract in src/clindoc/web/app.py (GET /api/explorer, /api/explorer/{id});
 * components and tokens from /assets/js/core.js and /assets/css/app.css.
 *
 * Filters, page and the selected example live in the URL query, so every view is a deep link.
 */
import {
  mountShell, api, renderError, renderEmpty, escapeHtml, icon, num, pct, confidence, DASH, isNum,
  VERDICTS, verdictLabel, verdictBadge, stageBadge, confidenceMeter, getQueryAll, setQuery, debounce,
  openDialog, prefersReducedMotion,
} from '/assets/js/core.js';

mountShell();

const PAGE_SIZE = 50;
const THRESHOLD = 0.6; // Stage B escalation threshold on calibrated confidence (DEC-005 operating point)
const LABELS = Object.keys(VERDICTS);
const FAMILIES = { LAB: 'Lab reports', MED: 'Medication sheets', NARRATIVE: 'Narrative notes' };
// URL / API filter keys and their validators (the query string is untrusted input).
const FILTERS = {
  document_id: (v) => /^(LAB|MED|NARRATIVE)_\d{4}$/.test(v),
  field_type: (v) => /^(lab|med)\.[a-z_]{1,30}$/.test(v),
  ground_truth: (v) => LABELS.includes(v),
  label: (v) => LABELS.includes(v),
  stage: (v) => v === 'B' || v === 'C',
  correct: (v) => v === 'correct' || v === 'errors',
  q: (v) => v.length <= 200,
};
const KEYS = Object.keys(FILTERS);
const SELECTS = ['document_id', 'field_type', 'ground_truth', 'label', 'stage'];

const $ = (id) => document.getElementById(id);
const form = $('ex-form');
const summary = $('ex-summary');
const statusEl = $('ex-status');
const tableHost = $('ex-table');
const panelBody = $('ex-panel-body');
const drawer = $('ex-drawer');
const drawerBody = $('ex-drawer-body');
const clearBtn = $('ex-panel-clear');
const presets = Object.fromEntries([...document.querySelectorAll('[data-preset]')].map((b) => [b.dataset.preset, b]));
const wide = matchMedia('(min-width: 1200px)');

const state = readState();
let listCtrl = null;
let detailCtrl = null;
let facetsReady = false;
let current = null; // loaded detail record
let focusFirstRow = false;

/* ---------------------------------------------------------------- state -- */

function readState() {
  const raw = getQueryAll();
  const s = { page: 1, id: '' };
  for (const k of KEYS) {
    const v = String(raw[k] ?? '').trim();
    s[k] = v && FILTERS[k](v) ? v : '';
  }
  const page = Number.parseInt(raw.page, 10);
  if (page > 0) s.page = page;
  if (/^[0-9a-f-]{8,64}$/i.test(raw.id || '')) s.id = raw.id;
  return s;
}

const syncUrl = () => setQuery({
  ...Object.fromEntries(KEYS.map((k) => [k, state[k]])),
  page: state.page > 1 ? state.page : null,
  id: state.id || null,
});

function fillForm() {
  SELECTS.forEach((k) => { form.elements[k].value = state[k]; });
  form.elements.correct.value = state.correct || 'all';
  if (document.activeElement !== form.elements.q) form.elements.q.value = state.q;
}

function readForm() {
  SELECTS.forEach((k) => { state[k] = form.elements[k].value; });
  const c = form.elements.correct.value;
  state.correct = c === 'all' ? '' : c;
  state.q = form.elements.q.value.trim().slice(0, 200);
  state.page = 1;
}

const isOnly = (key, value) => state[key] === value && KEYS.every((k) => k === key || !state[k]);
const PRESET = { errors: ['correct', 'errors'], escalated: ['stage', 'C'] };
function syncPresets() {
  for (const [name, [k, v]] of Object.entries(PRESET)) presets[name]?.setAttribute('aria-pressed', String(isOnly(k, v)));
}

/* ------------------------------------------------------------ rendering -- */

const humanize = (s) => {
  const t = String(s ?? '').replace(/_/g, ' ').trim();
  return t ? t[0].toUpperCase() + t.slice(1) : '';
};

function correctBadge(ok) {
  if (ok === true) return `<span class="badge ex-badge-ok">${icon('check-circle')}Correct</span>`;
  if (ok === false) return `<span class="badge ex-badge-err">${icon('x')}Error</span>`;
  return '';
}

const severityChip = (sev) => (sev
  ? `<span class="chip chip--split">${sev === 'clean' ? 'Clean render' : `Degraded: ${escapeHtml(sev)}`}</span>`
  : '');

const tile = (label, value, note = '') =>
  `<div class="stat"><p class="stat__label">${label}</p><p class="stat__value">${value}</p>${note ? `<p class="stat__note">${note}</p>` : ''}</div>`;

const skeletonTiles = () => Array.from({ length: 4 }, () =>
  '<div class="stat" aria-hidden="true"><span class="skeleton skeleton--text" style="width:60%"></span><span class="skeleton skeleton--value"></span></div>').join('');

const filtered = () => KEYS.some((k) => state[k]);

function renderSummary(d) {
  const n = d.total;
  summary.innerHTML = [
    tile('Claims', num(n), filtered() ? 'Matching the current filter' : 'Whole V3 OOD split'),
    tile('Cascade accuracy', n ? pct(d.accuracy) : DASH, n ? `${num(n - d.errors)} of ${num(n)} correct` : 'No claims match'),
    tile('Errors', num(d.errors), n ? `${pct(d.errors / n)} of these claims` : ''),
    tile('Escalated to Stage C', num(d.escalated), n ? `${pct(d.escalated / n)} reached the LLM judge` : ''),
  ].join('');
  statusEl.textContent = n
    ? `${num(n)} claims match. Accuracy ${pct(d.accuracy)}, ${num(d.errors)} errors, ${num(d.escalated)} escalated to Stage C.`
    : 'No claims match these filters.';
}

function renderTable(d) {
  if (!d.items.length) {
    renderEmpty(tableHost, {
      icon: 'filter', title: 'No claims match these filters', body: 'Clear a filter or change the search text.',
      actions: '<button class="btn btn--secondary btn--sm" type="button" data-reset>Reset filters</button>',
    });
    return;
  }
  const rows = d.items.map((it) => {
    const id = escapeHtml(it.example_id);
    const sel = it.example_id === state.id;
    const sev = it.severity && it.severity !== 'clean' ? ` · degraded (${escapeHtml(it.severity)})` : '';
    return `<tr class="is-clickable${sel ? ' is-selected' : ''}" data-id="${id}">
      <td>${correctBadge(it.correct)}</td>
      <td><button class="ex-claim" type="button" tabindex="-1" data-id="${id}"${sel ? ' aria-current="true"' : ''}>${escapeHtml(it.claim)}</button><span class="ex-meta">${escapeHtml(it.document_id)} · ${escapeHtml(it.field_type)}${sev}</span></td>
      <td>${verdictBadge(it.ground_truth)}</td>
      <td>${verdictBadge(it.final_label)}</td>
      <td>${stageBadge(it.stage_reached)}</td>
      <td class="num">${confidence(it.final_confidence)}</td>
    </tr>`;
  }).join('');
  tableHost.innerHTML = `<div class="table-wrap" tabindex="0" role="region" aria-labelledby="ex-results-title">
    <table class="table ex-table">
      <caption class="visually-hidden">Claims ${escapeHtml($('ex-range').textContent)}; select a claim to open its detail</caption>
      <thead><tr><th scope="col">Result</th><th scope="col">Claim</th><th scope="col">Ground truth</th><th scope="col">Final verdict</th><th scope="col">Stage</th><th scope="col" class="num">Confidence</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
  const btns = rowButtons();
  const target = btns.find((b) => b.dataset.id === state.id) || btns[0];
  target.tabIndex = 0;
  if (focusFirstRow) btns[0].focus();
  focusFirstRow = false;
}

function renderPager(d) {
  const pages = Math.max(1, Math.ceil(d.total / PAGE_SIZE));
  const from = (state.page - 1) * PAGE_SIZE + 1;
  $('ex-range').textContent = d.total ? `${num(from)}-${num(Math.min(d.total, state.page * PAGE_SIZE))} of ${num(d.total)}` : '';
  $('ex-page').textContent = `Page ${num(state.page)} of ${num(pages)}`;
  $('ex-prev').disabled = state.page <= 1;
  $('ex-next').disabled = state.page >= pages;
}

function buildFacets(facets) {
  const docs = facets?.documents || [];
  const fields = facets?.field_types || [];
  const groups = {};
  docs.forEach((d) => (groups[d.split('_')[0]] ||= []).push(d));
  const opt = (v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`;
  form.elements.document_id.insertAdjacentHTML('beforeend', Object.entries(FAMILIES)
    .filter(([prefix]) => groups[prefix])
    .map(([prefix, label]) => `<optgroup label="${label}">${groups[prefix].map(opt).join('')}</optgroup>`).join(''));
  form.elements.field_type.insertAdjacentHTML('beforeend', fields.map(opt).join(''));
  // A deep link may name a document or field outside the OOD split: drop it rather than show a stale filter.
  let changed = false;
  if (state.document_id && !docs.includes(state.document_id)) { state.document_id = ''; changed = true; }
  if (state.field_type && !fields.includes(state.field_type)) { state.field_type = ''; changed = true; }
  return changed;
}

/* ----------------------------------------------------------- list fetch -- */

async function refresh() {
  syncUrl();
  syncPresets();
  listCtrl?.abort();
  const ctrl = new AbortController();
  listCtrl = ctrl;
  const params = new URLSearchParams();
  KEYS.forEach((k) => { if (state[k]) params.set(k, state[k]); });
  params.set('offset', String((state.page - 1) * PAGE_SIZE));
  params.set('limit', String(PAGE_SIZE));
  tableHost.classList.add('is-loading-frame');
  tableHost.setAttribute('aria-busy', 'true');
  try {
    const data = await api(`/api/explorer?${params}`, { signal: ctrl.signal });
    if (!facetsReady) {
      facetsReady = true;
      const changed = buildFacets(data.facets);
      fillForm();
      if (changed) return refresh();
    }
    const pages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
    if (state.page > pages) {
      state.page = pages;
      return refresh();
    }
    renderPager(data);
    renderSummary(data);
    renderTable(data);
  } catch (err) {
    if (err.kind === 'abort') return;
    summary.innerHTML = '';
    $('ex-range').textContent = '';
    $('ex-page').textContent = '';
    $('ex-prev').disabled = true;
    $('ex-next').disabled = true;
    renderError(tableHost, err, refresh, { title: 'Could not load claims' });
  } finally {
    if (listCtrl === ctrl) {
      tableHost.classList.remove('is-loading-frame');
      tableHost.removeAttribute('aria-busy');
    }
  }
}

function loadPresetCounts() {
  api('/api/explorer?limit=1').then((d) => {
    presets.errors.querySelector('[data-preset-label]').textContent = `All ${num(d.errors)} cascade errors`;
    presets.escalated.querySelector('[data-preset-label]').textContent = `Escalated to Stage C (${num(d.escalated)})`;
  }, () => { /* labels keep their count-free text; the list shows the error */ });
}

/* -------------------------------------------------------------- detail -- */

const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const termRe = (t) => (/^[-\d.]/.test(t) ? `(?<![\\d.])${reEsc(t)}(?!\\d)` : `(?<!\\w)${reEsc(t)}(?!\\w)`);

/** Split "Total Bilirubin flag: HIGH" into entity "Total Bilirubin" and value "HIGH". */
function claimParts(claim) {
  const i = claim.lastIndexOf(': ');
  const head = i >= 0 ? claim.slice(0, i).trim() : '';
  const value = (i >= 0 ? claim.slice(i + 2) : claim).trim();
  const entity = head === 'Medication' ? '' : head.replace(/\s+(flag|dose|route|frequency|duration|unit|reference range)$/i, '');
  return { entity, value };
}

/** Regex sources for the claimed value; a flag also matches its table letter (H / L) standing alone. */
function valueTerms(value, fieldType) {
  if (fieldType === 'lab.flag') {
    const abbr = { HIGH: 'H', LOW: 'L' }[value.toUpperCase()];
    return [termRe(value), ...(abbr ? [`(?<=\\s)${abbr}(?=\\s|$)`] : [])];
  }
  const n = value.match(/^-?\d+(?:\.\d+)?/);
  return [termRe(n && (fieldType === 'lab.value' || fieldType === 'med.dose') ? n[0] : value)];
}

/**
 * Escape the evidence text and wrap the claim's value (<mark>) and entity (underline) occurrences.
 * Clean lab contexts are one analyte per line, so there the value only counts on lines naming the entity.
 */
function highlight(text, claim, fieldType) {
  const { entity, value } = claimParts(claim);
  const vals = value ? valueTerms(value, fieldType) : [];
  const ents = entity && entity.toLowerCase() !== value.toLowerCase() ? [termRe(entity)] : [];
  const group = (srcs) => `(${srcs.length ? srcs.join('|') : '(?!)'})`; // always one capture group
  const re = new RegExp(`${group(ents)}|${group(vals)}`, 'gi');
  const entRe = ents.length ? new RegExp(group(ents), 'i') : null;
  // Row-shaped only when the entity shares its line with numbers (OCR of degraded pages often yields one token per line).
  const lineOnly = String(fieldType).startsWith('lab.') && !!entRe
    && text.split('\n').some((l) => entRe.test(l) && /\d/.test(l.replace(entRe, '')));
  let html = '';
  let last = 0;
  let hits = 0;
  for (const m of text.matchAll(re)) {
    if (m[1] === undefined && lineOnly) {
      const end = text.indexOf('\n', m.index);
      if (!entRe.test(text.slice(text.lastIndexOf('\n', m.index) + 1, end < 0 ? undefined : end))) continue;
    }
    html += escapeHtml(text.slice(last, m.index));
    if (m[1] !== undefined) html += `<span class="ex-entity">${escapeHtml(m[0])}</span>`;
    else { hits += 1; html += `<mark>${escapeHtml(m[0])}</mark>`; }
    last = m.index + m[0].length;
  }
  const shown = value.match(/^-?\d+(?:\.\d+)?/);
  return { html: html + escapeHtml(text.slice(last)), hits, value: shown && fieldType !== 'lab.flag' ? shown[0] : value };
}

function fmtValue(v) {
  if (v === null || v === undefined) return DASH;
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (Array.isArray(v)) {
    const shown = v.slice(0, 12).map((x) => (typeof x === 'object' ? JSON.stringify(x) : String(x))).join(', ');
    return v.length > 12 ? `${shown} … (+${v.length - 12} more)` : shown || '(none)';
  }
  return typeof v === 'object' ? JSON.stringify(v) : String(v);
}

function escalationText(reason) {
  let m = String(reason || '').match(/^stage_a_disagreement \(A=(\w+), B=(\w+)\)/);
  if (m) return `Stage A and Stage B disagree (A: ${verdictLabel(m[1])}, B: ${verdictLabel(m[2])}).`;
  m = String(reason || '').match(/^low_confidence \(([\d.]+) < ([\d.]+)\)/);
  if (m) return `Calibrated confidence ${m[1]} is below the ${m[2]} threshold.`;
  if (reason === 'insufficient_evidence_precaution') return 'Stage B predicted Insufficient evidence, which always escalates.';
  return reason ? `${humanize(reason)}.` : 'Escalated to the LLM judge.';
}

const step = (key, label, body, { final = false, skipped = false } = {}) =>
  `<li class="ex-trace__step${final ? ' is-final' : ''}${skipped ? ' is-skipped' : ''}">
    <div class="ex-trace__head">${stageBadge(key)}<span class="cluster cluster--sm">${label}${final ? '<span class="badge badge--outline">Final verdict</span>' : ''}</span></div>
    ${body}
  </li>`;

const dl = (pairs) => (pairs.length ? `<dl class="ex-dl">${pairs.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>` : '');

function stageAHtml(a, reached) {
  if (!a) return step('A', '', '<p class="ex-trace__note">No Stage A record.</p>', { skipped: true });
  const label = a.label ? verdictBadge(a.label) : '<span class="badge">Abstained</span>';
  const reason = humanize(a.resolution_reason || a.details?.reason);
  const details = Object.entries(a.details || {}).filter(([k]) => k !== 'reason');
  const note = [
    reason && `Resolution: ${escapeHtml(reason)}.`,
    a.label ? `Confidence ${confidence(a.confidence)}.` : 'No Stage A verdict, so Stage B decides alone.',
    a.contradiction_type && `Contradiction: ${escapeHtml(humanize(a.contradiction_type))}.`,
  ].filter(Boolean).join(' ');
  return step('A', label, `<p class="ex-trace__note">${note}</p>${dl(details.map(([k, v]) => [escapeHtml(humanize(k)), `<span class="mono">${escapeHtml(fmtValue(v))}</span>`]))}`,
    { final: reached === 'A' });
}

function stageBHtml(b, reached, aLabel) {
  if (!b) return step('B', '', '<p class="ex-trace__note">No Stage B record.</p>', { skipped: true });
  const probs = b.calibrated_probabilities || {};
  const rows = LABELS.map((k) => `<span class="${k === b.label ? 'is-top' : ''}">${verdictBadge(k)}</span><span class="${k === b.label ? 'is-top' : ''}">${confidenceMeter(probs[k], { label: `Calibrated probability, ${verdictLabel(k)}` })}</span>`).join('');
  const cal = b.calibrated_confidence;
  const route = b.escalated
    ? `${icon('route')} Escalated: ${escapeHtml(escalationText(b.escalation_reason))}`
    : `${icon('check')} Settled here: Stage A ${aLabel ? 'agreed' : 'abstained'} and calibrated confidence ${confidence(cal)} is at least ${THRESHOLD.toFixed(2)}.`;
  return step('B', verdictBadge(b.label), `
    <div class="ex-probs" role="group" aria-label="Stage B calibrated probabilities">${rows}</div>
    <p class="ex-trace__note">Raw confidence ${confidence(b.raw_confidence)}, calibrated ${confidence(cal)} (temperature scaling).</p>
    <p class="ex-trace__note">${route}</p>`, { final: reached === 'B' });
}

function stageCHtml(c, reached) {
  if (!c?.invoked) {
    return step('C', '<span class="badge">Not invoked</span>', '<p class="ex-trace__note">Settled at Stage B; the LLM judge was not called.</p>', { skipped: true });
  }
  const pairs = [
    ['Model', `<span class="mono">${escapeHtml(c.model_name || DASH)}</span>`],
    ['Prompt', `<span class="mono">${escapeHtml(c.prompt_version || DASH)}</span>`],
    ['Confidence', `${confidence(c.calibrated_confidence ?? c.raw_confidence)} <span class="muted">(fixed by the judge, not calibrated)</span>`],
  ];
  if (c.contradiction_type) pairs.push(['Contradiction', escapeHtml(humanize(c.contradiction_type))]);
  const fallback = c.is_fallback
    ? `<div class="alert alert--warning">${icon('alert-triangle')}<div class="alert__body"><p class="alert__title">Fallback verdict</p><p>The judge call failed${c.error ? `: ${escapeHtml(c.error)}` : ''}.</p></div></div>`
    : '';
  return step('C', verdictBadge(c.label), `
    ${c.rationale ? `<blockquote class="ex-quote"><p>${escapeHtml(c.rationale)}</p></blockquote>` : '<p class="ex-trace__note">No rationale recorded.</p>'}
    ${dl(pairs)}${fallback}`, { final: reached === 'C' });
}

function evidenceHtml(d) {
  const text = String(d.evidence || '');
  if (!text) return '<p class="text-sm muted">No evidence text was recorded for this claim.</p>';
  const { html, hits, value } = highlight(text, String(d.claim || ''), d.field_type);
  const sev = d.severity && d.severity !== 'clean' ? d.severity : '';
  const ocr = d.evidence_detail?.ocr_confidence;
  return `<p class="text-sm text-2">The context window (DEC-003) that Stages B and C read${sev ? `, taken from the ${escapeHtml(sev)}-severity degraded render, so OCR noise is part of the test` : ''}.${isNum(ocr) ? ` OCR confidence ${confidence(ocr)}.` : ''}</p>
    <p class="ex-key"><span><mark>value</mark> claimed value</span><span><span class="ex-entity">name</span> claimed entity</span></p>
    <pre class="ex-evidence" tabindex="0" role="region" aria-label="Evidence context">${html}</pre>
    <p class="text-sm">${hits
      ? `The claimed value <strong class="mono">${escapeHtml(value)}</strong> appears ${num(hits)} ${hits === 1 ? 'time' : 'times'} in this context.`
      : `The claimed value <strong class="mono">${escapeHtml(value)}</strong> does not appear in this context.`}</p>`;
}

const validBox = (b) => Array.isArray(b) && b.length === 4 && b.every(isNum) && b[2] > b[0] && b[3] > b[1];
const at = (v) => `${(Math.max(0, Math.min(1, v)) * 100).toFixed(3)}%`;

function pageHtml(d) {
  const p = d.page;
  const doc = escapeHtml(d.document_id);
  if (!p?.image_url || !(p.width > 0) || !(p.height > 0)) {
    return `<p class="text-sm muted">${icon('image')} No page render: the source PDF for ${doc} is not on this server (lab PDFs are generated locally, not committed).</p>`;
  }
  const W = p.width;
  const H = p.height;
  const b = validBox(d.bbox) ? d.bbox : null;
  const box = b ? `<span class="ex-page__box" style="left:${at(b[0] / W)};top:${at(b[1] / H)};width:${at((b[2] - b[0]) / W)};height:${at((b[3] - b[1]) / H)}" aria-hidden="true"></span>` : '';
  const sev = d.severity && d.severity !== 'clean' ? d.severity : '';
  const note = !b
    ? 'No box: this is an unobserved-claim control, so the claimed item has no region on the page.'
    : sev ? `Box shown on the clean render; this claim came from the ${sev}-severity degraded variant.` : '';
  const url = escapeHtml(p.image_url);
  return `<figure class="ex-page">
      <div class="ex-page__frame" style="aspect-ratio:${W} / ${H}"><div class="ex-page__canvas"><img src="${url}" alt="Rendered page ${num(d.page_number)} of ${doc}${b ? ', with the evidence region outlined' : ''}" width="${Math.round(W)}" height="${Math.round(H)}" decoding="async">${box}</div></div>
      <figcaption>Page ${num(d.page_number)} of ${doc}${b ? `; evidence box [${b.map((v) => num(v, 1)).join(', ')}] in PDF points, top-left origin` : ''}.</figcaption>
    </figure>
    ${note ? `<p class="text-sm text-2">${icon('info')} ${escapeHtml(note)}</p>` : ''}
    <div class="cluster cluster--sm">
      ${b ? `<button class="btn btn--secondary btn--sm" type="button" data-zoom aria-pressed="false">${icon('zoom-in')}Zoom to box</button>` : ''}
      <a class="btn btn--ghost btn--sm" href="${url}" target="_blank" rel="noopener">${icon('external-link')}Open full page</a>
    </div>`;
}

/** CSS transform that centres and enlarges the bbox inside the page frame (all in % of the frame). */
function zoomTransform(d) {
  const [x0, y0, x1, y1] = d.bbox;
  const W = d.page.width;
  const H = d.page.height;
  const s = Math.min(3, Math.max(1, 0.85 / Math.max((x1 - x0) / W, (y1 - y0) / H)));
  const clamp = (t) => Math.min(0, Math.max(100 - 100 * s, t));
  return `translate(${clamp(50 - ((x0 + x1) / 2 / W) * 100 * s)}%, ${clamp(50 - ((y0 + y1) / 2 / H) * 100 * s)}%) scale(${s})`;
}

function compareHtml(d) {
  const c = d.cascade || {};
  const q = d.qwen_only;
  if (!q) return '<p class="text-sm muted">No Qwen-only (V9) record exists for this claim.</p>';
  const verdict = c.is_correct && q.correct ? 'Both verifiers are correct on this claim.'
    : c.is_correct ? 'The cascade gets this right; Qwen alone does not.'
      : q.correct ? 'Qwen alone gets this right; the cascade does not.'
        : 'Both verifiers get this claim wrong.';
  return `<div class="ex-compare">
      <div class="ex-compare__col"><h5>Cascade (V8, prompt V1)</h5><div class="cluster cluster--sm">${verdictBadge(c.final_label)}${correctBadge(c.is_correct)}</div><p class="text-sm text-2">Settled at ${stageBadge(c.stage_reached)}</p></div>
      <div class="ex-compare__col"><h5>Qwen-7B only (V9)</h5><div class="cluster cluster--sm">${verdictBadge(q.label)}${correctBadge(q.correct)}</div>${q.rationale ? `<p class="text-sm text-2">${escapeHtml(q.rationale)}</p>` : ''}</div>
    </div>
    <p class="text-sm"><strong>${verdict}</strong></p>`;
}

function detailHtml(d) {
  const c = d.cascade || {};
  const gtType = d.ground_truth_contradiction_type ? ` <span class="text-sm muted">${escapeHtml(humanize(d.ground_truth_contradiction_type))}</span>` : '';
  return `
    <div class="stack stack--sm">
      <div class="cluster cluster--sm">${correctBadge(c.is_correct)}${severityChip(d.severity)}</div>
      <h3 class="ex-detail__claim">${escapeHtml(d.claim)}</h3>
      <p class="ex-detail__meta">${escapeHtml(d.document_id)}, ${escapeHtml(d.field_type)}, template ${escapeHtml(d.doc_template || DASH)}</p>
    </div>
    <div class="ex-section">
      <h4>Verdict</h4>
      ${dl([
        ['Ground truth', `${verdictBadge(d.ground_truth)}${gtType}`],
        ['Final verdict', `${verdictBadge(c.final_label)} <span class="text-sm">confidence ${confidence(c.final_confidence)}</span>`],
        ['Settled at', stageBadge(c.stage_reached)],
        ['Contradiction', escapeHtml(humanize(c.contradiction_type) || 'None')],
        ['Generation', escapeHtml(humanize(d.generation_method) || DASH)],
        ['Example ID', `<span class="mono">${escapeHtml(d.example_id)}</span>`],
      ])}
      <div><button class="btn btn--ghost btn--sm" type="button" data-copy="${escapeHtml(location.href)}">${icon('link')}Copy link to this claim</button></div>
    </div>
    <div class="ex-section">
      <h4>Stage trace</h4>
      <ol class="ex-trace" aria-label="Stages A to C">${stageAHtml(d.stage_a, c.stage_reached)}${stageBHtml(d.stage_b, c.stage_reached, d.stage_a?.label)}${stageCHtml(d.stage_c, c.stage_reached)}</ol>
    </div>
    <div class="ex-section"><h4>Evidence context</h4>${evidenceHtml(d)}</div>
    <div class="ex-section"><h4>Source page</h4>${pageHtml(d)}</div>
    <div class="ex-section"><h4>Cascade vs Qwen-only</h4>${compareHtml(d)}</div>`;
}

const detailHost = () => (wide.matches ? panelBody : drawerBody);
const rowButtons = () => [...tableHost.querySelectorAll('.ex-claim')];

function showNoSelection() {
  renderEmpty(panelBody, { icon: 'eye', title: 'No claim selected', body: 'Choose a claim in the table to see its stage trace, evidence context and source page.' });
  clearBtn.hidden = true;
}

function markSelected() {
  tableHost.querySelectorAll('tr[data-id]').forEach((tr) => {
    const on = tr.dataset.id === state.id;
    tr.classList.toggle('is-selected', on);
    const b = tr.querySelector('.ex-claim');
    if (on) b.setAttribute('aria-current', 'true');
    else b.removeAttribute('aria-current');
  });
}

function paintDetail(el) {
  el.innerHTML = detailHtml(current);
  el.scrollTop = 0;
  el.querySelector('.ex-page img')?.addEventListener('error', (e) => {
    e.target.closest('.ex-page').outerHTML = '<p class="text-sm muted">The page image could not be loaded.</p>';
    el.querySelector('[data-zoom]')?.remove();
  });
}

async function openDetail(id, opener) {
  state.id = id;
  syncUrl();
  markSelected();
  if (!wide.matches) openDialog(drawer, { returnFocus: opener || document.activeElement });
  else clearBtn.hidden = false;
  const el = detailHost();
  detailCtrl?.abort();
  const ctrl = new AbortController();
  detailCtrl = ctrl;
  el.innerHTML = '<div class="stack" aria-hidden="true"><span class="skeleton skeleton--title"></span><span class="skeleton skeleton--text"></span><span class="skeleton skeleton--block" style="--h:220px"></span></div>';
  el.setAttribute('aria-busy', 'true');
  try {
    current = await api(`/api/explorer/${encodeURIComponent(id)}`, { signal: ctrl.signal });
    paintDetail(el);
    statusEl.textContent = `Claim detail loaded: ${current.claim}.`;
  } catch (err) {
    if (err.kind === 'abort') return;
    current = null;
    if (err.status === 404) { state.id = ''; syncUrl(); markSelected(); }
    renderError(el, err, err.status === 404 ? null : () => openDetail(id, opener),
      { title: err.status === 404 ? 'This claim is not in the benchmark' : 'Could not load this claim' });
  } finally {
    if (detailCtrl === ctrl) el.removeAttribute('aria-busy');
  }
}

/* -------------------------------------------------------------- events -- */

form.addEventListener('change', (e) => {
  if (e.target.name === 'q') return; // handled by the debounced input listener
  readForm();
  refresh();
});
form.elements.q.addEventListener('input', debounce(() => { readForm(); refresh(); }, 300));
form.addEventListener('submit', (e) => { e.preventDefault(); readForm(); refresh(); });
form.addEventListener('reset', (e) => {
  e.preventDefault();
  KEYS.forEach((k) => { state[k] = ''; });
  state.page = 1;
  fillForm();
  form.elements.q.value = '';
  refresh();
});

Object.entries(presets).forEach(([name, btn]) => btn.addEventListener('click', () => {
  const [k, v] = PRESET[name];
  const on = isOnly(k, v);
  KEYS.forEach((key) => { state[key] = ''; });
  if (!on) state[k] = v;
  state.page = 1;
  fillForm();
  form.elements.q.value = state.q;
  refresh();
}));

const goPage = (delta) => {
  state.page = Math.max(1, state.page + delta);
  focusFirstRow = true;
  refresh();
};
$('ex-prev').addEventListener('click', () => goPage(-1));
$('ex-next').addEventListener('click', () => goPage(1));

tableHost.addEventListener('click', (e) => {
  if (e.target.closest('[data-reset]')) { form.reset(); return; }
  const tr = e.target.closest('tr[data-id]');
  if (!tr) return;
  const btn = tr.querySelector('.ex-claim');
  rowButtons().forEach((b) => { b.tabIndex = b === btn ? 0 : -1; });
  openDetail(tr.dataset.id, btn);
});

// Roving focus: one tab stop in the table; Up/Down/Home/End move between claims.
tableHost.addEventListener('keydown', (e) => {
  const btn = e.target.closest('.ex-claim');
  if (!btn) return;
  const btns = rowButtons();
  const i = btns.indexOf(btn);
  const next = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: btns.length - 1 }[e.key];
  if (next === undefined) return;
  e.preventDefault();
  const to = btns[Math.max(0, Math.min(btns.length - 1, next))];
  btns.forEach((b) => { b.tabIndex = b === to ? 0 : -1; });
  to.focus();
});

clearBtn.addEventListener('click', () => {
  detailCtrl?.abort();
  state.id = '';
  current = null;
  syncUrl();
  markSelected();
  showNoSelection();
  (rowButtons().find((b) => b.tabIndex === 0) || $('ex-results-title')).focus?.();
});

document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-zoom]');
  if (!btn || !current) return;
  const canvas = btn.closest('.ex-panel__body, .dialog__body')?.querySelector('.ex-page__canvas');
  if (!canvas) return;
  const on = btn.getAttribute('aria-pressed') !== 'true';
  btn.setAttribute('aria-pressed', String(on));
  btn.querySelector('use')?.setAttribute('href', `/assets/icons.svg#${on ? 'zoom-out' : 'zoom-in'}`);
  canvas.style.transform = on ? zoomTransform(current) : '';
});

wide.addEventListener('change', () => {
  if (!current) { showNoSelection(); return; }
  if (wide.matches) {
    if (drawer.open) drawer.close();
    clearBtn.hidden = false;
    paintDetail(panelBody);
  } else {
    showNoSelection();
  }
});

/* ---------------------------------------------------------------- init -- */

summary.innerHTML = skeletonTiles();
tableHost.innerHTML = '<span class="skeleton skeleton--block" style="--h:360px" aria-hidden="true"></span>';
showNoSelection();
fillForm();
loadPresetCounts();
refresh().then(() => {
  if (!state.id) return;
  const btn = rowButtons().find((b) => b.dataset.id === state.id);
  openDetail(state.id, btn || $('main'));
  if (btn && wide.matches) btn.scrollIntoView({ block: 'nearest', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
});

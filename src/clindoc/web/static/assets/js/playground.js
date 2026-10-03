/**
 * Playground page: verify one claim through the cascade and show how each stage voted.
 * Authority: HTTP API contract (POST /api/verify, GET /api/verify/presets), DEC-019 (field tiers),
 * DEC-005 (Stage B escalation reasons, see stage_b.py), DEC-003 (context window used only when the
 * evidence text sits verbatim inside it, see web/service.py verify()).
 */
import {
  mountShell, api, getHealth, servingSummary, renderServingBanner, renderError, renderEmpty,
  escapeHtml, icon, num, ms, DASH, isNum, STAGES, VERDICTS, verdictLabel, verdictBadge, stageKey, confidenceMeter,
} from './core.js';
import { barChart } from './charts.js';

mountShell();
renderServingBanner(document.getElementById('serving'));

const $ = (id) => document.getElementById(id);
const form = $('verify-form');
const FIELDS = {
  'lab.value': { tier: 1, entity: 'Test name', unit: true, help: 'Numeric result as printed, for example 37.9. Put the unit in Unit.' },
  'lab.flag': { tier: 1, entity: 'Test name', help: 'Abnormal flag, for example HIGH, LOW or H.' },
  'med.name': { tier: 1, help: 'Drug name as written, for example Cephalexin.' },
  'med.dose': { tier: 1, entity: 'Medication name', unit: true, help: 'Dose amount, for example 250. Put the unit (mg, units) in Unit.' },
  'med.route': { tier: 1, entity: 'Medication name', help: 'Route of administration, for example oral or PO.' },
  'med.frequency': { tier: 1, entity: 'Medication name', help: 'Dosing schedule, for example BID or every 12 hours.' },
  'lab.unit': { tier: 2, help: 'Unit string as printed, for example mg/dL.' },
  'lab.reference_range': { tier: 2, help: 'Range as printed, for example 36.0 - 48.0.' },
  'lab.test_name': { tier: 2, help: 'Analyte name, for example Hematocrit.' },
  'med.duration': { tier: 2, help: 'Course length, for example 10 days.' },
};
const TIER_HELP = {
  1: 'Tier 1: the Stage A rules and the Stage B classifier both vote; disagreement, low confidence or an insufficient-evidence prediction escalates to the Stage C judge.',
  2: 'Tier 2: settled by the deterministic Stage A rules alone (DEC-019).',
};
const RECENT_KEY = 'clindoc-playground-recent';
const state = { presets: [], preset: null, health: null, chart: null };
const status = (msg) => { $('pg-status').textContent = msg; };

/* ---------------------------------------------------------------------- */
/* Honest stage names                                                       */
/* ---------------------------------------------------------------------- */

function stageName(k) {
  const s = state.health && servingSummary(state.health);
  if (k === 'B') return !s ? 'Classifier' : s.stageB.real ? STAGES.B.name : 'Heuristic'; // unknown serving: neutral name
  if (k === 'C') return !s ? 'Judge' : s.stageC.real ? STAGES.C.name : 'Mock judge';
  return STAGES[k]?.name ?? k;
}
function stageTag(stage) {
  const k = stageKey(stage);
  if (!k) return `<span class="badge">${escapeHtml(stage ?? DASH)}</span>`;
  return `<span class="badge badge--stage badge--stage-${k.toLowerCase()}"><span class="badge__key" aria-hidden="true"><span>${k}</span></span><span class="visually-hidden">Stage ${k}: </span>${escapeHtml(stageName(k))}</span>`;
}

/* ---------------------------------------------------------------------- */
/* Form                                                                     */
/* ---------------------------------------------------------------------- */

function syncField() {
  const f = FIELDS[$('field-type').value];
  $('tier-help').textContent = TIER_HELP[f.tier];
  $('field-help').textContent = f.help;
  $('entity-field').hidden = !f.entity;
  $('entity-label').textContent = f.entity || 'Entity';
  $('unit-field').hidden = !f.unit;
}

function syncCounts() {
  $('evidence-count').textContent = `${num($('evidence').value.length)} / ${num(20000)}`;
  const ctx = $('context').value, ev = $('evidence').value;
  const warn = Boolean(ctx.trim() && ev.trim() && !ctx.includes(ev));
  const w = $('context-warning');
  w.hidden = !warn;
  w.innerHTML = warn ? `${icon('alert-triangle')}<span>The evidence text does not appear verbatim in this context, so the server will ignore the context.</span>` : '';
}

function setValueError(msg) {
  const input = $('value');
  input.setAttribute('aria-invalid', String(Boolean(msg)));
  $('value-error').hidden = !msg;
  $('value-error').innerHTML = msg ? `${icon('alert-triangle')}<span>${escapeHtml(msg)}</span>` : '';
}

function request() {
  const field = $('field-type').value, f = FIELDS[field];
  return {
    field_type: field,
    value: $('value').value.trim(),
    unit: f.unit ? $('unit').value.trim() || null : null,
    entity: f.entity ? $('entity').value.trim() || null : null,
    evidence_text: $('evidence').value,
    context_text: $('context').value.trim() ? $('context').value : null,
  };
}

function fill(req) {
  $('field-type').value = FIELDS[req.field_type] ? req.field_type : 'lab.value';
  $('entity').value = req.entity || '';
  $('value').value = req.value || '';
  $('unit').value = req.unit || '';
  $('evidence').value = req.evidence_text || '';
  $('context').value = req.context_text || '';
  if (req.context_text) $('context-details').open = true;
  setValueError('');
  syncField();
  syncCounts();
}

/* ---------------------------------------------------------------------- */
/* Presets                                                                  */
/* ---------------------------------------------------------------------- */

async function loadPresets() {
  const el = $('presets');
  el.setAttribute('aria-busy', 'true');
  try {
    state.presets = (await api('/api/verify/presets')).presets || [];
    el.innerHTML = state.presets.length
      ? `<ul class="pg-presets__list" role="list">${state.presets.map((p, i) => `<li><button class="pg-preset" type="button" data-preset="${i}" aria-pressed="false"><span class="pg-preset__name">${escapeHtml(p.name)}</span>${verdictBadge(p.expected_label)}<span class="pg-preset__meta">${escapeHtml(p.field_type)} from ${escapeHtml(p.source_document_id || 'the benchmark')}</span></button></li>`).join('')}</ul>`
      : '<p class="text-sm muted">No benchmark examples are configured on this server.</p>';
  } catch (err) {
    renderError(el, err, loadPresets, { title: 'Benchmark examples unavailable' });
  } finally {
    el.removeAttribute('aria-busy');
  }
}

const markPreset = (btn) => $('presets').querySelectorAll('[data-preset]').forEach((b) => b.setAttribute('aria-pressed', String(b === btn)));

/* ---------------------------------------------------------------------- */
/* Result                                                                   */
/* ---------------------------------------------------------------------- */

/** Human text for stage_b.py escalation_reason strings (unknown strings pass through). */
function reasonText(r) {
  if (!r) return '';
  let m = /^stage_a_disagreement \(A=(\w+), B=(\w+)\)/.exec(r);
  if (m) return `Stage A (${verdictLabel(m[1])}) and Stage B (${verdictLabel(m[2])}) disagree.`;
  m = /^low_confidence \(([\d.]+) < ([\d.]+)\)/.exec(r);
  if (m) return `Calibrated confidence ${m[1]} is below the escalation threshold ${m[2]}.`;
  if (r === 'insufficient_evidence_precaution') return 'Stage B predicted Insufficient evidence, which always escalates.';
  return r;
}

const dd = (term, val) => `<div><dt>${term}</dt><dd>${val}</dd></div>`;
const fixed = (v, d = 3) => (isNum(v) ? v.toFixed(d) : DASH);
const detailsText = (details) => Object.entries(details || {}).slice(0, 6)
  .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${typeof v === 'string' ? v : JSON.stringify(v)}`.slice(0, 160)).join(' · ');

function cascade(res) {
  const decided = stageKey(res.final.stage_reached);
  const a = res.stage_a, b = res.stage_b, c = res.stage_c;
  const node = (k, cls, label, body) => `<li class="cascade__node ${cls}"><div class="cluster cluster--sm cluster--between">${stageTag(k)}<span class="cascade__state">${label}</span></div>${body}</li>`;
  const aNode = a
    ? node('A', decided === 'A' ? 'is-decided' : 'is-passed', decided === 'A' ? 'Decided here' : 'Passed on',
      `${a.label ? verdictBadge(a.label) : '<p class="muted">No rule fired (abstained)</p>'}<p>Confidence ${fixed(a.confidence, 2)}${a.contradiction_type ? ` · <code>${escapeHtml(a.contradiction_type)}</code>` : ''}</p>${detailsText(a.details) ? `<p class="text-xs text-2 wrap-anywhere">${escapeHtml(detailsText(a.details))}</p>` : ''}`)
    : node('A', 'is-skipped', 'Not run', '<p>No Stage A result was returned.</p>');
  const bWhy = res.tier === 2 ? 'Tier 2 field: Stage A rules only (DEC-019).' : 'Blank evidence stops at Stage A as Insufficient evidence.';
  const bNode = b
    ? node('B', decided === 'B' ? 'is-decided' : 'is-passed', decided === 'B' ? 'Decided here' : 'Escalated',
      `${verdictBadge(b.label)}<p>Calibrated ${fixed(b.calibrated_confidence)} · raw ${fixed(b.raw_confidence)}</p>`)
    : node('B', 'is-skipped', 'Not run', `<p>${bWhy}</p>`);
  const cNode = c
    ? node('C', 'is-decided', 'Decided here', `${verdictBadge(c.label)}<p>Confidence ${fixed(c.confidence, 2)} (uncalibrated)</p>`)
    : node('C', 'is-skipped', 'Not run', `<p>${b ? 'Not escalated: Stage A and Stage B agreed with enough confidence.' : 'Only Tier 1 claims with evidence can escalate.'}</p>`);
  return `<ol class="cascade" role="list" aria-label="Cascade path">${aNode}${bNode}${cNode}</ol>`;
}

function stageBCard(b) {
  const s = state.health && servingSummary(state.health);
  const heuristic = s && !s.stageB.real;
  const temp = state.health?.stage_b?.temperature;
  const sub = heuristic
    ? 'Heuristic fallback: rule-derived scores, not ModernBERT probabilities.'
    : `Softmax over the three labels${isNum(temp) ? ` after temperature scaling (T = ${temp.toFixed(3)})` : ''}.`;
  return `<article class="wb-panel chart-card" aria-labelledby="b-title">
    <div class="chart-card__header"><div><h3 class="chart-card__title" id="b-title">Stage B label probabilities</h3><p class="chart-card__subtitle">${escapeHtml(sub)}</p></div></div>
    <div id="b-chart"></div>
    <dl class="wb-dl">
      ${dd('Raw confidence', `<span class="num">${fixed(b.raw_confidence)}</span>`)}
      ${dd('Calibrated confidence', confidenceMeter(b.calibrated_confidence, { label: 'Calibrated confidence' }))}
      ${dd('Escalation', b.escalated ? `<span class="pg-reason">${escapeHtml(reasonText(b.escalation_reason))} <code>${escapeHtml(b.escalation_reason || '')}</code></span>` : 'Not escalated')}
    </dl>
  </article>`;
}

function stageCCard(c) {
  const s = state.health && servingSummary(state.health);
  const mock = (s && !s.stageC.real) || /^mock/i.test(c.model_name || '');
  const warn = (title, body) => `<div class="alert alert--warning">${icon('alert-triangle')}<div class="alert__body"><p class="alert__title">${title}</p><p>${body}</p></div></div>`;
  return `<article class="wb-panel" aria-labelledby="c-title">
    <div class="card__header"><h3 class="card__title" id="c-title">Stage C judge</h3><span class="label-mono">${escapeHtml(c.model_name || 'unknown model')}</span></div>
    ${mock ? warn('Mock judge', 'The verdict and rationale are deterministic placeholders, not a Qwen judgement.') : ''}
    ${c.is_fallback ? warn('Fallback verdict', 'The judge call failed or returned an unusable answer, so the cascade fell back to a default verdict.') : ''}
    ${c.rationale ? `<blockquote class="wb-evidence wb-evidence--prose">${escapeHtml(c.rationale)}</blockquote>` : '<p class="text-sm muted">No rationale returned.</p>'}
    <dl class="wb-dl">
      ${dd('Verdict', verdictBadge(c.label))}
      ${dd('Confidence', `<span class="num">${fixed(c.confidence, 2)}</span> <span class="muted text-sm">(Stage C confidence is not calibrated)</span>`)}
      ${dd('Contradiction type', c.contradiction_type ? `<code>${escapeHtml(c.contradiction_type)}</code>` : '<span class="muted">None</span>')}
    </dl>
  </article>`;
}

function renderResult(res, preset) {
  const f = res.final;
  const bench = preset
    ? dd('Benchmark label', `${verdictBadge(preset.expected_label)} <span class="text-sm text-2">${preset.expected_label === f.label ? 'matches this verdict' : 'differs from this verdict'}</span>`) : '';
  state.chart?.destroy();
  state.chart = null;
  $('result-body').innerHTML = `
    <article class="wb-panel" aria-labelledby="verdict-title">
      <div class="card__header"><h3 class="card__title" id="verdict-title">Final verdict</h3><span class="label-mono">${res.tier === 1 ? 'Tier 1, full cascade' : 'Tier 2, Stage A only'}</span></div>
      <div class="wb-verdict">${verdictBadge(f.label, { size: 'lg' })}${confidenceMeter(f.confidence, { label: 'Final confidence' })}</div>
      <dl class="wb-dl">
        ${dd('Decided at', stageTag(f.stage_reached))}
        ${dd('Contradiction type', f.contradiction_type ? `<code>${escapeHtml(f.contradiction_type)}</code>` : '<span class="muted">None</span>')}
        ${dd('Escalated to Stage C', f.escalated ? 'Yes' : 'No')}
        ${bench}
        ${dd('Server time', ms(res.elapsed_ms))}
      </dl>
      <div class="stack stack--xs">
        <p class="label-mono">Claim text the model saw</p>
        <div class="pg-claimtext"><code>${escapeHtml(res.claim_text)}</code><button class="btn btn--ghost btn--sm" type="button" data-copy="${escapeHtml(res.claim_text)}">${icon('copy')}Copy</button></div>
      </div>
    </article>
    <article class="wb-panel" aria-labelledby="cascade-title">
      <h3 class="card__title" id="cascade-title">Cascade path</h3>
      ${cascade(res)}
    </article>
    ${res.stage_b ? stageBCard(res.stage_b) : ''}
    ${res.stage_c ? stageCCard(res.stage_c) : ''}`;
  if (res.stage_b) {
    const p = res.stage_b.probabilities || {};
    state.chart = barChart($('b-chart'), {
      ariaLabel: 'Stage B probability per label', format: 'pct', domain: [0, 1], labels: 'all', yLabel: 'Probability',
      highlight: VERDICTS[res.stage_b.label]?.label,
      data: Object.keys(VERDICTS).map((k) => ({ label: VERDICTS[k].label, value: p[k] })),
    });
  }
}

/* ---------------------------------------------------------------------- */
/* Recent checks (localStorage; per-browser convenience only)              */
/* ---------------------------------------------------------------------- */

function readRecent() {
  try {
    const items = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
    return Array.isArray(items) ? items.filter((x) => x && x.req && FIELDS[x.req.field_type] && VERDICTS[x.label]) : [];
  } catch {
    return [];
  }
}
function writeRecent(items) {
  try {
    if (items.length) localStorage.setItem(RECENT_KEY, JSON.stringify(items));
    else localStorage.removeItem(RECENT_KEY);
  } catch { /* storage blocked or full: history is a convenience */ }
}
function renderRecent() {
  const items = readRecent();
  $('recent-clear').hidden = !items.length;
  $('recent').innerHTML = items.length
    ? items.map((x, i) => {
      const when = new Date(x.t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      return `<li><button class="pg-recent__item" type="button" data-recent="${i}">${verdictBadge(x.label)}<span class="pg-recent__text">${escapeHtml(x.text || x.req.value)}</span><span class="pg-recent__meta">${escapeHtml(x.req.field_type)} · Stage ${escapeHtml(stageKey(x.stage) || '?')} · ${escapeHtml(when)}</span></button></li>`;
    }).join('')
    : '<li class="text-sm muted">No checks yet.</li>';
}

/* ---------------------------------------------------------------------- */
/* Wiring                                                                   */
/* ---------------------------------------------------------------------- */

$('presets').addEventListener('click', (e) => {
  const b = e.target.closest('[data-preset]');
  const p = b && state.presets[Number(b.dataset.preset)];
  if (!p) return;
  fill({ ...p, context_text: null });
  state.preset = p;
  markPreset(b);
  $('preset-status').innerHTML = `Loaded <strong>${escapeHtml(p.name)}</strong> from ${escapeHtml(p.source_document_id || 'the benchmark')}: ${escapeHtml(p.description || '')} Benchmark label: ${verdictBadge(p.expected_label)}`;
});

form.addEventListener('input', (e) => {
  if (e.target.id === 'evidence' || e.target.id === 'context') syncCounts();
  if (e.target.id === 'value' && e.target.value.trim()) setValueError('');
  if (state.preset) {
    markPreset(null);
    $('preset-status').textContent = `Edited from "${state.preset.name}"; the benchmark label no longer applies.`;
    state.preset = null;
  }
});
$('field-type').addEventListener('change', syncField);
$('value').addEventListener('blur', () => { if (!$('value').value.trim() && $('value').getAttribute('aria-invalid') === 'true') setValueError('Enter the claimed value.'); });

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!$('value').value.trim()) {
    setValueError('Enter the claimed value.');
    $('value').focus();
    return;
  }
  const req = request(), preset = state.preset, btn = $('submit'), body = $('result-body');
  btn.disabled = true;
  btn.setAttribute('aria-busy', 'true');
  body.classList.add('is-loading-frame');
  status('Verifying claim.');
  try {
    state.health = await getHealth().catch(() => null);
    const res = await api('/api/verify', { json: req, timeout: 120000 });
    renderResult(res, preset);
    writeRecent([{ t: Date.now(), req, label: res.final.label, stage: res.final.stage_reached, text: res.claim_text }, ...readRecent()].slice(0, 10));
    renderRecent();
    status(`Verdict: ${verdictLabel(res.final.label)}, decided at Stage ${res.final.stage_reached}, confidence ${fixed(res.final.confidence, 2)}.`);
    if (matchMedia('(max-width: 1023.98px)').matches) $('result').scrollIntoView({ block: 'start' });
  } catch (err) {
    state.chart?.destroy();
    state.chart = null;
    renderError(body, err, () => form.requestSubmit(), { title: 'Verification failed' });
    status(`Verification failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
    body.classList.remove('is-loading-frame');
  }
});

$('clear').addEventListener('click', () => {
  form.reset();
  state.preset = null;
  markPreset(null);
  $('preset-status').textContent = '';
  setValueError('');
  syncField();
  syncCounts();
  $('field-type').focus();
});

$('recent').addEventListener('click', (e) => {
  const b = e.target.closest('[data-recent]');
  const item = b && readRecent()[Number(b.dataset.recent)];
  if (!item) return;
  fill(item.req);
  state.preset = null;
  markPreset(null);
  $('preset-status').textContent = 'Loaded a recent check into the form. Select Verify claim to run it again.';
  $('field-type').focus();
});
$('recent-clear').addEventListener('click', () => {
  writeRecent([]);
  renderRecent();
  status('Check history cleared.');
  $('recent-title').focus();
});

syncField();
syncCounts();
renderRecent();
renderEmpty($('result-body'), { icon: 'shield-check', title: 'No check yet', body: 'Pick a benchmark example or fill in the form, then select Verify claim. Each stage’s vote appears here.' });
loadPresets();

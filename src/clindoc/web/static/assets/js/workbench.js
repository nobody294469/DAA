/**
 * Workbench page: run the pipeline on a library sample or an upload, then review every claim
 * against the page region it came from.
 * Authority: HTTP API contract (/api/samples, /api/process, /api/runs/*), DEC-016 (claim provenance:
 * evidence span + bbox + per-stage verdicts), DEC-019 (tiering), DEC-005 (Stage B escalation rules,
 * mirrored in escalationReason()).
 */
import {
  mountShell, api, getHealth, servingSummary, renderServingBanner, renderError, renderEmpty, toast,
  escapeHtml, icon, num, ms, DASH, isNum, VERDICTS, STAGES, verdictLabel, verdictBadge, stageKey,
  codeChip, splitChip, confidenceMeter, openDialog, initDropzone, copyText, renderJsonTree,
  getQuery, setQuery, debounce, prefersReducedMotion,
} from './core.js';
import { stackedBar } from './charts.js';
import { STATIC as HOSTED } from './core.js';

// The hosted site has no pipeline server: uploads go to the Solve page.
if (HOSTED) {
  const panel = document.getElementById('panel-upload');
  if (panel) {  // keep the controls in the DOM (the rest of this file wires them) but show the link instead
    [...panel.children].forEach((el) => { el.hidden = true; });
    panel.insertAdjacentHTML('afterbegin', '<div class="stack"><p class="dropzone__title">Upload a document</p>'
      + '<p class="field__help">Upload a text PDF or paste a note on the Solve page to extract medications, labs and vitals.</p>'
      + '<a class="btn btn--primary" href="/solve.html">Open Solve</a></div>');
  }
}

mountShell();
renderServingBanner(document.getElementById('serving'));

const $ = (id) => document.getElementById(id);
const MAX_BYTES = 15 * 1024 * 1024;
const EXTENSIONS = ['.pdf', '.json', '.png', '.jpg', '.jpeg', '.tif', '.tiff'];
const KINDS = [['lab', 'Lab reports'], ['med', 'Medication sheets'], ['narrative', 'Narrative notes']];
const KIND_LABEL = { lab: 'Lab report', med: 'Medication sheet', narrative: 'Narrative note' };
const STEPS = ['Ingest', 'Extract', 'Normalize', 'Verify', 'FHIR + audit'];
const TIER_2 = new Set(['lab.unit', 'lab.reference_range', 'lab.test_name', 'med.duration']);
const VERDICT_KEYS = Object.keys(VERDICTS);
const SEVERITY = { SUPPORTED: 1, INSUFFICIENT_EVIDENCE: 2, DISCREPANCY: 3 };
const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3];
const METHODS = { deterministic: 'Deterministic parser', gliner: 'GLiNER entity model', llm_narrative: 'LLM narrative extractor' };

const list = $('claim-list');
const overlay = $('overlay');
const drawer = $('claim-drawer');
const state = {
  samples: [], sample: null, file: null, busy: false, health: null,
  run: null, claims: [], regions: [], visible: [], page: 1, zoom: 1,
  current: null, locate: false, json: {}, chart: null,
};

const smooth = () => (prefersReducedMotion() ? 'auto' : 'smooth');
const announce = (msg) => { $('run-status').textContent = msg; };
const plural = (n, word) => `${num(n)} ${word}${n === 1 ? '' : 's'}`;

/* ---------------------------------------------------------------------- */
/* Honest stage names (Stage B heuristic / Stage C mock are never "ModernBERT" / "LLM judge") */
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
const claimTitle = (c) => c.claim_text || [c.value, c.unit_raw].filter(Boolean).join(' ') || DASH;

/* ---------------------------------------------------------------------- */
/* Input: sample library                                                   */
/* ---------------------------------------------------------------------- */

async function loadSamples() {
  const lib = $('library');
  lib.setAttribute('aria-busy', 'true');
  try {
    state.samples = (await api('/api/samples')).samples || [];
    renderLibrary();
  } catch (err) {
    renderError(lib, err, loadSamples, { title: 'Sample library unavailable' });
  } finally {
    lib.removeAttribute('aria-busy');
  }
}

function renderLibrary() {
  const lib = $('library');
  $('library-count').textContent = state.samples.length ? num(state.samples.length) : '';
  if (!state.samples.length) {
    renderEmpty(lib, { icon: 'file-text', title: 'No samples on this server', body: 'Rendered sample PDFs are read from data/rendered, data/med_rendered and data/narrative_rendered. Upload a document instead.' });
    return;
  }
  const row = (s) => `<li data-q="${escapeHtml(`${s.id} ${s.title} ${KIND_LABEL[s.kind] || ''} ${s.split || ''}${s.has_frozen_verdicts ? ' frozen' : ''}`.toLowerCase())}">
    <label class="wb-sample"><input type="radio" name="sample" value="${escapeHtml(s.id)}"${state.sample === s.id ? ' checked' : ''}>
    <span class="wb-sample__id">${escapeHtml(s.id)}</span>
    <span class="wb-sample__chips">${splitChip(s.split)}${s.has_frozen_verdicts ? `<span class="chip chip--split wb-chip-frozen">${icon('database', 'icon--sm')}Frozen verdicts</span>` : ''}</span></label></li>`;
  lib.innerHTML = KINDS.map(([kind, label]) => {
    const items = state.samples.filter((s) => s.kind === kind);
    if (!items.length) return kind === 'lab' ? '<p class="text-sm muted">Lab samples appear once data/rendered/LAB_*.pdf has been generated.</p>' : '';
    return `<fieldset class="wb-library__group"><legend>${label} <span class="tabs__count">${items.length}</span></legend><ul class="wb-library__list" role="list">${items.map(row).join('')}</ul></fieldset>`;
  }).join('');
  filterLibrary();
}

function filterLibrary() {
  const q = $('sample-search').value.trim().toLowerCase();
  let shown = 0;
  $('library').querySelectorAll('.wb-library__group').forEach((group) => {
    let n = 0;
    group.querySelectorAll('li').forEach((li) => {
      const hit = !q || li.dataset.q.includes(q);
      li.hidden = !hit;
      n += hit ? 1 : 0;
    });
    group.hidden = !n;
    shown += n;
  });
  $('library-status').textContent = q ? `${shown} of ${state.samples.length} samples match.` : '';
}

/** Check a sample's radio (switching to the library tab); false if the id is not in the library. */
function selectSample(id) {
  const radio = [...document.querySelectorAll('input[name="sample"]')].find((r) => r.value === id);
  if (!radio) return false;
  if ($('tab-library').getAttribute('aria-selected') !== 'true') $('tab-library').click();
  if (radio.closest('li').hidden) { $('sample-search').value = ''; filterLibrary(); }
  radio.checked = true;
  state.sample = id;
  radio.closest('li').scrollIntoView({ block: 'nearest' });
  updateSelection();
  return true;
}

/* ---------------------------------------------------------------------- */
/* Input: upload                                                           */
/* ---------------------------------------------------------------------- */

function pickFile(file) {
  const ext = (file.name.match(/\.[^.]+$/)?.[0] || '').toLowerCase();
  let msg = '';
  if (!EXTENSIONS.includes(ext)) msg = `${file.name} is not a supported type. Use a PDF, DocumentText JSON, PNG, JPG or TIFF file.`;
  else if (file.size > MAX_BYTES) msg = `${file.name} is ${num(file.size / 1048576, 1)} MB, over the 15 MB limit. Choose a smaller file.`;
  else if (!file.size) msg = `${file.name} is empty. Choose a file with content.`;
  const err = $('file-error');
  err.hidden = !msg;
  err.innerHTML = msg ? `${icon('alert-triangle')}<span>${escapeHtml(msg)}</span>` : '';
  $('file-input').setAttribute('aria-invalid', String(Boolean(msg)));
  state.file = msg ? null : file;
  const size = file.size < 1024 ? plural(file.size, 'byte') : file.size < 1048576 ? `${num(file.size / 1024, 1)} KB` : `${num(file.size / 1048576, 1)} MB`;
  const ocr = /^\.(png|jpe?g|tiff?)$/.test(ext)
    ? `<span class="wb-ocr-note">${icon('info', 'icon--sm')}Images are read with PaddleOCR, so they take a few seconds longer per page than PDFs.</span>` : '';
  $('file-name').innerHTML = state.file ? `${icon('file-text')}<span>${escapeHtml(file.name)} <span class="muted">(${size})</span></span>${ocr}` : '';
  $('file-input').value = ''; // allow re-picking the same file
  updateSelection();
}

/* ---------------------------------------------------------------------- */
/* Run bar                                                                 */
/* ---------------------------------------------------------------------- */

const uploadTabActive = () => $('tab-upload').getAttribute('aria-selected') === 'true';

function activeInput() {
  if (uploadTabActive()) {
    return state.file ? { file: state.file, docType: document.querySelector('input[name="doc_type"]:checked')?.value || 'auto' } : null;
  }
  return state.sample ? { sample: state.sample } : null;
}

function updateSelection() {
  const input = activeInput();
  $('run').disabled = state.busy || !input;
  const sel = $('selection');
  if (!input) {
    sel.textContent = uploadTabActive() ? 'No file chosen.' : 'No sample selected.';
  } else if (input.sample) {
    const s = state.samples.find((x) => x.id === input.sample);
    const frozen = s?.has_frozen_verdicts
      ? ` · <a href="/explorer.html?document_id=${encodeURIComponent(s.id)}">frozen benchmark verdicts</a>` : '';
    sel.innerHTML = `Selected <strong>${escapeHtml(input.sample)}</strong> · ${escapeHtml(KIND_LABEL[s?.kind] || 'Sample')}${frozen}`;
  } else {
    const route = input.docType === 'auto' ? 'type detected automatically' : `routed as ${KIND_LABEL[input.docType].toLowerCase()}`;
    sel.innerHTML = `Selected <strong>${escapeHtml(input.file.name)}</strong> · ${route}`;
  }
}

/* ---------------------------------------------------------------------- */
/* Progress stepper (timer-driven: /api/process is one synchronous call)   */
/* ---------------------------------------------------------------------- */

function renderSteps({ current = -1, done = -1, error = -1, details = [] } = {}) {
  $('stepper').innerHTML = STEPS.map((label, i) => {
    const cls = i === error ? 'is-error' : i <= done ? 'is-done' : i === current ? 'is-current' : 'is-pending';
    const marker = i === error ? icon('x') : i <= done ? icon('check') : '';
    const status = i === error ? 'failed' : i <= done ? 'done' : i === current ? 'in progress' : 'pending';
    return `<li class="stepper__step ${cls}"${cls === 'is-current' ? ' aria-current="step"' : ''}><span class="stepper__marker">${marker}</span><div><p class="stepper__label">${label}<span class="visually-hidden">: ${status}</span></p>${details[i] ? `<p class="stepper__detail">${escapeHtml(details[i])}</p>` : ''}</div></li>`;
  }).join('');
}

/** Which step an API error belongs to (-1 = unknown, nothing is marked). */
function errorStep(err) {
  if (err?.kind !== 'http') return -1;
  if ([404, 413, 415, 422].includes(err.status)) return 0;
  if (err.status === 400) return /claim/i.test(err.message) ? 1 : 0;
  return -1;
}

async function run() {
  const input = activeInput();
  if (!input || state.busy) return;
  state.busy = true;
  const btn = $('run');
  btn.disabled = true;
  btn.setAttribute('aria-busy', 'true');
  const idleLabel = btn.innerHTML;
  btn.textContent = 'Running…';
  $('progress-note').textContent = 'A document can take up to a minute. Each step shows its result when the server answers.';
  if (drawer.open) { state.current = null; drawer.close(); }
  $('results').classList.add('is-loading-frame');
  $('progress').hidden = false;
  $('progress-note').hidden = false;
  $('progress-error').replaceChildren();
  const name = input.sample || input.file.name;
  announce(`Processing ${name}.`);
  const t0 = performance.now();
  // /api/process is one synchronous call, so per-step progress is unknown until it returns:
  // show an honest indeterminate wait, then fill every step with its real result (loading.md).
  renderSteps({});
  const timer = setInterval(() => {
    $('elapsed').textContent = `Processing… ${((performance.now() - t0) / 1000).toFixed(1)} s`;
  }, 100);
  try {
    const body = new FormData();
    if (input.sample) body.append('sample_id', input.sample);
    else { body.append('file', input.file, input.file.name); body.append('doc_type', input.docType); }
    // ponytail: 5 min client timeout; a cold ModernBERT load on CPU is the slow path.
    const data = await api('/api/process', { body, timeout: 300000 });
    clearInterval(timer);
    const coded = data.claims.filter((c) => c.normalized_code).length;
    renderSteps({
      done: 4,
      details: [plural(data.pages.length, 'page'), plural(data.summary.n_claims, 'claim'), `${num(coded)} coded`,
        `A ${data.stages.A} · B ${data.stages.B} · C ${data.stages.C}`, data.fhir.valid ? 'Valid bundle' : plural(data.fhir.errors.length, 'validation issue')],
    });
    $('elapsed').textContent = `Server ${ms(data.elapsed_ms)} · round trip ${ms(performance.now() - t0)}`;
    $('progress-note').hidden = true;
    setQuery({ run: data.run_id, sample: input.sample || null });
    await showResults(data);
    announce(`Done. ${plural(data.summary.n_claims, 'claim')} verified in ${ms(data.elapsed_ms)}: ${data.summary.n_supported} supported, ${data.summary.n_discrepancy} discrepancies, ${data.summary.n_insufficient_evidence} insufficient evidence.`);
    $('results-title').focus({ preventScroll: true });
    $('results').scrollIntoView({ block: 'start', behavior: smooth() });
  } catch (err) {
    clearInterval(timer);
    const at = errorStep(err);
    renderSteps({ done: at - 1, error: at });
    $('elapsed').textContent = `Failed after ${ms(performance.now() - t0)}`;
    $('progress-note').hidden = true;
    renderError($('progress-error'), err, run, { title: `Could not process ${name}` });
    announce(`Run failed: ${err.message}`);
  } finally {
    state.busy = false;
    btn.removeAttribute('aria-busy');
    btn.innerHTML = idleLabel;
    $('results').classList.remove('is-loading-frame');
    updateSelection();
  }
}

/* ---------------------------------------------------------------------- */
/* Results                                                                  */
/* ---------------------------------------------------------------------- */

/** Group claims by evidence box (several claims share one table row), numbered in reading order. */
function indexClaims(run) {
  const regions = new Map();
  run.claims.forEach((c, i) => {
    c._i = i;
    c._region = null;
    c._hay = [c.claim_text, c.value, c.unit_raw, c.field_type, c.code_system, c.normalized_code, c.evidence?.text]
      .filter(Boolean).join(' ').toLowerCase();
    const b = c.evidence?.bbox;
    if (!b || !(b.x1 > b.x0 && b.y1 > b.y0)) return; // absence evidence has no box
    const page = c.evidence.page_number || 1;
    const key = `${page}:${b.x0},${b.y0},${b.x1},${b.y1}`;
    if (!regions.has(key)) regions.set(key, { page, bbox: b, text: c.evidence.text, claims: [] });
    regions.get(key).claims.push(c);
    c._region = regions.get(key);
  });
  state.regions = [...regions.values()].sort((a, b) => a.page - b.page || a.bbox.y0 - b.bbox.y0 || a.bbox.x0 - b.bbox.x0);
  state.regions.forEach((r, i) => { r.n = i + 1; });
  state.claims = [...run.claims].sort((a, b) => (a._region?.n ?? 1e9) - (b._region?.n ?? 1e9) || a._i - b._i);
}

async function showResults(run) {
  state.health = await getHealth().catch(() => null);
  state.run = run;
  state.json = {};
  indexClaims(run);
  const s = run.summary;
  document.title = `${run.document_id} · Workbench · Clindoc2FHIR`;
  $('results').hidden = false;
  $('results-title').textContent = run.document_id;
  const sample = state.samples.find((x) => x.id === run.document_id);
  $('results-meta').innerHTML = [
    escapeHtml(run.source_name),
    escapeHtml(KIND_LABEL[run.doc_type] || run.doc_type),
    plural(run.pages.length, 'page'),
    `server time ${ms(run.elapsed_ms)}`,
    `run <code>${escapeHtml(run.run_id.slice(0, 8))}</code>`,
    sample?.has_frozen_verdicts ? `<a href="/explorer.html?document_id=${encodeURIComponent(run.document_id)}">compare with frozen benchmark verdicts</a>` : '',
  ].filter(Boolean).join(' · ');
  ['dl-fhir', 'dl-fhir-2'].forEach((id) => { $(id).href = run.downloads.fhir_bundle; });
  ['dl-audit', 'dl-audit-2'].forEach((id) => { $(id).href = run.downloads.audit_report; });

  // Stage B/C serving-mode warnings are already in the page's serving banner; show only run-specific notes.
  const notes = (run.warnings || []).filter((w) => !/^Stage [BC]\b/.test(w));
  if (!run.fhir.valid) notes.push(`The FHIR bundle has ${plural(run.fhir.errors.length, 'validation issue')}; see the FHIR bundle tab.`);
  $('warnings').innerHTML = notes.length
    ? `<div class="alert alert--warning">${icon('alert-triangle')}<div class="alert__body"><p class="alert__title">Notes on this run</p><ul class="wb-bullets">${notes.map((w) => `<li>${escapeHtml(w)}</li>`).join('')}</ul></div></div>` : '';

  const share = (n) => (s.n_claims ? `${num((n / s.n_claims) * 100, 0)}% of claims` : '');
  const tile = (label, value, note, verdict) => `<div class="stat"><p class="stat__label">${verdict ? `<span class="wb-kpi-icon wb-kpi-icon--${VERDICTS[verdict].cls}">${icon(VERDICTS[verdict].icon, 'icon--sm')}</span>` : ''}${label}</p><p class="stat__value">${num(value)}</p><p class="stat__note">${note}</p></div>`;
  const coded = run.claims.filter((c) => c.normalized_code).length;
  $('kpis').innerHTML = [
    tile('Claims extracted', s.n_claims, `${num(coded)} coded to LOINC, RxNorm or UCUM`),
    tile('Supported', s.n_supported, share(s.n_supported), 'SUPPORTED'),
    tile('Discrepancy', s.n_discrepancy, share(s.n_discrepancy), 'DISCREPANCY'),
    tile('Insufficient evidence', s.n_insufficient_evidence, share(s.n_insufficient_evidence), 'INSUFFICIENT_EVIDENCE'),
    tile('Escalated to Stage C', s.n_escalated_to_llm, { 'Mock judge': 'Mock judge: placeholder verdicts', [STAGES.C.name]: 'Settled by the LLM judge' }[stageName('C')] || 'Settled by Stage C'),
  ].join('');

  state.chart?.destroy();
  state.chart = stackedBar($('stage-chart'), {
    ariaLabel: 'Claims by deciding cascade stage', mode: 'percent', format: 'int', rowLabels: false, insideLabels: false,
    data: [{ label: `${plural(s.n_claims, 'claim')}`, a: run.stages.A, b: run.stages.B, c: run.stages.C }],
    series: [{ key: 'a', label: `A · ${stageName('A')}` }, { key: 'b', label: `B · ${stageName('B')}` }, { key: 'c', label: `C · ${stageName('C')}` }],
  });

  renderFhirStatus(run.fhir);
  ['fhir', 'audit'].forEach((k) => { $(`${k}-tree`).replaceChildren(); $(`${k}-search`).value = ''; $(`${k}-matches`).textContent = ''; });
  $('claims-tab-count').textContent = num(run.claims.length);
  if ($('tab-claims').getAttribute('aria-selected') !== 'true') $('tab-claims').click();
  initFilters();
  initViewer();
  applyFilters();
  collapseInput(run.document_id);
}

/** Fold the document picker to one line once results exist, so results lead the page (layout.md › Visual hierarchy). */
function collapseInput(name) {
  $('input-summary-name').textContent = name;
  $('input-summary').hidden = false;
  document.querySelector('.wb-input').classList.add('is-collapsed');
}

$('change-doc').addEventListener('click', () => {
  $('input-summary').hidden = true;
  document.querySelector('.wb-input').classList.remove('is-collapsed');
  document.querySelector('#input-tabs [role="tab"][aria-selected="true"]')?.focus();
});

/* ---------------------------------------------------------------------- */
/* Claim filters + list                                                    */
/* ---------------------------------------------------------------------- */

function initFilters() {
  const count = (fn) => state.claims.reduce((m, c) => { const k = fn(c); m[k] = (m[k] || 0) + 1; return m; }, {});
  const byVerdict = count((c) => c.verification.label);
  const byStage = count((c) => c.verification.stage_reached);
  const byField = count((c) => c.field_type);
  $('verdict-filters').innerHTML = VERDICT_KEYS.map((k) => `<label class="chip-check"><input type="checkbox" name="verdict" value="${k}" checked><span>${VERDICTS[k].label}<span class="tabs__count">${num(byVerdict[k] || 0)}</span></span></label>`).join('');
  $('stage-filter').innerHTML = '<option value="">All stages</option>' + ['A', 'B', 'C']
    .map((k) => `<option value="${k}">Stage ${k} · ${escapeHtml(stageName(k))} (${num(byStage[k] || 0)})</option>`).join('');
  $('field-filter').innerHTML = '<option value="">All fields</option>' + Object.keys(byField).sort()
    .map((f) => `<option value="${escapeHtml(f)}">${escapeHtml(f)} (${num(byField[f])})</option>`).join('');
  $('claim-search').value = '';
}

function applyFilters() {
  const verdicts = new Set([...document.querySelectorAll('input[name="verdict"]:checked')].map((i) => i.value));
  const stage = $('stage-filter').value, field = $('field-filter').value;
  const q = $('claim-search').value.trim().toLowerCase();
  state.visible = state.claims.filter((c) => verdicts.has(c.verification.label)
    && (!stage || c.verification.stage_reached === stage) && (!field || c.field_type === field) && (!q || c._hay.includes(q)));
  const filtered = verdicts.size < VERDICT_KEYS.length || stage || field || q;
  $('filters-reset').hidden = !filtered;
  $('claims-count').textContent = filtered ? `Showing ${num(state.visible.length)} of ${plural(state.claims.length, 'claim')}.` : `${plural(state.claims.length, 'claim')}.`;
  renderList();
  renderOverlay();
}

function claimCard(c) {
  const v = c.verification, r = c._region;
  const code = c.code_system || c.normalized_code ? codeChip(c.code_system, c.normalized_code) : '';
  return `<li class="wb-claim wb-claim--${VERDICTS[v.label]?.cls || 'unknown'}" data-id="${escapeHtml(c.claim_id)}" data-region="${r ? r.n : ''}">
    <div class="wb-claim__top">${r ? `<span class="wb-claim__region" aria-hidden="true">${r.n}</span>` : ''}<span class="wb-claim__field">${escapeHtml(c.field_type)}</span>${stageTag(v.stage_reached)}</div>
    <button class="wb-claim__open" type="button" aria-haspopup="dialog">${r ? `<span class="visually-hidden">Region ${r.n}: </span>` : ''}${escapeHtml(claimTitle(c))}</button>
    <div class="wb-claim__meta">${verdictBadge(v.label)}${confidenceMeter(v.confidence, { label: 'Verdict confidence' })}${code}</div>
  </li>`;
}

function renderList() {
  if (!state.visible.length) {
    list.innerHTML = '<li class="wb-claim-list__empty"></li>';
    renderEmpty(list.firstElementChild, { icon: 'filter', title: 'No claims match these filters', body: 'Widen the verdict, stage or field filters, or clear the search.' });
    return;
  }
  list.innerHTML = state.visible.map(claimCard).join('');
}

/* ---------------------------------------------------------------------- */
/* Document viewer: page image + evidence boxes (PDF points, top-left origin) */
/* ---------------------------------------------------------------------- */

function initViewer() {
  const pages = state.run.pages || [];
  const sel = $('page-select');
  sel.innerHTML = pages.map((p) => `<option value="${p.number}">Page ${p.number} of ${pages.length}</option>`).join('');
  sel.disabled = pages.length < 2;
  setZoom(1);
  setPage(1);
}

function setPage(n) {
  const p = state.run.pages[n - 1];
  if (!p) return;
  state.page = n;
  $('page-select').value = String(n);
  $('page').style.aspectRatio = `${p.width} / ${p.height}`;
  const img = $('page-img');
  img.alt = `Page ${n} of ${state.run.document_id} as rendered from the source document. Evidence regions are listed as buttons after the image.`;
  img.onerror = () => { img.alt = `Page ${n} image could not be loaded; the run may have expired on the server.`; };
  img.src = p.image_url;
  renderOverlay();
}

function setZoom(z) {
  state.zoom = z;
  $('page').style.width = `${z * 100}%`;
  $('zoom-level').textContent = z === 1 ? 'Fit' : `${Math.round(z * 100)}%`;
  $('zoom-out').disabled = z <= ZOOMS[0];
  $('zoom-in').disabled = z >= ZOOMS[ZOOMS.length - 1];
  $('zoom-fit').disabled = z === 1;
}
const stepZoom = (dir) => setZoom(ZOOMS[Math.max(0, Math.min(ZOOMS.length - 1, ZOOMS.indexOf(state.zoom) + dir))]);

function renderOverlay() {
  const p = state.run?.pages[state.page - 1];
  if (!p) return;
  const shown = new Set(state.visible);
  const boxes = [];
  const tally = { SUPPORTED: 0, DISCREPANCY: 0, INSUFFICIENT_EVIDENCE: 0 };
  for (const r of state.regions) {
    const claims = r.page === state.page ? r.claims.filter((c) => shown.has(c)) : [];
    if (!claims.length) continue;
    const worst = claims.reduce((w, c) => ((SEVERITY[c.verification.label] || 0) > (SEVERITY[w] || 0) ? c.verification.label : w), 'SUPPORTED');
    tally[worst] = (tally[worst] || 0) + 1;
    const b = r.bbox, pctX = (v) => (v / p.width) * 100, pctY = (v) => (v / p.height) * 100;
    const counts = VERDICT_KEYS.map((k) => [k, claims.filter((c) => c.verification.label === k).length])
      .filter(([, n]) => n).map(([k, n]) => `${n} ${verdictLabel(k).toLowerCase()}`).join(', ');
    const label = `Region ${r.n}: ${plural(claims.length, 'claim')} (${counts}). ${String(r.text || '').slice(0, 200)}`;
    boxes.push(`<button class="wb-box wb-box--${VERDICTS[worst]?.cls || 'supported'}${pctX(b.x0) < 5 ? ' wb-box--flip' : ''}" type="button" data-region="${r.n}" style="left:${pctX(b.x0)}%;top:${pctY(b.y0)}%;width:${pctX(b.x1 - b.x0)}%;height:${pctY(b.y1 - b.y0)}%" aria-label="${escapeHtml(label)}"><span class="wb-box__num" aria-hidden="true">${r.n}</span></button>`);
  }
  overlay.innerHTML = boxes.join('');
  $('viewer-key').innerHTML = VERDICT_KEYS.map((k) => `<li><span class="wb-key__swatch wb-box--${VERDICTS[k].cls}" aria-hidden="true"></span>${VERDICTS[k].label} <span class="muted">(${k === 'SUPPORTED' ? 'solid' : k === 'DISCREPANCY' ? 'dashed' : 'dotted'}, ${plural(tally[k], 'region')})</span></li>`).join('');
}

/** Hover / focus link between a box and its claim cards. */
function linkRegion(n, on) {
  if (!n) return;
  overlay.querySelector(`.wb-box[data-region="${n}"]`)?.classList.toggle('is-hot', on);
  list.querySelectorAll(`.wb-claim[data-region="${n}"]`).forEach((li) => li.classList.toggle('is-hot', on));
}

function selectRegion(n, { scrollCards = false, scrollBox = false } = {}) {
  overlay.querySelectorAll('.wb-box').forEach((b) => b.classList.toggle('is-selected', b.dataset.region === String(n)));
  list.querySelectorAll('.wb-claim').forEach((li) => li.classList.toggle('is-selected', li.dataset.region === String(n)));
  if (scrollCards) list.querySelector(`.wb-claim[data-region="${n}"]`)?.scrollIntoView({ block: 'nearest', behavior: smooth() });
  if (scrollBox) overlay.querySelector(`.wb-box[data-region="${n}"]`)?.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: smooth() });
}

function setViewerOpen(open) {
  const t = $('viewer-toggle');
  t.setAttribute('aria-expanded', String(open));
  $('viewer').classList.toggle('is-collapsed', !open);
  t.innerHTML = `${icon(open ? 'chevron-up' : 'chevron-down')}<span>${open ? 'Hide page' : 'Show page'}</span>`;
}

/* ---------------------------------------------------------------------- */
/* Claim drawer                                                             */
/* ---------------------------------------------------------------------- */

/** Mirrors Stage B's escalation rules (stage_b.py, DEC-005) from the labels in the run payload. */
function escalationReason(v) {
  if (v.stage_a_label && v.stage_a_label !== v.stage_b_label) {
    return `Stage A (${verdictLabel(v.stage_a_label)}) and Stage B (${verdictLabel(v.stage_b_label)}) disagreed.`;
  }
  if (v.stage_b_label === 'INSUFFICIENT_EVIDENCE') return 'Stage B predicted Insufficient evidence, which always escalates.';
  return "Stage B's calibrated confidence was below the escalation threshold.";
}

function traceStep(k, cls, stateText, bodyHtml) {
  return `<li class="trace__step ${cls}"><div class="trace__head">${stageTag(k)}<span class="trace__state">${stateText}</span></div><div class="trace__body">${bodyHtml}</div></li>`;
}

function trace(c) {
  const v = c.verification, decided = stageKey(v.stage_reached);
  const bRan = v.stage_b_label != null;
  const verdictOr = (label, empty) => (label ? verdictBadge(label) : `<span class="muted text-sm">${empty}</span>`);
  const a = traceStep('A', decided === 'A' ? 'is-decided' : 'is-passed', decided === 'A' ? 'Decided here' : 'Passed to Stage B',
    `${verdictOr(v.stage_a_label, 'No rule fired (abstained)')}${decided === 'A' ? confidenceMeter(v.confidence, { label: 'Stage A confidence' }) : ''}`);
  const bSkip = TIER_2.has(c.field_type) ? 'Tier 2 field: settled by the Stage A rules alone (DEC-019).' : 'Evidence is missing or blank, so Stage A returns Insufficient evidence.';
  const b = bRan
    ? traceStep('B', decided === 'B' ? 'is-decided' : 'is-passed', decided === 'B' ? 'Decided here' : 'Escalated',
      `${verdictBadge(v.stage_b_label)}<p class="text-sm text-2">Raw confidence ${isNum(v.stage_b_raw_confidence) ? v.stage_b_raw_confidence.toFixed(3) : DASH}${decided === 'B' ? ` · calibrated ${v.confidence.toFixed(3)}` : ''}</p>`)
    : traceStep('B', 'is-skipped', 'Not run', `<p class="text-sm muted">${bSkip}</p>`);
  const cStep = decided === 'C'
    ? traceStep('C', 'is-decided', 'Decided here', `${verdictBadge(v.label)}${confidenceMeter(v.confidence, { label: 'Stage C confidence (uncalibrated)' })}<p class="text-sm text-2">Why escalated: ${escalationReason(v)} <span class="muted">(Inferred from the stage labels.)</span></p>`)
    : traceStep('C', 'is-skipped', 'Not run', `<p class="text-sm muted">${bRan ? 'Not escalated: Stage A and Stage B agreed with enough confidence.' : 'Only Tier 1 claims with evidence can escalate.'}</p>`);
  return `<ol class="trace" role="list">${a}${b}${cStep}</ol>`;
}

function highlight(text, needle) {
  const t = String(text ?? ''), n = String(needle ?? '').trim();
  const i = n ? t.toLowerCase().indexOf(n.toLowerCase()) : -1;
  if (i < 0) return { html: escapeHtml(t), found: false };
  return { html: `${escapeHtml(t.slice(0, i))}<mark>${escapeHtml(t.slice(i, i + n.length))}</mark>${escapeHtml(t.slice(i + n.length))}`, found: true };
}

function renderDrawer(c) {
  const v = c.verification, ev = c.evidence, r = c._region;
  $('drawer-eyebrow').textContent = `${c.field_type}${r ? ` · region ${r.n}` : ''}`;
  $('drawer-title').textContent = claimTitle(c);
  const hl = highlight(ev?.text, c.value);
  const b = ev?.bbox;
  const where = ev ? [
    `Page ${ev.page_number}`,
    isNum(ev.char_start) ? `characters ${num(ev.char_start)} to ${num(ev.char_end)}` : '',
    b ? `box (${num(b.x0, 1)}, ${num(b.y0, 1)}) to (${num(b.x1, 1)}, ${num(b.y1, 1)}) pt` : '',
    isNum(ev.ocr_confidence) ? `OCR confidence ${ev.ocr_confidence.toFixed(2)}` : '',
  ].filter(Boolean).join(' · ') : '';
  const mockC = stageName('C') === 'Mock judge';
  const llmMock = c.extraction_method === 'llm_narrative' && state.health?.extraction_llm?.mode === 'mock';
  const dd = (term, val) => `<div><dt>${term}</dt><dd>${val}</dd></div>`;
  $('drawer-body').innerHTML = `
    <div class="wb-verdict">${verdictBadge(v.label, { size: 'lg' })}${confidenceMeter(v.confidence, { label: 'Final confidence' })}<span class="text-sm text-2">Decided at ${stageTag(v.stage_reached)}</span></div>
    ${v.contradiction_type ? `<p class="text-sm">Contradiction type: <code>${escapeHtml(v.contradiction_type)}</code></p>` : ''}
    <section class="stack stack--sm" aria-labelledby="d-evidence">
      <h3 class="h4" id="d-evidence">Evidence</h3>
      ${ev ? `<p class="text-xs muted">${where}</p><blockquote class="wb-evidence">${hl.html || '<span class="muted">(empty)</span>'}</blockquote>
      <p class="text-xs muted">${hl.found ? 'Highlighted: the claimed value as it appears in the evidence span.' : 'The claimed value does not appear verbatim in this evidence span.'}</p>`
        : '<p class="text-sm muted">No evidence span was linked to this claim.</p>'}
    </section>
    <section class="stack stack--sm" aria-labelledby="d-trace">
      <h3 class="h4" id="d-trace">Stage trace</h3>
      ${trace(c)}
    </section>
    ${v.rationale ? `<section class="stack stack--sm" aria-labelledby="d-rationale"><h3 class="h4" id="d-rationale">Stage C rationale</h3>${mockC ? '<p class="text-xs muted">Mock judge: a fixed placeholder sentence, not a Qwen judgement.</p>' : ''}<blockquote class="wb-evidence wb-evidence--prose">${escapeHtml(v.rationale)}</blockquote></section>` : ''}
    <section class="stack stack--sm" aria-labelledby="d-coding">
      <h3 class="h4" id="d-coding">Coding and extraction</h3>
      <dl class="wb-dl">
        ${dd('Normalized code', `${c.code_system || c.normalized_code ? codeChip(c.code_system, c.normalized_code) : '<span class="muted">Not coded for this field</span>'}`)}
        ${c.normalized_code ? dd('Code confidence', confidenceMeter(c.code_confidence, { label: 'Code confidence' })) : ''}
        ${dd('Extracted value', `<span class="mono">${escapeHtml(c.value)}</span>${c.unit_raw ? ` <span class="mono">${escapeHtml(c.unit_raw)}</span>` : ''}${isNum(c.value_numeric) ? ` <span class="muted">(numeric ${num(c.value_numeric, Number.isInteger(c.value_numeric) ? 0 : 2)})</span>` : ''}`)}
        ${dd('Extraction method', `${escapeHtml(METHODS[c.extraction_method] || c.extraction_method)}${llmMock ? ' <span class="badge">Mock provider</span>' : ''}`)}
        ${dd('Extraction confidence', confidenceMeter(c.extraction_confidence, { label: 'Extraction confidence' }))}
      </dl>
    </section>
    <details class="accordion"><summary>Identifiers</summary><div class="accordion__body"><dl class="wb-dl wb-dl--ids">
      ${dd('Claim', `<code>${escapeHtml(c.claim_id)}</code>`)}${dd('Group', `<code>${escapeHtml(c.group_id || DASH)}</code>`)}${ev ? dd('Evidence', `<code>${escapeHtml(ev.evidence_id)}</code>`) : ''}
    </dl></div></details>`;
  const i = state.visible.indexOf(c);
  $('drawer-prev').disabled = i <= 0;
  $('drawer-next').disabled = i < 0 || i >= state.visible.length - 1;
  $('drawer-locate').disabled = !r;
  $('drawer-body').scrollTop = 0;
}

function openClaim(id, opener) {
  const c = state.claims.find((x) => x.claim_id === id);
  if (!c) return;
  state.current = c;
  renderDrawer(c);
  if (c._region) {
    if (c._region.page !== state.page) setPage(c._region.page);
    selectRegion(c._region.n, { scrollBox: true });
  }
  if (!drawer.open) openDialog(drawer, { returnFocus: opener });
}

/* ---------------------------------------------------------------------- */
/* FHIR bundle / audit report JSON tabs (lazy)                             */
/* ---------------------------------------------------------------------- */

function renderFhirStatus(f) {
  const counts = Object.entries(f.resource_counts || {}).map(([k, n]) => `<span class="chip"><span class="chip__sys">${escapeHtml(k)}</span>${num(n)}</span>`).join('');
  $('fhir-status').innerHTML = f.valid
    ? `<div class="alert alert--success">${icon('check-circle')}<div class="alert__body"><p class="alert__title">Valid FHIR R4B bundle</p><p class="mono text-xs">${escapeHtml(f.bundle_id)}</p><div class="cluster cluster--sm">${counts}</div></div></div>`
    : `<div class="alert alert--warning">${icon('alert-triangle')}<div class="alert__body"><p class="alert__title">Bundle ${escapeHtml(f.bundle_id)} has ${plural(f.errors.length, 'validation issue')}</p><ul class="wb-bullets">${f.errors.map((e) => `<li>${escapeHtml(e)}</li>`).join('')}</ul><div class="cluster cluster--sm">${counts}</div></div></div>`;
}

async function showJson(kind) {
  const run = state.run;
  if (!run || state.json[kind]?.runId === run.run_id) return;
  const tree = $(`${kind}-tree`);
  state.json[kind] = { runId: run.run_id };
  tree.innerHTML = '<span class="skeleton skeleton--block" style="--h:16rem"></span>';
  try {
    const data = await api(kind === 'fhir' ? run.downloads.fhir_bundle : run.downloads.audit_report, { timeout: 30000 });
    if (state.run !== run) return;
    const ctl = renderJsonTree(tree, data, { expandDepth: 2, label: kind === 'fhir' ? 'FHIR bundle JSON' : 'Audit report JSON' });
    state.json[kind] = { runId: run.run_id, data, ctl };
    if ($(`${kind}-search`).value.trim()) searchJson(kind);
  } catch (err) {
    if (state.run !== run) return;
    state.json[kind] = null;
    renderError(tree, err, () => showJson(kind), { title: kind === 'fhir' ? 'Could not load the FHIR bundle' : 'Could not load the audit report' });
  }
}

function searchJson(kind) {
  const ctl = state.json[kind]?.ctl;
  const q = $(`${kind}-search`).value.trim();
  if (!ctl) return;
  const n = ctl.search(q);
  $(`${kind}-matches`).textContent = q ? `${num(n)} ${n === 1 ? 'match' : 'matches'}` : '';
}

/* ---------------------------------------------------------------------- */
/* Wiring                                                                   */
/* ---------------------------------------------------------------------- */

$('library').addEventListener('change', (e) => {
  if (e.target.name === 'sample') { state.sample = e.target.value; updateSelection(); }
});
$('sample-search').addEventListener('input', debounce(filterLibrary, 120));
initDropzone($('dropzone'), (files) => pickFile(files[0]));
document.querySelectorAll('input[name="doc_type"]').forEach((r) => r.addEventListener('change', updateSelection));
$('input-tabs').addEventListener('tabchange', updateSelection);
$('run').addEventListener('click', run);

$('verdict-filters').addEventListener('change', applyFilters);
$('stage-filter').addEventListener('change', applyFilters);
$('field-filter').addEventListener('change', applyFilters);
$('claim-search').addEventListener('input', debounce(applyFilters, 150));
$('filters-reset').addEventListener('click', () => {
  document.querySelectorAll('input[name="verdict"]').forEach((i) => { i.checked = true; });
  $('stage-filter').value = '';
  $('field-filter').value = '';
  $('claim-search').value = '';
  applyFilters();
  $('claim-search').focus();
});

list.addEventListener('click', (e) => {
  const btn = e.target.closest('.wb-claim__open');
  if (btn) openClaim(btn.closest('.wb-claim').dataset.id, btn);
});
const cardRegion = (e) => e.target.closest?.('.wb-claim')?.dataset.region;
list.addEventListener('pointerover', (e) => linkRegion(cardRegion(e), true));
list.addEventListener('pointerout', (e) => linkRegion(cardRegion(e), false));
list.addEventListener('focusin', (e) => linkRegion(cardRegion(e), true));
list.addEventListener('focusout', (e) => linkRegion(cardRegion(e), false));

const boxRegion = (e) => e.target.closest?.('.wb-box')?.dataset.region;
overlay.addEventListener('pointerover', (e) => linkRegion(boxRegion(e), true));
overlay.addEventListener('pointerout', (e) => linkRegion(boxRegion(e), false));
overlay.addEventListener('focusin', (e) => linkRegion(boxRegion(e), true));
overlay.addEventListener('focusout', (e) => linkRegion(boxRegion(e), false));
overlay.addEventListener('click', (e) => {
  const n = boxRegion(e);
  if (!n) return;
  selectRegion(n, { scrollCards: true });
  const k = list.querySelectorAll(`.wb-claim[data-region="${n}"]`).length;
  announce(`Region ${n}: ${plural(k, 'claim')} highlighted in the claim list.`);
});

$('page-select').addEventListener('change', (e) => setPage(Number(e.target.value)));
$('zoom-in').addEventListener('click', () => stepZoom(1));
$('zoom-out').addEventListener('click', () => stepZoom(-1));
$('zoom-fit').addEventListener('click', () => setZoom(1));
$('viewer-toggle').addEventListener('click', () => setViewerOpen($('viewer-toggle').getAttribute('aria-expanded') !== 'true'));

const step = (d) => {
  const next = state.visible[state.visible.indexOf(state.current) + d];
  if (next) openClaim(next.claim_id);
};
$('drawer-prev').addEventListener('click', () => step(-1));
$('drawer-next').addEventListener('click', () => step(1));
$('drawer-locate').addEventListener('click', () => { state.locate = true; drawer.close(); });
drawer.addEventListener('close', () => {
  const c = state.current, locate = state.locate;
  state.locate = false;
  // After core.js returns focus to the original opener: land on the claim shown last, or its box.
  setTimeout(() => {
    if (locate) setViewerOpen(true);
    const target = locate && c?._region
      ? overlay.querySelector(`.wb-box[data-region="${c._region.n}"]`)
      : list.querySelector(`.wb-claim[data-id="${CSS.escape(c?.claim_id || '')}"] .wb-claim__open`);
    target?.focus();
  });
});

$('result-tabs').addEventListener('tabchange', (e) => {
  if (e.detail.id === 'fhir' || e.detail.id === 'audit') showJson(e.detail.id);
});
['fhir', 'audit'].forEach((k) => $(`${k}-search`).addEventListener('input', debounce(() => searchJson(k), 200)));
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-json-action]');
  if (!b) return;
  const slot = state.json[b.dataset.json];
  if (!slot?.ctl) { toast('The JSON is still loading.', { kind: 'info', timeout: 2500 }); return; }
  if (b.dataset.jsonAction === 'expand') slot.ctl.expandAll();
  else if (b.dataset.jsonAction === 'collapse') slot.ctl.collapseAll();
  else copyText(JSON.stringify(slot.data, null, 2));
});
$('copy-link').addEventListener('click', () => copyText(location.href));

/* ---------------------------------------------------------------------- */
/* Boot + deep links: ?run=<id> reloads a stored run, ?sample=<id> runs it  */
/* ---------------------------------------------------------------------- */

async function boot() {
  const samplesLoaded = loadSamples();
  const runId = getQuery('run'), sampleId = getQuery('sample');
  if (runId) {
    try {
      await showResults(await api(`/api/runs/${encodeURIComponent(runId)}`));
      $('results').scrollIntoView({ block: 'start', behavior: smooth() });
      await samplesLoaded;
      if (state.run && state.samples.some((s) => s.id === state.run.document_id)) selectSample(state.run.document_id);
      return;
    } catch (err) {
      setQuery({ run: null });
      if (!sampleId) {
        $('progress').hidden = false;
        $('progress-note').hidden = true;
        $('stepper').replaceChildren();
        $('elapsed').textContent = '';
        renderError($('progress-error'), err, null, { title: 'That run is no longer available' });
        $('progress-error').insertAdjacentHTML('beforeend', '<p class="text-sm muted" style="margin-top:var(--space-2)">The server keeps its 32 most recent runs in memory until it restarts. Choose a document above and run it again.</p>');
        return;
      }
      toast(`Run ${runId.slice(0, 8)} has expired; processing ${sampleId} again.`, { kind: 'info' });
    }
  }
  if (sampleId) {
    await samplesLoaded;
    if (selectSample(sampleId)) run();
    else $('library-status').textContent = `Sample ${sampleId} is not in the library on this server.`;
  }
}
boot();

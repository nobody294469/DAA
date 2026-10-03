/**
 * Model page (/model.html): Stage B serving status, live training progress and model cards, from GET /api/model.
 * Authority: scripts/train_v3_experiment.py (model card and *_progress.json schemas), src/clindoc/web/app.py
 * (/api/model), reports/M2_V3_MODERNBERT_TRAINING_REPORT.md (the reference run). Any card key may be
 * missing or null; every renderer falls back to a dash instead of failing.
 */
import { mountShell, api, renderError, escapeHtml, icon, isNum, num, pct, seconds, verdictBadge, servingSummary, getQuery, setQuery, toast, DASH } from '/assets/js/core.js';
import { lineChart, heatGrid, sparkline } from '/assets/js/charts.js';

mountShell();

const $ = (id) => document.getElementById(id);
const LABELS = ['SUPPORTED', 'DISCREPANCY', 'INSUFFICIENT_EVIDENCE'];
const LABEL_NAMES = ['Supported', 'Discrepancy', 'Insufficient evidence'];
const REFERENCE_SOURCE = 'reports/M2_V3_MODERNBERT_TRAINING_REPORT.md';
const POLL_MS = 5000;
const PHASES = [['load', 'Load data and model'], ['train', 'Train and validate'], ['calibrate', 'Calibrate'], ['evaluate', 'Evaluate'], ['done', 'Done']];
const METRICS = [
  ['accuracy', 'Accuracy', (v) => pct(v, 2), true],
  ['macro_f1', 'Macro-F1', (v) => num(v, 4), false],
  ['discrepancy_recall', 'Discrepancy recall', (v) => pct(v, 2), true],
];
const DEFAULT_CMD = [
  '$env:PYTHONPATH = "src"',
  '# Full run; the script defaults reproduce the reference run and write a model card.',
  'python scripts/train_v3_experiment.py',
  '# Without Hugging Face hub access, point --model-id at a local copy of the base model:',
  '#   python scripts/train_v3_experiment.py --model-id <path to BioClinical-ModernBERT-base>',
  '# Quick end-to-end check on a tiny subset (1 epoch, no model card):',
  '#   python scripts/train_v3_experiment.py --smoke --device cpu',
].join('\n');

const state = { data: null, liveRun: null, liveKey: '', timer: 0, spark: null };
const charts = [];

/* Small renderers -------------------------------------------------------------------------------- */
const basename = (p) => String(p || '').split(/[\\/]/).filter(Boolean).pop() || '';
const when = (iso) => {
  const d = new Date(iso);
  return iso && !Number.isNaN(d.getTime()) ? d.toLocaleString() : DASH;
};
const esc = (v) => (v === null || v === undefined || v === '' ? DASH : escapeHtml(typeof v === 'object' ? JSON.stringify(v) : v));
const code = (v) => (v === null || v === undefined || v === '' ? DASH : `<code>${escapeHtml(v)}</code>`);
/** Definition list; values are trusted HTML (escape before passing). */
const kv = (pairs) => (pairs.length
  ? `<dl class="kv">${pairs.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${v}</dd>`).join('')}</dl>`
  : '<p class="muted text-sm">Not recorded.</p>');
const kvObject = (o) => kv(Object.entries(o || {}).map(([k, v]) => [k,
  v !== null && typeof v === 'object' ? code(JSON.stringify(v)) : Number.isInteger(v) && v >= 1e5 ? num(v) : esc(v)]));
const tile = (label, value, note = '', extra = '') =>
  `<div class="stat"><p class="stat__label">${escapeHtml(label)}</p><p class="stat__value">${value}</p>${extra}${note ? `<p class="stat__note">${note}</p>` : ''}</div>`;
/** Delta line: arrow = direction, sign in the text, colour only adds good/bad. */
const deltaLine = (d, good, text) =>
  `<p class="stat__delta${good === null ? '' : good ? ' is-good' : ' is-bad'}">${icon(d > 0 ? 'arrow-up' : d < 0 ? 'arrow-down' : 'minus', 'icon--sm')}${escapeHtml(text)}</p>`;
const codeBlock = (id, label, text) =>
  `<div class="code-block"><span class="code-block__label">${escapeHtml(label)}</span><button class="btn btn--ghost btn--sm code-block__copy" type="button" data-copy-target="#${id}" aria-label="Copy command">${icon('copy')}Copy</button><pre><code id="${id}">${escapeHtml(text)}</code></pre></div>`;
const figure = (id, title, subtitle) =>
  `<figure class="card card--ruled chart-card"><div class="chart-card__header"><div><h4 class="chart-card__title">${escapeHtml(title)}</h4><p class="chart-card__subtitle">${escapeHtml(subtitle)}</p></div></div><div id="${id}"></div></figure>`;

/* Serving ----------------------------------------------------------------------------------------- */
function renderServing(s, runs) {
  const b = servingSummary({ stage_b: s }).stageB;
  $('serving-pill').innerHTML = `<span class="status-pill ${b.real ? 'is-live' : 'is-fallback'}"><span class="status-pill__dot" aria-hidden="true"></span><span class="status-pill__text">${escapeHtml(b.label)}</span></span>`;
  const ck = basename(s?.checkpoint);
  const run = ck && runs.find((c) => basename(c.checkpoint?.path) === ck);
  const rows = [
    ['Mode', b.real ? 'Fine-tuned ModernBERT checkpoint' : 'Heuristic fallback (rule-based stand-in)'],
    ['Checkpoint', s?.checkpoint ? `${code(s.checkpoint)}${run ? ` <span class="muted">from run ${escapeHtml(run.run_name)}</span>` : ''}` : 'None loaded'],
    ['Device', esc(s?.device)],
    ['Temperature', isNum(s?.temperature) ? num(s.temperature, 4) : DASH],
    ['Base model', code(s?.base_model)],
  ];
  if (s?.load_error) rows.push(['Load error', code(s.load_error)]);
  $('serving-body').innerHTML = kv(rows) + (b.real ? '' : `<div class="alert alert--warning">${icon('alert-triangle')}<div class="alert__body"><p class="alert__title">Stage B verdicts are not coming from ModernBERT</p><p>No trained checkpoint is loaded, so the Workbench and Playground use a rule-based stand-in for Stage B. Train a run (see below) or start the server with <code>clindoc serve --checkpoint &lt;path to .pt&gt;</code>.</p></div></div>`);
}

/* Live training ----------------------------------------------------------------------------------- */
function renderLive(a) {
  state.spark?.destroy();
  state.spark = null;
  $('live').hidden = !a;
  if (!a) return;
  const frac = isNum(a.global_step) && isNum(a.total_steps) && a.total_steps > 0 ? Math.min(1, a.global_step / a.total_steps) : null;
  const bar = $('live-progress');
  if (frac === null) bar.removeAttribute('value');
  else bar.value = frac;
  $('live-steps').textContent = frac === null ? `Training progress: ${a.phase || 'starting'}` : `Training progress: step ${num(a.global_step)} of ${num(a.total_steps)}`;
  $('live-pct').textContent = frac === null ? '' : pct(frac, 0);
  $('live-sub').innerHTML = `Run <code>${escapeHtml(a.run_name)}</code> on ${escapeHtml(a.device || 'an unknown device')}, started ${escapeHtml(when(a.started_at))}`;
  const ago = Math.round((Date.now() - new Date(a.updated_at)) / 1000);
  $('live-updated').textContent = isNum(ago) && ago >= 0 ? `Live, updated ${ago} s ago` : 'Live';

  const cur = a.phase === 'validate' ? 1 : Math.max(0, PHASES.findIndex(([k]) => k === a.phase));
  $('live-phases').innerHTML = PHASES.map(([, label], i) => {
    const cls = i < cur ? 'is-done' : i === cur ? 'is-current' : 'is-pending';
    const detail = i === 1 && a.epoch > 0 ? `<p class="stepper__detail">Epoch ${num(a.epoch)} of ${num(a.max_epochs)}${a.phase === 'validate' ? ', validating' : ''}</p>` : '';
    return `<li class="stepper__step ${cls}"${i === cur ? ' aria-current="step"' : ''}><span class="stepper__marker">${i < cur ? icon('check') : ''}</span><div><p class="stepper__label">${label}${i < cur ? '<span class="visually-hidden">, done</span>' : ''}</p>${detail}</div></li>`;
  }).join('');

  const hist = (a.history || []).map((h) => h.val_macro_f1).filter(isNum);
  $('live-stats').innerHTML = [
    tile('Epoch', `${num(a.epoch)}<small>/ ${num(a.max_epochs)}</small>`, isNum(a.steps_per_epoch) ? `step ${num(a.step)} of ${num(a.steps_per_epoch)} in this epoch` : ''),
    tile('Train loss, running', num(a.train_loss, 4)),
    tile('Last validation macro-F1', num(a.last_val_macro_f1, 4), hist.length < 2 ? 'trend appears after two epochs' : '', '<div class="stat__trend live__spark" id="live-spark"></div>'),
    tile('Time remaining', isNum(a.eta_seconds) ? seconds(a.eta_seconds) : DASH, 'estimated from the step rate so far'),
  ].join('');
  state.spark = sparkline($('live-spark'), { data: hist, format: 'dec4', ariaLabel: 'Validation macro-F1 by epoch' });

  const key = `${a.run_name}|${a.phase}|${a.epoch}`;
  if (key !== state.liveKey) {
    state.liveKey = key;
    $('live-announce').textContent = `Training ${a.run_name}: ${a.phase || 'starting'}, epoch ${a.epoch ?? 0} of ${a.max_epochs ?? DASH}.`;
  }
}

const stopPolling = () => { clearTimeout(state.timer); state.timer = 0; };
function schedulePoll() {
  stopPolling();
  if (state.liveRun && !document.hidden) state.timer = setTimeout(poll, POLL_MS);
}
async function poll() {
  let d;
  try {
    d = await api('/api/model', { timeout: 10000 });
  } catch {
    $('live-updated').textContent = 'Update failed, retrying';
    schedulePoll();
    return;
  }
  if (d.active) {
    state.liveRun = d.active.run_name;
    renderLive(d.active);
    schedulePoll();
    return;
  }
  const name = state.liveRun;
  const card = (d.runs || []).find((c) => c.run_name === name);
  toast(card ? `Run ${name} finished; its model card is now listed.` : `Run ${name} stopped reporting progress.`, { kind: card ? 'success' : 'warning', title: 'Training update' });
  render(d, card ? name : null);
}
// Poll only while a run trains and the tab is visible; catch up at once when the tab returns.
document.addEventListener('visibilitychange', () => {
  if (document.hidden) stopPolling();
  else if (state.liveRun) poll();
});

/* Run card ---------------------------------------------------------------------------------------- */
/** The reported reference run (/api/model.reference) reshaped like a model card. */
const refCard = (r) => r && {
  run_name: r.run_name, base_model: r.base_model,
  hardware: { gpu: r.hardware, parameters: r.parameters },
  config: { model_id: r.base_model, max_seq_length: r.max_seq_length, batch_size: r.batch_size },
  training: { duration_s: r.duration_s, best_epoch: r.best_epoch, epochs_run: r.history?.length, history: r.history || [] },
  validation: r.validation, calibration: r.calibration, ood: r.ood,
  checkpoint: { path: r.checkpoint, size_bytes: r.checkpoint_size_bytes },
};

function headTiles(m = {}, ref) {
  return METRICS.map(([k, label, fmt, isPct]) => {
    const v = m?.[k], rv = ref?.[k];
    let delta = '';
    if (isNum(v) && isNum(rv)) {
      const d = v - rv, flat = Math.abs(d) < 5e-5;
      const size = isPct ? `${num(Math.abs(d) * 100, 2)} pts` : num(Math.abs(d), 4);
      delta = deltaLine(flat ? 0 : d, flat ? null : d > 0, flat ? `Same as reference (${fmt(rv)})` : `${d > 0 ? '+' : '-'}${size} vs reference ${fmt(rv)}`);
    }
    return tile(label, fmt(v), '', delta);
  }).join('');
}

function perClassTable(id, v, o) {
  if (!v?.per_class && !o?.per_class) return '';
  const KS = ['precision', 'recall', 'f1', 'support'];
  const cells = (pc, l) => KS.map((k) => `<td class="num">${k === 'support' ? num(pc?.[l]?.[k]) : num(pc?.[l]?.[k], 3)}</td>`).join('');
  const sub = '<th scope="col" class="num">Precision</th><th scope="col" class="num">Recall</th><th scope="col" class="num">F1</th><th scope="col" class="num">Support</th>';
  return `<div class="stack stack--sm"><h4 id="${id}-pc-h">Per-class metrics</h4>
    <div class="table-wrap" tabindex="0" role="region" aria-labelledby="${id}-pc-h"><table class="table table--compact">
      <caption class="visually-hidden">Precision, recall, F1 and support per verdict class on the validation and OOD splits.</caption>
      <colgroup><col></colgroup><colgroup span="4"></colgroup><colgroup span="4"></colgroup>
      <thead><tr><th scope="col" rowspan="2">Class</th><th scope="colgroup" colspan="4" class="res-group">Validation</th><th scope="colgroup" colspan="4" class="res-group">OOD</th></tr><tr>${sub}${sub}</tr></thead>
      <tbody>${LABELS.map((l) => `<tr><th scope="row">${verdictBadge(l)}</th>${cells(v?.per_class, l)}${cells(o?.per_class, l)}</tr>`).join('')}</tbody>
    </table></div></div>`;
}

function calibrationBlock(cal = {}, v = {}, o = {}) {
  const lower = (label, before, after) => {
    if (!isNum(after)) return tile(label, DASH);
    const d = isNum(before) ? after - before : 0;
    return tile(label, num(after, 4), '', isNum(before) ? deltaLine(d, d === 0 ? null : d < 0, `${d > 0 ? '+' : '-'}${num(Math.abs(d), 4)} from ${num(before, 4)} before`) : '');
  };
  const pick = (m, ...keys) => keys.map((k) => m?.[k]).find(isNum);
  const extra = [['ECE by split', ['ece']], ['Brier score by split', ['brier_score', 'brier']], ['AUROC, discrepancy vs rest', ['auroc_discrepancy']], ['AUROC, macro', ['auroc_macro']]]
    .filter(([, ks]) => isNum(pick(v, ...ks)) || isNum(pick(o, ...ks)))
    .map(([label, ks]) => [label, `Validation ${num(pick(v, ...ks), 4)} · OOD ${num(pick(o, ...ks), 4)}`]);
  return `<div class="stack stack--sm"><h4>Calibration</h4>
    <div class="stat-row stat-row--align" style="--min:12rem">
      ${tile('Temperature T', num(cal.temperature, 4), 'fitted on the calibration split only (DEC-009)')}
      ${lower('Expected calibration error after scaling', cal.ece_before, cal.ece_after)}
      ${lower('Negative log-likelihood after scaling', cal.nll_before, cal.nll_after)}
    </div>${extra.length ? kv(extra) : ''}</div>`;
}

function reproduceCommand(card) {
  const c = card.config || {};
  const arg = (v) => {
    const s = String(v);
    if (s.startsWith('~')) return `"$HOME${s.slice(1)}"`; // committed cards replace the home directory with "~"
    return /^[\w.:\\/-]+$/.test(s) ? s : `'${s.replace(/'/g, "''")}'`;
  };
  const flags = [
    ['--run-name', card.run_name], ['--model-id', c.model_id ?? card.base_model], ['--seed', c.seed ?? c.seeds?.[0]],
    ['--max-epochs', c.max_epochs], ['--patience', c.early_stopping_patience], ['--batch-size', c.batch_size],
    ['--grad-accum', c.grad_accum_steps], ['--max-seq-length', c.max_seq_length], ['--freeze-lower-n-layers', c.freeze_lower_n_layers],
    ['--device', c.device ?? card.device], ['--gpu-mem-fraction', c.gpu_mem_fraction],
  ].filter(([, v]) => v !== null && v !== undefined && v !== '').map(([k, v]) => `${k} ${arg(v)}`);
  if (c.gradient_checkpointing) flags.push('--gradient-checkpointing');
  return ['$env:PYTHONPATH = "src"', 'python scripts/train_v3_experiment.py `', ...flags.map((f, i) => `  ${f}${i < flags.length - 1 ? ' `' : ''}`)].join('\n');
}

function renderRun(host, card, ref, isReference = false) {
  charts.splice(0).forEach((c) => c.destroy());
  const id = isReference ? 'ref' : 'run';
  const t = card.training || {}, hist = t.history || [], hw = card.hardware || {}, ds = card.dataset || {}, ck = card.checkpoint || {};
  const meta = [
    ['clock', card.created_at ? `Trained ${when(card.created_at)}` : ''],
    ['activity', isNum(t.duration_s) ? `${seconds(t.duration_s)} wall time` : ''],
    ['cpu', [card.device, hw.gpu].filter(Boolean).join(', ')],
    ['layers', isNum(t.epochs_run) ? `${num(t.epochs_run)} epochs, best epoch ${num(t.best_epoch)}` : ''],
    ['git-branch', card.git_commit ? `commit ${String(card.git_commit).slice(0, 10)}` : ''],
  ].filter(([, x]) => x).map(([ic, x]) => `<span>${icon(ic, 'icon--sm')}${escapeHtml(x)}</span>`).join('');
  const refHist = !isReference && ref?.training?.history?.length ? ref.training.history : null;
  const cm = (m) => (Array.isArray(m) ? null : '<p class="chart__empty">Not recorded for this run.</p>');
  host.innerHTML = `<div class="stack stack--lg">
    <div class="stack stack--sm">
      <h3>${isReference ? 'Reported reference run' : 'Run'} <code>${escapeHtml(card.run_name || DASH)}</code></h3>
      <div class="run-meta">${meta}</div>
      ${isReference ? `<p class="res-note">As reported in <code>${REFERENCE_SOURCE}</code>. Per-class metrics, the full configuration and dataset hashes were not recorded for this run.</p>` : ''}
    </div>
    <div class="stack stack--sm"><h4>Validation split</h4><div class="stat-row stat-row--align" style="--min:12rem">${headTiles(card.validation, isReference ? null : ref?.validation)}</div></div>
    <div class="stack stack--sm"><h4>OOD split (document templates unseen in training)</h4><div class="stat-row stat-row--align" style="--min:12rem">${headTiles(card.ood, isReference ? null : ref?.ood)}</div></div>
    <div class="grid" style="--min:22rem">
      ${figure(`${id}-loss`, 'Loss by epoch', refHist ? 'Lower is better. Grey dotted line: reference validation loss.' : 'Lower is better.')}
      ${figure(`${id}-f1`, 'Validation macro-F1 by epoch', refHist ? 'Grey dashed line: reference run.' : 'Selected checkpoint: best epoch.')}
    </div>
    <div class="grid" style="--min:20rem">
      ${figure(`${id}-cm-val`, 'Confusion matrix, validation', 'Rows are the ground truth; count and row share per cell.')}
      ${figure(`${id}-cm-ood`, 'Confusion matrix, OOD', 'Rows are the ground truth; count and row share per cell.')}
    </div>
    ${perClassTable(id, card.validation, card.ood)}
    ${calibrationBlock(card.calibration, card.validation, card.ood)}
    <div>
      <details class="accordion"><summary>Configuration</summary><div class="accordion__body">${kvObject(card.config)}</div></details>
      <details class="accordion"><summary>Hardware and software</summary><div class="accordion__body">${kvObject(card.hardware)}</div></details>
      <details class="accordion"><summary>Dataset partitions and hashes</summary><div class="accordion__body">${kv([
        ...Object.entries(ds.partition_counts || {}).map(([k, v]) => [`${k} examples`, esc(v)]),
        ...Object.entries(ds.hashes || {}).map(([p, h]) => [`SHA-256 ${p}`, `<span class="mono text-xs">${escapeHtml(h)}</span>`]),
      ])}</div></details>
      <details class="accordion"><summary>Checkpoint</summary><div class="accordion__body">${kv([['Path', code(ck.path)], ['Size', isNum(ck.size_bytes) ? `${num(ck.size_bytes / 2 ** 20, 1)} MB` : DASH], ['Base model', code(card.base_model)]])}</div></details>
    </div>
    ${isReference ? '' : `<div class="stack stack--sm"><h4>Reproduce</h4><p class="res-note">The training command rebuilt from this run's recorded configuration.</p>${codeBlock(`${id}-cmd`, 'PowerShell, from the repository root', reproduceCommand(card))}</div>`}
  </div>`;

  const best = isNum(t.best_epoch) ? [{ type: 'x', x: t.best_epoch, label: 'Best epoch' }] : [];
  const grey = 'var(--chart-crosshair)';
  charts.push(lineChart($(`${id}-loss`), {
    x: 'epoch', format: 'dec4', xLabel: 'Epoch', yLabel: 'Loss', annotations: best, emptyText: 'No training history recorded.',
    series: [
      { label: 'Train loss', data: hist, y: 'train_loss' },
      { label: 'Validation loss', data: hist, y: 'val_loss' },
      ...(refHist ? [{ label: 'Reference validation loss', data: refHist, y: 'val_loss', color: grey }] : []),
    ],
    ariaLabel: 'Training and validation loss by epoch',
  }));
  charts.push(lineChart($(`${id}-f1`), {
    x: 'epoch', format: 'dec4', xLabel: 'Epoch', yLabel: 'Macro-F1', annotations: best, endLabels: true, emptyText: 'No training history recorded.',
    series: [
      { label: 'Validation macro-F1', data: hist, y: 'val_macro_f1' },
      ...(refHist ? [{ label: 'Reference validation macro-F1', data: refHist, y: 'val_macro_f1', color: grey }] : []),
    ],
    ariaLabel: 'Validation macro-F1 by epoch',
  }));
  [['cm-val', card.validation], ['cm-ood', card.ood]].forEach(([slot, m]) => {
    const el = $(`${id}-${slot}`);
    const empty = cm(m?.confusion_matrix);
    if (empty) el.innerHTML = empty;
    else charts.push(heatGrid(el, { matrix: m.confusion_matrix, rows: LABEL_NAMES, cols: LABEL_NAMES, ariaLabel: `Confusion matrix, ${slot === 'cm-val' ? 'validation' : 'OOD'} split, run ${card.run_name}` }));
  });
}

function renderEmptyRuns(ref) {
  $('runs-lede').textContent = 'No Stage B run has been trained on this machine yet.';
  $('run-view').innerHTML = `<div class="stack stack--lg">
    <div class="model-intro">
      <h3 class="model-intro__title">No model cards yet</h3>
      <p class="text-2 measure">Stage B is BioClinical-ModernBERT-base fine-tuned as a three-way classifier (Supported, Discrepancy, Insufficient evidence) that reads the claim text with a window of its source evidence. Its probabilities are temperature-scaled on a separate calibration split; a confident verdict that agrees with Stage A settles the claim, and everything else escalates to the Stage C judge.</p>
      <p class="text-2 measure">A full training run writes a model card to <code>reports/stage_b_runs/</code> and a checkpoint to <code>data/m2_results_v3/checkpoints/</code>. This page lists the card and shows live progress while the run trains. Until then the reported reference run below is the benchmark.</p>
      ${codeBlock('train-cmd', 'Train Stage B (PowerShell, from the repository root)', DEFAULT_CMD)}
    </div>
    <div class="model-ref" id="ref-view"></div>
  </div>`;
  if (ref) renderRun($('ref-view'), refCard(ref), null, true);
}

/* Page -------------------------------------------------------------------------------------------- */
function showRun() {
  const runs = state.data.runs || [];
  const card = runs.find((c) => c.run_name === $('run-select').value) || runs[0];
  $('runs-lede').innerHTML = `${num(runs.length)} model card${runs.length === 1 ? '' : 's'} written by <code>scripts/train_v3_experiment.py</code> to <code>reports/stage_b_runs/</code>, newest first. Deltas and grey lines compare each run with the reported reference run.`;
  renderRun($('run-view'), card, refCard(state.data.reference));
}

function render(d, pick) {
  state.data = d;
  const runs = Array.isArray(d.runs) ? d.runs : [];
  renderServing(d.serving, runs);
  state.liveRun = d.active?.run_name ?? null;
  renderLive(d.active);
  schedulePoll();
  $('run-controls').hidden = !runs.length;
  if (!runs.length) {
    renderEmptyRuns(d.reference);
    return;
  }
  const servingCk = basename(d.serving?.checkpoint);
  const sel = $('run-select');
  sel.innerHTML = runs.map((c) => `<option value="${escapeHtml(c.run_name)}">${escapeHtml(c.run_name)} · ${escapeHtml(when(c.created_at))}${servingCk && basename(c.checkpoint?.path) === servingCk ? ' (serving)' : ''}</option>`).join('');
  const want = pick || getQuery('run');
  if (want && runs.some((c) => c.run_name === want)) sel.value = want;
  sel.onchange = () => { setQuery({ run: sel.value }); showRun(); };
  showRun();
}

async function load(pick) {
  const btn = $('refresh');
  btn.setAttribute('aria-busy', 'true');
  btn.disabled = true;
  $('model-error').replaceChildren();
  try {
    const d = await api('/api/model');
    ['serving', 'runs'].forEach((s) => { $(s).hidden = false; });
    render(d, pick);
    $('model-status').textContent = `Model information loaded: ${num((d.runs || []).length)} runs; Stage B serving ${servingSummary({ stage_b: d.serving }).stageB.label}${d.active ? `; run ${d.active.run_name} is training` : ''}.`;
  } catch (err) {
    stopPolling();
    ['serving', 'live', 'runs'].forEach((s) => { $(s).hidden = true; });
    renderError($('model-error'), err, () => load(), { title: 'Model information unavailable' });
    $('model-status').textContent = 'Model information could not be loaded.';
  } finally {
    btn.removeAttribute('aria-busy');
    btn.disabled = false;
  }
}

$('refresh').addEventListener('click', () => load());
load();

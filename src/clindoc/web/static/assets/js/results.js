/**
 * Results page (/results.html): the frozen benchmark report, rendered from GET /api/results.
 * Authority: src/clindoc/web/data/results.json (shape in its "_schema" key and scripts/build_web_data.py);
 * charts and their rules come from charts.js. No metric is written here: every number is read from
 * the API payload, and each section prints the source files listed in results.sources.
 */
import { mountShell, api, renderError, escapeHtml, icon, isNum, num, pct, seconds, verdictLabel, verdictBadge, splitChip, DASH } from '/assets/js/core.js';
import { barChart, groupedBarChart, lineChart, heatGrid, stackedBar, smallMultiples, toggleTable } from '/assets/js/charts.js';

mountShell();

const $ = (id) => document.getElementById(id);
const LABELS = ['SUPPORTED', 'DISCREPANCY', 'INSUFFICIENT_EVIDENCE'];
const LABEL_SERIES = LABELS.map((l) => ({ key: l, label: verdictLabel(l) }));
const PRODUCTION = 'V8_V1';
const SHORT = {
  V1: 'V1 Deterministic', V2: 'V2 Levenshtein', V3: 'V3 TF-IDF + LR', V4: 'V4 SapBERT', V5: 'V5 PubMedBERT NLI',
  V6: 'V6 ModernBERT', V8_V0: 'V8 Cascade, prompt V0', V8_V1: 'V8 Cascade, prompt V1', V9: 'V9 Qwen-7B',
};
const short = (id) => SHORT[id] || id;
const human = (s) => String(s).replace(/_v\d+$/, '').replace(/_/g, ' ').replace(/\b(jpeg|ocr)\b/gi, (m) => m.toUpperCase());
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const list = (xs) => (xs.length < 2 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`);
const say = (id, text) => { $(id).textContent = text; };
const source = (id, r, key) => {
  const paths = r.sources?.[key] || [];
  $(id).innerHTML = paths.length ? `Source: ${paths.map((p) => `<code>${escapeHtml(p)}</code>`).join(', ')}` : '';
};
const tile = (label, value, note = '', extra = '') =>
  `<div class="stat"><p class="stat__label">${escapeHtml(label)}</p><p class="stat__value">${value}</p>${extra}${note ? `<p class="stat__note">${note}</p>` : ''}</div>`;
/** Delta line: arrow shows the direction, sign in the text, colour only says good/bad (never alone). */
const deltaLine = (d, good, text) =>
  `<p class="stat__delta${good === null ? '' : good ? ' is-good' : ' is-bad'}">${icon(d > 0 ? 'arrow-up' : d < 0 ? 'arrow-down' : 'minus', 'icon--sm')}${escapeHtml(text)}</p>`;
const table = (labelId, caption, inner) =>
  `<div class="table-wrap" tabindex="0" role="region" aria-labelledby="${labelId}"><table class="table table--compact"><caption class="visually-hidden">${escapeHtml(caption)}</caption>${inner}</table></div>`;

/** Live charts by slot. The old chart is destroyed BEFORE make() runs: destroy() empties the shared container. */
const charts = new Map();
const put = (slot, make) => {
  charts.get(slot)?.destroy();
  const chart = make();
  charts.set(slot, chart);
  return chart;
};

/* 1  Headline ------------------------------------------------------------------------------------ */
function headline(r) {
  const h = r.headline;
  $('headline-tiles').innerHTML = [
    tile('Cascade accuracy', pct(h.cascade_accuracy), `${num(h.cascade_correct)} of ${num(h.n_claims_ood)} OOD claims correct; macro-F1 ${num(h.cascade_macro_f1, 3)}`),
    tile('Discrepancy recall', pct(h.cascade_discrepancy_recall), `${num(h.cascade_discrepancies_caught)} of ${num(h.n_discrepancies_ood)} ground-truth discrepancies caught`),
    tile('Verification errors', num(h.cascade_errors), `ModernBERT alone makes ${num(h.modernbert_errors)}, Qwen-7B alone ${num(h.qwen_errors)}`),
    tile('Settled at Stage B', pct(h.autonomous_coverage), `${num(h.autonomous_claims)} claims at ${pct(h.autonomous_accuracy, 2)} accuracy, no LLM call`),
    tile('Escalated to the LLM judge', pct(h.escalation_rate), `${num(h.escalated_to_llm)} claims; Stage C is ${pct(h.stage_c_accuracy)} accurate on them`),
    tile('Runtime on the OOD split', seconds(h.runtime_cascade_seconds), `${num(h.speedup_vs_qwen, 1)}x faster than Qwen-7B on every claim (${seconds(h.runtime_qwen_seconds)})`),
    tile('Benchmark size', num(h.benchmark_claims), `labelled claims from ${num(h.benchmark_documents)} synthetic documents; ${num(h.n_claims_ood)} in the OOD split`),
  ].join('');
  $('headline-tiles').removeAttribute('aria-busy');
  say('headline-takeaway', `On ${num(h.n_claims_ood)} claims from document templates never seen in training, the cascade makes ${num(h.cascade_errors)} errors: ${pct(h.error_reduction_vs_modernbert, 0)} fewer than ModernBERT alone and ${pct(h.error_reduction_vs_qwen, 0)} fewer than Qwen-7B alone, while only ${pct(h.escalation_rate)} of claims reach the LLM.`);
  source('headline-source', r, 'headline');
}

/* 2  Leaderboard --------------------------------------------------------------------------------- */
const LB = {
  accuracy: { key: 'accuracy', label: 'Accuracy', format: 'pct', domain: [0, 1] },
  macro_f1: { key: 'macro_f1', label: 'Macro-F1', format: 'dec3', domain: [0, 1] },
  discrepancy_recall: { key: 'discrepancy_recall', label: 'Discrepancy recall', format: 'pct', domain: [0, 1] },
  errors: { key: 'errors', label: 'Errors', format: 'int', low: true },
  runtime: { key: 'runtime_seconds', label: 'Runtime for the OOD split (log scale)', format: 'sec', scale: 'log', low: true },
};

function drawLeaderboard(board, metric) {
  const m = LB[metric] || LB.accuracy;
  const host = $('lb-chart');
  const rows = board
    .filter((d) => isNum(d[m.key]) && (m.scale !== 'log' || d[m.key] > 0))
    .map((d) => ({ ...d, label: `${short(d.id)}${m.scale === 'log' && d.runtime_approx ? ' (approx.)' : ''}` }))
    .sort((a, b) => (m.low ? a[m.key] - b[m.key] : b[m.key] - a[m.key]));
  put('lb', () => barChart(host, {
    data: rows, x: 'label', y: m.key, format: m.format, domain: m.domain, scale: m.scale, labels: 'all',
    highlight: (d) => d.id === PRODUCTION, xLabel: 'Model', yLabel: m.label, table: false,
    // Log axis: every decade on wide charts, every other decade on phones so tick labels never collide.
    tickFormat: m.scale === 'log' ? (v) => (host.clientWidth < 520 && Math.round(Math.log10(v)) % 2 ? '' : `${v < 1 ? v : num(v)} s`) : undefined,
    ariaLabel: `${m.label} by model on the V3 OOD split`,
  }));
  $('lb-subtitle').textContent = `Production cascade highlighted; ${m.low ? 'lowest' : 'highest'} first.`;
}

function leaderboard(r) {
  const board = r.leaderboard;
  drawLeaderboard(board, document.querySelector('input[name="lb-metric"]:checked')?.value);
  $('lb-metric').onchange = (e) => drawLeaderboard(board, e.target.value);
  $('lb-table').replaceChildren();
  toggleTable($('lb-table'), [
    { key: 'name', label: 'Model' },
    { key: 'id', label: 'ID' },
    { key: 'architecture', label: 'Architecture' },
    { key: 'accuracy', label: 'Accuracy', numeric: true, format: (v) => pct(v, 2) },
    { key: 'macro_f1', label: 'Macro-F1', numeric: true, format: (v) => num(v, 4) },
    { key: 'discrepancy_recall', label: 'Discrepancy recall', numeric: true, format: (v) => pct(v, 2) },
    { key: 'errors', label: 'Errors', numeric: true, format: (v) => num(v) },
    { key: 'runtime_seconds', label: 'Runtime', numeric: true, format: (v, d) => `${d.runtime_approx ? 'approx. ' : ''}${seconds(v)}` },
  ], board, { label: 'View full leaderboard as table' });
  const by = Object.fromEntries(board.map((d) => [d.id, d]));
  const p = by[PRODUCTION], v6 = by.V6, v9 = by.V9;
  if (p && v6 && v9) {
    say('leaderboard-takeaway', `The production cascade reaches ${pct(p.accuracy)} accuracy with ${num(p.errors)} errors, ahead of ModernBERT alone (${pct(v6.accuracy)}, ${num(v6.errors)} errors) and Qwen-7B alone (${pct(v9.accuracy)}, ${num(v9.errors)} errors), in ${num(v9.runtime_seconds / p.runtime_seconds, 1)}x less time than Qwen-7B.`);
  }
  source('leaderboard-source', r, 'leaderboard');
  if (board.some((d) => d.runtime_approx)) $('leaderboard-source').insertAdjacentHTML('beforeend', '. Runtimes marked approx. are estimates from the data sheet.');
}

/* 3  Confusion matrices -------------------------------------------------------------------------- */
const CM_MODELS = [
  ['V8_V1', 'V8 cascade, prompt V1'], ['V6', 'V6 ModernBERT alone'], ['V9', 'V9 Qwen-7B alone'],
  ['V5', 'V5 PubMedBERT zero-shot NLI'], ['V1', 'V1 deterministic matcher'], ['stage_a', 'Stage A rules alone'],
];

function confusion(r) {
  const cm = r.confusion_matrices, M = cm.matrices || {};
  const opts = CM_MODELS.filter(([id]) => Array.isArray(M[id]));
  const sel = $('cm-model');
  const prev = sel.value;
  sel.innerHTML = opts.map(([id, l]) => `<option value="${id}">${escapeHtml(l)}</option>`).join('');
  if (opts.some(([id]) => id === prev)) sel.value = prev;
  const labels = (cm.labels || LABELS).map(verdictLabel);
  const draw = () => {
    const id = sel.value, m = M[id], name = opts.find(([k]) => k === id)?.[1] ?? id;
    if (!m) return;
    const total = m.flat().reduce((a, b) => a + b, 0), right = m.reduce((a, row, i) => a + (row[i] || 0), 0);
    const abstain = id === 'stage_a' && cm.stage_a_abstained ? ` Stage A abstained on ${num(cm.stage_a_abstained)} claims, which are not counted.` : '';
    $('cm-title').textContent = name;
    $('cm-summary').textContent = `${num(right)} of ${num(total)} claims on the diagonal (${pct(right / total)}). Count and row share in every cell.${abstain}`;
    put('cm', () => heatGrid($('cm-chart'), { matrix: m, rows: labels, cols: labels, ariaLabel: `Confusion matrix, ${name}, V3 OOD split` }));
  };
  sel.onchange = draw;
  draw();
  const v6 = M.V6, v8 = M[PRODUCTION];
  if (v6 && v8) {
    say('confusion-takeaway', `Rows are the ground truth, columns the prediction. ModernBERT alone flags ${num(v6[0][1])} supported claims as discrepancies; the cascade cuts that cell to ${num(v8[0][1])}.`);
  }
  source('confusion-source', r, 'confusion_matrices');
}

/* 4  Selective prediction ------------------------------------------------------------------------ */
function selective(r) {
  const rc = r.risk_coverage, op = rc.operating_point, conf = op.confidence_only, dual = op.dual_gate, esc = op.escalated || {}, a = rc.aurc || {};
  put('rc', () => lineChart($('rc-chart'), {
    x: 'actual_coverage', format: 'pct2', xFormat: 'pct', xLabel: 'Coverage', yLabel: 'Risk', height: 300,
    series: [
      { label: 'Stage B confidence only', data: rc.stage_b, y: 'risk' },
      { label: 'Cascade, joint confidence', data: rc.cascade, y: 'risk' },
    ],
    annotations: [
      { type: 'x', x: op.coverage, label: `Operating point, ${pct(op.coverage)} coverage` },
      { type: 'point', x: op.coverage, y: conf.risk, label: `Confidence only ${pct(conf.risk, 2)}` },
      { type: 'point', x: op.coverage, y: dual.risk, label: `Dual gate ${pct(dual.risk, 2)}` },
    ],
    ariaLabel: 'Risk versus coverage: Stage B confidence only and the cascade',
  }));
  $('rc-tiles').innerHTML = [
    tile('Risk at the operating point, dual gate', pct(dual.risk, 2), `${num(dual.errors)} errors among ${num(op.n_covered)} claims accepted when Stage A agrees with Stage B and confidence is at least ${num(op.threshold, 2)}`),
    tile('Same coverage, confidence only', pct(conf.risk, 2), `${num(conf.errors)} errors when a Stage B confidence threshold (${num(conf.confidence_threshold, 4)}) decides alone`),
    tile('Escalated claims', num(esc.n), `Stage C is ${pct(esc.accuracy)} accurate on them; Stage B alone would score ${pct(esc.stage_b_counterfactual_accuracy)}`),
    tile('Area under the risk-coverage curve', num(a.stage_b_calibrated, 4), `Stage B calibrated confidence; raw ${num(a.stage_b_raw, 4)}, cascade joint confidence ${num(a.cascade_joint, 4)}. Lower is better.`),
  ].join('');
  say('selective-takeaway', `At ${pct(op.coverage)} coverage, accepting a Stage B verdict only when Stage A agrees cuts risk from ${pct(conf.risk, 2)} to ${pct(dual.risk, 2)}, ${num(op.risk_reduction_factor, 1)}x lower than thresholding Stage B confidence alone.`);
  source('selective-source', r, 'risk_coverage');
}

/* 5  Routing ------------------------------------------------------------------------------------- */
function reasonLabel(key) {
  const m = /A=(\w+), B=(\w+)/.exec(key);
  if (m) return `A: ${verdictLabel(m[1])}, B: ${verdictLabel(m[2])}`;
  return key === 'low_confidence' ? 'Low Stage B confidence' : cap(human(key));
}

function routing(r) {
  const esc = r.escalation, bf = esc.by_field || {}, fields = Object.keys(bf);
  put('esc-reasons', () => barChart($('esc-reasons'), {
    data: Object.entries(esc.reasons || {}).map(([k, v]) => ({ label: reasonLabel(k), value: v })).sort((x, y) => y.value - x.value),
    format: 'int', labels: 'all', xLabel: 'Reason (A and B predictions)', yLabel: 'Escalated claims', ariaLabel: 'Escalated claims by reason',
  }));
  put('esc-fields', () => stackedBar($('esc-fields'), {
    data: fields.map((f) => ({ label: f, b: bf[f].n - bf[f].escalated, c: bf[f].escalated })),
    series: [{ key: 'b', label: 'Settled at Stage B' }, { key: 'c', label: 'Escalated to Stage C' }],
    mode: 'percent', format: 'int', table: false, ariaLabel: 'Share of claims settled at Stage B or escalated, by field',
  }));
  $('esc-fields-table').replaceChildren();
  toggleTable($('esc-fields-table'), [
    { key: 'field', label: 'Field' },
    { key: 'n', label: 'Claims', numeric: true, format: (v) => num(v) },
    { key: 'escalated', label: 'Escalated', numeric: true, format: (v) => num(v) },
    { key: 'rate', label: 'Escalation rate', numeric: true, format: (v) => pct(v) },
    { key: 'stage_c_accuracy', label: 'Stage C accuracy', numeric: true, format: (v) => pct(v) },
  ], fields.map((f) => ({ field: f, ...bf[f] })));
  const top = [...fields].sort((x, y) => bf[y].rate - bf[x].rate)[0];
  say('routing-takeaway', `${pct(esc.disagreement_share)} of the ${num(esc.n_escalated)} escalations come from Stage A and Stage B disagreeing; ${num(esc.reasons?.low_confidence ?? 0)} were low confidence alone.${top ? ` ${top} claims escalate most often (${pct(bf[top].rate)} of them).` : ''}`);
  source('routing-source', r, 'escalation');
}

/* 6  Per-field accuracy -------------------------------------------------------------------------- */
const PF_SERIES = [{ key: 'V6', label: 'V6 ModernBERT' }, { key: PRODUCTION, label: 'V8 Cascade' }, { key: 'V9', label: 'V9 Qwen-7B' }];

function drawPerField(pf, metric) {
  const acc = metric !== 'errors', k = acc ? 'accuracy' : 'errors';
  put('pf', () => groupedBarChart($('pf-chart'), {
    data: pf.fields.map((f) => ({ field: f, ...Object.fromEntries(PF_SERIES.map((s) => [s.key, pf.models?.[s.key]?.[f]?.[k]])) })),
    x: 'field', series: PF_SERIES, format: acc ? 'pct' : 'int', domain: acc ? [0, 1] : undefined,
    xLabel: 'Field', yLabel: acc ? 'Accuracy' : 'Errors', ariaLabel: `${acc ? 'Accuracy' : 'Errors'} per field for three verifiers`,
  }));
  $('pf-subtitle').textContent = acc ? 'Accuracy on the V3 OOD split; bars start at 0.' : 'Wrong verdicts per field on the V3 OOD split; fewer is better.';
}

function perField(r) {
  const pf = r.per_field, M = pf.models || {};
  drawPerField(pf, document.querySelector('input[name="pf-metric"]:checked')?.value);
  $('pf-metric').onchange = (e) => drawPerField(pf, e.target.value);
  const v6 = M.V6, v8 = M[PRODUCTION], v9 = M.V9;
  if (v6 && v8 && v9) {
    const by = (fn) => [...pf.fields].sort((x, y) => fn(y) - fn(x))[0];
    const gain = by((f) => (v8[f]?.accuracy ?? 0) - (v6[f]?.accuracy ?? 0));
    const qgap = by((f) => (v9[f]?.errors ?? 0) - (v8[f]?.errors ?? 0));
    say('per-field-takeaway', `The cascade's gain over ModernBERT alone is concentrated in ${gain}: ${pct(v6[gain]?.accuracy)} to ${pct(v8[gain]?.accuracy)}. On ${qgap}, Qwen-7B alone makes ${num(v9[qgap]?.errors)} errors where the cascade makes ${num(v8[qgap]?.errors)}.`);
  }
  source('per-field-source', r, 'per_field');
}

/* 7  Per-class table ----------------------------------------------------------------------------- */
function perClass(r) {
  const pc = r.per_class || {};
  const ids = [...r.leaderboard].sort((a, b) => b.accuracy - a.accuracy).map((d) => d.id).filter((id) => pc[id]);
  const f3 = (v) => num(v, 3);
  const head = `<colgroup><col></colgroup>${LABELS.map(() => '<colgroup span="3"></colgroup>').join('')}
    <thead><tr><th scope="col" rowspan="2">Verifier</th>${LABELS.map((l) => `<th scope="colgroup" colspan="3" class="res-group">${verdictBadge(l)} <span class="muted">n = ${num(pc[ids[0]]?.[l]?.support)}</span></th>`).join('')}</tr>
    <tr>${LABELS.map(() => '<th scope="col" class="num">Precision</th><th scope="col" class="num">Recall</th><th scope="col" class="num">F1</th>').join('')}</tr></thead>`;
  const body = ids.map((id) => `<tr${id === PRODUCTION ? ' class="res-row-strong"' : ''}><th scope="row">${escapeHtml(short(id))}${id === PRODUCTION ? ' <span class="badge">Production</span>' : ''}</th>${LABELS.map((l) => {
    const c = pc[id][l] || {};
    return `<td class="num">${f3(c.precision)}</td><td class="num">${f3(c.recall)}</td><td class="num">${f3(c.f1)}</td>`;
  }).join('')}</tr>`).join('');
  $('pc-table').innerHTML = table('per-class-h', 'Precision, recall and F1 per verdict class on the V3 OOD split, best verifier first; the production cascade row is highlighted.', `${head}<tbody>${body}</tbody>`);
  const I = 'INSUFFICIENT_EVIDENCE', rec = (id) => pct(pc[id]?.[I]?.recall);
  if (pc[PRODUCTION] && pc.V9 && pc.V6) {
    say('per-class-takeaway', `Insufficient evidence is the rare class (${num(pc[PRODUCTION][I]?.support)} claims): its recall is ${rec(PRODUCTION)} for the cascade, ${rec('V6')} for ModernBERT alone and ${rec('V9')} for Qwen-7B alone.`);
  }
  source('per-class-source', r, 'per_class');
}

/* 8  Prompt case study --------------------------------------------------------------------------- */
const TRANSITION = { supported: 'SUPPORTED', discrepancy: 'DISCREPANCY', insufficient: 'INSUFFICIENT_EVIDENCE' };

function prompt(r) {
  const p = r.prompt_case_study, v0 = p.v0, v1 = p.v1;
  const dTile = (label, a, b, fmt, diff, higherGood) => {
    const d = isNum(a) && isNum(b) ? b - a : 0;
    return tile(label, fmt(b), '', deltaLine(d, d === 0 ? null : (d > 0) === higherGood, `${diff(d)} from ${fmt(a)} with prompt V0`));
  };
  const ptsDiff = (d) => `${d >= 0 ? '+' : '-'}${num(Math.abs(d) * 100, 2)} pts`;
  const intDiff = (d) => `${d > 0 ? '+' : ''}${num(d)}`;
  $('pr-tiles').innerHTML = [
    dTile('Cascade accuracy', v0.cascade_accuracy, v1.cascade_accuracy, (v) => pct(v, 2), ptsDiff, true),
    dTile('Stage C accuracy', v0.stage_c_accuracy, v1.stage_c_accuracy, (v) => pct(v, 2), ptsDiff, true),
    dTile('Stage C errors', v0.stage_c_errors, v1.stage_c_errors, (v) => num(v), intDiff, false),
    dTile(`Escalated lab flag claims judged correctly (of ${num(v1.lab_flag_stage_c?.n)})`, v0.lab_flag_stage_c?.correct, v1.lab_flag_stage_c?.correct, (v) => num(v), intDiff, true),
  ].join('');
  const t0 = v0.error_transitions || {}, t1 = v1.error_transitions || {};
  const tLabel = (k) => {
    const [a, b] = k.split('_to_');
    return `${verdictLabel(TRANSITION[a] || a)} judged ${verdictLabel(TRANSITION[b] || b).toLowerCase()}`;
  };
  put('pr', () => groupedBarChart($('pr-chart'), {
    data: Object.keys({ ...t0, ...t1 }).map((k) => ({ label: tLabel(k), v0: t0[k], v1: t1[k] })).sort((x, y) => (y.v0 ?? 0) - (x.v0 ?? 0)),
    series: [{ key: 'v0', label: 'Prompt V0' }, { key: 'v1', label: 'Prompt V1' }],
    orientation: 'horizontal', format: 'int', labels: 'all', xLabel: 'Ground truth judged as', yLabel: 'Stage C errors',
    ariaLabel: 'Stage C errors by type, prompt V0 versus V1',
  }));
  $('pr-explain').innerHTML = `${p.description ? `<p class="res-note">${escapeHtml(p.description)}</p>` : ''}${p.v1_lab_flag_instruction ? `
    <details class="accordion"><summary>Prompt V1 instruction for lab flag claims</summary><div class="accordion__body">
      <div class="code-block"><button class="btn btn--ghost btn--sm code-block__copy" type="button" data-copy-target="#pr-instruction">${icon('copy')}Copy</button><pre style="white-space:pre-wrap"><code id="pr-instruction">${escapeHtml(p.v1_lab_flag_instruction)}</code></pre></div>
    </div></details>` : ''}`;
  const s = 'supported_to_discrepancy';
  say('prompt-takeaway', `Changing only the Stage C instruction for lab flag claims cut Stage C errors from ${num(v0.stage_c_errors)} to ${num(v1.stage_c_errors)} and lifted cascade accuracy to ${pct(v1.cascade_accuracy, 2)}; most of the fix is supported claims the judge had called discrepancies (${num(t0[s])} to ${num(t1[s])}).`);
  source('prompt-source', r, 'prompt_case_study');
}

/* 9  Dataset ------------------------------------------------------------------------------------- */
const splitName = (s) => (s === 'ood' ? 'OOD holdout' : cap(s));

function dataset(r) {
  const ds = r.dataset, S = ['train', 'calibration', 'validation', 'ood'].filter((s) => ds.splits?.[s]);
  const cols = [['documents', 'Documents'], ['claims', 'Claims'], ['groups', 'Claim groups'], ['clean', 'Clean'], ['degraded', 'Degraded']];
  const row = (head, o) => `<tr>${head}${cols.map(([k]) => `<td class="num">${num(o[k])}</td>`).join('')}${LABELS.map((l) => `<td class="num">${num(o.labels?.[l])}</td>`).join('')}</tr>`;
  $('ds-table').innerHTML = table('dataset-h', 'Documents, claims, claim groups, clean and degraded claims, and ground-truth labels per split, with totals.', `
    <thead><tr><th scope="col">Split</th>${cols.map(([, l]) => `<th scope="col" class="num">${l}</th>`).join('')}${LABELS.map((l) => `<th scope="col" class="num">${verdictLabel(l)}</th>`).join('')}</tr></thead>
    <tbody>${S.map((s) => row(`<th scope="row">${splitChip(s)}</th>`, ds.splits[s])).join('')}</tbody>
    <tfoot>${row('<th scope="row">All splits</th>', ds)}</tfoot>`);

  put('ds-labels', () => stackedBar($('ds-labels'), {
    data: S.map((s) => ({ label: splitName(s), ...ds.splits[s].labels })), series: LABEL_SERIES, mode: 'percent',
    ariaLabel: 'Ground-truth label mix per split',
  }));
  put('ds-severity', () => stackedBar($('ds-severity'), {
    data: ['clean', 'low', 'medium', 'high'].filter((k) => ds.severity?.[k]).map((k) => ({ label: cap(k), ...ds.severity[k] })),
    series: LABEL_SERIES, mode: 'absolute', ariaLabel: 'Ground-truth labels by degradation severity',
  }));
  const oodT = new Set(Object.keys(ds.splits?.ood?.templates || {}));
  const seenT = new Set(S.filter((s) => s !== 'ood').flatMap((s) => Object.keys(ds.splits[s].templates || {})));
  const oodOnly = (t) => oodT.has(t) && !seenT.has(t);
  const total = (o) => LABELS.reduce((acc, l) => acc + (o[l] || 0), 0);
  put('ds-templates', () => stackedBar($('ds-templates'), {
    data: Object.entries(ds.templates || {})
      .sort(([a, x], [b, y]) => oodOnly(a) - oodOnly(b) || total(y) - total(x))
      .map(([t, o]) => ({ label: `${human(t)}${oodOnly(t) ? ' (OOD)' : ''}`, ...o })),
    series: LABEL_SERIES, mode: 'absolute', ariaLabel: 'Ground-truth labels per document template',
  }));

  const lk = ds.leakage || {}, overlaps = Object.values(lk.document_overlap || {});
  const noOverlap = overlaps.length && overlaps.every((v) => v === 0);
  $('ds-integrity').innerHTML = `<h3 class="h4">Integrity checks</h3><dl class="kv">
    <dt>Document overlap</dt><dd>${noOverlap ? `None across all ${overlaps.length} split pairs` : escapeHtml(JSON.stringify(lk.document_overlap ?? null))}</dd>
    <dt>Group leakage</dt><dd>${num(lk.group_leakage_count)}</dd>
    <dt>OOD contamination</dt><dd>${num(lk.ood_contamination)}</dd>
    ${Object.entries(ds.hashes || {}).map(([p, h]) => `<dt>SHA-256</dt><dd><code>${escapeHtml(p)}</code><br><span class="mono text-xs">${escapeHtml(h)}</span></dd>`).join('')}
  </dl>`;
  const nOnly = [...oodT].filter(oodOnly).length;
  say('dataset-takeaway', `${num(ds.documents)} synthetic documents yield ${num(ds.claims)} labelled claims. The ${num(ds.splits?.ood?.documents)} OOD documents use ${num(nOnly)} templates that appear in no other split${noOverlap ? ', and no document is shared between splits' : ''}.`);
  source('dataset-source', r, 'dataset');
}

/* 10 Extraction ---------------------------------------------------------------------------------- */
function extraction(r) {
  const ex = r.extraction, det = ex.deterministic || {}, g = ex.gliner_e2 || {}, e3 = ex.narrative_e3 || {};
  const family = (f) => f.split('.')[0];
  const fields = [...new Set([...Object.keys(g.per_field || {}), ...Object.keys(e3.per_field || {})])]
    .sort((a, b) => family(a).localeCompare(family(b)));
  const heat = (v, sub = '', attrs = '') => (isNum(v)
    ? `<td class="res-heat"${attrs}><span class="res-heat__fill seq-${Math.round(Math.max(0, Math.min(1, v)) * 6)}">${num(v, 2)}${sub ? `<small>${escapeHtml(sub)}</small>` : ''}</span></td>`
    : `<td class="num muted"${attrs}>${DASH}</td>`);
  const done = new Set();
  const body = fields.map((f) => {
    const k = family(f), d = det[k];
    let detCell = '';
    if (!d) detCell = heat(null);
    else if (!done.has(k)) {
      done.add(k);
      detCell = heat(d.f1, `all ${k === 'med' ? 'medication' : k} fields, n = ${num(d.n)}`, ` rowspan="${fields.filter((x) => family(x) === k).length}"`);
    }
    return `<tr><th scope="row"><code>${escapeHtml(f)}</code></th>${detCell}${heat(g.per_field?.[f]?.f1)}${heat(e3.per_field?.[f]?.f1)}</tr>`;
  }).join('');
  const colHead = (title, sub) => `<th scope="col" class="res-wrap">${escapeHtml(title)}<br><span class="muted">${escapeHtml(sub)}</span></th>`;
  $('ex-table').innerHTML = `${table('extraction-h', 'Extraction F1 per field: deterministic parsers (family-level), GLiNER-biomed (E2) and the narrative extractor (E3).', `
    <thead><tr><th scope="col">Field</th>
      ${colHead('Deterministic parsers', 'family-level F1')}
      ${colHead('GLiNER-biomed (E2)', `${g.model || ''} · ${num(g.documents)} documents`)}
      ${colHead('Narrative extractor (E3)', `${e3.is_mock ? 'mock LLM provider' : e3.model || ''} · ${num(e3.documents)} documents`)}</tr></thead>
    <tbody>${body}</tbody>
    <tfoot>
      <tr><th scope="row">Micro-F1</th>${heat(null)}${heat(g.micro_f1)}${heat(e3.micro_f1)}</tr>
      <tr><th scope="row">Macro-F1</th>${heat(null)}${heat(g.macro_f1)}${heat(e3.macro_f1)}</tr>
    </tfoot>`)}
    <div class="scale-legend" style="margin-top:var(--space-3)"><span>F1 0</span><span class="scale-legend__ramp" aria-hidden="true">${[0, 1, 2, 3, 4, 5, 6].map((n) => `<span class="seq-${n}"></span>`).join('')}</span><span>1</span></div>`;
  $('ex-mock').innerHTML = e3.is_mock
    ? `<div class="alert alert--warning">${icon('alert-triangle')}<div class="alert__body"><p class="alert__title">The narrative extractor (E3) was benchmarked with a mock provider</p><p>Its scores come from <code>${escapeHtml(e3.model || 'a mock LLM provider')}</code>, an offline stand-in, not a real LLM. They measure the fallback path, not LLM extraction quality.</p></div></div>`
    : '';
  const strong = fields.filter((f) => (g.per_field?.[f]?.f1 ?? 0) >= 0.9);
  if (det.lab && det.med) {
    say('extraction-takeaway', `The template-aware deterministic parsers score F1 ${num(det.lab.f1, 2)} on lab reports and ${num(det.med.f1, 2)} on medication sheets. Zero-shot GLiNER reaches ${num(g.micro_f1, 2)} micro-F1${strong.length ? ` and is strong only on ${list(strong)}` : ''}.`);
  }
  source('extraction-source', r, 'extraction');
}

/* 11 Robustness ---------------------------------------------------------------------------------- */
const SEV = ['LOW', 'MEDIUM', 'HIGH'];

function robustness(r) {
  const rb = r.robustness, rows = rb.rows || [], c = rb.configuration || {};
  const keys = [...new Set(rows.map((x) => `${x.track}|${x.degradation}`))];
  put('rb', () => smallMultiples($('rb-chart'), {
    chart: lineChart, minWidth: 240, height: 180, x: 'sev', format: 'dec2', yDomain: [0, 1],
    xFormat: (v) => SEV[v - 1] ?? '', xLabel: 'Severity', ariaLabel: 'Extraction F1 by severity',
    series: [{ key: 'lab', label: 'Lab reports' }, { key: 'medication', label: 'Medication sheets' }],
    panels: keys.map((k) => {
      const [track, deg] = k.split('|');
      const sub = rows.filter((x) => x.track === track && x.degradation === deg);
      return {
        title: `${cap(human(deg))}${track === 'visual' ? '' : ' (OCR-text track)'}`,
        data: SEV.map((s, i) => ({ sev: i + 1, ...Object.fromEntries(sub.filter((x) => x.severity === s).map((x) => [x.dataset, x.extraction_f1])) })),
      };
    }),
  }));
  $('rb-subtitle').textContent = `Visual track: ${num(c.visual_sample_size)} documents per dataset and condition, OCR at ${num(c.paddleocr_dpi)} dpi; OCR-text track: ${num(c.text_sample_size)} documents. Shared y-axis from 0 to 1.${c.visual_sample_size < 10 ? ' The visual-track sample is small; read those panels as indicative.' : ''}`;
  const lab = rows.filter((x) => x.dataset === 'lab' && isNum(x.extraction_f1));
  const med = rows.filter((x) => x.dataset === 'medication' && isNum(x.extraction_f1));
  const worstLow = lab.filter((x) => x.severity === 'LOW').sort((a, b) => a.extraction_f1 - b.extraction_f1)[0];
  const zeroHigh = [...new Set(lab.filter((x) => x.severity === 'HIGH' && x.extraction_f1 === 0).map((x) => human(x.degradation)))];
  const mean = (xs) => xs.reduce((s, x) => s + x.extraction_f1, 0) / (xs.length || 1);
  if (worstLow) {
    say('robustness-takeaway', `Lab-report extraction already falls to F1 ${num(worstLow.extraction_f1, 2)} under low ${human(worstLow.degradation)}${zeroHigh.length ? ` and to 0 at high ${list(zeroHigh)}` : ''}. Averaged over every condition, medication sheets score ${num(mean(med), 2)} and lab reports ${num(mean(lab), 2)}.`);
  }
  source('robustness-source', r, 'robustness');
}

/* 12 Interoperability ---------------------------------------------------------------------------- */
const CODING = [['LOINC on Observations', 'loinc', 'observations'], ['RxNorm on medications', 'rxnorm', 'medications'], ['UCUM on quantities', 'ucum', 'quantities']];

function interop(r) {
  const f = r.fhir, pv = f.provenance || {}, ib = f.in_bundle_verification || {}, ca = f.compound_accuracy || {}, C = f.coding || {};
  const of = (a, b) => `${num(a)}<small>/ ${num(b)}</small>`;
  $('fh-tiles').innerHTML = [
    tile('Valid FHIR R4B bundles', of(f.valid_bundles, f.bundles), f.bundles === r.dataset?.splits?.ood?.documents ? 'One bundle per OOD document' : ''),
    tile('Claims with provenance', of(pv.claims_in_audit_report, f.claims), 'Claims carrying DEC-016 provenance in the audit report'),
    tile('Claims written into bundles', num(pv.claims_in_bundle), `${pct(ib.rate)} of them verified correct (${num(ib.correct)} of ${num(ib.total)})`),
    tile('Evidence bounding boxes', num(pv.real_bboxes), `plus ${num(pv.absence_bboxes)} absence markers for claims with no evidence on the page`),
    tile('Compound end-to-end accuracy', pct(ca.remediated?.rate), `${num(ca.remediated?.numerator)} of ${num(ca.remediated?.denominator)} claims, up from ${pct(ca.historical?.rate)} before terminology remediation`),
  ].join('');
  const share = (o, a, b) => (o && o[b] ? o[a] / o[b] : null);
  put('fh-coding', () => groupedBarChart($('fh-coding'), {
    data: CODING.map(([label, a, b]) => ({ label, before: share(C.historical, a, b), after: share(C.remediated, a, b) })),
    series: [{ key: 'before', label: 'Before remediation' }, { key: 'after', label: 'After remediation' }],
    orientation: 'horizontal', format: 'pct', domain: [0, 1], labels: 'all', xLabel: 'Code system', yLabel: 'Share of eligible resources coded',
    ariaLabel: 'Coded resources before and after terminology remediation',
  }));
  put('fh-resources', () => barChart($('fh-resources'), {
    data: Object.entries(f.resource_counts || {}).map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value),
    format: 'int', labels: 'all', xLabel: 'Resource type', yLabel: 'Resources', ariaLabel: 'FHIR resources in the OOD bundles by type',
  }));
  const sum = (o, i) => CODING.reduce((s, c) => s + (o?.[c[i]] || 0), 0);
  const before = sum(C.historical, 1) / (sum(C.historical, 2) || 1), after = sum(C.remediated, 1) / (sum(C.remediated, 2) || 1);
  say('interop-takeaway', `${num(f.valid_bundles)} of ${num(f.bundles)} OOD bundles validate, ${num(pv.claims_in_audit_report)} of ${num(f.claims)} claims keep their provenance in the audit report, and terminology remediation raised coded resources from ${pct(before, 0)} to ${pct(after, 0)}.`);
  source('interop-source', r, 'fhir');
}

/* Load ------------------------------------------------------------------------------------------- */
const SECTIONS = [
  ['headline', headline], ['leaderboard', leaderboard], ['confusion', confusion], ['selective', selective],
  ['routing', routing], ['per-field', perField], ['per-class', perClass], ['prompt', prompt],
  ['dataset', dataset], ['extraction', extraction], ['robustness', robustness], ['interop', interop],
];
const sections = () => document.querySelectorAll('.res-section');

async function load() {
  $('report-error').replaceChildren();
  document.querySelectorAll('[data-section-error]').forEach((n) => n.remove());
  let r;
  try {
    r = await api('/api/results');
  } catch (err) {
    sections().forEach((s) => { s.hidden = true; });
    renderError($('report-error'), err, load, { title: 'Benchmark results unavailable' });
    say('report-status', 'Benchmark results could not be loaded.');
    return;
  }
  sections().forEach((s) => { s.hidden = false; });
  for (const [id, render] of SECTIONS) {
    try {
      render(r);
    } catch (err) {
      // One malformed section must not blank the report; show where it failed and keep going.
      console.error(`results: section ${id} failed`, err);
      const box = document.createElement('div');
      box.dataset.sectionError = '';
      document.querySelector(`#${id} .res-section__head`).after(box);
      renderError(box, err, null, { title: 'This section could not be drawn from the API data' });
    }
  }
  say('report-status', 'Benchmark results loaded.');
}

// Table of contents: open beside the report on desktop, a collapsed menu on smaller screens.
const toc = $('toc'), wide = matchMedia('(min-width: 1024px)');
const syncToc = () => { toc.open = wide.matches; };
syncToc();
wide.addEventListener('change', syncToc);
toc.addEventListener('click', (e) => { if (e.target.closest('a') && !wide.matches) toc.open = false; });

load();

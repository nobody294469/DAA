/**
 * Clindoc2FHIR web — shared runtime helpers (ES module, no dependencies, no side effects on import).
 * Authority: HTTP API contract (src/clindoc/web/app.py); design tokens and components in assets/css/app.css.
 *
 * Typical page bootstrap:
 *   import { mountShell, api, renderError } from '/assets/js/core.js';
 *   mountShell();
 */

/* ------------------------------------------------------------------------ */
/* Icons, escaping, formatting                                               */
/* ------------------------------------------------------------------------ */

/** URL of the Lucide SVG sprite. */
export const ICON_SPRITE = '/assets/icons.svg';

/** Inline SVG icon markup from the sprite, decorative (aria-hidden). */
export const icon = (name, cls = '') =>
  `<svg class="icon${cls ? ` ${cls}` : ''}" aria-hidden="true" focusable="false"><use href="${ICON_SPRITE}#${name}"/></svg>`;

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
/** Escape any value for safe interpolation into HTML text or attribute values. */
export const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ESC[c]);

/** Placeholder shown for missing numbers. */
export const DASH = 'n/a';
/** True for finite numbers only (null, NaN, strings are false). */
export const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const nf = (opts) => new Intl.NumberFormat('en-US', opts);

/** 1234.5 -> "1,235"; num(0.9748, 4) -> "0.9748". */
export const num = (v, digits = 0) =>
  isNum(v) ? nf({ minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v) : DASH;
/** Fraction -> percent: pct(0.9748) -> "97.5%". */
export const pct = (v, digits = 1) =>
  isNum(v) ? nf({ style: 'percent', minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v) : DASH;
/** 12900 -> "12.9K". */
export const compact = (v) => (isNum(v) ? nf({ notation: 'compact', maximumFractionDigits: 1 }).format(v) : DASH);
/** Confidence in [0,1] -> "0.93". */
export const confidence = (v) => (isNum(v) ? v.toFixed(2) : DASH);
/** Milliseconds -> "840 ms", "12.4 s", "26 min 39 s", "1 h 43 min". */
export function ms(v) {
  if (!isNum(v)) return DASH;
  if (v < 1000) return `${Math.round(v)} ms`;
  const s = v / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 2 : 1)} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${Math.round(s % 60)} s`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}
/** Seconds -> same human format as ms(). */
export const seconds = (v) => (isNum(v) ? ms(v * 1000) : DASH);

/* ------------------------------------------------------------------------ */
/* Verdict / stage / code-system renderers (return HTML strings)             */
/* ------------------------------------------------------------------------ */

/** Verdict metadata keyed by API label. */
export const VERDICTS = {
  SUPPORTED: { label: 'Supported', icon: 'check', cls: 'supported' },
  DISCREPANCY: { label: 'Discrepancy', icon: 'x-octagon', cls: 'discrepancy' },
  INSUFFICIENT_EVIDENCE: { label: 'Insufficient evidence', icon: 'help-circle', cls: 'insufficient' },
};
/** Cascade stage metadata keyed by letter. */
export const STAGES = { A: { name: 'Deterministic' }, B: { name: 'ModernBERT' }, C: { name: 'LLM judge' } };

/** Human label for a verdict ("INSUFFICIENT_EVIDENCE" -> "Insufficient evidence"). */
export const verdictLabel = (label) => VERDICTS[String(label ?? '').toUpperCase()]?.label ?? String(label ?? DASH);

/** Verdict badge (icon + text, never colour alone). opts.size: 'lg'. */
export function verdictBadge(label, { size } = {}) {
  const v = VERDICTS[String(label ?? '').toUpperCase()];
  if (!v) return `<span class="badge">${escapeHtml(label ?? 'Unknown')}</span>`;
  return `<span class="badge badge--${v.cls}${size === 'lg' ? ' badge--lg' : ''}">${icon(v.icon)}${v.label}</span>`;
}

/** Normalise "B", "stage_b", "STAGE_B" -> "B" (or '' when unknown). */
export const stageKey = (stage) => (String(stage ?? '').match(/([ABC])$/i)?.[1] ?? '').toUpperCase();

/** Stage badge: letter key + name ("B ModernBERT"); screen readers hear "Stage B: ModernBERT". */
export function stageBadge(stage) {
  const key = stageKey(stage);
  if (!key) return `<span class="badge">${escapeHtml(stage ?? DASH)}</span>`;
  return `<span class="badge badge--stage badge--stage-${key.toLowerCase()}"><span class="badge__key" aria-hidden="true"><span>${key}</span></span><span class="visually-hidden">Stage ${key}: </span>${STAGES[key].name}</span>`;
}

/** Code-system chip: codeChip('LOINC', '718-7'); missing code renders "Uncoded". */
export function codeChip(system, code) {
  if (!code) return `<span class="chip chip--missing"><span class="chip__sys">${escapeHtml(system || 'Code')}</span>Uncoded</span>`;
  return `<span class="chip"><span class="chip__sys">${escapeHtml(system || '')}</span>${escapeHtml(code)}</span>`;
}

/** Split chip for train / calibration / validation / ood. */
export function splitChip(split) {
  if (!split) return '';
  const s = String(split).toLowerCase();
  const label = s === 'ood' ? 'OOD holdout' : s[0].toUpperCase() + s.slice(1);
  return `<span class="chip chip--split chip--split-${escapeHtml(s)}">${escapeHtml(label)}</span>`;
}

/** Confidence meter (role=meter) with optional threshold tick; returns HTML. */
export function confidenceMeter(value, { label = 'Confidence', threshold = null } = {}) {
  if (!isNum(value)) return `<span class="meter-row muted">${DASH}</span>`;
  const v = Math.max(0, Math.min(1, value));
  const low = isNum(threshold) && v < threshold;
  const mark = isNum(threshold) ? `<span class="meter__mark" style="--at:${threshold}" aria-hidden="true"></span>` : '';
  const hint = isNum(threshold) ? `, threshold ${threshold.toFixed(2)}` : '';
  return `<span class="meter-row"><span class="meter${low ? ' is-low' : ''}" role="meter" aria-valuemin="0" aria-valuemax="1" aria-valuenow="${v}" aria-label="${escapeHtml(label)}" aria-valuetext="${v.toFixed(2)}${hint}"><span class="meter__fill" style="--value:${v}"></span>${mark}</span><span class="meter__value">${v.toFixed(2)}</span></span>`;
}

/* ------------------------------------------------------------------------ */
/* API                                                                       */
/* ------------------------------------------------------------------------ */

/** Typed API error. kind: 'network' | 'timeout' | 'abort' | 'http'; status = HTTP status (0 if none). */
export class ApiError extends Error {
  constructor(message, { status = 0, kind = 'http', detail = null, path = '' } = {}) {
    super(message);
    this.name = 'ApiError';
    Object.assign(this, { status, kind, detail, path });
  }
}

function detailMessage(detail) {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => [(d.loc || []).filter((p) => p !== 'body').join('.'), d.msg].filter(Boolean).join(': ')).join('; ');
  }
  return '';
}

/**
 * fetch wrapper: api(path, { method, json, body, timeout = 20000, signal, headers }).
 * json -> JSON body; body -> FormData etc. Resolves parsed JSON (or text); rejects with ApiError
 * whose message is FastAPI's `detail` when present.
 */
export async function api(path, { method, json, body, timeout = 20000, signal, headers = {} } = {}) {
  if (STATIC) return staticApi(path, { json, body });
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  const onAbort = () => ctrl.abort();
  signal?.addEventListener('abort', onAbort, { once: true });
  try {
    let res;
    try {
      res = await fetch(path, {
        method: method || (json !== undefined || body !== undefined ? 'POST' : 'GET'),
        body: json !== undefined ? JSON.stringify(json) : body,
        headers: { Accept: 'application/json', ...(json !== undefined ? { 'Content-Type': 'application/json' } : {}), ...headers },
        signal: ctrl.signal,
      });
    } catch {
      if (signal?.aborted) throw new ApiError('Request cancelled.', { kind: 'abort', path });
      if (ctrl.signal.aborted) throw new ApiError(`No response within ${Math.round(timeout / 1000)} s.`, { kind: 'timeout', path });
      throw new ApiError('Cannot reach the API server.', { kind: 'network', path });
    }
    const type = res.headers.get('content-type') || '';
    const data = type.includes('json') ? await res.json().catch(() => null) : await res.text();
    if (!res.ok) {
      const detail = data && typeof data === 'object' ? data.detail : null;
      throw new ApiError(detailMessage(detail) || `${res.status} ${res.statusText || 'Error'}`.trim(), {
        status: res.status, kind: 'http', detail, path,
      });
    }
    return data;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', onAbort);
  }
}

/* Static demo mode (Vercel build, scripts/build_static_site.py sets window.CLINDOC_STATIC):
 * GETs read snapshot files under /static-api, recorded from the real server with the trained model.
 * ponytail: snapshots only; live uploads / free-form checks need `clindoc serve`. */
export const STATIC = typeof window !== 'undefined' && window.CLINDOC_STATIC === true;
const LIVE_ONLY = 'This static demo replays recorded results. Uploads and custom inputs need the local server (clindoc serve).';
let explorerIndex = null;

/** SHA-256 key of a /api/verify request; mirrors verify_key() in scripts/build_static_site.py. */
async function verifyKey(r) {
  const s = ['field_type', 'value', 'unit', 'entity', 'evidence_text', 'context_text'].map((k) => r?.[k] ?? '').join('\n');
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].slice(0, 8).map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function staticApi(path, { json, body }) {
  const [p, qs = ''] = path.split('?');
  const get = async (file, missing) => {
    const res = await fetch(`/static-api${file}`).catch(() => null);
    if (!res?.ok) throw new ApiError(missing || 'Not available in the static demo.', { status: 404, kind: 'http', detail: missing, path });
    return res.json();
  };
  if (p === '/api/process') {
    const id = body instanceof FormData ? body.get('sample_id') : null;
    if (!id) throw new ApiError(LIVE_ONLY, { status: 503, kind: 'http', detail: LIVE_ONLY, path });
    return get(`/runs/by-sample/${encodeURIComponent(id)}.json`, `${id} was not recorded for the static demo. ${LIVE_ONLY}`);
  }
  if (p === '/api/verify') return get(`/verify/${await verifyKey(json)}.json`, `Only the presets are recorded. ${LIVE_ONLY}`);
  if (p === '/api/explorer') {
    explorerIndex ||= await get('/explorer/index.json');
    const f = Object.fromEntries(new URLSearchParams(qs));
    const needle = (f.q || '').trim().toLowerCase();
    const correct = f.correct || 'all';
    const rows = explorerIndex.filter((i) => (!f.document_id || i.document_id === f.document_id)
      && (!f.field_type || i.field_type === f.field_type) && (!f.ground_truth || i.ground_truth === f.ground_truth)
      && (!f.label || i.final_label === f.label) && (!f.stage || i.stage_reached === f.stage)
      && (correct === 'all' || i.correct === (correct === 'correct'))
      && (!needle || `${i.claim} ${i.example_id} ${i.document_id}`.toLowerCase().includes(needle)));
    const ok = rows.filter((i) => i.correct).length;
    const offset = +f.offset || 0;
    return {
      total: rows.length, accuracy: rows.length ? Math.round((ok / rows.length) * 1e4) / 1e4 : 0, errors: rows.length - ok,
      escalated: rows.filter((i) => i.stage_reached === 'C').length, items: rows.slice(offset, offset + (+f.limit || 50)),
      facets: { documents: [...new Set(explorerIndex.map((i) => i.document_id))].sort(), field_types: [...new Set(explorerIndex.map((i) => i.field_type))].sort() },
    };
  }
  return get(`${p.slice(4)}.json`);
}

let healthPromise = null;
/** GET /api/health, cached for the page load (refresh: true refetches; failures are not cached). */
export function getHealth({ refresh = false } = {}) {
  if (refresh || !healthPromise) {
    healthPromise = api('/api/health', { timeout: 8000 });
    healthPromise.catch(() => { healthPromise = null; });
  }
  return healthPromise;
}

const basename = (p) => String(p || '').split(/[\\/]/).filter(Boolean).pop() || '';

/**
 * Honest serving summary from /api/health:
 * { live: bool, stageB: {real, label, detail}, stageC: {real, label, detail}, extractionReal }.
 */
export function servingSummary(health) {
  const b = health?.stage_b || {};
  const c = health?.stage_c || {};
  const bReal = b.mode === 'modernbert';
  const cReal = ['vllm', 'local', 'both', 'online'].includes(c.mode);
  const bDetail = bReal
    ? [basename(b.checkpoint), b.device, isNum(b.temperature) ? `T = ${b.temperature.toFixed(3)}` : ''].filter(Boolean).join(' · ')
    : b.load_error || 'No trained checkpoint loaded.';
  const cLabel = c.mode === 'local' ? `Local ${c.model || 'LLM'}` : c.mode === 'both' ? `Local + online (${c.model || 'LLM'})`
    : c.mode === 'online' ? `Online ${c.model || 'LLM'}` : cReal ? `vLLM ${c.model || ''}`.trim() : 'Mock judge';
  const cDetail = c.mode === 'local' ? 'In-process LLM judge on this machine.'
    : c.mode === 'both' ? 'Local judge first; the online model is asked only when the local one cannot answer.'
    : c.mode === 'online' ? 'Hosted LLM judge: claim text is sent to the online provider.' : cReal ? c.base_url || '' : 'No LLM judge configured.';
  return {
    live: bReal && cReal,
    stageB: { real: bReal, label: bReal ? 'ModernBERT (trained checkpoint)' : 'Heuristic fallback', detail: bDetail },
    stageC: { real: cReal, label: cLabel, detail: cDetail },
    extractionReal: health?.extraction_llm?.mode === 'vllm',
  };
}

/** Render the compact serving-status pill (links to the Model page) into el. Returns the health promise. */
export function renderServingPill(el) {
  if (!el) return Promise.resolve(null);
  el.innerHTML = `<a class="status-pill is-loading" href="/model.html"><span class="status-pill__dot" aria-hidden="true"></span><span class="status-pill__text">Checking model serving…</span></a>`;
  const pill = el.firstElementChild;
  const set = (cls, text) => {
    pill.className = `status-pill ${cls}`;
    pill.lastElementChild.textContent = text;
  };
  return getHealth().then(
    (h) => {
      const s = servingSummary(h);
      const m = h?.stage_c?.mode;
      const judge = m === 'local' ? 'local LLM' : m === 'both' ? 'local + online LLM' : m === 'online' ? 'online LLM' : s.stageC.real ? 'vLLM' : 'mock judge';
      set(s.live ? 'is-live' : 'is-fallback', `Stage B: ${s.stageB.real ? 'ModernBERT' : 'heuristic fallback'} · Stage C: ${judge}`);
      return h;
    },
    () => { set('is-offline', 'API offline'); return null; },
  );
}

/**
 * Render the honest serving banner(s) into el: warnings when Stage B is the heuristic fallback
 * or Stage C is the mock judge, a danger alert with Retry when the API is down, a short
 * success line when both real models serve. opts.stages: subset of ['B', 'C'].
 */
export function renderServingBanner(el, { stages = ['B', 'C'] } = {}) {
  if (!el) return Promise.resolve(null);
  el.classList.add('banner-stack');
  el.setAttribute('aria-live', 'polite');
  el.innerHTML = `<p class="muted text-sm">Checking which models are serving…</p>`;
  const alert = (kind, ico, title, body) =>
    `<div class="alert alert--${kind}">${icon(ico)}<div class="alert__body"><p class="alert__title">${escapeHtml(title)}</p><p>${body}</p></div></div>`;
  return getHealth().then(
    (h) => {
      const s = servingSummary(h);
      const parts = [];
      if (stages.includes('B') && !s.stageB.real) {
        parts.push(alert('warning', 'alert-triangle', 'Stage B is running the heuristic fallback',
          `No trained ModernBERT checkpoint is loaded, so Stage B verdicts come from a rule-based stand-in, not the fine-tuned classifier. <span class="muted">${escapeHtml(s.stageB.detail)}</span>`));
      }
      if (stages.includes('C') && !s.stageC.real) {
        parts.push(alert('warning', 'alert-triangle', 'Stage C is the mock judge',
          'No LLM judge is configured (start the server with --llm or a vLLM endpoint). Escalated claims receive a deterministic placeholder verdict, not a Qwen judgement.'));
      }
      if (!parts.length) {
        const live = [stages.includes('B') && `Stage B: ${escapeHtml(s.stageB.label)} (${escapeHtml(s.stageB.detail)})`, stages.includes('C') && `Stage C: ${escapeHtml(s.stageC.label)}`].filter(Boolean).join('; ');
        parts.push(alert('success', 'check-circle', 'Live models serving', `${live}.`));
      }
      el.innerHTML = parts.join('');
      return h;
    },
    (err) => {
      renderError(el, err, () => { getHealth({ refresh: true }); renderServingBanner(el, { stages }); }, { title: 'Model serving status unavailable' });
      return null;
    },
  );
}

/* ------------------------------------------------------------------------ */
/* States: error, empty                                                      */
/* ------------------------------------------------------------------------ */

/** Inline error state (role=alert) with an optional Retry button; hints at `clindoc serve` when offline. */
export function renderError(el, err, onRetry, { title = 'Could not load data' } = {}) {
  // A 404 without a FastAPI `detail` on /api/* means the pages are served without the API (e.g. a plain static server).
  const offline = err?.kind === 'network' || err?.kind === 'timeout' || (err?.status === 404 && !err.detail && String(err.path).startsWith('/api/'));
  const hint = offline ? '<p class="muted">Start the API with <code>clindoc serve</code>, then retry.</p>' : '';
  el.innerHTML = `<div class="alert alert--danger" role="alert">${icon('alert-triangle')}<div class="alert__body"><p class="alert__title">${escapeHtml(title)}</p><p>${escapeHtml(err?.message || String(err ?? 'Unknown error'))}</p>${hint}${onRetry ? `<div class="alert__actions"><button class="btn btn--secondary btn--sm" type="button" data-retry>${icon('refresh-cw')}Retry</button></div>` : ''}</div></div>`;
  el.querySelector('[data-retry]')?.addEventListener('click', onRetry);
}

/** Empty state: renderEmpty(el, { icon, title, body, actions }) — actions is trusted HTML. */
export function renderEmpty(el, { icon: ico = 'search', title = 'Nothing to show', body = '', actions = '' } = {}) {
  el.innerHTML = `<div class="empty">${icon(ico, 'empty__icon')}<p class="empty__title">${escapeHtml(title)}</p>${body ? `<p class="empty__body">${escapeHtml(body)}</p>` : ''}${actions ? `<div class="empty__actions">${actions}</div>` : ''}</div>`;
}

/* ------------------------------------------------------------------------ */
/* Toasts                                                                     */
/* ------------------------------------------------------------------------ */

let toastRegion = null;
function ensureToastRegion() {
  if (!toastRegion) {
    toastRegion = document.createElement('div');
    toastRegion.className = 'toast-region';
    toastRegion.setAttribute('aria-live', 'polite');
    toastRegion.setAttribute('aria-relevant', 'additions');
    document.body.append(toastRegion);
  }
  return toastRegion;
}
const KIND_ICON = { info: 'info', success: 'check-circle', warning: 'alert-triangle', danger: 'alert-triangle' };

/** Show a toast: toast(message, { kind: 'info'|'success'|'warning'|'danger', title, timeout = 5000 }). Returns close(). */
export function toast(message, { kind = 'info', title = '', timeout = 5000 } = {}) {
  const el = document.createElement('div');
  el.className = `alert alert--${kind} toast`;
  if (kind === 'danger') el.setAttribute('role', 'alert');
  el.innerHTML = `${icon(KIND_ICON[kind] || 'info')}<div class="alert__body">${title ? `<p class="alert__title">${escapeHtml(title)}</p>` : ''}<p>${escapeHtml(message)}</p></div><button class="btn btn--ghost btn--icon btn--sm alert__dismiss" type="button" aria-label="Dismiss notification">${icon('x')}</button>`;
  let timer = 0;
  const close = () => {
    clearTimeout(timer);
    el.classList.add('is-leaving');
    setTimeout(() => el.remove(), 220);
  };
  el.querySelector('button').addEventListener('click', close);
  ensureToastRegion().append(el);
  if (timeout) timer = setTimeout(close, timeout);
  return close;
}

/* ------------------------------------------------------------------------ */
/* Dialog / drawer (native <dialog>: inert background = focus trap, Esc)      */
/* ------------------------------------------------------------------------ */

/** Open a <dialog class="dialog|drawer"> modally; focus returns to the opener on close. */
export function openDialog(dialog, { returnFocus = document.activeElement } = {}) {
  const dlg = typeof dialog === 'string' ? document.querySelector(dialog) : dialog;
  if (!dlg || dlg.open) return dlg;
  dlg._returnFocus = returnFocus;
  if (!dlg._bound) {
    dlg._bound = true;
    dlg.addEventListener('close', () => {
      const back = dlg._returnFocus;
      if (back && document.contains(back)) back.focus();
    });
    dlg.addEventListener('click', (e) => {
      if (e.target === dlg || e.target.closest('[data-dialog-close]')) dlg.close();
    });
  }
  dlg.showModal();
  (dlg.querySelector('[autofocus]') || dlg.querySelector('[data-dialog-close]'))?.focus();
  return dlg;
}

/** Close a dialog opened with openDialog (selector or element). */
export const closeDialog = (dialog) => (typeof dialog === 'string' ? document.querySelector(dialog) : dialog)?.close();

/* ------------------------------------------------------------------------ */
/* Tabs (ARIA tablist, roving tabindex, arrows/Home/End, optional URL sync)   */
/* ------------------------------------------------------------------------ */

/**
 * Init every [data-tabs] under root. Markup: [data-tabs][data-tabs-param="tab"?] >
 * [role=tablist] > button[role=tab][aria-controls][data-tab?]; panels [role=tabpanel].
 * Fires a bubbling 'tabchange' event { detail: { id, tab } } on user selection.
 */
export function initTabs(root = document) {
  root.querySelectorAll('[data-tabs]').forEach((box) => {
    if (box._tabs) return;
    box._tabs = true;
    const list = box.querySelector('[role="tablist"]');
    const tabs = [...list.querySelectorAll('[role="tab"]')];
    const idOf = (t) => t.dataset.tab || t.id;
    const select = (tab, { focus = false, silent = false } = {}) => {
      tabs.forEach((t) => {
        const on = t === tab;
        t.setAttribute('aria-selected', String(on));
        t.tabIndex = on ? 0 : -1;
        const panel = document.getElementById(t.getAttribute('aria-controls'));
        if (panel) panel.hidden = !on;
      });
      if (focus) tab.focus();
      if (silent) return;
      if (box.dataset.tabsParam) setQuery({ [box.dataset.tabsParam]: idOf(tab) });
      box.dispatchEvent(new CustomEvent('tabchange', { bubbles: true, detail: { id: idOf(tab), tab } }));
    };
    tabs.forEach((t) => t.addEventListener('click', () => select(t)));
    list.addEventListener('keydown', (e) => {
      const i = tabs.indexOf(document.activeElement);
      const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
      if (i < 0 || next === undefined) return;
      e.preventDefault();
      select(tabs[(next + tabs.length) % tabs.length], { focus: true });
    });
    const fromUrl = box.dataset.tabsParam && tabs.find((t) => idOf(t) === getQuery(box.dataset.tabsParam));
    select(fromUrl || tabs.find((t) => t.getAttribute('aria-selected') === 'true') || tabs[0], { silent: true });
  });
}

/* ------------------------------------------------------------------------ */
/* Clipboard, JSON tree                                                       */
/* ------------------------------------------------------------------------ */

/** Copy text to the clipboard (with execCommand fallback) and toast the result. */
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
    document.body.append(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    if (!ok) {
      toast('Copy failed. Select the text and copy it manually.', { kind: 'warning' });
      return false;
    }
  }
  toast('Copied to clipboard.', { kind: 'success', timeout: 2000 });
  return true;
}

const span = (cls, text) => {
  const s = document.createElement('span');
  s.className = cls;
  s.textContent = text;
  return s;
};

/**
 * Collapsible JSON tree (native <details>, keyboard accessible) with search highlight.
 * renderJsonTree(el, data, { expandDepth = 1, label = 'JSON' }) -> { search(q) -> matchCount, expandAll(), collapseAll() }.
 */
export function renderJsonTree(container, data, { expandDepth = 1, label = 'JSON' } = {}) {
  // ponytail: eager DOM build; fine for FHIR bundles of a few thousand nodes, lazy-render children if bundles grow past ~20k nodes.
  const texts = [];
  const leafValue = (v) => {
    const s = typeof v === 'string' ? span('jt-str', JSON.stringify(v)) : typeof v === 'number' ? span('jt-num', String(v)) : span('jt-lit', String(v));
    texts.push(s);
    return s;
  };
  const build = (key, val, depth) => {
    const li = document.createElement('li');
    const keyParts = [];
    if (key !== undefined) {
      const k = span('jt-key', typeof key === 'number' ? String(key) : JSON.stringify(key));
      texts.push(k);
      keyParts.push(k, document.createTextNode(': '));
    }
    if (val === null || typeof val !== 'object') {
      li.className = 'jt-leaf';
      li.append(...keyParts, leafValue(val));
      return li;
    }
    const entries = Array.isArray(val) ? val.map((v, i) => [i, v]) : Object.entries(val);
    const details = document.createElement('details');
    details.open = depth < expandDepth;
    const summary = document.createElement('summary');
    summary.append(...keyParts, span('jt-meta', Array.isArray(val) ? `[${entries.length}]` : `{${entries.length}}`));
    const ul = document.createElement('ul');
    entries.forEach(([k, v]) => ul.append(build(k, v, depth + 1)));
    details.append(summary, ul);
    li.append(details);
    return li;
  };
  const root = document.createElement('ul');
  root.append(build(undefined, data, 0));
  container.classList.add('json-tree');
  container.setAttribute('role', 'region');
  container.setAttribute('aria-label', label);
  container.tabIndex = 0;
  container.replaceChildren(root);
  texts.forEach((s) => { s._t = s.textContent; });
  const setAll = (open) => container.querySelectorAll('details').forEach((d) => { d.open = open; });
  return {
    search(query) {
      const q = String(query || '').trim().toLowerCase();
      let count = 0;
      let first = null;
      for (const s of texts) {
        if (s.childElementCount) s.textContent = s._t;
        if (!q || !s._t.toLowerCase().includes(q)) continue;
        const parts = s._t.split(new RegExp(`(${q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'i'));
        s.replaceChildren(...parts.map((p, i) => {
          if (i % 2 === 0) return document.createTextNode(p);
          const m = document.createElement('mark');
          m.textContent = p;
          return m;
        }));
        count += (parts.length - 1) / 2;
        first ||= s;
        for (let d = s.closest('details'); d; d = d.parentElement.closest('details')) d.open = true;
      }
      first?.scrollIntoView({ block: 'nearest' });
      return count;
    },
    expandAll: () => setAll(true),
    collapseAll: () => setAll(false),
  };
}

/* ------------------------------------------------------------------------ */
/* URL state, timing, motion                                                  */
/* ------------------------------------------------------------------------ */

/** Read one query-string value (or fallback). */
export const getQuery = (key, fallback = null) => new URLSearchParams(location.search).get(key) ?? fallback;
/** All query-string values as a plain object. */
export const getQueryAll = () => Object.fromEntries(new URLSearchParams(location.search));
/** Merge params into the URL without reloading; null/undefined/'' removes a key. opts.push adds a history entry. */
export function setQuery(params, { push = false } = {}) {
  const url = new URL(location.href);
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === '') url.searchParams.delete(k);
    else url.searchParams.set(k, String(v));
  }
  history[push ? 'pushState' : 'replaceState'](history.state, '', url);
}

/** Debounce: run fn once calls stop for `wait` ms. */
export function debounce(fn, wait = 250) {
  let t = 0;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), wait);
  };
}

/** True when the user asked for reduced motion. */
export const prefersReducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

/** Fade/slide .reveal elements in on first scroll into view (no-op with reduced motion). */
export function initReveal(root = document) {
  if (prefersReducedMotion() || !('IntersectionObserver' in window)) return;
  document.documentElement.classList.add('js-reveal');
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      e.target.classList.add('is-visible');
      io.unobserve(e.target);
    });
  }, { rootMargin: '0px 0px -8% 0px' });
  root.querySelectorAll('.reveal').forEach((el) => io.observe(el));
}

/** Wire a .dropzone: drag-and-drop plus its inner <input type=file>; onFiles(File[]). */
export function initDropzone(zone, onFiles) {
  const input = zone.querySelector('input[type="file"]');
  input?.addEventListener('change', () => input.files.length && onFiles([...input.files]));
  const on = (e) => { e.preventDefault(); zone.classList.add('is-dragover'); };
  zone.addEventListener('dragenter', on);
  zone.addEventListener('dragover', on);
  zone.addEventListener('dragleave', (e) => { if (!zone.contains(e.relatedTarget)) zone.classList.remove('is-dragover'); });
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('is-dragover');
    const files = [...(e.dataTransfer?.files || [])];
    if (files.length) onFiles(files);
  });
}

/* ------------------------------------------------------------------------ */
/* Theme                                                                      */
/* ------------------------------------------------------------------------ */

const THEME_KEY = 'clindoc-theme';
// 'auto' (default) follows the system appearance (dark-mode.md); light/dark are explicit overrides.
const THEMES = ['auto', 'light', 'dark'];
const THEME_ICON = { auto: 'monitor', light: 'sun', dark: 'moon' };
const THEME_LABEL = { auto: 'match system', light: 'light', dark: 'dark' };
const systemDark = typeof matchMedia === 'function' ? matchMedia('(prefers-color-scheme: dark)') : null;

/** Stored theme preference: 'auto' (default) | 'light' | 'dark'. */
export function getTheme() {
  try {
    const t = localStorage.getItem(THEME_KEY);
    return t === 'light' || t === 'dark' ? t : 'auto';
  } catch {
    return 'auto';
  }
}

/** The appearance actually shown: the preference, or the system setting when 'auto'. */
export function effectiveTheme(pref = getTheme()) {
  return pref === 'auto' ? (systemDark?.matches ? 'dark' : 'light') : pref;
}

function applyTheme() {
  const t = effectiveTheme();
  document.documentElement.dataset.theme = t;
  syncThemeButtons();
  window.dispatchEvent(new CustomEvent('themechange', { detail: { theme: t } }));
}

/** Persist a preference ('auto' | 'light' | 'dark') and apply it; dispatches window 'themechange'. */
export function setTheme(pref) {
  const p = THEMES.includes(pref) ? pref : 'auto';
  try {
    if (p === 'auto') localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, p);
  } catch { /* storage blocked: theme still applies for this page */ }
  applyTheme();
}

// Follow live system changes (e.g. Auto appearance at sunset) while the preference is 'auto'.
systemDark?.addEventListener?.('change', () => { if (getTheme() === 'auto') applyTheme(); });

function syncThemeButtons() {
  const p = getTheme();
  const next = THEMES[(THEMES.indexOf(p) + 1) % THEMES.length];
  document.querySelectorAll('[data-theme-toggle]').forEach((b) => {
    b.innerHTML = icon(THEME_ICON[p]);
    b.setAttribute('aria-label', `Colour theme: ${THEME_LABEL[p]}. Switch to ${THEME_LABEL[next]}.`);
    b.title = `Theme: ${THEME_LABEL[p]}`;
  });
}

/* ------------------------------------------------------------------------ */
/* Shell                                                                      */
/* ------------------------------------------------------------------------ */

/** Site navigation: [page key (body[data-page]), label, href]. */
export const NAV = [
  ['home', 'Home', '/'],
  ['workbench', 'Workbench', '/workbench.html'],
  ['playground', 'Playground', '/playground.html'],
  ['explorer', 'Explorer', '/explorer.html'],
  ['results', 'Results', '/results.html'],
  ['model', 'Model', '/model.html'],
  ['docs', 'Docs', '/docs.html'],
];

/* Google sign-in, only when the server sets CLINDOC_GOOGLE_CLIENT_ID (clindoc/web/auth.py).
 * Signed in: name + Sign out in the header. Signed out: a full-page gate with the Google button. */
async function initAuth(header) {
  if (STATIC) return;
  let me;
  try { me = await (await fetch('/api/auth/me', { headers: { Accept: 'application/json' } })).json(); } catch { return; }
  if (!me?.enabled) return;
  if (me.user) {
    header?.querySelector('.site-header__tools')?.insertAdjacentHTML('afterbegin',
      `<span class="site-header__user" title="${escapeHtml(me.user.email)}">${escapeHtml(me.user.name)}</span>`
      + '<button class="btn btn--ghost btn--sm" type="button" data-sign-out>Sign out</button>');
    header?.querySelector('[data-sign-out]')?.addEventListener('click', async () => {
      await fetch('/api/auth/logout', { method: 'POST' }).catch(() => {});
      location.reload();
    });
    return;
  }
  document.body.insertAdjacentHTML('beforeend', `<div class="auth-gate" role="dialog" aria-modal="true" aria-labelledby="auth-title">
  <div class="auth-gate__card stack">
    <img src="/favicon.svg" alt="" width="40" height="40">
    <h1 class="auth-gate__title" id="auth-title">Sign in to Clindoc2FHIR</h1>
    <p class="auth-gate__lede">Use your Google account to continue.</p>
    <div class="auth-gate__button" data-google-button></div>
    <p class="auth-gate__error" role="alert" data-auth-error></p>
  </div>
</div>`);
  document.documentElement.classList.add('auth-locked');
  const error = document.querySelector('[data-auth-error]');
  const script = Object.assign(document.createElement('script'), { src: 'https://accounts.google.com/gsi/client', async: true });
  script.onerror = () => { error.textContent = 'Could not load Google sign-in. Check the network and reload.'; };
  script.onload = () => {
    window.google.accounts.id.initialize({
      client_id: me.client_id,
      callback: async ({ credential }) => {
        error.textContent = '';
        try {
          await api('/api/auth/google', { json: { credential } });
          location.reload();
        } catch (err) {
          error.textContent = err.message;
        }
      },
    });
    window.google.accounts.id.renderButton(document.querySelector('[data-google-button]'),
      { theme: effectiveTheme() === 'dark' ? 'filled_black' : 'outline', size: 'large', text: 'signin_with', shape: 'rectangular' });
  };
  document.head.append(script);
}

/**
 * Inject the shared header/footer into <header data-shell> / <footer data-shell>, mark the active
 * nav link from <body data-page>, wire theme toggle, mobile menu, serving pills, tabs, reveal,
 * [data-copy] / [data-copy-target] buttons and [data-dialog-open="#id"] triggers. Call once per page.
 */
export function mountShell() {
  const page = document.body.dataset.page || '';
  if (!document.querySelector('.skip-link')) {
    const main = document.querySelector('main');
    if (main && !main.id) main.id = 'main';
    document.body.insertAdjacentHTML('afterbegin', `<a class="skip-link" href="#${main?.id || 'main'}">Skip to main content</a>`);
  }
  if (STATIC) {
    document.querySelector('main')?.insertAdjacentHTML('afterbegin',
      '<p class="alert alert--info container" role="note" style="margin-block: 1rem 0;">Static demo: verdicts, runs and '
      + 'checks below were recorded from the trained model. Uploads and custom inputs need the local server '
      + '(<code>clindoc serve</code>).</p>');
  }
  const header = document.querySelector('header[data-shell]');
  if (header) {
    header.className = 'site-header';
    header.innerHTML = `<div class="container container--wide site-header__inner">
  <a class="brand" href="/"><img class="brand__mark" src="/favicon.svg" alt="" width="22" height="22">Clindoc2FHIR</a>
  <nav class="site-nav" id="site-nav" aria-label="Main">
    <ul class="site-nav__list" role="list">${NAV.map(([key, label, href]) => `<li><a class="site-nav__link" href="${href}"${key === page ? ' aria-current="page"' : ''}>${label}</a></li>`).join('')}</ul>
    <div class="site-nav__status" data-serving-pill></div>
  </nav>
  <div class="site-header__tools">
    <span class="site-header__status" data-serving-pill></span>
    <button class="btn btn--ghost btn--icon" type="button" data-theme-toggle></button>
    <button class="btn btn--ghost btn--icon nav-toggle" type="button" aria-expanded="false" aria-controls="site-nav" aria-label="Open menu">${icon('menu')}</button>
  </div>
</div>`;
    const nav = header.querySelector('#site-nav');
    const toggle = header.querySelector('.nav-toggle');
    const setOpen = (open) => {
      nav.dataset.open = String(open);
      toggle.setAttribute('aria-expanded', String(open));
      toggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
      toggle.innerHTML = icon(open ? 'x' : 'menu');
    };
    toggle.addEventListener('click', () => setOpen(nav.dataset.open !== 'true'));
    nav.addEventListener('click', (e) => { if (e.target.closest('a')) setOpen(false); });
    header.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && nav.dataset.open === 'true') { setOpen(false); toggle.focus(); }
    });
  }
  const footer = document.querySelector('footer[data-shell]');
  if (footer) {
    footer.className = 'site-footer';
    footer.innerHTML = `<div class="container container--wide site-footer__inner">
  <div class="stack stack--sm">
    <p class="site-footer__disclaimer">${icon('info')}<span><strong>Research prototype on synthetic data.</strong> Not a medical device and not validated for clinical use. Verdicts check whether an extracted claim matches its source document, not whether it is clinically correct.</span></p>
    <p class="site-footer__meta">Clindoc2FHIR · <span data-api-version>API status unknown</span> · Icons: Lucide (ISC)</p>
  </div>
  <ul class="site-footer__links" role="list">
    <li><a href="/docs.html">Documentation</a></li>
    <li><a href="/results.html">Benchmark results</a></li>
    <li><a href="/model.html">Model card</a></li>
  </ul>
</div>`;
  }
  initAuth(header);
  syncThemeButtons();
  document.querySelectorAll('[data-theme-toggle]').forEach((b) => b.addEventListener('click', () => {
    const t = getTheme();
    setTheme(THEMES[(THEMES.indexOf(t) + 1) % THEMES.length]);
  }));
  ensureToastRegion();
  document.querySelectorAll('[data-serving-pill]').forEach((el) => renderServingPill(el));
  getHealth().then(
    (h) => document.querySelectorAll('[data-api-version]').forEach((el) => { el.textContent = `API ${h?.version || ''}`.trim(); }),
    () => document.querySelectorAll('[data-api-version]').forEach((el) => { el.textContent = 'API offline'; }),
  );
  initTabs();
  initReveal();
  document.addEventListener('click', (e) => {
    const copy = e.target.closest('[data-copy], [data-copy-target]');
    if (copy) {
      const target = copy.dataset.copyTarget && document.querySelector(copy.dataset.copyTarget);
      copyText(copy.dataset.copy ?? target?.textContent ?? '');
    }
    const opener = e.target.closest('[data-dialog-open]');
    if (opener) openDialog(opener.dataset.dialogOpen, { returnFocus: opener });
  });
  // Tooltips (.tip) must be dismissible without moving focus (WCAG 1.4.13).
  const root = document.documentElement;
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') root.classList.add('tips-off'); });
  document.addEventListener('pointerover', () => root.classList.remove('tips-off'));
  document.addEventListener('focusin', () => root.classList.remove('tips-off'));
}

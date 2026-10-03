/**
 * Clindoc2FHIR web — dependency-free, responsive SVG/HTML charts.
 * Authority: dataviz rules — one y-axis only, bars <= 24px with 4px rounded data end and square
 * baseline, 2px lines, >= 8px markers with a 2px surface ring, 2px surface gaps between touching
 * fills, hairline recessive grid, legend whenever >= 2 series, text in text tokens (never series
 * colour), hover + keyboard-focus tooltip, and a "View as table" twin for every chart.
 * Colours are CSS tokens from app.css (--series-1..8, --seq-0..6, --chart-*), so themes switch
 * without re-rendering.
 *
 * Every chart: chartFn(container, opts) -> { update(data), destroy() }.
 * Common opts: data, x, y, series [{ key, label, color? }], format, ariaLabel, onSelect(datum),
 *              legend (false hides), table (false hides the table toggle), emptyText.
 * format: function or preset 'pct' | 'pct0' | 'pct2' | 'int' | 'num' | 'dec2' | 'dec3' | 'dec4' | 'sec'.
 * Keyboard: Tab to the chart's active mark, arrows move between marks (2D in heatGrid),
 * Home/End/PageUp/PageDown jump, Enter selects (when onSelect is set), Esc hides the tooltip.
 */
import { escapeHtml, icon, isNum, num, pct, seconds } from './core.js';

const NS = 'http://www.w3.org/2000/svg';
const DASHES = ['', '6 4', '2 3'];
const SHAPES = ['circle', 'square', 'diamond'];
const FONT = '12px "Noto Sans", system-ui, sans-serif';
const FONT_BOLD = `600 ${FONT}`;
let uid = 0;

/* ------------------------------------------------------------------------ */
/* Helpers                                                                   */
/* ------------------------------------------------------------------------ */

function el(tag, attrs = {}, parent) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null && v !== false && v !== '') n.setAttribute(k, v);
  parent?.append(n);
  return n;
}
function text(parent, x, y, str, attrs = {}) {
  const t = el('text', { x, y, ...attrs }, parent);
  t.textContent = str;
  return t;
}
let measureCtx;
function measure(str, font = FONT) {
  measureCtx ||= document.createElement('canvas').getContext('2d');
  measureCtx.font = font;
  return measureCtx.measureText(String(str)).width;
}
/** Truncate a label to maxW px with an ellipsis (full text stays in aria-label, tooltip and table). */
function fit(str, maxW) {
  const s = String(str);
  if (measure(s) <= maxW) return s;
  let out = s;
  while (out.length > 1 && measure(`${out}…`) > maxW) out = out.slice(0, -1);
  return `${out}…`;
}
const FORMATS = {
  pct: (v) => pct(v, 1), pct0: (v) => pct(v, 0), pct2: (v) => pct(v, 2),
  int: (v) => num(v, 0), num: (v) => num(v, Number.isInteger(v) ? 0 : 2),
  dec2: (v) => num(v, 2), dec3: (v) => num(v, 3), dec4: (v) => num(v, 4), sec: (v) => seconds(v),
};
const fmt = (f) => (typeof f === 'function' ? f : FORMATS[f] || FORMATS.num);
/** Axis ticks: explicit tickFormat, else whole percents for pct formats, else the value format. */
const tickFmt = (o, key = 'format') => (o.tickFormat && key === 'format' ? fmt(o.tickFormat) : /^pct/.test(o[key]) ? FORMATS.pct0 : fmt(o[key]));
const finite = (v) => v !== null && v !== undefined && v !== '' && Number.isFinite(+v);
/** Series colour: s.color may be a slot number (1-8) or any CSS colour; default = slot by index. */
const seriesColor = (s, i) => (typeof s?.color === 'number' ? `var(--series-${s.color})` : s?.color || `var(--series-${(i % 8) + 1})`);

function niceStep(span, n) {
  const raw = span / Math.max(1, n);
  const p = 10 ** Math.floor(Math.log10(raw));
  const f = raw / p;
  return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * p;
}
function niceTicks(a, b, n = 5) {
  if (!(b > a)) return [a];
  const step = niceStep(b - a, n);
  const out = [];
  for (let v = Math.ceil(a / step - 1e-9) * step; v <= b + step * 1e-9; v += step) out.push(+v.toFixed(12));
  return out;
}
function niceDomain([a, b], n = 5) {
  if (!(b > a)) return [a - 1, a + 1];
  const step = niceStep(b - a, n);
  return [Math.floor(a / step + 1e-9) * step, Math.ceil(b / step - 1e-9) * step];
}
function linear([d0, d1], [r0, r1]) {
  const s = (v) => r0 + ((v - d0) / (d1 - d0 || 1)) * (r1 - r0);
  s.invert = (p) => d0 + ((p - r0) / (r1 - r0 || 1)) * (d1 - d0);
  s.ticks = (n) => niceTicks(d0, d1, n);
  return s;
}
function logScale([d0, d1], [r0, r1]) {
  const l0 = Math.log10(d0), l1 = Math.log10(d1);
  const s = (v) => r0 + ((Math.log10(Math.max(v, d0)) - l0) / (l1 - l0 || 1)) * (r1 - r0);
  s.ticks = () => {
    const out = [];
    for (let e = Math.ceil(l0); e <= Math.floor(l1); e++) out.push(10 ** e);
    return out;
  };
  return s;
}
/** Bar path: 2px rounded data end, square baseline. orient 'v' grows up from y+h; 'h' grows right from x. */
function barPath(x, y, w, h, orient) {
  const r = Math.max(0, Math.min(2, orient === 'v' ? w / 2 : h / 2, orient === 'v' ? h : w));
  if (orient === 'v') return `M${x},${y + h}V${y + r}A${r},${r} 0 0 1 ${x + r},${y}H${x + w - r}A${r},${r} 0 0 1 ${x + w},${y + r}V${y + h}Z`;
  return `M${x},${y}H${x + w - r}A${r},${r} 0 0 1 ${x + w},${y + r}V${y + h - r}A${r},${r} 0 0 1 ${x + w - r},${y + h}H${x}Z`;
}
function marker(parent, shape, x, y, r, fill, attrs = {}) {
  if (shape === 'square') return el('rect', { x: x - r, y: y - r, width: 2 * r, height: 2 * r, rx: 1, fill, ...attrs }, parent);
  if (shape === 'diamond') {
    const d = r * 1.3;
    return el('path', { d: `M${x},${y - d}L${x + d},${y}L${x},${y + d}L${x - d},${y}Z`, fill, ...attrs }, parent);
  }
  return el('circle', { cx: x, cy: y, r, fill, ...attrs }, parent);
}
function luminance(hex) {
  const h = hex.replace('#', '');
  const c = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
/** Label ink for text inside a fill: white or dark ink if either clears 4.5:1, else null (label moves to legend/tooltip). */
function inkFor(scope, color) {
  const m = /var\((--[\w-]+)\)/.exec(color);
  const hex = (m ? getComputedStyle(scope).getPropertyValue(m[1]) : color).trim();
  if (!/^#[0-9a-f]{6}$/i.test(hex)) return null;
  const L = luminance(hex);
  const white = 1.05 / (L + 0.05), ink = (L + 0.05) / (luminance('#1A1A17') + 0.05);
  if (Math.max(white, ink) < 4.5) return null;
  return white >= ink ? '#FFFFFF' : '#1A1A17';
}

/* ------------------------------------------------------------------------ */
/* Legend, tooltip, table                                                    */
/* ------------------------------------------------------------------------ */

/**
 * Render a legend into container. items: [{ label, color, shape: 'rect'|'line', dash, marker, value }].
 * Mirrors the mark: rect swatch for bars/areas, line key (+ marker) for lines. Returns the <ul>.
 */
export function legend(container, items) {
  const ul = document.createElement('ul');
  ul.className = 'legend';
  ul.setAttribute('role', 'list');
  for (const it of items) {
    const li = document.createElement('li');
    li.className = 'legend__item';
    const key = el('svg', { class: 'legend__key', viewBox: '0 0 18 12', 'aria-hidden': 'true' });
    if (it.shape === 'line') {
      el('line', { x1: 1, y1: 6, x2: 17, y2: 6, stroke: it.color, 'stroke-width': 2, 'stroke-dasharray': it.dash, 'stroke-linecap': 'round' }, key);
      if (it.marker) marker(key, it.marker, 9, 6, 3, it.color);
    } else {
      el('rect', { x: 3, y: 1, width: 12, height: 10, rx: 2, fill: it.color }, key);
    }
    const label = document.createElement('span');
    label.textContent = it.label;
    li.append(key, label);
    if (it.value !== undefined && it.value !== null) {
      const v = document.createElement('span');
      v.className = 'legend__value';
      v.textContent = it.value;
      li.append(v);
    }
    ul.append(li);
  }
  container.replaceChildren(ul);
  return ul;
}

function fillTip(tip, { title, rows = [] }) {
  tip.replaceChildren();
  if (title) {
    const t = document.createElement('div');
    t.className = 'chart__tooltip-title';
    t.textContent = title;
    tip.append(t);
  }
  for (const r of rows) {
    const row = document.createElement('div');
    row.className = 'chart__tooltip-row';
    const key = el('svg', { viewBox: '0 0 14 10', 'aria-hidden': 'true' });
    if (r.color) el('line', { x1: 1, y1: 5, x2: 13, y2: 5, stroke: r.color, 'stroke-width': 2, 'stroke-dasharray': r.dash, 'stroke-linecap': 'round' }, key);
    const v = document.createElement('strong');
    v.textContent = r.value;
    const l = document.createElement('span');
    l.textContent = r.label ?? '';
    row.append(key, v, l);
    tip.append(row);
  }
}

/**
 * "View as table" fallback: appends a toggle button + hidden table to container.
 * columns: [{ key, label, numeric?, format?(value, row) }]; rows: objects. First column renders as row header.
 * Returns { update(columns, rows), button, element }.
 */
export function toggleTable(container, columns = [], rows = [], { label = 'View as table' } = {}) {
  const id = `chart-table-${++uid}`;
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn btn--ghost btn--sm chart__table-toggle';
  btn.setAttribute('aria-expanded', 'false');
  btn.setAttribute('aria-controls', id);
  btn.innerHTML = `${icon('table')}<span>${escapeHtml(label)}</span>`;
  const wrap = document.createElement('div');
  wrap.className = 'chart__table';
  wrap.id = id;
  wrap.hidden = true;
  btn.addEventListener('click', () => {
    const open = wrap.hidden;
    wrap.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
    btn.lastElementChild.textContent = open ? 'Hide table' : label;
  });
  container.append(btn, wrap);
  function update(cols, rs) {
    const table = document.createElement('table');
    table.className = 'table table--compact';
    const head = table.createTHead().insertRow();
    cols.forEach((c) => {
      const th = document.createElement('th');
      th.scope = 'col';
      th.textContent = c.label ?? c.key;
      if (c.numeric) th.className = 'num';
      head.append(th);
    });
    const body = table.createTBody();
    rs.forEach((r) => {
      const tr = body.insertRow();
      cols.forEach((c, i) => {
        const cell = document.createElement(i === 0 ? 'th' : 'td');
        if (i === 0) cell.scope = 'row';
        const v = r[c.key];
        cell.textContent = c.format ? c.format(v, r) : v === null || v === undefined ? '—' : String(v);
        if (c.numeric) cell.className = 'num';
        tr.append(cell);
      });
    });
    const tw = document.createElement('div');
    tw.className = 'table-wrap';
    tw.append(table);
    wrap.replaceChildren(tw);
  }
  update(columns, rows);
  return { update, button: btn, element: wrap };
}

/* ------------------------------------------------------------------------ */
/* Mount: responsive frame, tooltip, roving focus, legend, table             */
/* ------------------------------------------------------------------------ */

/**
 * render(ctx, data) draws into ctx.stage and returns { marks, legend, grid }.
 * mark = { el, aria, tip: { title, rows }, anchor: [x, y] | () => [x, y], datum, on?(kb), off?(), noPointer? }.
 */
function mount(container, o, render, tableOf) {
  container.classList.add('chart');
  container.replaceChildren();
  const legendEl = document.createElement('div');
  const plot = document.createElement('div');
  plot.className = 'chart__plot';
  const stage = document.createElement('div');
  stage.setAttribute('role', 'group');
  stage.setAttribute('aria-label', o.ariaLabel || 'Chart');
  const tip = document.createElement('div');
  tip.className = 'chart__tooltip';
  tip.hidden = true;
  tip.setAttribute('aria-hidden', 'true');
  plot.append(stage, tip);
  const footer = document.createElement('div');
  footer.className = 'chart__footer';
  container.append(legendEl, plot, footer);
  const table = o.table === false ? null : toggleTable(footer);
  footer.hidden = !table;
  const st = { data: o.data, marks: [], active: 0, width: 0, current: null, grid: 0, dead: false };

  const pos = (e) => {
    const r = plot.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  };
  function showTip(data, [x, y]) {
    fillTip(tip, data);
    tip.hidden = false;
    const pw = plot.clientWidth, tw = tip.offsetWidth, th = tip.offsetHeight;
    let left = x + 14;
    if (left + tw > pw) left = Math.max(0, x - tw - 14);
    let top = y - th - 10;
    if (top < 0) top = Math.min(y + 14, Math.max(0, plot.clientHeight - th));
    tip.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
  }
  function hideTip() {
    tip.hidden = true;
    if (st.current) {
      st.current.el.classList.remove('is-active');
      st.current.off?.();
      st.current = null;
    }
  }
  function activate(m, at, kb) {
    if (st.current && st.current !== m) {
      st.current.el.classList.remove('is-active');
      st.current.off?.();
    }
    st.current = m;
    m.el.classList.add('is-active');
    m.on?.(kb);
    showTip(m.tip, at || (typeof m.anchor === 'function' ? m.anchor() : m.anchor));
  }
  function bind(m, i) {
    m.el.setAttribute('tabindex', i === st.active ? '0' : '-1');
    m.el.setAttribute('role', 'img');
    m.el.setAttribute('aria-label', m.aria);
    if (!m.noPointer) {
      m.el.addEventListener('pointerenter', (e) => activate(m, pos(e)));
      m.el.addEventListener('pointermove', (e) => showTip(m.tip, pos(e)));
      m.el.addEventListener('pointerleave', hideTip);
      if (o.onSelect) m.el.addEventListener('click', () => o.onSelect(m.datum));
    }
    m.el.addEventListener('focus', () => { st.active = i; activate(m, null, true); });
    m.el.addEventListener('blur', hideTip);
  }
  plot.addEventListener('keydown', (e) => {
    const n = st.marks.length;
    const i = st.marks.findIndex((m) => m.el === document.activeElement);
    if (i < 0) return;
    if (e.key === 'Escape') { hideTip(); return; }
    if ((e.key === 'Enter' || e.key === ' ') && o.onSelect) {
      e.preventDefault();
      o.onSelect(st.marks[i].datum);
      return;
    }
    const g = st.grid, page = Math.max(1, Math.ceil(n / 10));
    const map = { ArrowRight: i + 1, ArrowLeft: i - 1, ArrowDown: g ? i + g : i + 1, ArrowUp: g ? i - g : i - 1, Home: 0, End: n - 1, PageDown: i + page, PageUp: i - page };
    if (!(e.key in map)) return;
    e.preventDefault();
    const j = Math.max(0, Math.min(n - 1, map[e.key]));
    if (j === i) return;
    st.marks[i].el.setAttribute('tabindex', '-1');
    st.marks[j].el.setAttribute('tabindex', '0');
    st.marks[j].el.focus();
  });

  const ctx = {
    stage, pos, showTip,
    hideTip: () => { tip.hidden = true; },
    get width() { return st.width; },
    svg(height) {
      const s = el('svg', { class: 'chart__svg', width: st.width, height, viewBox: `0 0 ${st.width} ${height}` });
      stage.append(s);
      return s;
    },
    empty(msg) {
      stage.innerHTML = `<p class="chart__empty">${escapeHtml(msg || o.emptyText || 'No data to display.')}</p>`;
      return { marks: [] };
    },
  };
  function draw(force) {
    if (st.dead) return;
    const w = Math.floor(plot.clientWidth);
    if (!w || (w === st.width && !force)) return;
    st.width = w;
    hideTip();
    stage.replaceChildren();
    const out = render(ctx, st.data || []) || {};
    st.marks = out.marks || [];
    st.grid = out.grid || 0;
    st.active = Math.min(st.active, Math.max(0, st.marks.length - 1));
    st.marks.forEach(bind);
    const items = o.legend === false ? [] : out.legend || [];
    legendEl.hidden = !items.length;
    if (items.length) legend(legendEl, items);
  }
  function refresh() {
    if (table) {
      const t = tableOf(st.data || []);
      table.update(t.columns, t.rows);
    }
    draw(true);
  }
  const ro = new ResizeObserver(() => draw());
  ro.observe(plot);
  const redraw = () => draw(true);
  const mq = matchMedia('(prefers-color-scheme: dark)');
  window.addEventListener('themechange', redraw);
  mq.addEventListener?.('change', redraw);
  document.fonts?.ready.then(redraw);
  refresh();
  return {
    update(data) { st.data = data; refresh(); },
    destroy() {
      st.dead = true;
      ro.disconnect();
      window.removeEventListener('themechange', redraw);
      mq.removeEventListener?.('change', redraw);
      container.replaceChildren();
      container.classList.remove('chart');
    },
  };
}

/* ------------------------------------------------------------------------ */
/* Bars (single + grouped share one renderer)                                */
/* ------------------------------------------------------------------------ */

function renderBars(ctx, rows, o, S) {
  const f = fmt(o.format), tf = tickFmt(o);
  const xf = (d) => String(d[o.x] ?? '');
  const data = rows.filter((d) => S.some((s) => finite(d[s.key])));
  if (!data.length) return ctx.empty();
  const k = S.length, W = ctx.width, log = o.scale === 'log';
  // Vertical bars flip to horizontal when category labels would be truncated (narrow screens).
  const horiz = o.orientation !== 'vertical' || Math.max(...data.map((d) => measure(xf(d)))) > (W - 60) / data.length - 4;
  const vals = data.flatMap((d) => S.map((s) => +d[s.key])).filter(Number.isFinite);
  const max = Math.max(...vals);
  let dom = o.domain;
  if (!dom && log) {
    const pos = vals.filter((v) => v > 0);
    dom = [10 ** Math.floor(Math.log10(Math.min(...pos))), 10 ** Math.ceil(Math.log10(Math.max(...pos)))];
    if (dom[0] === dom[1]) dom = [dom[0] / 10, dom[1]];
  }
  dom ||= max > 0 ? niceDomain([0, max]) : [0, 1];
  const isHi = (d) => o.highlight !== undefined && o.highlight !== null && (typeof o.highlight === 'function' ? o.highlight(d) : xf(d) === String(o.highlight));
  const showVal = (d) => o.labels === 'all' || (o.labels === 'highlight' && isHi(d)) || (Array.isArray(o.labels) && o.labels.includes(xf(d)));
  const colorOf = (s, i, d) => (k === 1 && o.highlight !== undefined && o.highlight !== null && !isHi(d) ? 'var(--chart-muted-mark)' : seriesColor(s, i));
  const labelled = data.filter(showVal);
  const valW = labelled.length ? Math.max(...labelled.flatMap((d) => S.map((s) => measure(f(+d[s.key]), FONT_BOLD)))) + 10 : 0;
  const tipOf = (d) => ({ title: xf(d), rows: S.filter((s) => finite(d[s.key])).map((s, i) => ({ value: f(+d[s.key]), label: k > 1 ? s.label : o.yLabel || '', color: colorOf(s, i, d) })) });
  const marks = [];
  const scaleOf = log ? logScale : linear;

  if (horiz) {
    const thick = k === 1 ? 20 : 14, groupH = k * thick + (k - 1) * 2, rowH = groupH + (k === 1 ? 14 : 18);
    const labelW = Math.min(Math.max(...data.map((d) => measure(xf(d), isHi(d) ? FONT_BOLD : FONT))) + 12, W * 0.42);
    const top = 4, axisH = 22 + (o.yLabel ? 18 : 0), H = top + rowH * data.length + axisH;
    const x0 = labelW, x1 = Math.max(x0 + 40, W - Math.max(valW, 8));
    const sc = scaleOf(dom, [x0, x1]);
    const svg = ctx.svg(H);
    const grid = el('g', { class: 'chart__grid' }, svg);
    for (const t of sc.ticks(Math.max(2, Math.floor((x1 - x0) / 90)))) {
      const x = Math.round(sc(t)) + 0.5;
      el('line', { x1: x, x2: x, y1: top, y2: top + rowH * data.length }, grid);
      text(svg, x, top + rowH * data.length + 16, tf(t), { 'text-anchor': 'middle' });
    }
    if (o.yLabel) text(svg, (x0 + x1) / 2, H - 4, o.yLabel, { 'text-anchor': 'middle', class: 'chart__axis-title' });
    data.forEach((d, r) => {
      const y = top + r * rowH + (rowH - groupH) / 2;
      const lab = text(svg, x0 - 8, top + r * rowH + rowH / 2, fit(xf(d), labelW - 12), { 'text-anchor': 'end', dy: '0.35em', class: isHi(d) ? 'chart__label-strong' : null });
      el('title', {}, lab).textContent = xf(d);
      S.forEach((s, i) => {
        if (!finite(d[s.key])) return;
        const v = +d[s.key], by = y + i * (thick + 2), len = Math.max(0, sc(v) - x0);
        const path = el('path', { d: barPath(x0, by, len, thick, 'h'), fill: colorOf(s, i, d), class: 'chart__mark' }, svg);
        if (showVal(d)) text(svg, x0 + len + 6, by + thick / 2, f(v), { dy: '0.35em', class: 'chart__label-strong' });
        marks.push({ el: path, datum: d, aria: `${xf(d)}${k > 1 ? `, ${s.label}` : ''}: ${f(v)}`, tip: tipOf(d), anchor: [x0 + len, by] });
      });
    });
    el('line', { class: 'chart__baseline', x1: x0 + 0.5, x2: x0 + 0.5, y1: top, y2: top + rowH * data.length }, svg);
  } else {
    const H = o.height || 260, top = labelled.length ? 22 : 10, axisH = 26 + (o.xLabel ? 18 : 0);
    const sc = scaleOf(dom, [H - axisH, top]);
    const ticks = sc.ticks(Math.max(2, Math.floor((H - axisH - top) / 48)));
    const left = Math.max(...ticks.map((t) => measure(tf(t)))) + 10 + (o.yLabel ? 18 : 0);
    const x0 = left, x1 = W - 4, band = (x1 - x0) / data.length;
    const thick = Math.max(2, Math.min(24, (band * 0.7 - (k - 1) * 2) / k)), groupW = k * thick + (k - 1) * 2;
    const svg = ctx.svg(H);
    const grid = el('g', { class: 'chart__grid' }, svg);
    for (const t of ticks) {
      const y = Math.round(sc(t)) + 0.5;
      el('line', { x1: x0, x2: x1, y1: y, y2: y }, grid);
      text(svg, x0 - 6, y, tf(t), { 'text-anchor': 'end', dy: '0.35em' });
    }
    if (o.yLabel) text(svg, 0, 0, o.yLabel, { transform: `translate(12 ${(top + H - axisH) / 2}) rotate(-90)`, 'text-anchor': 'middle', class: 'chart__axis-title' });
    if (o.xLabel) text(svg, (x0 + x1) / 2, H - 4, o.xLabel, { 'text-anchor': 'middle', class: 'chart__axis-title' });
    const base = sc(dom[0]);
    data.forEach((d, c) => {
      const gx = x0 + c * band + (band - groupW) / 2;
      const lab = text(svg, x0 + c * band + band / 2, H - axisH + 16, fit(xf(d), band - 4), { 'text-anchor': 'middle', class: isHi(d) ? 'chart__label-strong' : null });
      el('title', {}, lab).textContent = xf(d);
      S.forEach((s, i) => {
        if (!finite(d[s.key])) return;
        const v = +d[s.key], bx = gx + i * (thick + 2), yv = Math.min(base, sc(v));
        const path = el('path', { d: barPath(bx, yv, thick, base - yv, 'v'), fill: colorOf(s, i, d), class: 'chart__mark' }, svg);
        if (showVal(d)) text(svg, bx + thick / 2, yv - 6, f(v), { 'text-anchor': 'middle', class: 'chart__label-strong' });
        marks.push({ el: path, datum: d, aria: `${xf(d)}${k > 1 ? `, ${s.label}` : ''}: ${f(v)}`, tip: tipOf(d), anchor: [bx + thick, yv] });
      });
    });
    el('line', { class: 'chart__baseline', x1: x0, x2: x1, y1: Math.round(base) + 0.5, y2: Math.round(base) + 0.5 }, svg);
  }
  return { marks, legend: k > 1 ? S.map((s, i) => ({ label: s.label, color: seriesColor(s, i) })) : [] };
}

/**
 * Bar chart (one series). opts: { data, x = 'label', y = 'value', orientation: 'horizontal'|'vertical',
 * highlight: category | (d) => bool (others muted), labels: 'none'|'all'|'highlight'|[categories],
 * scale: 'linear'|'log', domain: [min, max], format, tickFormat, xLabel, yLabel, height (vertical), color }.
 */
export function barChart(container, opts) {
  const o = { x: 'label', y: 'value', orientation: 'horizontal', labels: 'none', format: 'num', ...opts };
  const S = [{ key: o.y, label: o.yLabel || o.y, color: o.color }];
  const f = fmt(o.format);
  return mount(container, o, (ctx, data) => renderBars(ctx, data, o, S), (data) => ({
    columns: [{ key: o.x, label: o.xLabel || 'Category' }, { key: o.y, label: o.yLabel || 'Value', numeric: true, format: (v) => (finite(v) ? f(+v) : '—') }],
    rows: data,
  }));
}

/**
 * Grouped bar chart (<= 3 series for all-pairs colour safety). opts: { data, x, series: [{ key, label, color? }],
 * orientation: 'vertical'|'horizontal', domain, format, labels, xLabel, yLabel, height }.
 * Vertical charts render horizontally when category labels would not fit (phones).
 */
export function groupedBarChart(container, opts) {
  const o = { x: 'label', orientation: 'vertical', labels: 'none', format: 'num', ...opts };
  const S = (o.series || []).slice(0, 3);
  if ((o.series || []).length > 3) console.warn('groupedBarChart: more than 3 series; extra series dropped (fold to Other or use smallMultiples).');
  const f = fmt(o.format);
  return mount(container, o, (ctx, data) => renderBars(ctx, data, o, S), (data) => ({
    columns: [{ key: o.x, label: o.xLabel || 'Category' }, ...S.map((s) => ({ key: s.key, label: s.label, numeric: true, format: (v) => (finite(v) ? f(+v) : '—') }))],
    rows: data,
  }));
}

/* ------------------------------------------------------------------------ */
/* Line chart                                                                 */
/* ------------------------------------------------------------------------ */

function nearestIndex(arr, x, get = (v) => v) {
  let lo = 0, hi = arr.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (get(arr[mid]) < x) lo = mid;
    else hi = mid;
  }
  return Math.abs(get(arr[lo]) - x) <= Math.abs(get(arr[hi]) - x) ? lo : hi;
}

/**
 * Multi-series line chart with crosshair. Series get distinct dash styles and marker shapes.
 * Data either as rows (opts.data + series[].key as y column, opts.x as x column) or per series
 * (series[].data = [{ x, y }] with optional series[].x / series[].y accessor keys).
 * opts: { xDomain, yDomain, format (y), xFormat, xLabel, yLabel, height = 280, markers (max points
 * per series to mark, default 30), endLabels (value at line end), annotations: [{ type: 'x'|'y'|'point', x, y, label }] }.
 */
export function lineChart(container, opts) {
  const o = { x: 'x', height: 280, format: 'num', markers: 30, ...opts };
  const yf = fmt(o.format);
  const xfmt = o.xFormat ? fmt(o.xFormat) : (v) => num(v, Number.isInteger(v) ? 0 : 2);
  const norm = (rows) => (o.series || []).slice(0, 8).map((s, i) => {
    const xk = s.x || o.x, yk = s.y || s.key || o.y || 'y';
    const pts = (s.data || rows || [])
      .map((d) => ({ x: +d[xk], y: +d[yk] }))
      .filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y) && finite(p.y))
      .sort((a, b) => a.x - b.x);
    return { ...s, color: seriesColor(s, i), dash: DASHES[i % 3], shape: SHAPES[i % 3], pts };
  });
  const tableOf = (rows) => {
    const S = norm(rows);
    const xs = [...new Set(S.flatMap((s) => s.pts.map((p) => p.x)))].sort((a, b) => a - b);
    const lookup = S.map((s) => new Map(s.pts.map((p) => [p.x, p.y])));
    return {
      columns: [{ key: 'x', label: o.xLabel || 'x', format: (v) => xfmt(v) }, ...S.map((s, i) => ({ key: `s${i}`, label: s.label, numeric: true, format: (v) => (isNum(v) ? yf(v) : '—') }))],
      rows: xs.map((x) => Object.fromEntries([['x', x], ...lookup.map((m, i) => [`s${i}`, m.get(x)])])),
    };
  };
  return mount(container, o, (ctx, rows) => {
    const S = norm(rows).filter((s) => s.pts.length);
    if (!S.length) return ctx.empty();
    const W = ctx.width, H = o.height;
    const xs = [...new Set(S.flatMap((s) => s.pts.map((p) => p.x)))].sort((a, b) => a - b);
    const ys = S.flatMap((s) => s.pts.map((p) => p.y));
    const xd = o.xDomain || (xs[0] === xs[xs.length - 1] ? [xs[0] - 1, xs[0] + 1] : [xs[0], xs[xs.length - 1]]);
    const yd = o.yDomain || niceDomain([Math.min(...ys), Math.max(...ys)]);
    const top = 12, bottom = 24 + (o.xLabel ? 18 : 0);
    const ysc = linear(yd, [H - bottom, top]);
    const yt = ysc.ticks(Math.max(2, Math.floor((H - top - bottom) / 48)));
    const ytf = tickFmt(o), xtf = o.xFormat ? tickFmt(o, 'xFormat') : xfmt;
    const left = Math.max(...yt.map((t) => measure(ytf(t)))) + 10 + (o.yLabel ? 18 : 0);
    const lastVal = (s) => s.pts[s.pts.length - 1];
    const right = o.endLabels ? Math.max(...S.map((s) => measure(yf(lastVal(s).y), FONT_BOLD))) + 16 : 12;
    const xsc = linear(xd, [left, W - right]);
    const plotB = H - bottom;
    const svg = ctx.svg(H);
    const grid = el('g', { class: 'chart__grid' }, svg);
    for (const t of yt) {
      const y = Math.round(ysc(t)) + 0.5;
      el('line', { x1: left, x2: W - right, y1: y, y2: y }, grid);
      text(svg, left - 6, y, ytf(t), { 'text-anchor': 'end', dy: '0.35em' });
    }
    const intX = xs.every(Number.isInteger) && xd[1] - xd[0] <= 12;
    const xt = intX ? xs : niceTicks(xd[0], xd[1], Math.max(2, Math.floor((W - left - right) / 90)));
    for (const t of xt) text(svg, xsc(t), plotB + 16, xtf(t), { 'text-anchor': 'middle' });
    el('line', { class: 'chart__baseline', x1: left, x2: W - right, y1: plotB + 0.5, y2: plotB + 0.5 }, svg);
    if (o.yLabel) text(svg, 0, 0, o.yLabel, { transform: `translate(12 ${(top + plotB) / 2}) rotate(-90)`, 'text-anchor': 'middle', class: 'chart__axis-title' });
    if (o.xLabel) text(svg, (left + W - right) / 2, H - 4, o.xLabel, { 'text-anchor': 'middle', class: 'chart__axis-title' });
    const ann = o.annotations || [];
    for (const a of ann) {
      const type = a.type || (isNum(a.x) && isNum(a.y) ? 'point' : isNum(a.x) ? 'x' : 'y');
      if (type === 'x') {
        const X = Math.round(xsc(a.x)) + 0.5;
        el('line', { class: 'chart__annotation', x1: X, x2: X, y1: top, y2: plotB }, svg);
        if (a.label) text(svg, X + (X > W * 0.7 ? -4 : 4), top + 10, a.label, { class: 'chart__annotation-label', 'text-anchor': X > W * 0.7 ? 'end' : 'start' });
      } else if (type === 'y') {
        const Y = Math.round(ysc(a.y)) + 0.5;
        el('line', { class: 'chart__annotation', x1: left, x2: W - right, y1: Y, y2: Y }, svg);
        if (a.label) text(svg, W - right - 4, Y - 5, a.label, { class: 'chart__annotation-label', 'text-anchor': 'end' });
      }
    }
    for (const s of S) {
      const d = s.pts.map((p, j) => `${j ? 'L' : 'M'}${xsc(p.x).toFixed(1)},${ysc(p.y).toFixed(1)}`).join('');
      el('path', { d, class: 'chart__line', stroke: s.color, 'stroke-dasharray': s.dash }, svg);
      const pts = s.pts.length <= o.markers ? s.pts : [lastVal(s)];
      for (const p of pts) marker(svg, s.shape, xsc(p.x), ysc(p.y), 4, s.color, { class: 'chart__dot' });
    }
    if (o.endLabels) {
      let prev = -Infinity;
      [...S].sort((a, b) => ysc(lastVal(a).y) - ysc(lastVal(b).y)).forEach((s) => {
        const p = lastVal(s), Y = ysc(p.y);
        if (Y - prev < 14) return; // collision: legend + tooltip carry it
        prev = Y;
        text(svg, xsc(p.x) + 8, Y, yf(p.y), { dy: '0.35em', class: 'chart__label-strong' });
      });
    }
    for (const a of ann) {
      if (!(a.type === 'point' || (!a.type && isNum(a.x) && isNum(a.y)))) continue;
      const X = xsc(a.x), Y = ysc(a.y), flip = X > W * 0.65;
      marker(svg, 'circle', X, Y, 5, 'var(--chart-text-strong)', { class: 'chart__dot' });
      if (a.label) text(svg, X + (flip ? -10 : 10), Y - 10, a.label, { class: 'chart__annotation-label', 'text-anchor': flip ? 'end' : 'start' });
    }
    const cross = el('line', { class: 'chart__crosshair', y1: top, y2: plotB, visibility: 'hidden' }, svg);
    const dots = el('g', {}, svg);
    const rowsAt = (x) => S.filter((s) => x >= s.pts[0].x && x <= lastVal(s).x).map((s) => {
      const p = s.pts[nearestIndex(s.pts, x, (q) => q.x)];
      return { value: yf(p.y), label: s.label, color: s.color, dash: s.dash, y: p.y, x: p.x, shape: s.shape };
    });
    const tipAt = (x) => ({ title: `${o.xLabel ? `${o.xLabel} ` : ''}${xfmt(x)}`, rows: rowsAt(x) });
    const drawCross = (x, kb) => {
      const X = Math.round(xsc(x)) + 0.5;
      cross.setAttribute('x1', X);
      cross.setAttribute('x2', X);
      cross.removeAttribute('visibility');
      cross.classList.toggle('is-focus', !!kb);
      dots.replaceChildren();
      for (const r of rowsAt(x)) marker(dots, r.shape, xsc(r.x), ysc(r.y), 5, r.color, { class: 'chart__dot' });
    };
    const hideCross = () => { cross.setAttribute('visibility', 'hidden'); dots.replaceChildren(); };
    const overlay = el('rect', { class: 'chart__hit', x: left, y: top, width: Math.max(0, W - left - right), height: Math.max(0, plotB - top) }, svg);
    overlay.addEventListener('pointermove', (e) => {
      const at = ctx.pos(e);
      const x = xs[nearestIndex(xs, xsc.invert(at[0]))];
      drawCross(x);
      ctx.showTip(tipAt(x), at);
    });
    overlay.addEventListener('pointerleave', () => { hideCross(); ctx.hideTip(); });
    // ponytail: keyboard stops sampled to <= 200 x positions; pointer still snaps to every x.
    const step = Math.max(1, Math.ceil(xs.length / 200));
    const marks = xs.filter((_, i) => i % step === 0 || i === xs.length - 1).map((x) => {
      const stop = el('rect', { class: 'chart__hit', x: xsc(x) - 0.5, y: top, width: 1, height: Math.max(0, plotB - top), 'pointer-events': 'none' }, svg);
      const tip = tipAt(x);
      return {
        el: stop, noPointer: true, datum: { x }, tip,
        aria: `${tip.title}: ${tip.rows.map((r) => `${r.label} ${r.value}`).join(', ')}`,
        anchor: [xsc(x), ysc(tip.rows[0]?.y ?? yd[1])],
        on: (kb) => drawCross(x, kb), off: hideCross,
      };
    });
    const items = S.length > 1 ? S.map((s) => ({ label: s.label, color: s.color, shape: 'line', dash: s.dash, marker: s.shape })) : [];
    return { marks, legend: items };
  }, tableOf);
}

/* ------------------------------------------------------------------------ */
/* Heat grid (confusion matrix)                                               */
/* ------------------------------------------------------------------------ */

/**
 * Confusion-matrix heat grid: every cell prints the count and its row share; fill = row share on the
 * one-hue sequential ramp (--seq-0..6) with label ink chosen per step for >= 4.5:1.
 * opts: { matrix: number[][], rows: [labels], cols: [labels], rowTitle = 'Ground truth', colTitle = 'Predicted', format = 'int' }.
 */
export function heatGrid(container, opts) {
  const o = { rowTitle: 'Ground truth', colTitle: 'Predicted', format: 'int', ...opts };
  o.data = o.matrix || o.data;
  const f = fmt(o.format);
  const labels = (m) => [o.rows || m.map((_, i) => `Row ${i + 1}`), o.cols || (m[0] || []).map((_, j) => `Column ${j + 1}`)];
  const shareOf = (row, v) => {
    const sum = row.reduce((a, b) => a + (+b || 0), 0);
    return sum ? (+v || 0) / sum : 0;
  };
  const tableOf = (m) => {
    const [R, C] = labels(m);
    return {
      columns: [{ key: 'row', label: `${o.rowTitle} / ${o.colTitle}` }, ...C.map((c, j) => ({ key: j, label: c, numeric: true }))],
      rows: m.map((row, i) => ({ row: R[i], ...Object.fromEntries(row.map((v, j) => [j, `${f(+v || 0)} (${pct(shareOf(row, v), 1)})`])) })),
    };
  };
  return mount(container, o, (ctx, m) => {
    if (!m.length) return ctx.empty();
    const [R, C] = labels(m);
    const grid = document.createElement('div');
    grid.className = 'heat';
    grid.style.gridTemplateColumns = `minmax(min-content, max-content) repeat(${C.length}, minmax(min-content, 1fr))`;
    const cell = (cls, txt, style) => {
      const d = document.createElement('div');
      d.className = cls;
      d.textContent = txt;
      if (style) d.style.cssText = style;
      grid.append(d);
      return d;
    };
    cell('heat__axis-title', o.rowTitle, 'align-self:end');
    cell('heat__axis-title', o.colTitle, 'grid-column:2 / -1;text-align:center');
    cell('heat__corner', '');
    C.forEach((c) => cell('heat__col', c));
    const marks = [];
    m.forEach((row, i) => {
      cell('heat__row', R[i]);
      row.forEach((v, j) => {
        const share = shareOf(row, v);
        const c = document.createElement('div');
        c.className = `heat__cell seq-${Math.min(6, Math.round(share * 6))}`;
        const strong = document.createElement('strong');
        strong.textContent = f(+v || 0);
        const sub = document.createElement('span');
        sub.textContent = pct(share, 1);
        c.append(strong, sub);
        grid.append(c);
        marks.push({
          el: c, datum: { row: i, col: j, rowLabel: R[i], colLabel: C[j], value: +v || 0, share },
          aria: `${o.rowTitle} ${R[i]}, ${o.colTitle} ${C[j]}: ${f(+v || 0)}, ${pct(share, 1)} of row`,
          tip: { title: `${o.rowTitle}: ${R[i]} · ${o.colTitle}: ${C[j]}`, rows: [{ value: f(+v || 0), label: `${pct(share, 1)} of row` }] },
          anchor: () => [c.offsetLeft - grid.scrollLeft + c.offsetWidth / 2, c.offsetTop],
        });
      });
    });
    const scale = document.createElement('div');
    scale.className = 'scale-legend';
    scale.innerHTML = `<span>Row share 0%</span><span class="scale-legend__ramp" aria-hidden="true">${[0, 1, 2, 3, 4, 5, 6].map((n) => `<span class="seq-${n}"></span>`).join('')}</span><span>100%</span>`;
    ctx.stage.append(grid, scale);
    return { marks, grid: C.length };
  }, tableOf);
}

/* ------------------------------------------------------------------------ */
/* Stacked bar                                                               */
/* ------------------------------------------------------------------------ */

/**
 * Horizontal stacked bar(s), 100% (mode 'percent') or absolute. Segments are separated by 2px surface
 * gaps; only the bar's data end is rounded. Inside labels appear only when they fit and clear 4.5:1;
 * a single-row chart lists every segment's value in the legend.
 * opts: { data: rows, x = 'label', series: [{ key, label, color? }], mode, format = 'int', insideLabels = true, rowLabels = true, domain }.
 */
export function stackedBar(container, opts) {
  const o = { x: 'label', mode: 'percent', format: 'int', insideLabels: true, rowLabels: true, ...opts };
  const f = fmt(o.format);
  const S = (o.series || []).map((s, i) => ({ ...s, color: seriesColor(s, i) }));
  const totalOf = (d) => S.reduce((a, s) => a + (finite(d[s.key]) ? +d[s.key] : 0), 0);
  const tableOf = (rows) => ({
    columns: [{ key: o.x, label: 'Row' }, ...S.map((s) => ({ key: s.key, label: s.label, numeric: true, format: (v, r) => (finite(v) ? `${f(+v)} (${pct(+v / (totalOf(r) || 1), 1)})` : '—') })), { key: '_total', label: 'Total', numeric: true, format: (_, r) => f(totalOf(r)) }],
    rows,
  });
  return mount(container, o, (ctx, rows) => {
    const data = rows.filter((d) => totalOf(d) > 0);
    if (!data.length) return ctx.empty();
    const W = ctx.width, thick = 24, rowH = thick + 18, top = 4, axisH = 22;
    const H = top + rowH * data.length + axisH;
    const labelW = o.rowLabels ? Math.min(Math.max(...data.map((d) => measure(String(d[o.x])))) + 12, W * 0.35) : 0;
    const max = o.mode === 'percent' ? 1 : (o.domain?.[1] ?? niceDomain([0, Math.max(...data.map(totalOf))])[1]);
    const x0 = labelW, x1 = W - 4, sc = linear([0, max], [x0, x1]);
    const svg = ctx.svg(H);
    const grid = el('g', { class: 'chart__grid' }, svg);
    const ticks = o.mode === 'percent' ? [0, 0.25, 0.5, 0.75, 1] : sc.ticks(Math.max(2, Math.floor((x1 - x0) / 90)));
    for (const t of ticks) {
      const x = Math.round(sc(t)) + 0.5;
      el('line', { x1: x, x2: x, y1: top, y2: top + rowH * data.length }, grid);
      text(svg, Math.min(Math.max(x, x0 + 12), x1 - 12), H - 6, o.mode === 'percent' ? pct(t, 0) : f(t), { 'text-anchor': 'middle' });
    }
    const marks = [];
    data.forEach((d, r) => {
      const y = top + r * rowH + (rowH - thick) / 2, total = totalOf(d);
      if (o.rowLabels) {
        const lab = text(svg, x0 - 8, y + thick / 2, fit(String(d[o.x]), labelW - 12), { 'text-anchor': 'end', dy: '0.35em' });
        el('title', {}, lab).textContent = String(d[o.x]);
      }
      const segs = S.filter((s) => finite(d[s.key]) && +d[s.key] > 0);
      let acc = 0;
      segs.forEach((s, j) => {
        const v = +d[s.key], share = v / total, len = o.mode === 'percent' ? share : v;
        const xa = sc(acc), last = j === segs.length - 1;
        acc += len;
        const w = Math.max(1, sc(acc) - xa - (last ? 0 : 2));
        const path = el('path', { d: last ? barPath(xa, y, w, thick, 'h') : `M${xa},${y}h${w}v${thick}h${-w}Z`, fill: s.color, class: 'chart__mark' }, svg);
        const lbl = o.mode === 'percent' ? pct(share, 1) : f(v);
        const ink = o.insideLabels ? inkFor(ctx.stage, s.color) : null;
        if (ink && measure(lbl, FONT_BOLD) + 12 <= w) text(svg, xa + w / 2, y + thick / 2, lbl, { 'text-anchor': 'middle', dy: '0.35em', style: `fill:${ink};font-weight:600` });
        marks.push({
          el: path, datum: { row: d, series: s.key, value: v, share },
          aria: `${d[o.x]}, ${s.label}: ${f(v)} (${pct(share, 1)})`,
          tip: { title: String(d[o.x]), rows: [{ value: `${f(v)} (${pct(share, 1)})`, label: s.label, color: s.color }] },
          anchor: [xa + w / 2, y],
        });
      });
    });
    el('line', { class: 'chart__baseline', x1: x0 + 0.5, x2: x0 + 0.5, y1: top, y2: top + rowH * data.length }, svg);
    const single = data.length === 1 ? data[0] : null;
    return {
      marks,
      legend: S.map((s) => ({
        label: s.label, color: s.color,
        value: single && finite(single[s.key]) ? `${f(+single[s.key])} (${pct(+single[s.key] / totalOf(single), 1)})` : null,
      })),
    };
  }, tableOf);
}

/* ------------------------------------------------------------------------ */
/* Small multiples, sparkline                                                */
/* ------------------------------------------------------------------------ */

/**
 * Grid of same-type charts sharing options (pass a shared yDomain so panels compare honestly).
 * opts: { panels: [{ title, data, ...overrides }], chart = lineChart, minWidth = 260, sharedLegend = true, ...sharedOpts }.
 * Returns { update(panels), destroy() }.
 */
export function smallMultiples(container, { panels = [], chart = lineChart, minWidth = 260, sharedLegend = true, ...shared } = {}) {
  container.classList.add('small-multiples');
  container.style.setProperty('--min', `${minWidth}px`);
  let charts = [];
  const isLine = chart === lineChart;
  function build(ps) {
    charts.forEach((c) => c.destroy());
    container.replaceChildren();
    const series = shared.series || [];
    if (sharedLegend && series.length > 1) {
      const lg = document.createElement('div');
      lg.style.gridColumn = '1 / -1';
      legend(lg, series.map((s, i) => ({ label: s.label, color: seriesColor(s, i), shape: isLine ? 'line' : 'rect', dash: isLine ? DASHES[i % 3] : null, marker: isLine ? SHAPES[i % 3] : null })));
      container.append(lg);
    }
    charts = ps.map((p) => {
      const panel = document.createElement('div');
      panel.className = 'small-multiples__panel';
      const title = document.createElement('p');
      title.className = 'small-multiples__title';
      title.textContent = p.title || '';
      const host = document.createElement('div');
      panel.append(title, host);
      container.append(panel);
      return chart(host, { ...shared, ...(sharedLegend ? { legend: false } : {}), ...p, ariaLabel: p.ariaLabel || [shared.ariaLabel, p.title].filter(Boolean).join(': ') });
    });
  }
  build(panels);
  return {
    update: build,
    destroy() {
      charts.forEach((c) => c.destroy());
      container.replaceChildren();
      container.classList.remove('small-multiples');
    },
  };
}

/**
 * Sparkline for stat tiles: de-emphasis line, current (last) point in series slot 1, role=img summary.
 * No tooltip or table by design (the tile's value is the readable number).
 * opts: { data: number[] | rows, y, ariaLabel, format, height = 32 }.
 */
export function sparkline(container, opts) {
  const o = { format: 'num', height: 32, ...opts };
  const f = fmt(o.format);
  const svg = el('svg', { class: 'sparkline', role: 'img' });
  container.replaceChildren(svg);
  let data = o.data || [];
  const draw = () => {
    const W = Math.floor(container.clientWidth) || 120, H = o.height, pad = 5;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('height', H);
    svg.replaceChildren();
    const vals = data.map((d) => (typeof d === 'number' ? d : +d[o.y])).filter(Number.isFinite);
    svg.setAttribute('aria-label', vals.length ? `${o.ariaLabel || 'Trend'}: ${vals.length} points, latest ${f(vals[vals.length - 1])}` : `${o.ariaLabel || 'Trend'}: no data`);
    if (vals.length < 2) return;
    const mn = Math.min(...vals), mx = Math.max(...vals);
    const X = (i) => pad + (i / (vals.length - 1)) * (W - 2 * pad);
    const Y = (v) => H - pad - ((v - mn) / (mx - mn || 1)) * (H - 2 * pad);
    el('path', { d: vals.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(''), class: 'chart__line', stroke: 'var(--chart-crosshair)' }, svg);
    marker(svg, 'circle', X(vals.length - 1), Y(vals[vals.length - 1]), 4, 'var(--series-1)', { class: 'chart__dot' });
  };
  const ro = new ResizeObserver(draw);
  ro.observe(container);
  draw();
  return {
    update(d) { data = d || []; draw(); },
    destroy() { ro.disconnect(); container.replaceChildren(); },
  };
}

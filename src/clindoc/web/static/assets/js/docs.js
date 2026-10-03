/**
 * Docs page: table-of-contents tracking, decision records from GET /api/decisions,
 * and curl examples pointed at the server that serves this page.
 * Authority: HTTP API contract in src/clindoc/web/app.py; components from /assets/js/core.js.
 */
import { mountShell, api, renderError, renderEmpty, escapeHtml, icon, num } from '/assets/js/core.js';

mountShell();

const REPO_BLOB = 'https://github.com/nobody294469/DAA/blob/feature/web-platform/';

// curl examples: use this server's origin instead of the default port.
if (location.protocol.startsWith('http')) {
  document.querySelectorAll('[data-origin]').forEach((el) => { el.textContent = location.origin; });
}

// Table of contents: mark the section currently at the top of the viewport.
const links = new Map([...document.querySelectorAll('.docs-toc a[href^="#"]')].map((a) => [a.hash.slice(1), a]));
if ('IntersectionObserver' in window) {
  const visible = new Set();
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => (e.isIntersecting ? visible.add(e.target.id) : visible.delete(e.target.id)));
    const first = [...links.keys()].find((id) => visible.has(id));
    if (!first) return;
    links.forEach((a, id) => {
      if (id === first) a.setAttribute('aria-current', 'true');
      else a.removeAttribute('aria-current');
    });
  }, { rootMargin: '-72px 0px -55% 0px' });
  links.forEach((_, id) => { const s = document.getElementById(id); if (s) io.observe(s); });
}

// Decision records.
const list = document.getElementById('decisions-list');
const summary = document.getElementById('decisions-summary');
const plain = (s) => String(s ?? '').replace(/\*\*|`/g, '');

function decisionHtml(d) {
  const status = String(d.status || '');
  const locked = /^LOCKED\b/i.test(status);
  const open = !locked && /\bOPEN\b/i.test(status);
  const chip = locked ? '<span class="badge docs-status--locked">Locked</span>'
    : open ? '<span class="badge docs-status--open">Open</span>'
      : `<span class="badge">${escapeHtml(status || 'Unknown')}</span>`;
  const provisional = status.match(/\((.*)\)\s*$/)?.[1];
  const path = String(d.path || '');
  const href = /^docs\/decisions\/[\w.-]+\.md$/.test(path) ? REPO_BLOB + path : '';
  return `<details class="accordion docs-dec">
      <summary><span><span class="mono">${escapeHtml(d.id)}</span><span>${escapeHtml(plain(d.title))}</span></span>${chip}</summary>
      <div class="accordion__body stack stack--sm">
        ${d.decision ? `<p>${escapeHtml(plain(d.decision))}</p>` : ''}
        ${provisional ? `<p class="text-sm"><strong>Status:</strong> ${escapeHtml(plain(provisional))}</p>` : ''}
        ${href ? `<p class="text-sm"><a href="${escapeHtml(href)}">${icon('external-link', 'icon--sm')} Read the full record</a> <span class="muted">(${escapeHtml(path)})</span></p>` : ''}
      </div>
    </details>`;
}

async function loadDecisions() {
  list.innerHTML = '<span class="skeleton skeleton--text"></span><span class="skeleton skeleton--text"></span><span class="skeleton skeleton--text" style="width:70%"></span>';
  list.setAttribute('aria-busy', 'true');
  summary.textContent = '';
  try {
    const decisions = (await api('/api/decisions')).decisions || [];
    if (!decisions.length) {
      renderEmpty(list, { icon: 'book-open', title: 'No decision records found', body: 'The server found no docs/decisions/DEC-*.md files.' });
      return;
    }
    const locked = decisions.filter((d) => /^LOCKED\b/i.test(d.status || '')).length;
    const open = decisions.filter((d) => !/^LOCKED\b/i.test(d.status || '') && /\bOPEN\b/i.test(d.status || '')).length;
    summary.textContent = `${num(decisions.length)} records: ${num(locked)} locked, ${num(open)} open.`;
    list.innerHTML = decisions.map(decisionHtml).join('');
  } catch (err) {
    renderError(list, err, loadDecisions, { title: 'Decision records unavailable' });
  } finally {
    list.removeAttribute('aria-busy');
  }
}
loadDecisions();

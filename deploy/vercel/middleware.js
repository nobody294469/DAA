// Routing Middleware: every page and data file needs the signed session cookie once sign-in is configured
// (CLINDOC_GOOGLE_CLIENT_ID + CLINDOC_SESSION_SECRET). Cookie format: see api/_lib/session.py.
import { next } from '@vercel/functions';

export const config = { matcher: ['/((?!api/|assets/|favicon.svg|login.html).*)'] };

const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');

async function signedIn(cookieHeader, secret) {
  const m = /(?:^|;\s*)clindoc_session=([^;]+)/.exec(cookieHeader || '');
  const parts = m ? m[1].split('.') : [];
  if (parts.length !== 3 || Number(parts[0]) < Date.now() / 1000) return false;
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const want = hex(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(`${parts[0]}.${parts[1]}`)));
  return want.length === parts[2].length && want === parts[2];
}

export default async function middleware(request) {
  const secret = process.env.CLINDOC_SESSION_SECRET;
  if (!process.env.CLINDOC_GOOGLE_CLIENT_ID || !secret) return next();
  if (await signedIn(request.headers.get('cookie'), secret)) return next();
  const url = new URL(request.url);
  return Response.redirect(new URL(`/login.html?next=${encodeURIComponent(url.pathname + url.search)}`, url), 302);
}

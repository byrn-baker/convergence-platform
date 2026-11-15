// This is a finite read adapter. The HUD never forwards a browser URL, header,
// cookie, request body or method to the Convergence service.
import { isIP } from 'node:net';
import { hasOwner, mayBootstrap } from './owner-access.js';
import { parseTarget, projectRead, readOperation, ReadFailure, validSite } from './read-operations.js';

const maxBytes = 1024 * 1024;
const maxPending = 24; // An initial dashboard refresh reads several resources together.
const dnsName = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$/;

function canonicalOrigin(raw) {
  if (typeof raw !== 'string' || !raw || raw.length > 512) return null;
  try {
    const url = new URL(raw);
    return raw === url.origin || raw === url.origin + '/' ? url : null;
  } catch {
    return null;
  }
}

function reviewerOrigin(ctx) {
  const url = canonicalOrigin(ctx.env('CONVERGENCE_REVIEWER_ORIGIN'));
  if (!url || url.protocol !== 'https:' || !dnsName.test(url.hostname) ||
      url.hostname.endsWith('.localhost') || isIP(url.hostname)) return null;
  return url.origin;
}

function backend(ctx) {
  const url = canonicalOrigin(ctx.env('CONVERGENCE_API_URL'));
  const token = ctx.env('CONVERGENCE_HUD_READ_TOKEN');
  const site = ctx.env('CONVERGENCE_SITE');
  if (!validSite(site) || typeof token !== 'string' || !/^[A-Za-z0-9._~+\/=:-]{32,4096}$/.test(token) || !url) return null;
  const localHTTP = url.protocol === 'http:' && ['127.0.0.1', '[::1]'].includes(url.hostname);
  if (!localHTTP && url.protocol !== 'https:') return null;
  return {origin: url.origin, token, site};
}

function sameBackend(first, next) {
  return next && first.origin === next.origin && first.token === next.token && first.site === next.site;
}

async function jsonBody(response) {
  const mediaType = response.headers.get('content-type')?.split(';', 1)[0].trim().toLowerCase();
  if (mediaType !== 'application/json' ||
      Number(response.headers.get('content-length')) > maxBytes || !response.body) {
    await response.body?.cancel();
    throw new ReadFailure(502, 'Convergence returned an invalid response.');
  }
  const chunks = [];
  let size = 0;
  for await (const chunk of response.body) {
    size += chunk.length;
    if (size > maxBytes) throw new ReadFailure(502, 'Convergence response exceeds the display limit.');
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks, size).toString('utf8'));
}

async function fetchRead(config, op, signal) {
  const url = new URL(op.path, config.origin);
  url.search = op.query.toString();
  const response = await fetch(url, {
    method: 'GET', headers: {Authorization: 'Bearer ' + config.token, Accept: 'application/json'},
    redirect: 'error', signal,
  });
  if (!response.ok) {
    await response.body?.cancel();
    if (response.status === 404) throw new ReadFailure(404, 'Convergence record is unavailable.');
    throw new ReadFailure(502, 'Scoped Convergence data is unavailable.');
  }
  const projected = projectRead(op, await jsonBody(response), config.site);
  // Do not return an accidentally echoed credential, even in a free-text field.
  if (JSON.stringify(projected).includes(config.token)) throw new ReadFailure(502, 'Invalid Convergence response.');
  return projected;
}

export function register(app, ctx) {
  let pending = 0;
  app.use('/api/home', async (req, res) => {
    res.set({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'});
    try {
      if (req.method !== 'GET') {
        res.set('Allow', 'GET');
        throw new ReadFailure(405, 'Convergence HUD reads require GET.');
      }
      if (req.headers['transfer-encoding'] !== undefined ||
          (req.headers['content-length'] !== undefined && req.headers['content-length'] !== '0')) {
        throw new ReadFailure(400, 'Unexpected request body.');
      }
      const target = parseTarget(req.originalUrl);
      if (target.resource === 'status') {
        if (target.params.size) throw new ReadFailure(400, 'Invalid status request.');
        return res.json({configured: hasOwner(req, ctx) && !!backend(ctx),
          reviewerOrigin: reviewerOrigin(ctx), localOwnerMode: mayBootstrap(req, ctx)});
      }
      if (!hasOwner(req, ctx)) throw new ReadFailure(403, 'A local NetClaw owner session is required.');
      const config = backend(ctx);
      if (!config) throw new ReadFailure(503, 'Scoped Convergence reads are not configured.');
      const op = readOperation(target, config.site);
      if (op.value) return res.json(op.value);
      if (pending >= maxPending) throw new ReadFailure(429, 'Convergence reads are busy.');

      const stop = new AbortController();
      const cancel = () => stop.abort();
      res.once('close', cancel);
      pending++;
      try {
        const signal = AbortSignal.any([stop.signal, AbortSignal.timeout(10000)]);
        const value = await fetchRead(config, op, signal);
        if (!hasOwner(req, ctx) || !sameBackend(config, backend(ctx))) {
          throw new ReadFailure(403, 'The local owner or site binding has changed.');
        }
        return res.json(value);
      } finally {
        res.removeListener('close', cancel);
        pending--;
      }
    } catch (err) {
      if (res.destroyed || res.headersSent) return;
      if (err instanceof ReadFailure) return res.status(err.status).json({error: err.message});
      return res.status(502).json({error: 'Scoped Convergence data is unavailable.'});
    }
  });
}

import https from 'node:https';
import {Admission, cookieName, failure, ReviewerSessions, tokenPattern} from './sessions.mjs';

const uuid = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
const casePath = new RegExp('^/review/sites/([A-Za-z0-9_.-]{1,128})/cases/(' + uuid +
  ')(?:/(disposition)(?:/(' + uuid + '))?)?$', 'i');
const errors = {
  400: 'Invalid request', 401: 'Sign-in required or credentials invalid', 403: 'Access denied',
  404: 'Not found', 409: 'Request conflicts with current state', 413: 'Request too large',
  415: 'Expected JSON', 429: 'Too many requests', 503: 'Reviewer service unavailable',
};

function readCookie(req) {
  let selected;
  for (const pair of (req.headers.cookie || '').split(';')) {
    const item = pair.trim();
    if (!item.startsWith(cookieName + '=')) continue;
    const value = item.slice(cookieName.length + 1);
    if (selected !== undefined || !tokenPattern.test(value)) throw failure(400);
    selected = value;
  }
  return selected;
}

function originGate(req, origin) {
  // Duplicate headers can be combined or silently discarded by HTTP parsers.
  for (const values of Object.values(req.headersDistinct)) {
    if (values.length !== 1) throw failure(400);
  }
  if (req.headers.host !== origin.host || (req.headers.origin !== undefined && req.headers.origin !== origin.origin)) {
    throw failure(403);
  }
  if (req.headers['sec-fetch-site'] === 'cross-site' || req.headers.authorization !== undefined ||
      req.headers.forwarded !== undefined || Object.keys(req.headers).some(name => name.startsWith('x-forwarded-'))) {
    throw failure(403);
  }
  if (req.method === 'POST' && req.headers.origin !== origin.origin) throw failure(403);
  if (req.method === 'GET' && (req.headers['transfer-encoding'] || Number(req.headers['content-length'] || 0))) {
    throw failure(400);
  }
}

function cookie(res, value, seconds) {
  res.setHeader('Set-Cookie', cookieName + '=' + value + '; Path=/; Max-Age=' + seconds + '; Secure; HttpOnly; SameSite=Strict');
}

function sessionReply(entry) {
  return entry.grant ? {user: entry.user, csrf: entry.csrf, expiresAt: new Date(Math.min(entry.idleUntil,entry.expires)).toISOString()}
    : {user: null, csrf: entry.csrf};
}

function json(res, status, value) {
  if (res.destroyed || res.writableEnded) return;
  const body = value === undefined ? undefined : JSON.stringify(value);
  res.writeHead(status, {'Content-Type': 'application/json'});
  res.end(body);
}

async function readJSON(req, keys) {
  const limit = 32 * 1024;
  if (!/^application\/json(?:\s*;\s*charset=utf-8)?$/i.test(req.headers['content-type'] || '') ||
      req.headers['content-encoding'] !== undefined) throw failure(415);
  if (Number(req.headers['content-length']) > limit) throw failure(413);
  const chunks = [];
  let size = 0;
  for await (const chunk of req.iterator({destroyOnReturn: false})) {
    size += chunk.length;
    if (size > limit) throw failure(413);
    chunks.push(chunk);
  }
  let data;
  try { data = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(Buffer.concat(chunks))); }
  catch { throw failure(400); }
  if (!data || typeof data !== 'object' || Array.isArray(data) || Object.keys(data).length !== keys.length ||
      !keys.every(key => Object.hasOwn(data,key) && typeof data[key] === 'string')) throw failure(400);
  return data;
}

function identity(value) {
  if (typeof value?.id !== 'string' || !value.id || typeof value.name !== 'string' ||
      !['viewer','operator','admin'].includes(value.role) || !Array.isArray(value.sites) ||
      !value.sites.every(site => typeof site === 'string' && site.length > 0)) throw failure(503);
  return {id:value.id, name:value.name, role:value.role, sites:value.sites};
}

export function createReviewerServer({tls, origin, client, assets}) {
  const url = new URL(origin);
  if (url.protocol !== 'https:' || url.origin !== origin) throw new Error('Expected a canonical HTTPS reviewer origin');
  if (!(assets instanceof Map)) throw new Error('Expected a static reviewer asset map');
  for (const path of assets.keys()) {
    if (typeof path !== 'string' || !/^\/(?:[A-Za-z0-9/_.-]*)$/.test(path) ||
        path.split('/').some(segment => segment === '.' || segment === '..') ||
        /^\/(?:review|api)(?:\/|$)/i.test(path)) throw new Error('Invalid static reviewer asset path');
  }

  const sessions = new ReviewerSessions();
  const bootstrap = new Admission({total:240, perAddress:30});
  const login = new Admission({total:120, perAddress:20});
  let activeLogins = 0;
  let closing = false;

  async function current(entry, wrote = false) {
    sessions.requireCurrent(entry);
    if (!entry.grant) throw failure(401);
    try {
      const snapshot = await client.current(entry.grant);
      const user = identity(snapshot);
      sessions.updateDeadline(entry, snapshot, wrote);
      if (user.id !== entry.user.id) throw failure(401);
      entry.user = user;
      return user;
    } catch (error) {
      if (error.status === 401) sessions.remove(entry);
      throw error;
    }
  }

  async function signIn(req, res, entry) {
    sessions.checkCsrf(entry, req.headers['x-review-csrf']);
    if (entry.grant || entry.pending) throw failure(409);
    login.take(req.socket.remoteAddress);
    if (activeLogins >= 8) throw failure(429);
    activeLogins += 1;
    entry.pending = true;
    let grant;
    try {
      const credentials = await readJSON(req,['username','password']);
      sessions.requireCurrent(entry);
      grant = await client.issue(credentials);
      if (!tokenPattern.test(grant?.token || '') || typeof grant.token !== 'string') throw failure(503);
      const snapshot = await client.current(grant.token);
      const user = identity(snapshot);
      if (res.destroyed || user.id !== grant.id) throw failure(401);
      const authenticated = sessions.authenticated(entry, grant, user, snapshot);
      cookie(res, authenticated.id, Math.ceil((authenticated.expires - Date.now()) / 1000));
      json(res, 200, sessionReply(authenticated));
      grant = null;
    } finally {
      entry.pending = false;
      activeLogins -= 1;
      if (typeof grant?.token === 'string') await client.revoke(grant.token).catch(() => {});
    }
  }

  const server = https.createServer({ ...tls, minVersion:'TLSv1.2', maxHeaderSize:8192,
    requestTimeout:15000, headersTimeout:10000 }, async (req,res) => {
    // All documents, JSON, and errors use the same reviewer-origin policy.
    res.setHeader('Cache-Control','no-store');
    res.setHeader('X-Content-Type-Options','nosniff');
    res.setHeader('Referrer-Policy','no-referrer');
    res.setHeader('Strict-Transport-Security','max-age=31536000');
    res.setHeader('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'");
    try {
      if (closing) throw failure(503);
      originGate(req,url);
      const id = readCookie(req);
      const {method, url: target} = req;
      if (method === 'GET' && assets.has(target)) {
        const {type,content} = assets.get(target);
        res.writeHead(200, {'Content-Type':type});
        return res.end(content);
      }

      let entry = sessions.get(id);
      if (method === 'GET' && target === '/review/session') {
        if (!entry) {
          bootstrap.take(req.socket.remoteAddress);
          entry = sessions.bootstrap();
          cookie(res, entry.id, 300);
        }
        if (entry.grant) await current(entry);
        return json(res,200,sessionReply(entry));
      }
      if (method === 'POST' && target === '/review/sign-in') return await signIn(req,res,entry);
      if (method === 'POST' && target === '/review/sign-out') {
        sessions.checkCsrf(entry,req.headers['x-review-csrf']);
        await readJSON(req,[]);
        sessions.requireCurrent(entry);
        sessions.remove(entry);
        cookie(res,'',0);
        if (entry.grant) await client.revoke(entry.grant);
        return json(res,204);
      }

      const match = casePath.exec(target);
      if (!match) throw failure(404);
      const [,site,caseId,disposition,operationId] = match;
      if (site === '.' || site === '..') throw failure(400);
      const write = method === 'POST' && Boolean(disposition) && !operationId;
      const read = method === 'GET' && (!disposition || Boolean(operationId));
      if (!write && !read) throw failure(404);
      sessions.requireCurrent(entry);
      if (!entry.grant) throw failure(401);
      if (write) sessions.checkCsrf(entry,req.headers['x-review-csrf']);
      const data = write ? await readJSON(req,['kind','reason','operationId']) : null;
      sessions.requireCurrent(entry);
      // The service atomically authorizes the case access or mutation. It also
      // owns idempotency and reconciliation via the original operation ID.
      let result;
      if (write) result = await client.recordDisposition(entry.grant,site,caseId,data);
      else if (operationId) result = await client.readReceipt(entry.grant,site,caseId,operationId);
      else result = await client.readCase(entry.grant,site,caseId);
      if (res.destroyed) return;
      const user = await current(entry, write);
      if (!user.sites.includes(site) || (write && user.role === 'viewer')) throw failure(403);
      // The service authorizes its data source. Check the response identity
      // independently before publishing any case contents on this origin.
      const requestedOperation = write ? data.operationId : operationId;
      const resultCaseId = disposition ? result?.case_id : result?.id;
      if (result?.site_id !== site || resultCaseId !== caseId.toLowerCase()) throw failure(503);
      if (disposition && (result?.operation_id !== requestedOperation.toLowerCase() ||
          result?.reviewed_by !== user.id)) throw failure(503);
      res.setHeader('X-Review-Expires-At',new Date(Math.min(entry.idleUntil,entry.expires)).toISOString());
      return json(res,200,result);
    } catch (error) {
      const status = Object.hasOwn(errors,error?.status) ? error.status : 503;
      if (!req.complete && !res.headersSent) res.setHeader('Connection','close');
      json(res,status,{error:errors[status]});
    }
  });
  server.maxConnections = 64;
  server.setTimeout(20000, socket => socket.destroy());
  server.on('upgrade', (_req,socket) => socket.destroy());
  const drained = Promise.withResolvers();
  server.grantsDrained = drained.promise;
  const closeListener = server.close.bind(server);
  server.close = callback => {
    if (!closing) {
      closing = true;
      // Invalidate every local handle before yielding, including pending
      // sign-ins. Limit parallel revocations and the shutdown wait; the
      // backend's own idle and absolute expiry cover unreached credentials.
      const tokens = sessions.close();
      let stopped = false;
      const revoke = async () => {
        while (!stopped && tokens.length) {
          const token = tokens.pop();
          try { await client.revoke(token); } catch { /* best effort */ }
        }
      };
      const deadline = setTimeout(() => { stopped = true; drained.resolve(); }, 10_000);
      deadline.unref();
      Promise.all(Array.from({length:Math.min(8,tokens.length)},revoke)).finally(() => {
        clearTimeout(deadline);
        drained.resolve();
      });
    }
    return closeListener(callback);
  };
  return server;
}

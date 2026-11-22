import https from 'node:https';
import {parseTelemetryTarget} from './telemetry-contract.mjs';
import {OidcTransactions, transactionLifetimeMs} from './oidc.mjs';
import {Admission, cookieName, failure, ReviewerSessions, tokenPattern} from './sessions.mjs';

// The sign-in attempt cookie returns on the provider's redirect, so it is Lax. It
// names server-side state only, and the session cookie stays Strict.
const attemptCookie = '__Host-convergence-oidc';

const uuid = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
const casePath = new RegExp('^/review/sites/([A-Za-z0-9_.-]{1,128})/cases/(' + uuid +
  ')(?:/(disposition|investigation|jev-review)(?:/(' + uuid + '))?)?$', 'i');
const errors = {
  400: 'Invalid request', 401: 'Sign-in required or credentials invalid', 403: 'Access denied',
  404: 'Not found', 409: 'Request conflicts with current state', 413: 'Request too large',
  415: 'Expected JSON', 429: 'Too many requests', 503: 'Reviewer service unavailable',
};

function readCookie(req, name = cookieName) {
  let selected;
  for (const pair of (req.headers.cookie || '').split(';')) {
    const item = pair.trim();
    if (!item.startsWith(name + '=')) continue;
    const value = item.slice(name.length + 1);
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
  // A link from another site must be able to open the public sign-in document.
  // This exception cannot bootstrap a session, fetch data, submit or frame it.
  // The identity provider's redirect is the other cross-site navigation. Its
  // handler accepts nothing without this browser's own sign-in attempt.
  const publicNavigation = req.method === 'GET' && (req.url === '/' || req.url.startsWith('/oidc/callback?')) &&
    req.headers['sec-fetch-mode'] === 'navigate' && req.headers['sec-fetch-dest'] === 'document';
  if ((req.headers['sec-fetch-site'] === 'cross-site' && !publicNavigation) || req.headers.authorization !== undefined ||
      req.headers.forwarded !== undefined || Object.keys(req.headers).some(name => name.startsWith('x-forwarded-'))) {
    throw failure(403);
  }
  if (req.method === 'POST' && req.headers.origin !== origin.origin) throw failure(403);
  if (req.method === 'GET' && (req.headers['transfer-encoding'] || Number(req.headers['content-length'] || 0))) {
    throw failure(400);
  }
}

function cookie(res, value, seconds, name = cookieName, sameSite = 'Strict') {
  res.appendHeader('Set-Cookie', name + '=' + value + '; Path=/; Max-Age=' + seconds + '; Secure; HttpOnly; SameSite=' + sameSite);
}

function sessionReply(entry, signIn) {
  return entry.grant ? {user: entry.user, csrf: entry.csrf, capabilities:{telemetry:entry.capabilities?.telemetry === true}, expiresAt: new Date(Math.min(entry.idleUntil,entry.expires)).toISOString()}
    : {user: null, csrf: entry.csrf, signIn};
}

const signInFailures = {
  403: 'You are signed in to the identity provider, but that identity is not linked to a Convergence account. Ask an administrator to link it.',
  429: 'Too many sign-in attempts. Try again later.',
  503: 'Sign-in is unavailable because the identity provider or the reviewer service could not be reached.',
};

// The callback is a navigation, so its failures are a document, not JSON.
function signInFailed(res, status) {
  if (res.destroyed || res.writableEnded) return;
  const shown = [403, 429, 503].includes(status) ? status : 401;
  res.writeHead(shown, {'Content-Type': 'text/html; charset=utf-8'});
  res.end('<!doctype html><html lang="en"><meta charset="utf-8"><title>Sign-in failed</title>' +
    '<link rel="stylesheet" href="/reviewer.css"><main><h1>Sign-in failed</h1><p>' +
    (signInFailures[shown] || 'The sign-in could not be completed.') + '</p><p><a href="/">Return to Convergence</a></p></main></html>');
}

function redirect(res, location) {
  res.writeHead(303, {Location: location});
  res.end();
}

function callbackParameters(target) {
  const query = new URLSearchParams(target.slice('/oidc/callback?'.length));
  const allowed = ['code', 'state', 'session_state', 'iss', 'error', 'error_description'];
  const values = {};
  for (const [name, value] of query) {
    if (!allowed.includes(name) || Object.hasOwn(values, name) || value.length > 2048) throw failure(400);
    values[name] = value;
  }
  return values;
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

// passwordSignIn.addresses, when set, lists the only browser addresses that may
// use password sign-in. With an identity provider that is the break-glass path.
export function createReviewerServer({tls, origin, client, assets, oidc,
  passwordSignIn = {enabled: true, addresses: null}, audit = record => console.log(JSON.stringify(record))}) {
  const url = new URL(origin);
  if (url.protocol !== 'https:' || url.origin !== origin) throw new Error('Expected a canonical HTTPS reviewer origin');
  if (!(assets instanceof Map)) throw new Error('Expected a static reviewer asset map');
  for (const path of assets.keys()) {
    if (typeof path !== 'string' || !/^\/(?:[A-Za-z0-9/_.-]*)$/.test(path) ||
        path.split('/').some(segment => segment === '.' || segment === '..') ||
        /^\/(?:review|api)(?:\/|$)/i.test(path)) throw new Error('Invalid static reviewer asset path');
  }

  if (!oidc && !passwordSignIn.enabled) throw new Error('No sign-in method is enabled');
  if (oidc && passwordSignIn.enabled && !passwordSignIn.addresses?.size) {
    throw new Error('Password sign-in beside an identity provider needs an address list');
  }
  const sessions = new ReviewerSessions();
  const attempts = new OidcTransactions();
  const address = req => (req.socket.remoteAddress || '').replace(/^::ffff:/, '');
  const passwordAllowed = req => passwordSignIn.enabled &&
    (!passwordSignIn.addresses || passwordSignIn.addresses.has(address(req)));
  const signInMethods = req => ({oidc: Boolean(oidc), password: passwordAllowed(req)});
  const bootstrap = new Admission({total:240, perAddress:30});
  const login = new Admission({total:120, perAddress:20});
  const callbacks = new Admission({total:240, perAddress:30});
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
      entry.capabilities = {telemetry:snapshot.capabilities?.telemetry === true};
      return user;
    } catch (error) {
      if (error.status === 401) sessions.remove(entry);
      throw error;
    }
  }

  // Shared by both sign-in methods: the service's own view of the new grant
  // must name the same account before a session exists.
  async function establish(res, entry, grant) {
    if (!tokenPattern.test(grant?.token || '') || typeof grant.token !== 'string') throw failure(503);
    const snapshot = await client.current(grant.token);
    const user = identity(snapshot);
    if (res.destroyed || user.id !== grant.id) throw failure(401);
    const authenticated = sessions.authenticated(entry, grant, user, snapshot);
    authenticated.capabilities = {telemetry:snapshot.capabilities?.telemetry === true};
    cookie(res, authenticated.id, Math.ceil((authenticated.expires - Date.now()) / 1000));
    return authenticated;
  }

  async function signIn(req, res, entry) {
    if (!passwordSignIn.enabled) throw failure(404);
    if (!passwordAllowed(req)) {
      audit({event: 'reviewer-password-sign-in', address: address(req), outcome: 'refused-address', at: new Date().toISOString()});
      throw failure(403);
    }
    sessions.checkCsrf(entry, req.headers['x-review-csrf']);
    if (entry.grant || entry.pending) throw failure(409);
    login.take(req.socket.remoteAddress);
    if (activeLogins >= 8) throw failure(429);
    activeLogins += 1;
    entry.pending = true;
    let grant;
    // Beside an identity provider every password attempt is an audited exception.
    const record = oidc ? {event: 'reviewer-password-sign-in', address: address(req), outcome: 'rejected'} : null;
    try {
      const credentials = await readJSON(req,['username','password']);
      if (record) record.username = credentials.username.slice(0, 128);
      sessions.requireCurrent(entry);
      grant = await client.issue(credentials);
      const authenticated = await establish(res, entry, grant);
      if (record) Object.assign(record, {outcome: 'accepted', account: authenticated.user.id});
      json(res, 200, sessionReply(authenticated));
      grant = null;
    } finally {
      entry.pending = false;
      activeLogins -= 1;
      if (record) audit({...record, at: new Date().toISOString()});
      if (typeof grant?.token === 'string') await client.revoke(grant.token).catch(() => {});
    }
  }

  async function startOidc(req, res, entry) {
    bootstrap.take(req.socket.remoteAddress);
    // No attempt is held while the provider cannot be reached.
    await oidc.ready();
    const attempt = attempts.begin(address(req));
    // The Strict session cookie may not return with the provider's redirect.
    attempt.previous = entry;
    let location;
    try { location = await oidc.authorizationUrl(attempt); }
    catch (error) { attempts.discard(attempt); throw error; }
    cookie(res, attempt.id, transactionLifetimeMs / 1000, attemptCookie, 'Lax');
    redirect(res, location);
  }

  async function finishOidc(req, res, current) {
    callbacks.take(req.socket.remoteAddress);
    const handle = readCookie(req, attemptCookie);
    const parameters = callbackParameters(req.url);
    // Spend the attempt before anything else, so a replayed or forged callback
    // cannot reach the provider or the reviewer service. Only a genuine callback
    // counts against the sign-in limit that password sign-in shares.
    const attempt = attempts.take(handle, parameters.state);
    cookie(res, '', 0, attemptCookie, 'Lax');
    login.take(req.socket.remoteAddress);
    const entry = current || attempt.previous;
    if (parameters.iss !== undefined && parameters.iss !== oidc.issuer) throw failure(400);
    if (parameters.error !== undefined || !parameters.code) throw failure(401);
    if (activeLogins >= 8) throw failure(429);
    activeLogins += 1;
    let grant;
    try {
      const idToken = await oidc.redeem(parameters.code, attempt.verifier);
      grant = await client.issueOidc({idToken, nonce: attempt.nonce});
      // A second sign-in in the same browser replaces the first session.
      if (entry && sessions.get(entry.id) === entry) {
        sessions.remove(entry);
        if (entry.grant) await client.revoke(entry.grant).catch(() => {});
      }
      const authenticated = await establish(res, sessions.bootstrap(), grant);
      authenticated.idToken = idToken;
      grant = null;
      redirect(res, '/');
    } finally {
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
    res.setHeader('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'");
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
      if (oidc && method === 'GET' && (target === '/oidc/login' || target.startsWith('/oidc/callback?'))) {
        try {
          return await (target === '/oidc/login' ? startOidc(req,res,entry) : finishOidc(req,res,entry));
        } catch (error) {
          return signInFailed(res,error?.status);
        }
      }
      if (method === 'GET' && target === '/review/session') {
        if (!entry) {
          bootstrap.take(req.socket.remoteAddress);
          entry = sessions.bootstrap();
          cookie(res, entry.id, 300);
        }
        if (entry.grant) await current(entry);
        return json(res,200,sessionReply(entry,signInMethods(req)));
      }
      if (method === 'POST' && target === '/review/sign-in') return await signIn(req,res,entry);
      if (method === 'POST' && target === '/review/sign-out') {
        sessions.checkCsrf(entry,req.headers['x-review-csrf']);
        await readJSON(req,[]);
        sessions.requireCurrent(entry);
        sessions.remove(entry);
        cookie(res,'',0);
        if (entry.grant) await client.revoke(entry.grant);
        // The page follows this to end the provider session as well. Without it
        // the next visit would sign straight back in.
        const location = oidc && entry.idToken ? await oidc.endSessionUrl(entry.idToken) : null;
        return location ? json(res,200,{location}) : json(res,204);
      }

      const telemetry = parseTelemetryTarget(target);
      if (telemetry) {
        if (method !== 'GET') throw failure(404);
        const user = await current(entry);
        if (!entry.capabilities.telemetry || !user.sites.includes(telemetry.site)) throw failure(403);
        const grant = entry.grant;
        const result = await client.readTelemetry(grant, telemetry.site, telemetry.resource, telemetry.query);
        const after = await current(entry);
        if (!entry.capabilities.telemetry || entry.grant !== grant || after.id !== user.id || !after.sites.includes(telemetry.site)) throw failure(403);
        const singleEvent = /^events\/[0-9a-f-]{36}$/i.test(telemetry.resource);
        if (!result || typeof result !== 'object' || Array.isArray(result) ||
            (telemetry.resource !== 'isp/providers' && result[singleEvent ? 'site_id' : 'site'] !== telemetry.site) ||
            (result.site !== undefined && result.site !== telemetry.site) ||
            (result.site_id !== undefined && result.site_id !== telemetry.site)) throw failure(503);
        res.setHeader('X-Review-Expires-At',new Date(Math.min(entry.idleUntil,entry.expires)).toISOString());
        return json(res,200,result);
      }

      const match = casePath.exec(target);
      if (!match) throw failure(404);
      const [,site,caseId,operation,operationId] = match;
      const commands = {
        disposition:{fields:['kind','reason','operationId'],write:'recordDisposition',receipt:'readReceipt',actor:'reviewed_by'},
        investigation:{fields:['feedback','operationId'],write:'requestInvestigation',receipt:'readInvestigationReceipt',actor:'requested_by'},
        'jev-review':{fields:['operationId'],write:'requestJevReview',receipt:'readJevReceipt',actor:'requested_by'},
      };
      const command = commands[operation];
      if (site === '.' || site === '..') throw failure(400);
      const write = method === 'POST' && Boolean(operation) && !operationId;
      const read = method === 'GET' && (!operation || Boolean(operationId));
      if (!write && !read) throw failure(404);
      sessions.requireCurrent(entry);
      if (!entry.grant) throw failure(401);
      if (write) sessions.checkCsrf(entry,req.headers['x-review-csrf']);
      const data = write ? await readJSON(req,command.fields) : null;
      sessions.requireCurrent(entry);
      // The service atomically authorizes the case access or mutation. It also
      // owns idempotency and reconciliation via the original operation ID.
      let result;
      if (write) result = await client[command.write](entry.grant,site,caseId,data);
      else if (operationId) result = await client[command.receipt](entry.grant,site,caseId,operationId);
      else result = await client.readCase(entry.grant,site,caseId);
      if (res.destroyed) return;
      const user = await current(entry, write);
      if (!user.sites.includes(site) || (write && user.role === 'viewer')) throw failure(403);
      // The service authorizes its data source. Check the response identity
      // independently before publishing any case contents on this origin.
      const requestedOperation = write ? data.operationId : operationId;
      const resultCaseId = operation ? result?.case_id : result?.id;
      if (result?.site_id !== site || resultCaseId !== caseId.toLowerCase()) throw failure(503);
      if (operation && (result?.operation_id !== requestedOperation.toLowerCase() ||
          result?.[command.actor] !== user.id)) throw failure(503);
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

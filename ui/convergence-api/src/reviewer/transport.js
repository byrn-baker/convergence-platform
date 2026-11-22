'use strict';

const https = require('node:https');
const { readCase, readDispositionReceipt, recordDisposition } = require('./cases');
const {requestInvestigation, readInvestigationReceipt} = require('./investigations');
const {requestJevReview,readJevReceipt}=require('./jev');
const {parseTelemetryTarget} = require('./telemetry-contract');
const {listSignatures, parseSignatureQuery} = require('./queue');

const signaturesPath = /^\/v1\/sites\/([a-zA-Z0-9_.-]{1,128})\/signatures(?:\?(.*))?$/;
const casePath = /^\/v1\/sites\/([a-zA-Z0-9_.-]{1,128})\/cases\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:\/(disposition|investigation|jev-review)(?:\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}))?)?$/i;
const messages = {
  400: 'Invalid request', 401: 'Invalid credentials or grant', 403: 'Access denied',
  404: 'Not found', 409: 'Conflicting operation', 413: 'Request too large',
  415: 'Expected JSON', 429: 'Too many requests', 503: 'Reviewer service unavailable',
};

function reject(status) {
  throw Object.assign(new Error(messages[status]), {status});
}

function reply(res, status, data) {
  if (res.destroyed) return;
  res.writeHead(status, {
    'Content-Type': 'application/json', 'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  });
  res.end(data === undefined ? undefined : JSON.stringify(data));
}

function parseEnrollments(records) {
  if (!Array.isArray(records) || !records.length) throw new Error('Expected enrolled installation certificates');
  const certs = new Map();
  for (const item of records) {
    if (typeof item?.installation !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(item.installation) ||
        typeof item.fingerprint256 !== 'string' || !/^(?:[a-f0-9]{2}:){31}[a-f0-9]{2}$/i.test(item.fingerprint256)) {
      throw new Error('Invalid reviewer installation enrollment');
    }
    const pin = item.fingerprint256.toUpperCase();
    if (certs.has(pin)) throw new Error('Duplicate reviewer certificate enrollment');
    certs.set(pin, item.installation);
  }
  return certs;
}

async function readJSON(req, fields) {
  if (!/^application\/json(?:\s*;\s*charset=utf-8)?$/i.test(req.headers['content-type'] || '') ||
      req.headers['content-encoding']) reject(415);
  const limit = 24 * 1024;
  if (Number(req.headers['content-length']) > limit) reject(413);
  const chunks = [];
  let size = 0;
  for await (const chunk of req.iterator({destroyOnReturn: false})) {
    size += chunk.length;
    if (size > limit) reject(413);
    chunks.push(chunk);
  }
  let data;
  try {
    data = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(Buffer.concat(chunks)));
  } catch {
    reject(400);
  }
  if (!data || typeof data !== 'object' || Array.isArray(data) ||
      Object.keys(data).length !== fields.length || !fields.every(key => Object.hasOwn(data, key))) reject(400);
  return data;
}

function bearer(req) {
  const values = req.headersDistinct.authorization;
  if (values?.length !== 1) reject(401);
  const match = /^Bearer ([a-zA-Z0-9_-]{43})$/.exec(values[0]);
  if (!match) reject(401);
  return match[1];
}

function createReviewerService({tls, enrollments, authority, investigationEnabled = false, jevEnabled = false, telemetry, telemetryTimeoutMs = 18000}) {
  if (!Number.isInteger(telemetryTimeoutMs) || telemetryTimeoutMs < 1 || telemetryTimeoutMs > 18000) throw new Error('Invalid telemetry deadline');
  let activeTelemetry = 0;
  let installations = parseEnrollments(enrollments);
  const server = https.createServer({
    ...tls, requestCert: true, rejectUnauthorized: true, minVersion: 'TLSv1.2',
    maxHeaderSize: 8192, requestTimeout: 15000, headersTimeout: 10000,
  }, async (req, res) => {
    try {
      const installation = installations.get(req.socket.getPeerCertificate()?.fingerprint256);
      if (!req.socket.authorized || !installation) reject(403);
      if (req.headers.origin !== undefined || req.headers.cookie !== undefined) reject(403);
      if (req.method === 'POST' && req.url === '/v1/grants') {
        if (req.headers.authorization !== undefined) reject(400);
        const credentials = await readJSON(req, ['username', 'password']);
        const grant = await authority.issue(installation, credentials);
        return reply(res, 201, grant);
      }
      if (req.method === 'POST' && req.url === '/v1/grants/oidc') {
        if (req.headers.authorization !== undefined) reject(400);
        const exchange = await readJSON(req, ['idToken', 'nonce']);
        return reply(res, 201, await authority.issueOidc(installation, exchange));
      }
      if (req.url.startsWith('/v1/sites/') && req.url.includes('/telemetry/')) {
        if (req.method !== 'GET' || typeof telemetry !== 'function') reject(404);
        if (req.headers['transfer-encoding'] || Number(req.headers['content-length'] || 0) !== 0) reject(400);
        const op = parseTelemetryTarget(req.url);
        const token = bearer(req);
        const principal = (_client, actor) => ({id:actor.id,role:actor.role,sites:[...actor.sites].sort()});
        // Each authorization transaction finishes before external acquisition starts.
        const before = await authority.withGrant(installation,token,{site:op.site,write:false},principal);
        if (activeTelemetry >= 24) reject(429);
        const stop = new AbortController();
        const cancel = () => stop.abort();
        const timeout = setTimeout(cancel,telemetryTimeoutMs);
        res.once('close',cancel);
        activeTelemetry++;
        try {
          const data = await new Promise((resolve,rejectRead)=>{
            const aborted = () => rejectRead(Object.assign(new Error('Telemetry deadline'),{status:503}));
            stop.signal.addEventListener('abort',aborted,{once:true});
            Promise.resolve().then(()=>telemetry(op.site,op.resource,op.query,{signal:stop.signal}))
              .then(resolve,rejectRead).finally(()=>{activeTelemetry--;stop.signal.removeEventListener('abort',aborted);});
          });
          const after = await authority.withGrant(installation,token,{site:op.site,write:false},principal);
          if (stop.signal.aborted || JSON.stringify(before)!==JSON.stringify(after) ||
              installations.get(req.socket.getPeerCertificate()?.fingerprint256)!==installation) reject(403);
          return reply(res,200,data);
        } finally {
          clearTimeout(timeout);
          res.removeListener('close',cancel);
        }
      }
      const queue = signaturesPath.exec(req.url);
      if (queue) {
        if (req.method !== 'GET') reject(404);
        if (req.headers['transfer-encoding'] || Number(req.headers['content-length'] || 0) !== 0) reject(400);
        const query = parseSignatureQuery(queue[2]);
        return reply(res, 200, await authority.withGrant(installation, bearer(req), {site: queue[1], write: false},
          (client, actor) => listSignatures(client, actor, queue[1], query)));
      }
      const match = casePath.exec(req.url);
      const status = req.method === 'GET' && req.url === '/v1/grants/current';
      const logout = req.method === 'DELETE' && req.url === '/v1/grants/current';
      const read = match && !match[3] && req.method === 'GET';
      const write = match && match[3] && !match[4] && req.method === 'POST';
      const receipt = match && match[4] && req.method === 'GET';
      if (!status && !logout && !read && !write && !receipt) reject(404);
      if (!write && (req.headers['transfer-encoding'] || Number(req.headers['content-length'] || 0) !== 0)) reject(400);
      const token = bearer(req);
      if (logout) {
        await authority.revoke(installation, token);
        return reply(res, 204);
      }
      const site = match?.[1] ?? null;
      const investigation = match?.[3] === 'investigation';
      const jev = match?.[3] === 'jev-review';
      if(write && jev && !jevEnabled)reject(503);
      if (write && investigation && !investigationEnabled) reject(503);
      const input = write ? await readJSON(req, jev ? ['operationId'] : investigation ? ['operationId','feedback'] : ['operationId', 'kind', 'reason']) : undefined;
      const data = await authority.withGrant(installation, token, {site, write: Boolean(write)}, (client, actor) => {
        if (status) return {id: actor.id, name: actor.name, role: actor.role, sites: actor.sites,
          expiresAt: actor.expiresAt, idleExpiresAt: actor.idleExpiresAt,capabilities:{telemetry:typeof telemetry==='function'}};
        if (receipt) return (jev ? readJevReceipt : investigation ? readInvestigationReceipt : readDispositionReceipt)(client, actor, site, match[2], match[4]);
        if (write) return (jev ? requestJevReview : investigation ? requestInvestigation : recordDisposition)(client, actor, site, match[2], input);
        return readCase(client, actor, site, match[2]).then(record => ({...record, investigation_enabled: investigationEnabled,jev_enabled:jevEnabled}));
      });
      return reply(res, 200, data);
    } catch (error) {
      const status = Object.hasOwn(messages, error?.status) ? error.status : 503;
      if (!req.complete) res.setHeader('Connection', 'close');
      reply(res, status, {error: messages[status]});
    }
  });
  server.maxConnections = 64;
  server.setTimeout(20000, socket => socket.destroy());
  server.on('upgrade', (_req, socket) => socket.destroy());
  // Renewed identity, client CA and enrollments apply together, after all validate. Several
  // pins per installation let an installation certificate rotate without a 403 window.
  server.reload = next => {
    const pins = parseEnrollments(next.enrollments);
    server.setSecureContext({ca: next.ca, cert: next.cert, key: next.key, minVersion: 'TLSv1.2'});
    installations = pins;
  };
  return server;
}

module.exports = {createReviewerService};

'use strict';

const {createHash, createPublicKey, timingSafeEqual, verify: verifySignature} = require('node:crypto');

const segment = '[A-Za-z0-9_-]+';
const compact = new RegExp('^(' + segment + ')\\.(' + segment + ')\\.(' + segment + ')$');
const keyLifetimeMs = 10 * 60_000;
const refetchIntervalMs = 30_000;

function failure(status, message) {
  return Object.assign(new Error(message), {status});
}
const rejected = () => failure(401, 'Invalid identity token');
const unavailable = () => failure(503, 'Identity provider unavailable');

function part(value) {
  try {
    const data = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(Buffer.from(value, 'base64url')));
    if (data && typeof data === 'object' && !Array.isArray(data)) return data;
  } catch { /* rejected below */ }
  throw rejected();
}

// The exact issuer string is the identity namespace, so it is never normalized.
function checkIssuer(issuer, allowInsecureLoopback) {
  let url;
  try { url = new URL(issuer); } catch { throw new Error('Invalid identity provider issuer'); }
  const loopback = allowInsecureLoopback && url.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(url.hostname);
  if (typeof issuer !== 'string' || issuer.length > 512 || (url.protocol !== 'https:' && !loopback) || url.username ||
      url.password || url.search || url.hash || issuer.endsWith('/') || !issuer.startsWith(url.origin)) {
    throw new Error('Invalid identity provider issuer');
  }
  return url;
}

async function fetchJSON(url) {
  const response = await fetch(url, {redirect: 'error', signal: AbortSignal.timeout(5000),
    headers: {accept: 'application/json'}});
  if (response.status !== 200) throw unavailable();
  const chunks = [];
  let size = 0;
  for await (const chunk of response.body) {
    size += chunk.length;
    if (size > 256 * 1024) throw unavailable();
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

// The reviewer service checks every ID token itself. The HUD that relays the
// token is trusted to be an enrolled installation, not to have read the token.
function createIdTokenVerifier({issuer, audience, requiredGroup = '', skewSeconds = 60, maxAgeSeconds = 600,
  clock = Date.now, request = fetchJSON, allowInsecureLoopback = false}) {
  const origin = checkIssuer(issuer, allowInsecureLoopback).origin;
  if (typeof audience !== 'string' || !/^[A-Za-z0-9._:-]{1,128}$/.test(audience) ||
      typeof requiredGroup !== 'string' || requiredGroup.length > 128) throw new Error('Invalid identity provider client');
  let keys = new Map();
  let loadedAt = -Infinity;
  let attemptedAt = -Infinity;
  let loading;

  async function load() {
    attemptedAt = clock();
    try {
      // Read with every key refresh, so a moved key set needs no restart.
      const document = await request(issuer + '/.well-known/openid-configuration');
      if (document?.issuer !== issuer || typeof document.jwks_uri !== 'string' ||
          new URL(document.jwks_uri).origin !== origin) throw unavailable();
      const set = await request(document.jwks_uri);
      if (!Array.isArray(set?.keys) || set.keys.length > 32) throw unavailable();
      const next = new Map();
      for (const jwk of set.keys) {
        if (jwk?.kty !== 'RSA' || typeof jwk.kid !== 'string' || !jwk.kid || jwk.kid.length > 256 ||
            (jwk.use !== undefined && jwk.use !== 'sig') || (jwk.alg !== undefined && jwk.alg !== 'RS256')) continue;
        let key;
        try { key = createPublicKey({key: {kty: 'RSA', n: jwk.n, e: jwk.e}, format: 'jwk'}); } catch { continue; }
        if (key.asymmetricKeyDetails.modulusLength >= 2048 && !next.has(jwk.kid)) next.set(jwk.kid, key);
      }
      if (!next.size) throw unavailable();
      keys = next;
      loadedAt = clock();
    } catch {
      throw unavailable();
    }
  }
  const refresh = () => (loading ||= load().finally(() => { loading = undefined; }));

  async function signingKey(kid) {
    // Fail closed when the key set is stale. An unknown key identifier triggers
    // one bounded refetch, which is how a rotated signing key is picked up.
    if (clock() - loadedAt >= keyLifetimeMs) await refresh();
    else if (!keys.has(kid) && clock() - attemptedAt >= refetchIntervalMs) await refresh();
    const key = keys.get(kid);
    if (!key) throw rejected();
    return key;
  }

  async function verify(idToken, nonce) {
    const match = typeof idToken === 'string' && idToken.length <= 16384 && compact.exec(idToken);
    if (!match || typeof nonce !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(nonce)) throw rejected();
    const header = part(match[1]);
    if (header.alg !== 'RS256' || typeof header.kid !== 'string' || !header.kid || header.kid.length > 256 ||
        header.crit !== undefined) throw rejected();
    const key = await signingKey(header.kid);
    if (!verifySignature('RSA-SHA256', Buffer.from(match[1] + '.' + match[2]), key,
      Buffer.from(match[3], 'base64url'))) throw rejected();
    const claims = part(match[2]);
    const now = clock() / 1000;
    const audiences = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
    const time = value => typeof value === 'number' && Number.isFinite(value);
    if (claims.iss !== issuer || !audiences.includes(audience) ||
        (audiences.length !== 1 && claims.azp !== audience) ||
        (claims.azp !== undefined && claims.azp !== audience) ||
        !time(claims.exp) || now >= claims.exp + skewSeconds ||
        !time(claims.iat) || claims.iat > now + skewSeconds || now - claims.iat > maxAgeSeconds + skewSeconds ||
        (claims.nbf !== undefined && (!time(claims.nbf) || claims.nbf > now + skewSeconds)) ||
        typeof claims.nonce !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(claims.nonce) ||
        !timingSafeEqual(Buffer.from(claims.nonce), Buffer.from(nonce)) ||
        typeof claims.sub !== 'string' || !claims.sub || claims.sub.length > 255 || claims.sub.includes('\0') ||
        (requiredGroup && !(Array.isArray(claims.groups) && claims.groups.includes(requiredGroup)))) throw rejected();
    // The spent-token record is removed by the database clock. Keep it past the
    // last moment this process could still accept the token.
    return {issuer, subject: claims.sub, digest: createHash('sha256').update(idToken).digest(),
      expiresAt: new Date((claims.exp + 2 * skewSeconds + 60) * 1000)};
  }

  return {verify};
}

module.exports = {createIdTokenVerifier};

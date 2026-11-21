import {createHash, randomBytes, timingSafeEqual} from 'node:crypto';
import {failure, tokenPattern} from './sessions.mjs';

const random = () => randomBytes(32).toString('base64url');
const metadataLifetimeMs = 10 * 60_000;
export const transactionLifetimeMs = 10 * 60_000;

async function fetchJSON(url, options = {}) {
  let response;
  try {
    response = await fetch(url, {...options, redirect: 'error', signal: AbortSignal.timeout(8000),
      headers: {accept: 'application/json', ...options.headers}});
  } catch { throw failure(503); }
  const chunks = [];
  let size = 0;
  try {
    for await (const chunk of response.body) {
      size += chunk.length;
      if (size > 256 * 1024) throw failure(503);
      chunks.push(chunk);
    }
  } catch { throw failure(503); }
  let data;
  try { data = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { data = null; }
  return {status: response.status, data};
}

// One sign-in attempt. State binds the callback to this browser, the nonce binds
// the ID token to this attempt, and the verifier binds the code to this server.
// All three stay here; the browser holds only an opaque handle.
export class OidcTransactions {
  constructor(clock = Date.now) {
    this.clock = clock;
    this.entries = new Map();
  }

  begin() {
    const now = this.clock();
    for (const [id, entry] of this.entries) if (now >= entry.expires) this.entries.delete(id);
    if (this.entries.size >= 512) throw failure(429);
    const entry = {id: random(), state: random(), nonce: random(), verifier: random(),
      expires: now + transactionLifetimeMs};
    this.entries.set(entry.id, entry);
    return entry;
  }

  // A handle is spent on first use, whether or not the callback then succeeds.
  take(id, state) {
    const entry = this.entries.get(id);
    if (!entry) throw failure(400);
    this.entries.delete(id);
    if (this.clock() >= entry.expires || typeof state !== 'string' || !tokenPattern.test(state) ||
        !timingSafeEqual(Buffer.from(entry.state), Buffer.from(state))) throw failure(400);
    return entry;
  }
}

export function createOidcClient({issuer, clientId, clientSecret, origin, clock = Date.now, request = fetchJSON,
  allowInsecureLoopback = false}) {
  let base;
  try { base = new URL(issuer); } catch { throw new Error('Invalid identity provider issuer'); }
  const loopback = allowInsecureLoopback && base.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(base.hostname);
  if ((base.protocol !== 'https:' && !loopback) || base.username || base.password || base.search || base.hash ||
      issuer.endsWith('/') || !issuer.startsWith(base.origin) ||
      typeof clientId !== 'string' || !/^[A-Za-z0-9._:-]{1,128}$/.test(clientId) ||
      typeof clientSecret !== 'string' || !clientSecret || /[\s\0]/.test(clientSecret)) {
    throw new Error('Invalid identity provider settings');
  }
  const redirectUri = origin + '/oidc/callback';
  const authorization = 'Basic ' + Buffer.from(encodeURIComponent(clientId) + ':' + encodeURIComponent(clientSecret)).toString('base64');
  let metadata;
  let loadedAt = -Infinity;
  let loading;

  // Every endpoint must live on the issuer's own origin, so a tampered discovery
  // document cannot send the code, the client secret or the browser elsewhere.
  const endpoint = value => {
    if (typeof value !== 'string' || new URL(value).origin !== base.origin) throw failure(503);
    return value;
  };
  async function load() {
    const {status, data} = await request(issuer + '/.well-known/openid-configuration');
    if (status !== 200 || data?.issuer !== issuer) throw failure(503);
    try {
      metadata = {
        authorization: endpoint(data.authorization_endpoint), token: endpoint(data.token_endpoint),
        endSession: data.end_session_endpoint === undefined ? null : endpoint(data.end_session_endpoint),
      };
    } catch { throw failure(503); }
    if (!data.code_challenge_methods_supported?.includes('S256')) throw failure(503);
    loadedAt = clock();
    return metadata;
  }
  async function discover() {
    if (clock() - loadedAt < metadataLifetimeMs) return metadata;
    return (loading ||= load().finally(() => { loading = undefined; }));
  }

  return {
    issuer,
    async authorizationUrl(transaction) {
      const url = new URL((await discover()).authorization);
      url.search = new URLSearchParams({response_type: 'code', client_id: clientId, redirect_uri: redirectUri,
        scope: 'openid', state: transaction.state, nonce: transaction.nonce,
        code_challenge: createHash('sha256').update(transaction.verifier).digest('base64url'),
        code_challenge_method: 'S256'}).toString();
      return url.href;
    },
    // Returns the ID token unread. The reviewer service verifies it.
    async redeem(code, verifier) {
      const {token} = await discover();
      const {status, data} = await request(token, {method: 'POST',
        headers: {authorization, 'content-type': 'application/x-www-form-urlencoded'},
        body: new URLSearchParams({grant_type: 'authorization_code', code, redirect_uri: redirectUri,
          code_verifier: verifier}).toString()});
      if (status >= 500) throw failure(503);
      if (status !== 200 || typeof data?.id_token !== 'string' || data.id_token.length > 16384) throw failure(401);
      return data.id_token;
    },
    async endSessionUrl(idToken) {
      let endSession;
      try { ({endSession} = await discover()); } catch { return null; }
      if (!endSession) return null;
      const url = new URL(endSession);
      url.search = new URLSearchParams({id_token_hint: idToken, post_logout_redirect_uri: origin + '/'}).toString();
      return url.href;
    },
  };
}

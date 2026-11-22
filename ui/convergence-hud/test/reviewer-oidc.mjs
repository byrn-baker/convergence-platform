import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createHash, randomBytes} from 'node:crypto';
import {once} from 'node:events';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {createOidcClient, OidcTransactions} from '../reviewer/oidc.mjs';
import {createReviewerServer} from '../reviewer/server.mjs';

const origin = 'https://reviewer.test';
const sessionCookie = '__Host-convergence-review';
const attemptCookie = '__Host-convergence-oidc';
const random = () => randomBytes(32).toString('base64url');
const users = {
  'subject-alice': {id: 'alice', name: 'Alice', role: 'operator', sites: ['red']},
  'subject-bob': {id: 'bob', name: 'Bob', role: 'viewer', sites: ['red']},
};

// A stand-in identity provider with real authorization-code semantics: codes are
// single use and bound to the client, redirect address and PKCE challenge.
async function identityProvider(t) {
  const state = {codes: new Map(), tokenRequests: 0, down: false, overrides: {}};
  const server = http.createServer(async (req, res) => {
    const send = (status, body) => { res.writeHead(status, {'content-type': 'application/json'}); res.end(JSON.stringify(body)); };
    if (state.down) return req.socket.destroy();
    if (req.url === '/realms/home/.well-known/openid-configuration') {
      return send(200, {issuer: state.issuer, authorization_endpoint: state.issuer + '/auth',
        token_endpoint: state.issuer + '/token', end_session_endpoint: state.issuer + '/logout',
        code_challenge_methods_supported: ['S256'], ...state.overrides});
    }
    if (req.method === 'POST' && req.url === '/realms/home/token') {
      state.tokenRequests += 1;
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const form = new URLSearchParams(Buffer.concat(chunks).toString());
      const code = state.codes.get(form.get('code'));
      state.codes.delete(form.get('code'));
      const basic = 'Basic ' + Buffer.from('convergence-cases:fixture-secret').toString('base64');
      if (req.headers.authorization !== basic) return send(401, {error: 'invalid_client'});
      if (!code || form.get('grant_type') !== 'authorization_code' || form.get('redirect_uri') !== code.redirect ||
          createHash('sha256').update(form.get('code_verifier') || '').digest('base64url') !== code.challenge) {
        return send(400, {error: 'invalid_grant'});
      }
      return send(200, {id_token: ['fixture', code.subject, code.nonce].join('.'), access_token: 'unused'});
    }
    send(404, {});
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }));
  state.issuer = 'http://127.0.0.1:' + server.address().port + '/realms/home';
  // What the provider does after the person authenticates at the authorization URL.
  state.authenticate = (location, subject) => {
    const url = new URL(location);
    const query = url.searchParams;
    assert.equal(url.origin + url.pathname, state.issuer + '/auth');
    assert.deepEqual([query.get('response_type'), query.get('client_id'), query.get('redirect_uri'), query.get('scope'),
      query.get('code_challenge_method')], ['code', 'convergence-cases', origin + '/oidc/callback', 'openid', 'S256']);
    const code = random();
    state.codes.set(code, {subject, nonce: query.get('nonce'), challenge: query.get('code_challenge'),
      redirect: query.get('redirect_uri')});
    return '/oidc/callback?' + new URLSearchParams({state: query.get('state'), session_state: 'fixture', iss: state.issuer, code});
  };
  return state;
}

test('reviewer HUD signs in through an identity provider', async t => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'reviewer-oidc-'));
  t.after(() => rmSync(dir, {recursive: true, force: true}));
  const pem = name => path.join(dir, name + '.pem');
  execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-out', pem('cert'),
    '-keyout', pem('key'), '-subj', '/CN=reviewer.test', '-addext', 'subjectAltName=DNS:reviewer.test'], {stdio: 'ignore'});
  const tls = {cert: readFileSync(pem('cert')), key: readFileSync(pem('key'))};
  const idp = await identityProvider(t);
  const grants = new Map();
  const calls = [];
  const audits = [];
  const client = {
    async issueOidc({idToken, nonce}) {
      calls.push('issueOidc');
      const [, subject, issued] = idToken.split('.');
      // The reviewer service rejects a token minted for another attempt.
      if (issued !== nonce) throw Object.assign(new Error('nonce'), {status: 401});
      if (!users[subject]) throw Object.assign(new Error('unlinked'), {status: 403});
      return grant(users[subject]);
    },
    async issue({username, password}) {
      calls.push('issue');
      if (password !== 'fixture') throw Object.assign(new Error('password'), {status: 401});
      return grant(Object.values(users).find(user => user.id === username));
    },
    async current(token) {
      if (!grants.has(token)) throw Object.assign(new Error('revoked'), {status: 401});
      return {...grants.get(token), expiresAt: new Date(Date.now() + 1_700_000).toISOString(),
        idleExpiresAt: new Date(Date.now() + 450_000).toISOString(), capabilities: {telemetry: false}};
    },
    async revoke(token) { calls.push('revoke'); grants.delete(token); },
  };
  function grant(user) {
    const token = random();
    grants.set(token, user);
    return {...user, token, expiresAt: new Date(Date.now() + 1_700_000).toISOString(),
      idleExpiresAt: new Date(Date.now() + 450_000).toISOString()};
  }
  const provider = () => createOidcClient({issuer: idp.issuer, clientId: 'convergence-cases', clientSecret: 'fixture-secret',
    origin, allowInsecureLoopback: true});
  const assets = new Map([['/', {type: 'text/html', content: Buffer.from('<p>Reviewer</p>')}]]);
  async function listen(options) {
    const server = createReviewerServer({tls, origin, client, assets, audit: record => audits.push(record), ...options});
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    t.after(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }));
    return (target, {method = 'GET', cookies = {}, csrf, data, headers = {}} = {}) => new Promise((resolve, reject) => {
      const body = data === undefined ? undefined : JSON.stringify(data);
      const cookie = Object.entries(cookies).map(([name, value]) => name + '=' + value).join('; ');
      const req = https.request({host: '127.0.0.1', port: server.address().port, servername: 'reviewer.test', ca: tls.cert,
        path: target, method, agent: false, headers: {host: 'reviewer.test', ...(cookie && {cookie}),
          ...(csrf && {'x-review-csrf': csrf}), ...(method === 'POST' && {origin, 'content-type': 'application/json'}),
          ...(body !== undefined && {'content-length': Buffer.byteLength(body)}), ...headers}}, res => {
        const chunks = [];
        res.on('data', chunk => chunks.push(chunk));
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString();
          const set = {};
          for (const line of res.headers['set-cookie'] || []) {
            const [pair, ...attributes] = line.split('; ');
            set[pair.slice(0, pair.indexOf('='))] = {value: pair.slice(pair.indexOf('=') + 1), attributes};
          }
          resolve({status: res.statusCode, headers: res.headers, set,
            body: res.headers['content-type'] === 'application/json' && text ? JSON.parse(text) : text});
        });
      });
      req.on('error', reject);
      req.end(body);
    });
  }
  const navigation = {'sec-fetch-mode': 'navigate', 'sec-fetch-dest': 'document', 'sec-fetch-site': 'cross-site'};
  // One browser: start the attempt, authenticate at the provider, follow the redirect back.
  // Each server counts sign-in attempts per address, so longer cases take their own.
  async function site() {
    const request = await listen({oidc: provider(), passwordSignIn: {enabled: false, addresses: null}});
    async function begin() {
      const started = await request('/oidc/login');
      assert.equal(started.status, 303);
      return {location: started.headers.location, cookies: {[attemptCookie]: started.set[attemptCookie].value}, started};
    }
    async function signIn(subject) {
      const attempt = await begin();
      const done = await request(idp.authenticate(attempt.location, subject), {cookies: attempt.cookies, headers: navigation});
      return {attempt, done, cookies: done.set[sessionCookie] && {[sessionCookie]: done.set[sessionCookie].value}};
    }
    return {request, begin, signIn, session: cookies => request('/review/session', {cookies})};
  }
  let {request, begin, signIn, session} = await site();

  await t.test('login redirects with state, nonce and an S256 challenge, and keeps the verifier', async () => {
    const {location, started} = await begin();
    const query = new URL(location).searchParams;
    for (const name of ['state', 'nonce', 'code_challenge']) assert.match(query.get(name), /^[A-Za-z0-9_-]{43}$/);
    assert.equal(new Set([query.get('state'), query.get('nonce'), query.get('code_challenge'), started.set[attemptCookie].value]).size, 4);
    assert.ok(!location.includes('code_verifier') && !location.includes('fixture-secret'));
    assert.deepEqual(started.set[attemptCookie].attributes, ['Path=/', 'Max-Age=600', 'Secure', 'HttpOnly', 'SameSite=Lax']);
    assert.equal(started.set[sessionCookie], undefined);
  });

  await t.test('a completed sign-in creates the usual Strict session and reports the methods', async () => {
    const anonymous = await session();
    assert.deepEqual(anonymous.body.signIn, {oidc: true, password: false});
    const {done, cookies} = await signIn('subject-alice');
    assert.equal(done.status, 303);
    assert.equal(done.headers.location, '/');
    assert.equal(done.set[attemptCookie].attributes[1], 'Max-Age=0');
    assert.deepEqual(done.set[sessionCookie].attributes.slice(2), ['Secure', 'HttpOnly', 'SameSite=Strict']);
    const current = await session(cookies);
    assert.deepEqual(current.body.user, users['subject-alice']);
    assert.match(current.body.csrf, /^[A-Za-z0-9_-]{43}$/);
    assert.equal(current.body.signIn, undefined);
  });

  await t.test('tampered or missing state, a missing attempt and a replayed callback', async () => {
    const before = idp.tokenRequests;
    const attempt = await begin();
    const callback = idp.authenticate(attempt.location, 'subject-alice');
    const tampered = callback.replace(/state=[^&]+/, 'state=' + random());
    const refused = await request(tampered, {cookies: attempt.cookies, headers: navigation});
    assert.equal(refused.status, 401);
    assert.equal(refused.set[attemptCookie], undefined);
    assert.equal(idp.tokenRequests, before);
    // A forged callback, which any site can send, does not cancel the real sign-in.
    assert.equal((await request(callback, {cookies: attempt.cookies, headers: navigation})).status, 303);

    const second = await begin();
    const genuine = idp.authenticate(second.location, 'subject-alice');
    assert.equal((await request(genuine, {headers: navigation})).status, 401, 'no attempt cookie');
    assert.equal((await request(genuine, {cookies: {[attemptCookie]: random()}, headers: navigation})).status, 401, 'another browser');
    assert.equal((await request(genuine.replace(/state=[^&]+&/, ''), {cookies: second.cookies, headers: navigation})).status, 401);
    assert.equal(idp.tokenRequests, before + 1, 'no rejected callback reached the provider');

    const third = await begin();
    const once = idp.authenticate(third.location, 'subject-alice');
    assert.equal((await request(once, {cookies: third.cookies, headers: navigation})).status, 303);
    const replay = await request(once, {cookies: third.cookies, headers: navigation});
    assert.equal(replay.status, 401);
    assert.equal(replay.set[sessionCookie], undefined);
    assert.equal(idp.tokenRequests, before + 2);
  });

  await t.test('a code from another attempt fails PKCE, and a swapped token fails the nonce', async () => {
    const mine = await begin();
    const theirs = await begin();
    const theirCallback = new URLSearchParams(idp.authenticate(theirs.location, 'subject-bob').split('?')[1]);
    const myCallback = new URLSearchParams(idp.authenticate(mine.location, 'subject-alice').split('?')[1]);
    // An attacker injects the victim's code into their own attempt.
    myCallback.set('code', theirCallback.get('code'));
    const injected = await request('/oidc/callback?' + myCallback, {cookies: mine.cookies, headers: navigation});
    assert.equal(injected.status, 401);
    assert.equal(injected.set[sessionCookie], undefined);

    // A provider that ignored PKCE would still be caught by the nonce.
    const attempt = await begin();
    const query = new URL(attempt.location).searchParams;
    const code = random();
    idp.codes.set(code, {subject: 'subject-alice', nonce: random(), challenge: query.get('code_challenge'),
      redirect: query.get('redirect_uri')});
    const swapped = await request('/oidc/callback?' + new URLSearchParams({state: query.get('state'), code}),
      {cookies: attempt.cookies, headers: navigation});
    assert.equal(swapped.status, 401);
  });

  await t.test('provider errors, a wrong issuer and malformed callbacks', async () => {
    const cases = [
      query => 'state=' + query.get('state') + '&error=access_denied',
      query => 'state=' + query.get('state'),
      query => 'state=' + query.get('state') + '&code=x&iss=' + encodeURIComponent('https://evil.example/realms/home'),
      query => 'state=' + query.get('state') + '&code=x&code=y',
      query => 'state=' + query.get('state') + '&code=x&unexpected=1',
      query => 'state=' + query.get('state') + '&code=' + 'x'.repeat(2049),
    ];
    for (const build of cases) {
      const attempt = await begin();
      const response = await request('/oidc/callback?' + build(new URL(attempt.location).searchParams),
        {cookies: attempt.cookies, headers: navigation});
      assert.equal(response.status, 401, build.toString());
      assert.match(response.headers['content-type'], /^text\/html/);
      assert.ok(!response.body.includes('evil') && !response.body.includes('<script'));
    }
  });

  await t.test('an identity with no linked account is told so, without a session', async () => {
    const {done} = await signIn('subject-stranger');
    assert.equal(done.status, 403);
    assert.match(done.body, /not linked to a Convergence account/);
    assert.equal(done.set[sessionCookie], undefined);
  });

  await t.test('two people hold independent sessions', async () => {
    ({request, begin, signIn, session} = await site());
    const alice = await signIn('subject-alice');
    const bob = await signIn('subject-bob');
    assert.notEqual(alice.cookies[sessionCookie], bob.cookies[sessionCookie]);
    const [a, b] = [await session(alice.cookies), await session(bob.cookies)];
    assert.equal(a.body.user.id, 'alice');
    assert.equal(b.body.user.id, 'bob');
    assert.notEqual(a.body.csrf, b.body.csrf);
    // Alice's proof does not work on Bob's session, and ending hers leaves his.
    const crossed = await request('/review/sign-out', {method: 'POST', cookies: bob.cookies, csrf: a.body.csrf, data: {}});
    assert.equal(crossed.status, 403);
    const out = await request('/review/sign-out', {method: 'POST', cookies: alice.cookies, csrf: a.body.csrf, data: {}});
    assert.equal(out.status, 200);
    const end = new URL(out.body.location);
    assert.equal(end.origin + end.pathname, idp.issuer + '/logout');
    assert.equal(end.searchParams.get('id_token_hint').split('.')[1], 'subject-alice');
    assert.equal(end.searchParams.get('post_logout_redirect_uri'), origin + '/');
    assert.equal((await session(alice.cookies)).body.user, null);
    assert.equal((await session(bob.cookies)).body.user.id, 'bob');
  });

  await t.test('signing in again in one browser replaces the first session', async () => {
    ({request, begin, signIn, session} = await site());
    const first = await signIn('subject-alice');
    const attempt = await begin();
    const revoked = calls.filter(call => call === 'revoke').length;
    const done = await request(idp.authenticate(attempt.location, 'subject-bob'),
      {cookies: {...attempt.cookies, ...first.cookies}, headers: {...navigation, 'sec-fetch-site': 'same-site'}});
    assert.equal(done.status, 303);
    assert.equal(calls.filter(call => call === 'revoke').length, revoked + 1);
    assert.equal((await session(first.cookies)).body.user, null);
    assert.equal((await session({[sessionCookie]: done.set[sessionCookie].value})).body.user.id, 'bob');

    // A cross-site provider's redirect does not carry the Strict session cookie,
    // so the attempt remembers which session started it.
    const started = await request('/oidc/login', {cookies: {[sessionCookie]: done.set[sessionCookie].value}});
    const again = await request(idp.authenticate(started.headers.location, 'subject-alice'),
      {cookies: {[attemptCookie]: started.set[attemptCookie].value}, headers: navigation});
    assert.equal(again.status, 303);
    assert.equal(calls.filter(call => call === 'revoke').length, revoked + 2);
    assert.equal((await session({[sessionCookie]: done.set[sessionCookie].value})).body.user, null);
  });

  await t.test('only the callback navigation is accepted across sites', async () => {
    ({request, begin, signIn, session} = await site());
    const crossSite = {'sec-fetch-site': 'cross-site'};
    assert.equal((await request('/oidc/login', {headers: {...navigation}})).status, 403);
    assert.equal((await request('/oidc/callback?state=x', {headers: {...crossSite, 'sec-fetch-mode': 'cors', 'sec-fetch-dest': 'empty'}})).status, 403);
    assert.equal((await request('/oidc/callback', {headers: navigation})).status, 403);
    assert.equal((await request('/oidc/callback?state=x', {method: 'POST', data: {}})).status, 404);
    assert.equal((await request('/review/session', {headers: navigation})).status, 403);
  });

  await t.test('password sign-in is absent when it is turned off', async () => {
    const pre = await session();
    const cookies = {[sessionCookie]: pre.set[sessionCookie].value};
    const response = await request('/review/sign-in', {method: 'POST', cookies, csrf: pre.body.csrf,
      data: {username: 'alice', password: 'fixture'}});
    assert.equal(response.status, 404);
    assert.ok(!calls.includes('issue'));
  });

  await t.test('identity provider outage fails closed and existing sessions continue', async () => {
    ({request, begin, signIn, session} = await site());
    const existing = await signIn('subject-alice');
    const pending = await begin();
    const callback = idp.authenticate(pending.location, 'subject-alice');
    idp.down = true;
    t.after(() => { idp.down = false; });
    const duringRedeem = await request(callback, {cookies: pending.cookies, headers: navigation});
    assert.equal(duringRedeem.status, 503);
    assert.match(duringRedeem.body, /identity provider/);
    assert.equal(duringRedeem.set[sessionCookie], undefined);
    assert.equal((await session(existing.cookies)).body.user.id, 'alice');
    // Sign-out still ends the local session and grant when the provider is away.
    const state = await session(existing.cookies);
    const out = await request('/review/sign-out', {method: 'POST', cookies: existing.cookies, csrf: state.body.csrf, data: {}});
    assert.equal(out.status, 200);
    assert.equal((await session(existing.cookies)).body.user, null);
    idp.down = false;

    // With no cached provider metadata the login redirect itself is refused.
    const cold = await listen({oidc: provider(), passwordSignIn: {enabled: false, addresses: null}});
    idp.down = true;
    const refused = await cold('/oidc/login');
    assert.equal(refused.status, 503);
    assert.equal(refused.headers.location, undefined);
    idp.down = false;
    assert.equal((await cold('/oidc/login')).status, 303, 'recovers without a restart');
  });

  await t.test('provider endpoints must stay on the issuer origin', async () => {
    for (const overrides of [{token_endpoint: 'http://127.0.0.1:1/token'}, {authorization_endpoint: 'https://evil.example/auth'},
      {issuer: idp.issuer + '/'}, {code_challenge_methods_supported: ['plain']}, {end_session_endpoint: 'https://evil.example/out'}]) {
      idp.overrides = overrides;
      const strict = await listen({oidc: provider(), passwordSignIn: {enabled: false, addresses: null}});
      assert.equal((await strict('/oidc/login')).status, 503, JSON.stringify(overrides));
    }
    idp.overrides = {};
  });

  await t.test('break-glass password sign-in is limited by address and audited', async () => {
    const allowed = await listen({oidc: provider(), passwordSignIn: {enabled: true, addresses: new Set(['127.0.0.1'])}});
    const pre = await allowed('/review/session');
    assert.deepEqual(pre.body.signIn, {oidc: true, password: true});
    const cookies = {[sessionCookie]: pre.set[sessionCookie].value};
    const wrong = await allowed('/review/sign-in', {method: 'POST', cookies, csrf: pre.body.csrf, data: {username: 'alice', password: 'no'}});
    assert.equal(wrong.status, 401);
    const right = await allowed('/review/sign-in', {method: 'POST', cookies, csrf: pre.body.csrf, data: {username: 'alice', password: 'fixture'}});
    assert.equal(right.status, 200);
    assert.deepEqual(audits.map(({at, ...record}) => record), [
      {event: 'reviewer-password-sign-in', address: '127.0.0.1', outcome: 'rejected', username: 'alice'},
      {event: 'reviewer-password-sign-in', address: '127.0.0.1', outcome: 'accepted', username: 'alice', account: 'alice'},
    ]);
    assert.ok(audits.every(record => !JSON.stringify(record).includes('fixture') && !Number.isNaN(Date.parse(record.at))));
    // A password session has no provider session to end.
    assert.equal((await allowed('/review/sign-out', {method: 'POST',
      cookies: {[sessionCookie]: right.set[sessionCookie].value}, csrf: right.body.csrf, data: {}})).status, 204);

    const elsewhere = await listen({oidc: provider(), passwordSignIn: {enabled: true, addresses: new Set(['192.168.100.32'])}});
    const other = await elsewhere('/review/session');
    assert.deepEqual(other.body.signIn, {oidc: true, password: false});
    const issued = calls.filter(call => call === 'issue').length;
    const blocked = await elsewhere('/review/sign-in', {method: 'POST', cookies: {[sessionCookie]: other.set[sessionCookie].value},
      csrf: other.body.csrf, data: {username: 'alice', password: 'fixture'}});
    assert.equal(blocked.status, 403);
    assert.equal(calls.filter(call => call === 'issue').length, issued);
    assert.deepEqual({...audits.at(-1), at: undefined},
      {event: 'reviewer-password-sign-in', address: '127.0.0.1', outcome: 'refused-address', at: undefined});
  });

  await t.test('settings', () => {
    const base = {tls, origin, client, assets};
    assert.throws(() => createReviewerServer({...base, passwordSignIn: {enabled: false, addresses: null}}), /No sign-in method/);
    assert.throws(() => createReviewerServer({...base, oidc: provider(), passwordSignIn: {enabled: true, addresses: null}}), /address list/);
    const settings = {issuer: 'https://sso.test/realms/home', clientId: 'convergence-cases', clientSecret: 's', origin};
    assert.ok(createOidcClient(settings));
    for (const bad of [{issuer: 'http://sso.test/realms/home'}, {issuer: idp.issuer}, {issuer: 'https://sso.test/realms/home/'},
      {issuer: 'https://sso.test/realms/home?x'}, {clientSecret: ''}, {clientSecret: 'a b'}, {clientId: 'a b'}]) {
      assert.throws(() => createOidcClient({...settings, ...bad}), /Invalid identity provider/, JSON.stringify(bad));
    }
  });

  await t.test('sign-in attempts expire and are bounded', () => {
    let now = 0;
    const attempts = new OidcTransactions(() => now);
    const first = attempts.begin();
    now = 600_000;
    assert.throws(() => attempts.take(first.id, first.state), error => error.status === 400);
    const wrong = attempts.begin();
    assert.throws(() => attempts.take(wrong.id, first.state), error => error.status === 400);
    assert.equal(attempts.take(wrong.id, wrong.state), wrong, 'a wrong state leaves the attempt');
    assert.throws(() => attempts.take(wrong.id, wrong.state), error => error.status === 400);
    for (let index = 0; index < 16; index += 1) attempts.begin('192.0.2.1');
    assert.throws(() => attempts.begin('192.0.2.1'), error => error.status === 429, 'per address');
    for (let index = 0; index < 496; index += 1) attempts.begin('192.0.2.' + (2 + index % 100) + ':' + index);
    assert.throws(() => attempts.begin('192.0.2.200'), error => error.status === 429);
    now = 1_200_000;
    assert.ok(attempts.begin());
  });
});

import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {X509Certificate, randomUUID} from 'node:crypto';
import {once} from 'node:events';
import fs from 'node:fs';
import https from 'node:https';
import {createRequire} from 'node:module';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {createOidcClient, OidcTransactions} from '../../ui/convergence-hud/reviewer/oidc.mjs';
import {createReviewerServer} from '../../ui/convergence-hud/reviewer/server.mjs';
import {createReviewerClient} from '../../ui/convergence-hud/reviewer/service-client.mjs';

const api = fileURLToPath(new URL('../../ui/convergence-api/', import.meta.url));
const require = createRequire(api);
const {Pool} = require('pg');
const {provision} = require('./reviewer-accounts');
const {createGrantAuthority} = require('./src/reviewer/grants');
const {createIdTokenVerifier} = require('./src/reviewer/oidc');
const {createReviewerService} = require('./src/reviewer/transport');

const {REVIEWER_TEST_DATABASE_URL: database, KEYCLOAK_URL: keycloak, KEYCLOAK_ADMIN_PASSWORD: adminPassword,
  KEYCLOAK_CONTAINER: container} = process.env;
const origin = 'https://reviewer.test';
const realm = 'home-' + randomUUID().slice(0, 8);
const issuer = keycloak + '/realms/' + realm;
const clientSecret = randomUUID();
const password = 'Fixture-' + randomUUID();
const sessionCookie = '__Host-convergence-review';
const attemptCookie = '__Host-convergence-oidc';
const navigation = {'sec-fetch-mode': 'navigate', 'sec-fetch-dest': 'document', 'sec-fetch-site': 'cross-site'};

function certificates(folder) {
  const run = args => execFileSync('openssl', args, {cwd: folder, stdio: 'pipe'});
  const key = name => run(['req', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
    '-subj', '/CN=' + name, '-keyout', name + '.key', '-out', name + '.csr']);
  fs.writeFileSync(path.join(folder, 'ca.ext'), 'basicConstraints=critical,CA:TRUE');
  key('ca');
  run(['x509', '-req', '-days', '1', '-in', 'ca.csr', '-signkey', 'ca.key', '-out', 'ca.pem', '-set_serial', '1', '-extfile', 'ca.ext']);
  const data = name => fs.readFileSync(path.join(folder, name));
  let serial = 1;
  for (const [name, usage] of [['localhost', 'serverAuth'], ['installation', 'clientAuth'], ['reviewer.test', 'serverAuth']]) {
    key(name);
    fs.writeFileSync(path.join(folder, 'leaf.ext'), 'basicConstraints=CA:FALSE\nsubjectAltName=DNS:' + name + '\nextendedKeyUsage=' + usage);
    run(['x509', '-req', '-days', '1', '-in', name + '.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key',
      '-set_serial', String(++serial), '-extfile', 'leaf.ext', '-out', name + '.pem']);
  }
  return {ca: data('ca.pem'), pair: name => ({cert: data(name + '.pem'), key: data(name + '.key')})};
}

// The Keycloak admin interface, used only to build and change the fixture realm.
async function administrator() {
  const response = await fetch(keycloak + '/realms/master/protocol/openid-connect/token', {method: 'POST',
    body: new URLSearchParams({grant_type: 'password', client_id: 'admin-cli', username: 'fixture-admin', password: adminPassword})});
  assert.equal(response.status, 200, 'Keycloak admin sign-in');
  const {access_token: token} = await response.json();
  return async (method, target, body) => {
    const result = await fetch(keycloak + '/admin' + target, {method,
      headers: {authorization: 'Bearer ' + token, ...(body && {'content-type': 'application/json'})},
      body: body && JSON.stringify(body)});
    assert.ok(result.status < 300, method + ' ' + target + ' -> ' + result.status + ' ' + await result.clone().text());
    return {location: result.headers.get('location'), data: result.status === 204 || result.status === 201 ? null : await result.json()};
  };
}

// A browser's cookie jar for the identity provider, and its sign-in form.
class ProviderBrowser {
  jar = new Map();

  async fetch(url, options = {}) {
    const response = await fetch(url, {...options, redirect: 'manual',
      headers: {...options.headers, cookie: [...this.jar].map(pair => pair.join('=')).join('; ')}});
    for (const line of response.headers.getSetCookie()) {
      const pair = line.split(';')[0];
      const name = pair.slice(0, pair.indexOf('='));
      if (/max-age=0|expires=thu, 01[ -]jan[ -]1970/i.test(line)) this.jar.delete(name);
      else this.jar.set(name, pair.slice(name.length + 1));
    }
    return response;
  }

  // Returns the redirect back to the relying party, or null when Keycloak refuses.
  async authenticate(authorizationUrl, username) {
    let response = await this.fetch(authorizationUrl);
    if (response.status === 200) {
      const form = /<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"/.exec(await response.text());
      if (!form) return null;
      response = await this.fetch(form[1].replaceAll('&amp;', '&'), {method: 'POST',
        headers: {'content-type': 'application/x-www-form-urlencoded'},
        body: new URLSearchParams({username, password, credentialId: ''})});
    }
    const location = response.headers.get('location');
    return response.status === 302 && location?.startsWith(origin + '/oidc/callback?') ? location.slice(origin.length) : null;
  }
}

const skip = !(database && keycloak && adminPassword) && 'Run through tests/reviewer-oidc/run.sh';
test('Cases sign-in through a real Keycloak', {skip, timeout: 240_000}, async t => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'reviewer-keycloak-'));
  t.after(() => fs.rmSync(folder, {recursive: true, force: true}));
  const {ca, pair} = certificates(folder);

  // Realm: what gitops/home/identity configures, less MFA, which a form post cannot do.
  const admin = await administrator();
  const mapper = {name: 'groups', protocol: 'openid-connect', protocolMapper: 'oidc-group-membership-mapper',
    config: {'claim.name': 'groups', 'full.path': 'false', 'id.token.claim': 'true', 'access.token.claim': 'true', 'userinfo.token.claim': 'true'}};
  const relyingParty = (clientId, redirect) => ({clientId, secret: clientSecret, publicClient: false, standardFlowEnabled: true,
    directAccessGrantsEnabled: false, redirectUris: [redirect + '/oidc/callback'], protocolMappers: [mapper],
    attributes: {'pkce.code.challenge.method': 'S256', 'post.logout.redirect.uris': redirect + '/'}});
  await admin('POST', '/realms', {realm, enabled: true, sslRequired: 'none', accessTokenLifespan: 300,
    groups: [{name: 'convergence-users'}],
    clients: [relyingParty('convergence-cases', origin), relyingParty('netclaw-hud', 'https://hud.test')]});
  t.after(() => admin('DELETE', '/realms/' + realm).catch(() => {}));
  const subjects = {};
  for (const [username, groups] of [['alice', ['convergence-users']], ['bob', ['convergence-users']],
    ['carol', ['convergence-users']], ['dave', []]]) {
    const {location} = await admin('POST', '/realms/' + realm + '/users', {username, enabled: true, emailVerified: true,
      email: username + '@fixture.test', firstName: username, lastName: 'Fixture', groups,
      credentials: [{type: 'password', value: password, temporary: false}]});
    subjects[username] = location.split('/').pop();
  }

  // Directory: alice, bob and dave have accounts and links. Carol has neither.
  const schema = 'keycloak_test_' + randomUUID().replaceAll('-', '');
  const owner = new Pool({connectionString: database});
  await owner.query('CREATE SCHEMA ' + schema);
  const pool = new Pool({connectionString: database, options: '-c search_path=' + schema, max: 8});
  t.after(async () => {
    await pool.end();
    try { await owner.query('DROP SCHEMA ' + schema + ' CASCADE'); } finally { await owner.end(); }
  });
  await pool.query("CREATE TABLE sites (id text PRIMARY KEY); INSERT INTO sites VALUES ('red')");
  await pool.query(fs.readFileSync(path.join(api, 'src/reviewer/grants.sql'), 'utf8'));
  const secret = () => ({isTTY: false, async *[Symbol.asyncIterator]() { yield Buffer.from(randomUUID()); }});
  for (const [username, role] of [['alice', 'operator'], ['bob', 'viewer'], ['dave', 'viewer']]) {
    await provision(pool, ['create', username, role, 'red'], secret());
    // The command accepts HTTPS issuers only, so the loopback fixture links directly.
    await assert.rejects(provision(pool, ['link', username, issuer, subjects[username]]), /Usage/);
    await pool.query(`INSERT INTO reviewer_identity_links (issuer, subject, account_id)
      SELECT $2, $3, id FROM reviewer_accounts WHERE username = $1`, [username, issuer, subjects[username]]);
  }

  // Reviewer service over mutual TLS. Its clock can be moved to age keys and tokens.
  let clockOffset = 0;
  const verifier = createIdTokenVerifier({issuer, audience: 'convergence-cases', requiredGroup: 'convergence-users',
    allowInsecureLoopback: true, clock: () => Date.now() + clockOffset});
  const service = createReviewerService({tls: {ca, ...pair('localhost')},
    enrollments: [{installation: 'fixture', fingerprint256: new X509Certificate(pair('installation').cert).fingerprint256}],
    authority: createGrantAuthority(pool, {oidc: verifier, passwordSignIn: false})});
  service.listen(0, '127.0.0.1');
  await once(service, 'listening');
  const client = createReviewerClient({origin: 'https://localhost:' + service.address().port, tls: {ca, ...pair('installation')}});
  const provider = createOidcClient({issuer, clientId: 'convergence-cases', clientSecret, origin, allowInsecureLoopback: true});
  const hud = createReviewerServer({tls: pair('reviewer.test'), origin, client, oidc: provider,
    passwordSignIn: {enabled: false, addresses: null},
    assets: new Map([['/', {type: 'text/html', content: Buffer.from('<p>Reviewer</p>')}]])});
  hud.listen(0, '127.0.0.1');
  await once(hud, 'listening');
  t.after(async () => {
    await new Promise(resolve => { hud.close(resolve); hud.closeAllConnections(); });
    client.close();
    await new Promise(resolve => { service.close(resolve); service.closeAllConnections(); });
  });

  const request = (target, {method = 'GET', cookies = {}, csrf, data, headers = {}} = {}) => new Promise((resolve, reject) => {
    const body = data === undefined ? undefined : JSON.stringify(data);
    const cookie = Object.entries(cookies).map(pair => pair.join('=')).join('; ');
    const req = https.request({host: '127.0.0.1', port: hud.address().port, servername: 'reviewer.test', ca, path: target,
      method, agent: false, headers: {host: 'reviewer.test', ...(cookie && {cookie}), ...(csrf && {'x-review-csrf': csrf}),
        ...(method === 'POST' && {origin, 'content-type': 'application/json'}),
        ...(body !== undefined && {'content-length': Buffer.byteLength(body)}), ...headers}}, res => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString();
        const set = Object.fromEntries((res.headers['set-cookie'] || []).map(line => {
          const item = line.split(';')[0];
          return [item.slice(0, item.indexOf('=')), item.slice(item.indexOf('=') + 1)];
        }));
        resolve({status: res.statusCode, headers: res.headers, set,
          body: res.headers['content-type'] === 'application/json' && text ? JSON.parse(text) : text});
      });
    });
    req.on('error', reject);
    req.end(body);
  });
  const session = cookies => request('/review/session', {cookies});
  // The whole browser flow. Returns the HUD's answer to the provider's redirect.
  async function signIn(username, browser = new ProviderBrowser()) {
    const started = await request('/oidc/login');
    assert.equal(started.status, 303);
    const callback = await browser.authenticate(started.headers.location, username);
    if (!callback) return {browser, refused: true};
    const done = await request(callback, {cookies: {[attemptCookie]: started.set[attemptCookie]}, headers: navigation});
    return {browser, callback, attempt: started.set[attemptCookie], done,
      cookies: done.set[sessionCookie] && {[sessionCookie]: done.set[sessionCookie]}};
  }
  // A real ID token for any client, fetched the way the HUD does, without spending it.
  async function idToken(username, clientId = 'convergence-cases', redirect = origin) {
    const own = createOidcClient({issuer, clientId, clientSecret, origin: redirect, allowInsecureLoopback: true});
    const attempt = new OidcTransactions().begin();
    const response = await new ProviderBrowser().fetch(await own.authorizationUrl(attempt));
    const form = /<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"/.exec(await response.text());
    const cookie = response.headers.getSetCookie().map(line => line.split(';')[0]).join('; ');
    const posted = await fetch(form[1].replaceAll('&amp;', '&'), {method: 'POST', redirect: 'manual',
      headers: {'content-type': 'application/x-www-form-urlencoded', cookie},
      body: new URLSearchParams({username, password, credentialId: ''})});
    const code = new URL(posted.headers.get('location')).searchParams.get('code');
    return {own, attempt, code, token: () => own.redeem(code, attempt.verifier)};
  }
  const status = code => error => error.status === code;

  await t.test('a linked person signs in and keeps their Convergence role and name', async () => {
    assert.deepEqual((await session()).body.signIn, {oidc: true, password: false});
    const {done, cookies} = await signIn('alice');
    assert.equal(done.status, 303);
    assert.equal(done.headers.location, '/');
    const {body} = await session(cookies);
    assert.deepEqual({name: body.user.name, role: body.user.role, sites: body.user.sites}, {name: 'alice', role: 'operator', sites: ['red']});
    const {rows} = await pool.query("SELECT id FROM reviewer_accounts WHERE username = 'alice'");
    assert.equal(body.user.id, rows[0].id, 'the existing account identity, not the provider subject');
    assert.notEqual(body.user.id, subjects.alice);
  });

  await t.test('two people hold independent sessions', async () => {
    const [alice, bob] = [await signIn('alice'), await signIn('bob')];
    const [a, b] = [await session(alice.cookies), await session(bob.cookies)];
    assert.deepEqual([a.body.user.name, a.body.user.role, b.body.user.name, b.body.user.role], ['alice', 'operator', 'bob', 'viewer']);
    assert.equal((await request('/review/sign-out', {method: 'POST', cookies: bob.cookies, csrf: a.body.csrf, data: {}})).status, 403);
    const out = await request('/review/sign-out', {method: 'POST', cookies: alice.cookies, csrf: a.body.csrf, data: {}});
    assert.equal(out.status, 200);
    assert.equal((await session(alice.cookies)).body.user, null);
    assert.equal((await session(bob.cookies)).body.user.name, 'bob');

    // Sign-out also ends the provider session: the next attempt asks for the password again.
    assert.ok(out.body.location.startsWith(issuer + '/protocol/openid-connect/logout?'));
    const again = await request('/oidc/login');
    assert.equal((await alice.browser.fetch(again.headers.location)).status, 302, 'single sign-on while the provider session lives');
    const ended = await alice.browser.fetch(out.body.location);
    assert.equal(ended.status, 302);
    assert.equal(ended.headers.get('location'), origin + '/');
    const afterwards = await request('/oidc/login');
    assert.equal((await alice.browser.fetch(afterwards.headers.location)).status, 200, 'sign-in form');
  });

  await t.test('tampered state, a replayed callback and a replayed code', async () => {
    const browser = new ProviderBrowser();
    const started = await request('/oidc/login');
    const callback = await browser.authenticate(started.headers.location, 'alice');
    const cookies = {[attemptCookie]: started.set[attemptCookie]};
    const forged = callback.replace(/state=[^&]+/, 'state=' + 'A'.repeat(43));
    assert.equal((await request(forged, {cookies, headers: navigation})).status, 401);
    assert.equal((await request(callback, {cookies, headers: navigation})).status, 303, 'a forged callback cannot cancel the sign-in');
    assert.equal((await request(callback, {cookies, headers: navigation})).status, 401, 'the attempt is spent');

    const good = await signIn('alice');
    assert.equal(good.done.status, 303);
    const replay = await request(good.callback, {cookies: {[attemptCookie]: good.attempt}, headers: navigation});
    assert.equal(replay.status, 401);
    assert.equal(replay.set[sessionCookie], undefined);

    // Keycloak itself refuses a code twice, and a code without its PKCE verifier.
    const first = await idToken('alice');
    assert.match(await first.token(), /^[\w-]+\.[\w-]+\.[\w-]+$/);
    await assert.rejects(first.token(), status(401));
    const second = await idToken('alice');
    await assert.rejects(second.own.redeem(second.code, new OidcTransactions().begin().verifier), status(401));
    await assert.rejects(second.token(), status(401), 'the failed attempt burned the code');
  });

  await t.test('the service rejects a wrong nonce, a spent token and another client\'s token', async () => {
    const mine = await idToken('alice');
    const token = await mine.token();
    await assert.rejects(client.issueOidc({idToken: token, nonce: new OidcTransactions().begin().nonce}), status(401));
    const tampered = token.split('.');
    tampered[1] = Buffer.from(JSON.stringify({...JSON.parse(Buffer.from(tampered[1], 'base64url')), sub: subjects.bob})).toString('base64url');
    await assert.rejects(client.issueOidc({idToken: tampered.join('.'), nonce: mine.attempt.nonce}), status(401));
    const grant = await client.issueOidc({idToken: token, nonce: mine.attempt.nonce});
    assert.equal(grant.name, 'alice');
    await assert.rejects(client.issueOidc({idToken: token, nonce: mine.attempt.nonce}), status(401));
    await client.revoke(grant.token);

    // Same realm, same person, same keys: only the audience differs.
    const other = await idToken('alice', 'netclaw-hud', 'https://hud.test');
    await assert.rejects(client.issueOidc({idToken: await other.token(), nonce: other.attempt.nonce}), status(401));
    // A service configured for another realm refuses it. Keycloak gives each realm its own
    // keys, so this fails on the key; the issuer comparison itself is covered with forged
    // tokens in test-reviewer-oidc.cjs.
    const elsewhere = createGrantAuthority(pool, {oidc: createIdTokenVerifier({issuer: keycloak + '/realms/master',
      audience: 'convergence-cases', allowInsecureLoopback: true})});
    const third = await idToken('alice');
    await assert.rejects(elsewhere.issueOidc('fixture', {idToken: await third.token(), nonce: third.attempt.nonce}), status(401));
  });

  await t.test('an expired token is rejected', async () => {
    const mine = await idToken('alice');
    const token = await mine.token();
    clockOffset = 361_000; // five-minute lifetime plus the one-minute skew allowance
    try { await assert.rejects(client.issueOidc({idToken: token, nonce: mine.attempt.nonce}), status(401)); }
    finally { clockOffset = 0; }
    assert.equal((await client.issueOidc({idToken: token, nonce: mine.attempt.nonce})).name, 'alice');
  });

  await t.test('an unlinked person and a person outside the group get no session', async () => {
    const carol = await signIn('carol');
    assert.equal(carol.done.status, 403);
    assert.match(carol.done.body, /not linked to a Convergence account/);
    assert.equal(carol.cookies, undefined);
    const dave = await signIn('dave');
    assert.equal(dave.done.status, 401);
    assert.equal(dave.cookies, undefined);
    assert.equal((await pool.query('SELECT 1 FROM reviewer_grants g JOIN reviewer_accounts a ON a.id = g.account_id WHERE a.username = $1', ['dave'])).rowCount, 0);
  });

  await t.test('a person disabled in Keycloak cannot start a session; the grant ends on its own limit', async () => {
    const bob = await signIn('bob');
    assert.equal((await session(bob.cookies)).body.user.name, 'bob');
    await admin('PUT', '/realms/' + realm + '/users/' + subjects.bob, {enabled: false});
    assert.equal((await signIn('bob')).refused, true, 'fresh browser');
    assert.equal((await signIn('bob', bob.browser)).refused, true, 'browser with the old provider session');
    const current = await session(bob.cookies);
    assert.equal(current.body.user.name, 'bob');
    assert.ok(Date.parse(current.body.expiresAt) - Date.now() <= 30 * 60_000);
    const {rows} = await pool.query(`SELECT max(expires_at) - statement_timestamp() <= interval '30 minutes' AS bounded
      FROM reviewer_grants g JOIN reviewer_accounts a ON a.id = g.account_id WHERE a.username = 'bob'`);
    assert.equal(rows[0].bounded, true);
    // Disabling the Convergence account ends the session at once.
    await provision(pool, ['disable', 'bob']);
    assert.equal((await session(bob.cookies)).status, 401);
  });

  await t.test('key rotation is picked up without a restart', async () => {
    const before = await signIn('alice');
    assert.equal(before.done.status, 303);
    const kid = token => JSON.parse(Buffer.from(token.split('.')[0], 'base64url')).kid;
    const old = kid(await (await idToken('alice')).token());
    await admin('POST', '/realms/' + realm + '/components', {name: 'rotated', providerId: 'rsa-generated',
      providerType: 'org.keycloak.keys.KeyProvider', config: {priority: ['200'], keySize: ['2048'], algorithm: ['RS256']}});
    const fresh = await idToken('alice');
    const token = await fresh.token();
    assert.notEqual(kid(token), old);
    // Inside the refetch interval the unknown key is refused rather than fetched again.
    await assert.rejects(client.issueOidc({idToken: token, nonce: fresh.attempt.nonce}), status(401));
    clockOffset = 31_000;
    t.after(() => { clockOffset = 0; });
    assert.equal((await client.issueOidc({idToken: token, nonce: fresh.attempt.nonce})).name, 'alice');
    assert.equal((await signIn('alice')).done.status, 303);
    assert.equal((await session(before.cookies)).body.user.name, 'alice', 'earlier sessions are unaffected');
  });

  await t.test('identity provider outage fails closed; sessions continue; sign-in returns', {skip: !container && 'needs the container name'}, async () => {
    const existing = await signIn('alice');
    const started = await request('/oidc/login');
    const callback = await new ProviderBrowser().authenticate(started.headers.location, 'alice');
    execFileSync('docker', ['pause', container]);
    try {
      const during = await request(callback, {cookies: {[attemptCookie]: started.set[attemptCookie]}, headers: navigation});
      assert.equal(during.status, 503);
      assert.equal(during.set[sessionCookie], undefined);
      assert.equal((await session(existing.cookies)).body.user.name, 'alice');
    } finally { execFileSync('docker', ['unpause', container]); }
    assert.equal((await signIn('alice')).done.status, 303);
  });
});

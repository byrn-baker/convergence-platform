'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createHmac, createSign, generateKeyPairSync, randomBytes, randomUUID} = require('node:crypto');
const {test} = require('node:test');
const {Pool} = require('pg');
const {provision} = require('./reviewer-accounts');
const {createGrantAuthority} = require('./src/reviewer/grants');
const {createIdTokenVerifier} = require('./src/reviewer/oidc');

const issuer = 'https://sso.test/realms/home';
const audience = 'convergence-cases';
const status = code => error => error.status === code;
const b64 = value => Buffer.from(JSON.stringify(value)).toString('base64url');
const newNonce = () => randomBytes(32).toString('base64url');

// A stand-in identity provider: it holds signing keys and publishes them through
// the two documents the verifier fetches.
function provider() {
  const state = {keys: new Map(), down: false, fetches: [], now: Date.parse('2026-11-21T12:00:00Z'),
    discovery: {issuer, jwks_uri: issuer + '/protocol/openid-connect/certs'}};
  state.addKey = (kid, bits = 2048) => {
    const pair = generateKeyPairSync('rsa', {modulusLength: bits});
    state.keys.set(kid, pair);
    return pair;
  };
  state.request = async url => {
    state.fetches.push(url);
    if (state.down) throw new Error('connection refused');
    if (url === issuer + '/.well-known/openid-configuration') return state.discovery;
    if (url !== state.discovery.jwks_uri) throw new Error('unexpected ' + url);
    return {keys: [...state.keys].map(([kid, pair]) => ({...pair.publicKey.export({format: 'jwk'}), kid, use: 'sig', alg: 'RS256'}))};
  };
  state.token = (nonce, claims = {}, {kid = 'k1', header = {}, key} = {}) => {
    const seconds = Math.floor(state.now / 1000);
    const body = {iss: issuer, aud: audience, azp: audience, sub: 'subject-alice', iat: seconds, exp: seconds + 300,
      nonce, groups: ['convergence-users'], jti: randomUUID(), ...claims};
    for (const [name, value] of Object.entries(body)) if (value === undefined) delete body[name];
    const input = b64({alg: 'RS256', typ: 'JWT', kid, ...header}) + '.' + b64(body);
    return input + '.' + createSign('RSA-SHA256').update(input).sign(key || state.keys.get(kid).privateKey).toString('base64url');
  };
  state.verifier = (options = {}) => createIdTokenVerifier({issuer, audience, requiredGroup: 'convergence-users',
    clock: () => state.now, request: state.request, ...options});
  state.addKey('k1');
  return state;
}

test('the reviewer service checks each ID token itself', async t => {
  const idp = provider();
  const verifier = idp.verifier();
  const nonce = newNonce();

  await t.test('a valid token yields the issuer and subject only', async () => {
    const token = idp.token(nonce, {email: 'someone-else@example.test', preferred_username: 'admin'});
    const identity = await verifier.verify(token, nonce);
    assert.deepEqual({issuer: identity.issuer, subject: identity.subject}, {issuer, subject: 'subject-alice'});
    assert.equal(identity.digest.length, 32);
    assert.equal(+identity.expiresAt, idp.now + 480_000, 'kept past the last moment it could be accepted');
  });

  await t.test('wrong issuer, audience, authorized party, nonce, subject or group', async () => {
    const cases = {
      issuer: {iss: 'https://sso.test/realms/other'},
      'issuer with a trailing slash': {iss: issuer + '/'},
      audience: {aud: 'netclaw-hud', azp: 'netclaw-hud'},
      'audience list without this client as authorized party': {aud: [audience, 'netclaw-hud'], azp: 'netclaw-hud'},
      'authorized party': {azp: 'netclaw-hud'},
      'missing nonce': {nonce: undefined},
      'another attempt\'s nonce': {nonce: newNonce()},
      'empty subject': {sub: ''},
      'non-string subject': {sub: 7},
      'no groups': {groups: undefined},
      'other group': {groups: ['netclaw-owners']},
      'group as a string': {groups: 'convergence-users'},
    };
    for (const [name, claims] of Object.entries(cases)) {
      await assert.rejects(verifier.verify(idp.token(nonce, claims), nonce), status(401), name);
    }
    await assert.rejects(verifier.verify(idp.token(nonce), 'short'), status(401));
    assert.ok(await verifier.verify(idp.token(nonce, {aud: [audience, 'account']}), nonce));
  });

  await t.test('expired, not yet valid and stale tokens, with bounded clock skew', async () => {
    const seconds = Math.floor(idp.now / 1000);
    assert.ok(await verifier.verify(idp.token(nonce, {exp: seconds - 59}), nonce));
    await assert.rejects(verifier.verify(idp.token(nonce, {exp: seconds - 60}), nonce), status(401));
    assert.ok(await verifier.verify(idp.token(nonce, {iat: seconds + 60}), nonce));
    await assert.rejects(verifier.verify(idp.token(nonce, {iat: seconds + 61}), nonce), status(401));
    await assert.rejects(verifier.verify(idp.token(nonce, {nbf: seconds + 61}), nonce), status(401));
    await assert.rejects(verifier.verify(idp.token(nonce, {iat: seconds - 661, exp: seconds + 300}), nonce), status(401));
    for (const claims of [{exp: undefined}, {exp: '9999999999'}, {iat: undefined}, {exp: Infinity}]) {
      await assert.rejects(verifier.verify(idp.token(nonce, claims), nonce), status(401));
    }
  });

  await t.test('signature and algorithm', async () => {
    const good = idp.token(nonce);
    const [head, body, signature] = good.split('.');
    const forged = JSON.parse(Buffer.from(body, 'base64url'));
    forged.sub = 'subject-admin';
    await assert.rejects(verifier.verify([head, b64(forged), signature].join('.'), nonce), status(401));
    await assert.rejects(verifier.verify([head, body, ''].join('.'), nonce), status(401));
    await assert.rejects(verifier.verify(b64({alg: 'none', kid: 'k1'}) + '.' + body + '.e30', nonce), status(401));
    // Algorithm confusion: an HMAC keyed with the published public key.
    const input = b64({alg: 'HS256', kid: 'k1'}) + '.' + body;
    const pem = idp.keys.get('k1').publicKey.export({type: 'spki', format: 'pem'});
    const mac = createHmac('sha256', pem).update(input).digest('base64url');
    await assert.rejects(verifier.verify(input + '.' + mac, nonce), status(401));
    const stranger = generateKeyPairSync('rsa', {modulusLength: 2048}).privateKey;
    await assert.rejects(verifier.verify(idp.token(nonce, {}, {key: stranger}), nonce), status(401));
    await assert.rejects(verifier.verify(idp.token(nonce, {}, {header: {crit: ['x']}}), nonce), status(401));
    for (const junk of ['', 'a.b', 'a.b.c.d', good + '.', 'x'.repeat(20000), null, {}]) {
      await assert.rejects(verifier.verify(junk, nonce), status(401));
    }
  });

  await t.test('key rotation: a new key is fetched once, and a retired key stops working', async () => {
    const keyFetches = () => idp.fetches.filter(url => url === idp.discovery.jwks_uri).length;
    const before = keyFetches();
    idp.addKey('k2');
    // An unknown key identifier is refetched, but not more often than the interval.
    await assert.rejects(verifier.verify(idp.token(nonce, {}, {kid: 'k2'}), nonce), status(401));
    assert.equal(keyFetches(), before, 'no refetch inside the interval');
    idp.now += 31_000;
    assert.ok(await verifier.verify(idp.token(nonce, {}, {kid: 'k2'}), nonce));
    assert.equal(keyFetches(), before + 1);
    await assert.rejects(verifier.verify(idp.token(nonce, {}, {kid: 'never', key: idp.keys.get('k2').privateKey}), nonce), status(401));
    assert.equal(keyFetches(), before + 1, 'unknown identifiers cannot force a fetch per request');
    const retired = idp.keys.get('k1');
    idp.keys.delete('k1');
    assert.ok(await verifier.verify(idp.token(nonce, {}, {key: retired.privateKey}), nonce), 'cached until the set expires');
    idp.now += 10 * 60_000;
    await assert.rejects(verifier.verify(idp.token(nonce, {}, {key: retired.privateKey}), nonce), status(401));
    assert.ok(await verifier.verify(idp.token(nonce, {}, {kid: 'k2'}), nonce));
  });

  await t.test('identity provider outage fails closed, then recovers', async () => {
    idp.now += 10 * 60_000;
    idp.down = true;
    await assert.rejects(verifier.verify(idp.token(nonce, {}, {kid: 'k2'}), nonce), status(503));
    idp.down = false;
    assert.ok(await verifier.verify(idp.token(nonce, {}, {kid: 'k2'}), nonce));
  });

  await t.test('untrustworthy discovery documents and key sets', async () => {
    const other = provider();
    other.discovery = {issuer: issuer + '/', jwks_uri: other.discovery.jwks_uri};
    await assert.rejects(other.verifier().verify(other.token(nonce), nonce), status(503));
    other.discovery = {issuer, jwks_uri: 'https://keys.example.test/certs'};
    await assert.rejects(other.verifier().verify(other.token(nonce), nonce), status(503));
    const weak = provider();
    weak.keys.clear();
    weak.addKey('k1', 1024);
    await assert.rejects(weak.verifier().verify(weak.token(nonce), nonce), status(503));
  });

  await t.test('settings', () => {
    for (const bad of ['http://sso.test/realms/home', 'https://sso.test/realms/home/', 'https://sso.test/realms/home?x=1',
      'https://user@sso.test/realms/home', 'http://127.0.0.1:8080/realms/home', 'not a url']) {
      assert.throws(() => createIdTokenVerifier({issuer: bad, audience}), /issuer/, bad);
    }
    assert.throws(() => createIdTokenVerifier({issuer, audience: ''}), /client/);
    assert.ok(createIdTokenVerifier({issuer: 'http://127.0.0.1:8080/realms/home', audience, allowInsecureLoopback: true}));
  });
});

const url = process.env.REVIEWER_TEST_DATABASE_URL;
const schemaSql = fs.readFileSync(path.join(__dirname, 'src/reviewer/grants.sql'), 'utf8');
const read = {site: 'red', write: false};
const principal = (_client, actor) => actor;

test('identity links and the OIDC grant exchange', {skip: !url && 'Set REVIEWER_TEST_DATABASE_URL to an isolated PostgreSQL database'}, async t => {
  const schema = 'oidc_test_' + randomUUID().replaceAll('-', '');
  const admin = new Pool({connectionString: url});
  await admin.query('CREATE SCHEMA ' + schema);
  const pool = new Pool({connectionString: url, options: '-c search_path=' + schema, max: 8});
  t.after(async () => {
    await pool.end();
    try { await admin.query('DROP SCHEMA ' + schema + ' CASCADE'); } finally { await admin.end(); }
  });
  await pool.query('CREATE TABLE sites (id text PRIMARY KEY)');
  await pool.query("INSERT INTO sites VALUES ('red'), ('blue')");
  await pool.query(schemaSql);
  await pool.query(schemaSql); // The migration is additive and repeatable.
  const idp = provider();
  idp.now = Date.now();
  const authority = createGrantAuthority(pool, {oidc: idp.verifier({clock: Date.now}), passwordSignIn: false});
  const exchange = (subject, nonce = newNonce(), claims = {}) =>
    ({idToken: idp.token(nonce, {sub: subject, ...claims}), nonce});
  const password = {isTTY: false, async *[Symbol.asyncIterator]() { yield Buffer.from('fixture-password\n'); }};
  await provision(pool, ['create', 'alice', 'operator', 'red'], password);
  await provision(pool, ['create', 'bob', 'viewer', 'red', 'blue'], password);

  await t.test('linking is explicit and keyed by issuer and subject', async () => {
    await assert.rejects(authority.issueOidc('one', exchange('subject-alice')), status(403), 'no link, no account');
    await provision(pool, ['link', 'alice', issuer, 'subject-alice']);
    await assert.rejects(provision(pool, ['link', 'bob', issuer, 'subject-alice']), /already linked/);
    await assert.rejects(provision(pool, ['link', 'nobody', issuer, 'subject-x']), /Unknown reviewer/);
    for (const bad of [['link', 'alice', 'http://sso.test/realms/home', 's'], ['link', 'alice', issuer + '/', 's'],
      ['link', 'alice', issuer], ['link', 'alice', issuer, 's', 'extra'], ['link', 'alice', issuer, 'a\nb']]) {
      await assert.rejects(provision(pool, bad), /Usage/);
    }
    const grant = await authority.issueOidc('one', exchange('subject-alice'));
    assert.match(grant.token, /^[A-Za-z0-9_-]{43}$/);
    const {rows: [account]} = await pool.query("SELECT id FROM reviewer_accounts WHERE username = 'alice'");
    assert.deepEqual({id: grant.id, name: grant.name, role: grant.role, sites: grant.sites},
      {id: account.id, name: 'alice', role: 'operator', sites: ['red']});
    assert.equal((await authority.withGrant('one', grant.token, read, principal)).id, account.id);
    await assert.rejects(authority.withGrant('two', grant.token, read, principal), status(401));
    // The same subject string from another issuer is another identity.
    const stranger = provider();
    const foreign = createGrantAuthority(pool, {oidc: createIdTokenVerifier({issuer: 'https://sso.test/realms/other',
      audience, request: async target => target.endsWith('configuration')
        ? {issuer: 'https://sso.test/realms/other', jwks_uri: 'https://sso.test/realms/other/certs'}
        : stranger.request(stranger.discovery.jwks_uri)})});
    const nonce = newNonce();
    await assert.rejects(foreign.issueOidc('one', {nonce,
      idToken: stranger.token(nonce, {iss: 'https://sso.test/realms/other', iat: Math.floor(Date.now() / 1000), exp: Math.floor(Date.now() / 1000) + 300})}), status(403));
  });

  await t.test('an ID token buys one grant', async () => {
    const once = exchange('subject-alice');
    await authority.issueOidc('one', once);
    await assert.rejects(authority.issueOidc('one', once), status(401));
    await assert.rejects(authority.issueOidc('two', once), status(401));
    const stored = await pool.query('SELECT * FROM reviewer_oidc_exchanges');
    assert.ok(stored.rows.every(row => row.token_digest.length === 32 && Object.keys(row).length === 2));
  });

  await t.test('a token rejected for want of a link can be used once the link exists', async () => {
    const early = exchange('subject-bob');
    await assert.rejects(authority.issueOidc('one', early), status(403));
    await provision(pool, ['link', 'bob', issuer, 'subject-bob']);
    assert.equal((await authority.issueOidc('one', early)).name, 'bob');
  });

  await t.test('invalid tokens never reach the directory', async () => {
    await assert.rejects(authority.issueOidc('one', exchange('subject-alice', newNonce(), {aud: 'netclaw-hud', azp: 'netclaw-hud'})), status(401));
    await assert.rejects(authority.issueOidc('one', {idToken: idp.token(newNonce(), {sub: 'subject-alice'}), nonce: newNonce()}), status(401));
    await assert.rejects(authority.issueOidc('one', {idToken: 7, nonce: newNonce()}), status(400));
    await assert.rejects(authority.issueOidc('one', {idToken: 'a.b.c'}), status(400));
    await assert.rejects(authority.issueOidc('bad installation!', exchange('subject-alice')), status(401));
  });

  await t.test('a disabled account cannot start a session and its grants stop', async () => {
    const grant = await authority.issueOidc('one', exchange('subject-bob'));
    await provision(pool, ['disable', 'bob']);
    await assert.rejects(authority.withGrant('one', grant.token, read, principal), status(401));
    await assert.rejects(authority.issueOidc('one', exchange('subject-bob')), status(403));
    await pool.query("UPDATE reviewer_accounts SET enabled = true WHERE username = 'bob'");
  });

  await t.test('unlinking ends the sessions that the link started', async () => {
    const mine = await authority.issueOidc('one', exchange('subject-alice'));
    const theirs = await authority.issueOidc('one', exchange('subject-bob'));
    await assert.rejects(provision(pool, ['unlink', 'bob', issuer, 'subject-alice']), /Unknown reviewer or identity link/);
    await provision(pool, ['unlink', 'alice', issuer, 'subject-alice']);
    await assert.rejects(authority.withGrant('one', mine.token, read, principal), status(401));
    assert.equal((await authority.withGrant('one', theirs.token, read, principal)).name, 'bob');
    await assert.rejects(authority.issueOidc('one', exchange('subject-alice')), status(403));
  });

  await t.test('unlinking and a sign-in in progress cannot pass each other', async () => {
    await provision(pool, ['link', 'alice', issuer, 'subject-alice']);
    // A sign-in that holds the account, as issueOidc does while it inserts the grant.
    const signingIn = await pool.connect();
    await signingIn.query('BEGIN');
    await signingIn.query("SELECT id FROM reviewer_accounts WHERE username = 'alice' FOR SHARE");
    let unlinked = false;
    const unlinking = provision(pool, ['unlink', 'alice', issuer, 'subject-alice']).then(() => { unlinked = true; });
    await new Promise(resolve => setTimeout(resolve, 300));
    assert.equal(unlinked, false, 'unlink waits for the sign-in');
    await signingIn.query('COMMIT');
    signingIn.release();
    await unlinking;
    // And the other order: the sign-in waits, then finds the link gone.
    await provision(pool, ['link', 'alice', issuer, 'subject-alice']);
    const removing = await pool.connect();
    await removing.query('BEGIN');
    await removing.query("SELECT id FROM reviewer_accounts WHERE username = 'alice' FOR UPDATE");
    await removing.query('DELETE FROM reviewer_identity_links WHERE subject = $1', ['subject-alice']);
    const late = authority.issueOidc('one', exchange('subject-alice'));
    await new Promise(resolve => setTimeout(resolve, 300));
    await removing.query('COMMIT');
    removing.release();
    await assert.rejects(late, status(403));
    assert.equal((await pool.query("SELECT 1 FROM reviewer_grants g JOIN reviewer_accounts a ON a.id = g.account_id WHERE a.username = 'alice'")).rowCount, 0);
  });

  await t.test('deleting an account removes its links', async () => {
    await pool.query("DELETE FROM reviewer_accounts WHERE username = 'bob'");
    assert.equal((await pool.query('SELECT 1 FROM reviewer_identity_links')).rowCount, 0);
  });

  await t.test('each sign-in method is off unless configured', async () => {
    await assert.rejects(authority.issue('one', {username: 'alice', password: 'fixture-password'}), status(404));
    const passwordOnly = createGrantAuthority(pool);
    assert.equal((await passwordOnly.issue('one', {username: 'alice', password: 'fixture-password'})).name, 'alice');
    await assert.rejects(passwordOnly.issueOidc('one', exchange('subject-alice')), status(404));
  });

  await t.test('exchange attempts are rate limited per installation', async () => {
    await pool.query('DELETE FROM reviewer_login_buckets');
    const attempt = () => authority.issueOidc('flood', {idToken: 'a.b.c', nonce: newNonce()}).catch(error => error.status);
    const results = await Promise.all(Array.from({length: 70}, attempt));
    assert.equal(results.filter(code => code === 401).length, 60);
    assert.equal(results.filter(code => code === 429).length, 10);
  });
});

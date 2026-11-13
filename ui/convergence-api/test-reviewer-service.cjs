'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {X509Certificate, randomUUID} = require('node:crypto');
const {once} = require('node:events');
const fs = require('node:fs');
const https = require('node:https');
const os = require('node:os');
const path = require('node:path');
const {Pool} = require('pg');
const {Readable} = require('node:stream');
const express = require('express');
const jwt = require('jsonwebtoken');
const {provision} = require('./reviewer-accounts');
const {migrate} = require('./reviewer-service');
const {createGrantAuthority} = require('./src/reviewer/grants');
const {createReviewerService} = require('./src/reviewer/transport');

function certificates(folder) {
  const run = args => execFileSync('openssl', args, {cwd: folder, stdio: 'pipe'});
  function key(name) {
    run(['req', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
      '-subj', '/CN=' + name, '-keyout', name + '.key', '-out', name + '.csr']);
  }
  key('ca');
  run(['x509', '-req', '-days', '1', '-in', 'ca.csr', '-signkey', 'ca.key',
    '-out', 'ca.pem', '-set_serial', '1', '-extfile', 'ca.ext']);
  const data = name => fs.readFileSync(path.join(folder, name));
  const cert = name => ({cert: data(name + '.pem'), key: data(name + '.key')});
  let serial = 1;
  for (const name of ['localhost', 'reviewer', 'other', 'unenrolled']) {
    key(name);
    const usage = name === 'localhost' ? 'serverAuth' : 'clientAuth';
    fs.writeFileSync(path.join(folder, 'leaf.ext'), 'basicConstraints=CA:FALSE\nsubjectAltName=DNS:' + name + '\nextendedKeyUsage=' + usage);
    run(['x509', '-req', '-days', '1', '-in', name + '.csr', '-CA', 'ca.pem',
      '-CAkey', 'ca.key', '-set_serial', String(++serial), '-extfile', 'leaf.ext', '-out', name + '.pem']);
  }
  return {ca: data('ca.pem'), cert};
}

test('two mTLS reviewer services authorize and audit one durable case disposition', {
  skip: !process.env.REVIEWER_TEST_DATABASE_URL, timeout: 30000,
}, async t => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'reviewer-https-'));
  t.after(() => fs.rmSync(folder, {recursive: true, force: true}));
  fs.writeFileSync(path.join(folder, 'ca.ext'), 'basicConstraints=critical,CA:TRUE');
  const {ca, cert} = certificates(folder);
  const enrollments = ['reviewer', 'other'].map(name => ({
    installation: name, fingerprint256: new X509Certificate(cert(name).cert).fingerprint256,
  }));

  const connectionString = process.env.REVIEWER_TEST_DATABASE_URL;
  const admin = new Pool({connectionString});
  const schema = 'service_' + randomUUID().replaceAll('-', '');
  await admin.query('CREATE SCHEMA ' + schema);
  const options = {connectionString, options: '-c search_path=' + schema};
  const first = new Pool(options);
  const second = new Pool(options);
  t.after(async () => {
    await Promise.all([first.end(), second.end()]);
    try { await admin.query('DROP SCHEMA ' + schema + ' CASCADE'); } finally { await admin.end(); }
  });
  await first.query(fs.readFileSync(path.join(__dirname, 'src/db/schema.sql'), 'utf8'));
  await Promise.all([migrate(first), migrate(second)]);
  await first.query("INSERT INTO sites (id,name) VALUES ('red','Red'), ('blue','Blue')");
  const passwordInput = () => Readable.from([Buffer.from('synthetic-password\n')]);
  await assert.rejects(provision(first, ['create', 'alice', 'operator', 'absent'], passwordInput()), /Unknown case site/);
  await provision(first, ['create', 'alice', 'operator', 'red'], passwordInput());
  await provision(first, ['create', 'bob', 'viewer', 'blue'], passwordInput());
  const {rows: [aliceRow]} = await first.query("SELECT id FROM reviewer_accounts WHERE username='alice'");
  const {rows: [event]} = await first.query(`INSERT INTO events(site_id, message, alert_name)
    VALUES ('red','Synthetic case','SuricataIntrusion') RETURNING id`);
  // Use the real standalone auth route against the same directory. The fixture
  // isolates the DB pool while preserving route initialization and JWT issuance.
  const db = require('./src/db');
  const originalPool = db.pool;
  const originalSource = process.env.CONVERGENCE_ACCOUNT_SOURCE;
  const originalSecret = process.env.JWT_SECRET;
  const restore = (name, value) => value === undefined ? delete process.env[name] : process.env[name] = value;
  let router, webAuth;
  const secret = randomUUID();
  try {
    db.pool = first;
    process.env.CONVERGENCE_ACCOUNT_SOURCE = 'postgres';
    for (const bad of [undefined, 'dev-secret', 'short', 'change-me-to-a-strong-random-string', 'change-me-convergence-api-jwt', '<generate with: openssl rand -base64 32>']) {
      restore('JWT_SECRET', bad);
      assert.throws(() => require('./src/routes/auth'), /explicit JWT/);
    }
    process.env.JWT_SECRET = secret;
    router = require('./src/routes/auth');
    webAuth = require('./src/middleware/auth').authMiddleware;
  } finally {
    db.pool = originalPool;
    restore('JWT_SECRET', originalSecret);
    restore('CONVERGENCE_ACCOUNT_SOURCE', originalSource);
  }
  const web = express().use(express.json()).use('/api/auth', router)
    .get('/protected', webAuth, (req, res) => res.json({site: req.site})).listen(0, '127.0.0.1');
  await once(web, 'listening');
  t.after(() => new Promise(resolve => web.close(resolve)));
  const standaloneLogin = () => fetch('http://127.0.0.1:' + web.address().port + '/api/auth/login', {
    method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({username: 'alice', password: 'synthetic-password'}),
  });
  const standalone = await standaloneLogin();
  assert.equal(standalone.status, 200);
  const webToken = (await standalone.json()).token;
  assert.equal(jwt.verify(webToken, secret).sub, aliceRow.id);
  const webSite = (token, site) => fetch('http://127.0.0.1:' + web.address().port + '/protected?site=' + site,
    {headers: {authorization: 'Bearer ' + token}});
  assert.equal((await webSite(webToken, 'red')).status, 200);
  assert.equal((await webSite(webToken, 'blue')).status, 403);
  async function start(pool) {
    const server = createReviewerService({tls: {ca, ...cert('localhost')}, enrollments, authority: createGrantAuthority(pool)});
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    t.after(() => new Promise(resolve => server.close(resolve)));
    return server.address().port;
  }
  const portA = await start(first);
  const portB = await start(second);
  function request(port, url, {id = 'reviewer', method = 'GET', token, body, headers = {}} = {}) {
    return new Promise((resolve, reject) => {
      const req = https.request({hostname: '127.0.0.1', servername: 'localhost', port, path: url,
        method, ca, agent: false, ...(id ? cert(id) : {}),
        headers: {...(token && {authorization: 'Bearer ' + token}), ...(body && {'content-type': 'application/json'}), ...headers},
      }, res => {
        let out = '';
        res.setEncoding('utf8');
        res.on('data', piece => { out += piece; });
        res.on('end', () => resolve({status: res.statusCode, body: out ? JSON.parse(out) : null}));
      });
      req.on('error', reject);
      req.end(body && JSON.stringify(body));
    });
  }
  const login = username => request(portA, '/v1/grants', {method: 'POST', body: {username, password: 'synthetic-password'}});
  await assert.rejects(request(portA, '/v1/grants/current', {id: null}));
  assert.equal((await request(portA, '/v1/grants/current', {id: 'unenrolled', headers: {'x-forwarded-installation': 'reviewer'}})).status, 403);
  assert.equal((await request(portA, '/api/auth/login')).status, 404);
  const alice = await login('alice');
  const bob = await login('bob');
  assert.equal(alice.status, 201);
  assert.equal(bob.status, 201);
  const token = alice.body.token;
  assert.equal((await request(portB, '/v1/grants/current', {token, id: 'other'})).status, 401);
  const url = '/v1/sites/red/cases/' + event.id;
  assert.equal((await request(portB, url, {token, headers: {origin: 'https://foreign.invalid'}})).status, 403);
  assert.equal((await request(portB, url, {token: bob.body.token})).status, 403);
  assert.equal((await request(portB, url, {token})).body.id, event.id);
  const write = {operationId: randomUUID(), kind: 'needs_review', reason: 'Synthetic observed traffic requires investigation'};
  const post = body => request(portB, url + '/disposition', {method: 'POST', token, body});
  assert.equal((await post({...write, actor: 'u2'})).status, 400);
  const a = await post(write);
  assert.equal(a.status, 200);
  assert.equal(a.body.reviewed_by, aliceRow.id);
  assert.deepEqual(await post(write), a);
  assert.equal((await post({...write, reason: 'A second unexpected payload'})).status, 409);
  assert.deepEqual(await request(portA, url + '/disposition/' + write.operationId, {token}), a);
  const reviewed = await request(portA, url, {token});
  assert.equal(reviewed.body.review_history.length, 1);
  assert.equal(reviewed.body.security_disposition.reviewed_by, aliceRow.id);
  await provision(first, ['access', 'alice', 'admin', 'red']);
  assert.equal((await webSite(webToken, 'red')).status, 401);
  const adminToken = (await (await standaloneLogin()).json()).token;
  assert.equal((await webSite(adminToken, 'blue')).status, 403);
  assert.equal((await webSite(adminToken, 'red')).status, 200);
  await provision(first, ['access', 'alice', 'viewer', 'red']);
  assert.equal((await post({...write, operationId: randomUUID()})).status, 403);
  assert.equal((await request(portA, '/v1/grants/current', {token, method: 'DELETE'})).status, 204);
  assert.equal((await request(portB, url, {token})).status, 401);
  assert.equal((await request(portB, '/v1/grants/current', {token: bob.body.token})).status, 200);
  await provision(second, ['password', 'bob'], Readable.from([Buffer.from('fixture-rotated')]));
  assert.equal((await request(portB, '/v1/grants/current', {token: bob.body.token})).status, 401);
  await provision(second, ['disable', 'alice']);
  assert.equal((await login('alice')).status, 401);
  assert.equal((await standaloneLogin()).status, 401);
});

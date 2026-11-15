'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const https = require('node:https');
const {execFileSync} = require('node:child_process');
const {X509Certificate} = require('node:crypto');
const {once} = require('node:events');
const {createReviewerService} = require('./src/reviewer/transport');
const {createReceiverClient} = require('./src/reviewer/relay');
const {identity, trust, watchTls} = require('./src/reviewer/tls-reload');

const openssl = args => execFileSync('openssl', args, {stdio: 'ignore'});

// Throwaway CAs and leaves, published the way kubelet updates a Secret volume.
function pki(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'reviewer-api-tls-'));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  const file = name => path.join(dir, name);
  let serial = 0;
  const ca = name => openssl(['req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
    '-days', '2', '-subj', '/CN=' + name, '-keyout', file(name + '.key'), '-out', file(name + '.crt')]);
  const leaf = (issuer, name) => {
    openssl(['req', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes', '-subj', '/CN=' + name,
      '-keyout', file(name + '.key'), '-out', file(name + '.csr')]);
    fs.writeFileSync(file(name + '.ext'), 'subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth,clientAuth\n');
    openssl(['x509', '-req', '-in', file(name + '.csr'), '-CA', file(issuer + '.crt'), '-CAkey', file(issuer + '.key'),
      '-set_serial', String(++serial), '-days', '1', '-extfile', file(name + '.ext'), '-out', file(name + '.crt')]);
    return {cert: fs.readFileSync(file(name + '.crt')), key: fs.readFileSync(file(name + '.key'))};
  };
  const pem = name => fs.readFileSync(file(name + '.crt'));
  const mount = file('mount');
  fs.mkdirSync(mount);
  let version = 0;
  const publish = contents => {
    const data = path.join(mount, '..' + ++version);
    fs.mkdirSync(data);
    for (const [name, value] of Object.entries(contents)) fs.writeFileSync(path.join(data, name), value);
    fs.symlinkSync(path.basename(data), path.join(mount, '..tmp'));
    fs.renameSync(path.join(mount, '..tmp'), path.join(mount, '..data'));
    for (const name of Object.keys(contents)) {
      try { fs.symlinkSync(path.join('..data', name), path.join(mount, name)); } catch (e) { if (e.code !== 'EEXIST') throw e; }
    }
  };
  const paths = names => Object.fromEntries(names.map(name => [name, path.join(mount, name)]));
  return {ca, leaf, pem, publish, paths};
}

const pin = cert => new X509Certificate(cert).fingerprint256;
const enrolled = (...certs) => JSON.stringify(certs.map(cert => ({installation: 'home', fingerprint256: pin(cert)})));

function call(port, client, ca) {
  return new Promise((resolve, reject) => {
    const req = https.request({host: '127.0.0.1', port, path: '/v1/unknown', agent: false, ca, ...client}, res => {
      res.resume();
      resolve({status: res.statusCode, served: res.socket.getPeerCertificate().fingerprint256});
    });
    req.on('error', reject);
    req.end();
  });
}

test('the reviewer API renews its identity and rotates installation pins with an overlap', async t => {
  const {ca, leaf, pem, publish, paths} = pki(t);
  ca('api-ca'); ca('install-ca');
  const service = leaf('api-ca', 'service');
  const oldInstall = leaf('install-ca', 'install-old');
  const newInstall = leaf('install-ca', 'install-new');
  publish({ca: pem('install-ca'), ...service, enrollments: enrolled(oldInstall.cert)});
  const server = createReviewerService({tls: {ca: pem('install-ca'), ...service},
    enrollments: JSON.parse(enrolled(oldInstall.cert)), authority: {}});
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => { server.closeAllConnections(); server.close(); });
  const {port} = server.address();
  const logs = [];
  const watcher = watchTls({files: paths(['ca', 'cert', 'key', 'enrollments']), intervalMs: 3600000, log: m => logs.push(m),
    build: ({enrollments, ...material}) => ({...identity(material), enrollments: JSON.parse(enrollments)}),
    apply: next => server.reload(next)});
  t.after(() => watcher.close());
  await watcher.check();
  const as = install => call(port, install, pem('api-ca'));

  assert.equal((await as(oldInstall)).status, 404, 'an enrolled installation reaches routing');
  assert.equal((await as(newInstall)).status, 403);

  // Step 1: enroll the new pin beside the old one. Both work: no 403 window.
  publish({ca: pem('install-ca'), ...service, enrollments: enrolled(oldInstall.cert, newInstall.cert)});
  await watcher.check();
  assert.equal((await as(oldInstall)).status, 404);
  assert.equal((await as(newInstall)).status, 404);

  // An invalid enrollment file is rejected as a whole; the overlap stays in force.
  publish({ca: pem('install-ca'), ...service, enrollments: '[{"installation":"home","fingerprint256":"nope"}]'});
  await watcher.check();
  assert.match(watcher.state.error, /Invalid reviewer installation enrollment/);
  assert.equal((await as(newInstall)).status, 404);

  // Step 2: drop the old pin, and renew the service's own certificate at the same time.
  const renewed = leaf('api-ca', 'service-renewed');
  publish({ca: pem('install-ca'), ...renewed, enrollments: enrolled(newInstall.cert)});
  await watcher.check();
  assert.equal((await as(oldInstall)).status, 403);
  const after = await as(newInstall);
  assert.equal(after.status, 404);
  assert.equal(after.served, pin(renewed.cert));
  assert.equal(watcher.state.error, null);
  assert.equal(watcher.state.reloads, 2);
  assert.ok(!logs.some(line => line.includes('BEGIN') || line.includes(pin(oldInstall.cert))), 'logs carry no material');
});

test('the relay follows a receiver CA rollover without restarting', async t => {
  const {ca, leaf, pem, publish, paths} = pki(t);
  ca('receiver-old'); ca('receiver-new');
  const served = {current: leaf('receiver-old', 'receiver-a')};
  const server = https.createServer(served.current, (req, res) => {
    res.writeHead(200, {'content-type': 'application/json'});
    res.end('{"state":"enqueued"}');
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => { server.closeAllConnections(); server.close(); });
  publish({ca: pem('receiver-old')});
  const receiver = createReceiverClient({origin: 'https://127.0.0.1:' + server.address().port,
    token: 'synthetic-service-token-0123456789abcdef', ca: pem('receiver-old'), deadlineMs: 500});
  t.after(() => receiver.close());
  const watcher = watchTls({files: paths(['ca']), build: trust, intervalMs: 3600000, log() {},
    apply: next => receiver.reload(next)});
  t.after(() => watcher.close());
  await watcher.check();
  const id = '00000000-0000-4000-8000-000000000000';
  assert.deepEqual(await receiver.read(id), {state: 'enqueued'});

  publish({ca: 'not a certificate'});
  await watcher.check();
  assert.match(watcher.state.error, /no certificate/);
  server.closeIdleConnections();
  assert.deepEqual(await receiver.read(id), {state: 'enqueued'}, 'the previous trust stays in force');

  publish({ca: Buffer.concat([pem('receiver-old'), pem('receiver-new')])});
  await watcher.check();
  server.setSecureContext(leaf('receiver-new', 'receiver-b'));
  server.closeIdleConnections();
  assert.deepEqual(await receiver.read(id), {state: 'enqueued'});
  publish({ca: pem('receiver-new')});
  await watcher.check();
  assert.deepEqual(await receiver.read(id), {state: 'enqueued'});
  assert.equal(watcher.state.reloads, 2);
});

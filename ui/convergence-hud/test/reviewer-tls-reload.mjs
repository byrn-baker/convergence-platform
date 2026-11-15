import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {X509Certificate} from 'node:crypto';
import {once} from 'node:events';
import {mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, symlinkSync, writeFileSync} from 'node:fs';
import https from 'node:https';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {createReviewerServer} from '../reviewer/server.mjs';
import {createReviewerClient} from '../reviewer/service-client.mjs';
import {identity, watchTls} from '../reviewer/tls-reload.mjs';

const host = 'reviewer.test';
const openssl = args => execFileSync('openssl', args, {stdio: 'ignore'});

// Throwaway PKI: CAs and short-lived leaves, written the way kubelet publishes a Secret
// (files behind a ..data symlink that is swapped atomically).
function pki(t) {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'reviewer-tls-'));
  t.after(() => rmSync(dir, {recursive: true, force: true}));
  const file = name => path.join(dir, name);
  const ca = name => {
    openssl(['req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes', '-days', '2',
      '-subj', '/CN=' + name, '-keyout', file(name + '.key'), '-out', file(name + '.crt')]);
    return name;
  };
  let serial = 0;
  const leaf = (issuer, name, {days = 1, start} = {}) => {
    openssl(['req', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes', '-subj', '/CN=' + name,
      '-keyout', file(name + '.key'), '-out', file(name + '.csr')]);
    writeFileSync(file(name + '.ext'), `subjectAltName=DNS:${host},IP:127.0.0.1\nextendedKeyUsage=serverAuth,clientAuth\n`);
    // openssl x509 -req cannot backdate, so an expired leaf comes from `ca` with fixed dates.
    if (start) {
      writeFileSync(file('db.txt'), '');
      writeFileSync(file('ca.cnf'), `[ca]\ndefault_ca=c\n[c]\ndatabase=${file('db.txt')}\nnew_certs_dir=${dir}\n` +
        `default_md=sha256\npolicy=p\nunique_subject=no\nserial=${file('serial')}\n[p]\ncommonName=supplied\n`);
      writeFileSync(file('serial'), String(1000 + ++serial) + '\n');
      openssl(['ca', '-batch', '-config', file('ca.cnf'), '-cert', file(issuer + '.crt'), '-keyfile', file(issuer + '.key'),
        '-in', file(name + '.csr'), '-out', file(name + '.crt'), '-startdate', start.from, '-enddate', start.to,
        '-extfile', file(name + '.ext'), '-notext']);
    } else {
      openssl(['x509', '-req', '-in', file(name + '.csr'), '-CA', file(issuer + '.crt'), '-CAkey', file(issuer + '.key'),
        '-set_serial', String(++serial), '-days', String(days), '-extfile', file(name + '.ext'), '-out', file(name + '.crt')]);
    }
    return {cert: readFileSync(file(name + '.crt')), key: readFileSync(file(name + '.key'))};
  };
  const pem = name => readFileSync(file(name + '.crt'));
  let version = 0;
  const mount = path.join(dir, 'mount');
  mkdirSync(mount);
  const publish = contents => {
    const data = path.join(mount, '..' + ++version);
    mkdirSync(data);
    for (const [name, value] of Object.entries(contents)) writeFileSync(path.join(data, name), value);
    symlinkSync(path.basename(data), path.join(mount, '..tmp'));
    renameSync(path.join(mount, '..tmp'), path.join(mount, '..data'));
    for (const name of Object.keys(contents)) {
      try { symlinkSync(path.join('..data', name), path.join(mount, name)); } catch (error) { if (error.code !== 'EEXIST') throw error; }
    }
  };
  const paths = names => Object.fromEntries(names.map(name => [name, path.join(mount, name)]));
  return {ca, leaf, pem, publish, paths};
}

const fingerprint = cert => new X509Certificate(cert).fingerprint256;

async function servedFingerprint(port, ca) {
  return new Promise((resolve, reject) => {
    const req = https.request({host: '127.0.0.1', port, servername: host, ca, agent: false, path: '/',
      headers: {host}}, res => { res.resume(); resolve(res.socket.getPeerCertificate().fingerprint256); });
    req.on('error', reject);
    req.end();
  });
}

test('identity accepts a current matching pair and rejects mismatched, expired or empty material', t => {
  const {ca, leaf, pem} = pki(t);
  ca('root');
  const good = leaf('root', 'good');
  const other = leaf('root', 'other');
  const expired = leaf('root', 'expired', {start: {from: '20200101000000Z', to: '20200102000000Z'}});
  assert.equal(identity({...good, ca: pem('root')}).notAfter, Date.parse(new X509Certificate(good.cert).validTo));
  assert.throws(() => identity({cert: good.cert, key: other.key}), /does not match/);
  assert.throws(() => identity(expired), /not currently valid/);
  assert.throws(() => identity({cert: '', key: good.key}), /no certificate/);
  assert.throws(() => identity({...good, ca: 'not a certificate'}), /no certificate/);
});

test('a renewed browser certificate is served in place, keeping reviewer sessions', async t => {
  const {ca, leaf, pem, publish, paths} = pki(t);
  ca('root');
  const first = leaf('root', 'first');
  publish(first);
  const server = createReviewerServer({origin: 'https://' + host, assets: new Map(), tls: first,
    client: {close() {}, current: async () => { throw new Error('unused'); }}});
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => server.close());
  const {port} = server.address();
  const logs = [];
  const watcher = watchTls({files: paths(['cert', 'key']), build: identity, intervalMs: 3600000, log: m => logs.push(m),
    apply: ({cert, key}) => server.setSecureContext({cert, key, minVersion: 'TLSv1.2'})});
  t.after(() => watcher.close());
  await watcher.check();
  assert.equal(watcher.state.reloads, 0, 'the first poll establishes the baseline');

  const session = async cookie => new Promise((resolve, reject) => {
    const req = https.request({host: '127.0.0.1', port, servername: host, ca: pem('root'), agent: false,
      path: '/review/session', headers: {host, ...(cookie ? {cookie} : {})}}, res => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => resolve({cookie: res.headers['set-cookie']?.[0]?.split(';')[0], body: JSON.parse(Buffer.concat(chunks))}));
    });
    req.on('error', reject);
    req.end();
  });
  const signedIn = await session();
  assert.ok(signedIn.cookie && signedIn.body.csrf);
  assert.equal(await servedFingerprint(port, pem('root')), fingerprint(first.cert));

  const renewed = leaf('root', 'renewed');
  publish(renewed);
  await watcher.check();
  assert.equal(watcher.state.reloads, 1);
  assert.equal(await servedFingerprint(port, pem('root')), fingerprint(renewed.cert));
  const after = await session(signedIn.cookie);
  assert.equal(after.body.csrf, signedIn.body.csrf, 'the in-memory session survives the reload');
  assert.equal(after.cookie, undefined, 'no new session was bootstrapped');
  assert.deepEqual(logs, ['TLS material reloaded']);
});

test('a half-written or expired renewal keeps serving the previous certificate until it is corrected', async t => {
  const {ca, leaf, pem, publish, paths} = pki(t);
  ca('root');
  const first = leaf('root', 'first');
  publish(first);
  const server = https.createServer({...first, minVersion: 'TLSv1.2'}, (req, res) => res.end());
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => server.close());
  const {port} = server.address();
  const logs = [];
  const watcher = watchTls({files: paths(['cert', 'key']), build: identity, intervalMs: 3600000, log: m => logs.push(m),
    apply: ({cert, key}) => server.setSecureContext({cert, key})});
  t.after(() => watcher.close());
  await watcher.check();

  const next = leaf('root', 'next');
  publish({cert: next.cert, key: first.key});
  await watcher.check();
  await watcher.check();
  assert.equal(await servedFingerprint(port, pem('root')), fingerprint(first.cert));
  assert.match(watcher.state.error, /key does not match/);

  const expired = leaf('root', 'expired', {start: {from: '20200101000000Z', to: '20200102000000Z'}});
  publish(expired);
  await watcher.check();
  assert.equal(await servedFingerprint(port, pem('root')), fingerprint(first.cert));
  assert.match(watcher.state.error, /not currently valid/);

  publish(next);
  await watcher.check();
  assert.equal(await servedFingerprint(port, pem('root')), fingerprint(next.cert));
  assert.equal(watcher.state.error, null);
  assert.deepEqual(logs, ['TLS reload rejected: key does not match certificate',
    'TLS reload rejected: certificate is not currently valid', 'TLS material reloaded'],
    'each distinct failure is logged once, without certificate contents');
});

test('the installation client switches identity and survives a service CA rollover', async t => {
  const {ca, leaf, pem, publish, paths} = pki(t);
  ca('old-ca');
  ca('new-ca');
  ca('install-ca');
  let serving = leaf('old-ca', 'service-old');
  const presented = [];
  const service = https.createServer({...serving, ca: pem('install-ca'), requestCert: true, rejectUnauthorized: true},
    (req, res) => {
      presented.push(req.socket.getPeerCertificate().fingerprint256);
      res.writeHead(200, {'content-type': 'application/json'});
      res.end(JSON.stringify({user: {id: 'alice'}}));
    });
  service.listen(0, '127.0.0.1');
  await once(service, 'listening');
  t.after(() => service.close());
  const origin = 'https://127.0.0.1:' + service.address().port;
  const first = leaf('install-ca', 'install-old');
  publish({ca: pem('old-ca'), ...first});
  const client = createReviewerClient({origin, tls: {ca: pem('old-ca'), ...first}, deadlineMs: 2000});
  t.after(() => client.close());
  const watcher = watchTls({files: paths(['ca', 'cert', 'key']), build: identity, intervalMs: 3600000, log() {},
    apply: next => client.reload(next)});
  t.after(() => watcher.close());
  await watcher.check();
  const token = 'a'.repeat(43);
  await client.current(token);

  // Rollover: trust old+new CA, rotate the installation identity, then move the service.
  const second = leaf('install-ca', 'install-new');
  publish({ca: Buffer.concat([pem('old-ca'), pem('new-ca')]), ...second});
  await watcher.check();
  await client.current(token);
  serving = leaf('new-ca', 'service-new');
  service.setSecureContext({...serving, ca: pem('install-ca')});
  await client.current(token);
  assert.deepEqual(presented.slice(-2), [fingerprint(second.cert), fingerprint(second.cert)]);
  assert.equal(presented[0], fingerprint(first.cert));
});

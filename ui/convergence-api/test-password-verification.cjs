'use strict';

const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { once } = require('node:events');
const { test } = require('node:test');
const bcrypt = require('bcrypt');
const express = require('express');
const jwt = require('jsonwebtoken');

test('standalone login preserves JWT claims, rejects malformed inputs and conceals account existence', async t => {
  const password = ' password with surrounding whitespace '.repeat(3);
  const account = {
    id: 'operator-1', username: 'operator', name: 'Case operator', role: 'operator',
    sites: ['north', 'south'], passwordHash: await bcrypt.hash(password, 5),
  };
  const secret = randomUUID();
  t.mock.method(bcrypt, 'compare');
  const oldUsers = process.env.USERS;
  const oldSecret = process.env.JWT_SECRET;
  process.env.USERS = JSON.stringify([account]);
  process.env.JWT_SECRET = secret;
  const router = require('./src/routes/auth');
  if (oldUsers === undefined) delete process.env.USERS;
  else process.env.USERS = oldUsers;
  if (oldSecret === undefined) delete process.env.JWT_SECRET;
  else process.env.JWT_SECRET = oldSecret;

  const app = express();
  app.use(express.json());
  app.use('/api/auth', router);
  const server = app.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => new Promise(resolve => server.close(resolve)));
  const url = 'http://127.0.0.1:' + server.address().port + '/api/auth/login';
  async function login(body) {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return { status: response.status, body: await response.json() };
  }

  for (const body of [{username: 'operator'}, {username: 'operator', password: {}},
    {username: ['operator'], password}, {username: '', password}, {username: 'operator', password: ''}]) {
    assert.deepEqual(await login(body), {status: 400, body: {error: 'Username and password required'}});
  }
  assert.equal(bcrypt.compare.mock.callCount(), 0);

  const unknown = await login({username: 'absent', password});
  assert.deepEqual(unknown, {status: 401, body: {error: 'Invalid credentials'}});
  assert.deepEqual(await login({username: 'operator', password: 'wrong'}), unknown);
  const checks = bcrypt.compare.mock.calls;
  assert.equal(checks.length, 2);
  assert.equal(bcrypt.getRounds(checks[0].arguments[1]), bcrypt.getRounds(checks[1].arguments[1]));

  assert.notEqual(checks[0].arguments[1], account.passwordHash);

  const {status, body} = await login({username: 'operator', password});
  assert.equal(status, 200);
  const {token, ...metadata} = body;
  assert.deepEqual(metadata, {expiresIn: 86400, sites: account.sites, role: 'operator', name: 'Case operator'});
  const {iat, exp, ...claims} = jwt.verify(token, secret);
  assert.equal(exp - iat, 86400);
  assert.deepEqual(claims, {sub: account.id, sites: account.sites, role: 'operator', name: 'Case operator'});
});

test('password verification queries the supplied account source each time and returns only identity fields', async () => {
  const { createPasswordVerifier } = require('./src/lib/password-verification');
  let account = {id: '1', username: 'alice', passwordHash: await bcrypt.hash('first', 4), role: 'viewer', sites: ['red']};
  const verify = createPasswordVerifier(async username => username === account?.username ? account : null);
  assert.deepEqual(await verify('alice', 'first'), {id: '1', name: 'alice', role: 'viewer', sites: ['red']});
  account = {...account, role: 'operator', passwordHash: await bcrypt.hash('second', 4)};
  assert.equal(await verify('alice', 'first'), null);
  assert.equal((await verify('alice', 'second')).role, 'operator');
  account = null;
  assert.equal(await verify('alice', 'second'), null);
});

test('legacy hashes authenticate and unusable account hashes take the unknown-user comparison path', async t => {
  const {createPasswordVerifier} = require('./src/lib/password-verification');
  const password = 'private fixture password';
  const account = {id: '1', username: 'fixture'};
  const representative = await bcrypt.hash(password, 4);
  const verify = createPasswordVerifier(async username => username === account.username ? account : null, representative);
  t.mock.method(bcrypt, 'compare');

  for (const prefix of ['$2$', '$2a$', '$2b$']) {
    account.passwordHash = await bcrypt.hash(password, prefix + '04$abcdefghijklmnopqrstuu');
    assert.equal((await verify('fixture', password)).id, '1');
  }

  assert.equal(await verify('absent', password), null);
  const fallback = bcrypt.compare.mock.calls.at(-1).arguments[1];
  assert.equal(bcrypt.getRounds(fallback), 4);
  const unusable = [null, '', {}, [representative], 7, 'truncated', representative + '\0ignored',
    representative.slice(0, -1), representative.replace('$2b$', '$2y$'), representative.replace('$04$', '$03$')];
  for (const hash of unusable) {
    account.passwordHash = hash;
    assert.equal(await verify('fixture', password), null);
    assert.equal(bcrypt.compare.mock.calls.at(-1).arguments[1], fallback);
  }
});

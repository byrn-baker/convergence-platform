'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {randomBytes, randomUUID} = require('node:crypto');
const {execFileSync} = require('node:child_process');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {Readable} = require('node:stream');
const {listSignatures, parseSignatureQuery} = require('./src/reviewer/queue');
const {Pool} = require('pg');
const {migrate} = require('./reviewer-service');
const {provision} = require('./reviewer-accounts');
const {createGrantAuthority} = require('./src/reviewer/grants');
const {recordDisposition, readCase} = require('./src/reviewer/cases');

const connectionString = process.env.REVIEWER_TEST_DATABASE_URL;
const container = process.env.REVIEWER_PSQL_CONTAINER;

test('the deployed runtime and provisioning grants allow a complete review and separate account administration', {
  skip: !connectionString || !container,
}, async t => {
  const suffix = randomUUID().replaceAll('-', '');
  const schema = 'priv_' + suffix;
  const runtime = 'runtime_' + suffix;
  const provisioner = 'provisioner_' + suffix;
  const url = new URL(connectionString);
  const admin = new Pool({connectionString});
  // NOLOGIN roles exercise the permissions without creating a reusable credential.
  const scopedPool = role => new Pool({
    connectionString,
    options: '-c search_path=' + schema + (role ? ' -c role=' + role : ''),
  });
  const owner = scopedPool();
  const service = scopedPool(runtime);
  const directory = scopedPool(provisioner);
  t.after(async () => {
    await Promise.all([owner.end(), service.end(), directory.end()]);
    try {
      await admin.query('DROP SCHEMA IF EXISTS ' + schema + ' CASCADE');
      await admin.query('DROP ROLE IF EXISTS ' + runtime + ', ' + provisioner);
    } finally { await admin.end(); }
  });
  await admin.query('CREATE SCHEMA ' + schema);
  await admin.query('CREATE ROLE ' + runtime + ' NOLOGIN; CREATE ROLE ' + provisioner + ' NOLOGIN');
  await owner.query(readFileSync(path.join(__dirname, 'src/db/schema.sql'), 'utf8'));
  await migrate(owner);

  execFileSync('docker', ['exec', '-i', container, 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
    '-U', decodeURIComponent(url.username), '-d', decodeURIComponent(url.pathname.slice(1)),
    '-v', 'reviewer_schema=' + schema, '-v', 'reviewer_runtime=' + runtime, '-v', 'reviewer_provisioner=' + provisioner],
  {input: readFileSync(path.join(__dirname, 'src/reviewer/permissions.sql'))});
  assert.equal((await service.query('SELECT current_user')).rows[0].current_user, runtime);
  assert.equal((await directory.query('SELECT current_user')).rows[0].current_user, provisioner);

  await provision(directory, ['create', 'alice', 'operator', 'home'], Readable.from([Buffer.from('fixture')]));
  const authority = createGrantAuthority(service);
  const grant = await authority.issue('installation', {username: 'alice', password: 'fixture'});
  const {rows: [item]} = await owner.query("INSERT INTO events(site_id,message,alert_name) VALUES ('home','Synthetic review','SuricataIntrusion') RETURNING id");
  const command = {operationId: randomUUID(), kind: 'needs_review', reason: 'Synthetic review of captured traffic'};
  const receipt = await authority.withGrant('installation', grant.token, {site: 'home', write: true},
    (db, actor) => recordDisposition(db, actor, 'home', item.id, command));
  const reviewed = await authority.withGrant('installation', grant.token, {site: 'home', write: false},
    (db, actor) => readCase(db, actor, 'home', item.id));
  assert.equal(receipt.reviewed_by_name, 'alice');
  assert.equal(reviewed.review_history[0].operation_id, command.operationId);
  assert.equal((await owner.query('SELECT 1 FROM event_history WHERE event_id=$1', [item.id])).rowCount, 1);

  // The case queue: triggers running as the schema owner keep it current when the
  // restricted runtime writes a disposition. The runtime can read it and nothing more.
  const queued = async () => (await service.query('SELECT needs_review FROM reviewer_case_queue WHERE case_id=$1', [item.id])).rows[0];
  assert.equal((await queued()).needs_review, true);
  await authority.withGrant('installation', grant.token, {site: 'home', write: true},
    (db, actor) => recordDisposition(db, actor, 'home', item.id,
      {operationId: randomUUID(), kind: 'false_positive', reason: 'Synthetic duplicate capture'}));
  assert.equal((await queued()).needs_review, false);
  const queue = await authority.withGrant('installation', grant.token, {site: 'home', write: false},
    (db, actor) => listSignatures(db, actor, 'home', parseSignatureQuery('state=all')));
  assert.deepEqual(queue.signatures.map(row => [row.cases, row.needs_review]), [[1, 0]]);
  for (const statement of ["INSERT INTO reviewer_case_queue VALUES (gen_random_uuid(),'home',now(),'1','info',true)",
    'UPDATE reviewer_case_queue SET needs_review = true', 'DELETE FROM reviewer_case_queue', 'TRUNCATE reviewer_case_queue']) {
    await assert.rejects(service.query(statement), {code: '42501'});
  }
  await assert.rejects(directory.query('SELECT 1 FROM reviewer_case_queue'), {code: '42501'});

  // Bulk decisions: the runtime writes one and reads it back, and can do nothing else to
  // the table. Not even the schema owner can change or remove a recorded decision.
  const bulk = (values = {}) => {
    const row = {installation_id: 'installation', actor_id: receipt.reviewed_by, operation_id: randomUUID(), actor_name: 'alice',
      site_id: 'home', signature_id: '2900417', window_start: '2026-11-23T00:00:00Z', window_end: '2026-11-24T00:00:00Z',
      source_host: null, kind: 'false_positive', reason: 'Synthetic duplicate capture', case_count: 418, ...values};
    const columns = Object.keys(row);
    return {row, text: `INSERT INTO reviewer_bulk_dispositions (${columns}) VALUES (${columns.map((_, i) => '$' + (i + 1))})`,
      values: Object.values(row)};
  };
  const decision = bulk({source_host: '10.20.4.17'});
  await service.query(decision.text, decision.values);
  const {rows: [stored]} = await service.query(
    'SELECT kind, case_count, host(source_host) AS source_host, reviewed_at FROM reviewer_bulk_dispositions WHERE site_id=$1 AND operation_id=$2',
    ['home', decision.row.operation_id]);
  assert.deepEqual([stored.kind, stored.case_count, stored.source_host], ['false_positive', 418, '10.20.4.17']);
  assert.ok(stored.reviewed_at instanceof Date);
  await assert.rejects(service.query(decision.text, decision.values), {code: '23505'});
  // An operation ID belongs to one command: a single disposition's ID cannot be reused, nor the reverse.
  await assert.rejects(service.query(bulk({operation_id: command.operationId}).text, bulk({operation_id: command.operationId}).values),
    {code: '23505', constraint: 'reviewer_command_identity'});
  await assert.rejects(authority.withGrant('installation', grant.token, {site: 'home', write: true},
    (db, actor) => recordDisposition(db, actor, 'home', item.id,
      {operationId: decision.row.operation_id, kind: 'false_positive', reason: 'Synthetic reuse of a bulk operation'})), {status: 409});
  for (const invalid of [{kind: 'needs_review'}, {kind: 'ignored'}, {case_count: 0}, {reason: ''}, {reason: 'x'.repeat(4001)},
    {actor_name: ''}, {signature_id: '1'.repeat(65)}, {source_host: '10.20.4.0/24'}, {source_host: '2001:db8::/64'},
    {window_start: '2026-11-24T00:00:00Z'}]) {
    const attempt = bulk(invalid);
    await assert.rejects(service.query(attempt.text, attempt.values), {code: '23514'}, JSON.stringify(invalid));
  }
  for (const statement of ["UPDATE reviewer_bulk_dispositions SET reason = 'changed'", 'DELETE FROM reviewer_bulk_dispositions']) {
    await assert.rejects(service.query(statement), {code: '42501'});
    await assert.rejects(owner.query(statement), {code: '55000'});
  }
  await assert.rejects(service.query('TRUNCATE reviewer_bulk_dispositions'), {code: '42501'});
  // The list of covered cases references the decision, which already rules out truncating it.
  await assert.rejects(owner.query('TRUNCATE reviewer_bulk_dispositions'), {code: '0A000'});
  await assert.rejects(owner.query('TRUNCATE reviewer_bulk_dispositions CASCADE'), {code: '55000'});
  await assert.rejects(directory.query('SELECT 1 FROM reviewer_bulk_dispositions'), {code: '42501'});
  // The list of covered cases follows the same rules as the decision it belongs to.
  await service.query(`INSERT INTO reviewer_bulk_disposition_cases (installation_id, actor_id, operation_id, site_id, case_id)
    VALUES ('installation', $1, $2, 'home', $3)`, [receipt.reviewed_by, decision.row.operation_id, item.id]);
  await assert.rejects(service.query(`INSERT INTO reviewer_bulk_disposition_cases (installation_id, actor_id, operation_id, site_id, case_id)
    VALUES ('installation', $1, $2, 'home', $3)`, [receipt.reviewed_by, randomUUID(), item.id]), {code: '23503'});
  for (const statement of ['DELETE FROM reviewer_bulk_disposition_cases', "UPDATE reviewer_bulk_disposition_cases SET site_id = 'x'",
    'TRUNCATE reviewer_bulk_disposition_cases']) {
    await assert.rejects(service.query(statement), {code: '42501'});
    await assert.rejects(owner.query(statement), {code: '55000'});
  }
  await assert.rejects(directory.query('SELECT 1 FROM reviewer_bulk_disposition_cases'), {code: '42501'});
  const withBulk = await authority.withGrant('installation', grant.token, {site: 'home', write: false},
    (db, actor) => readCase(db, actor, 'home', item.id));
  assert.deepEqual(withBulk.review_history.map(row => row.bulk_case_count).sort(), [418, null, null]);
  assert.equal((await owner.query('SELECT count(*)::int AS n FROM reviewer_bulk_dispositions')).rows[0].n, 1);
  // Migrating again keeps the recorded decision and its operation key.
  await migrate(owner);
  assert.equal((await owner.query("SELECT count(*)::int AS n FROM reviewer_operation_keys WHERE command = 'bulk_disposition'")).rows[0].n, 1);

  for (const column of ['name', 'role', 'sites', 'id', 'password_hash', 'enabled', 'credential_revision']) {
    await assert.rejects(service.query('UPDATE reviewer_accounts SET ' + column + ' = ' + column), {code: '42501'});
  }
  await assert.rejects(service.query('DELETE FROM reviewer_case_dispositions'), {code: '42501'});
  await assert.rejects(directory.query('SELECT password_hash FROM reviewer_accounts'), {code: '42501'});
  await assert.rejects(directory.query('SELECT id FROM events'), {code: '42501'});

  // Identity-provider sign-in under the same two roles. Token checks are covered
  // elsewhere; this stand-in accepts one subject.
  const issuer = 'https://sso.test/realms/home';
  const oidc = {verify: async () => ({issuer, subject: 'subject-alice', digest: randomBytes(32),
    expiresAt: new Date(Date.now() + 360_000)})};
  const sso = createGrantAuthority(service, {oidc, passwordSignIn: false});
  const exchange = {idToken: 'a.b.c', nonce: 'A'.repeat(43)};
  await assert.rejects(sso.issueOidc('installation', exchange), {status: 403});
  await assert.rejects(service.query(`INSERT INTO reviewer_identity_links (issuer, subject, account_id)
    SELECT $1, 'subject-alice', id FROM reviewer_accounts`, [issuer]), {code: '42501'});
  await provision(directory, ['link', 'alice', issuer, 'subject-alice']);
  const linked = await sso.issueOidc('installation', exchange);
  assert.equal(linked.name, 'alice');
  await assert.rejects(service.query('DELETE FROM reviewer_identity_links'), {code: '42501'});
  await assert.rejects(directory.query('SELECT token_digest FROM reviewer_grants'), {code: '42501'});
  await assert.rejects(directory.query('SELECT * FROM reviewer_oidc_exchanges'), {code: '42501'});
  await provision(directory, ['unlink', 'alice', issuer, 'subject-alice']);
  await assert.rejects(sso.withGrant('installation', linked.token, {site: 'home', write: false}, (_db, actor) => actor), {status: 401});
  await authority.revoke('installation', grant.token);
  await provision(directory, ['disable', 'alice']);
});

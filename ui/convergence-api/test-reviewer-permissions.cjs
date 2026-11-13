'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
const {execFileSync} = require('node:child_process');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {Readable} = require('node:stream');
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

  for (const column of ['name', 'role', 'sites', 'id', 'password_hash', 'enabled', 'credential_revision']) {
    await assert.rejects(service.query('UPDATE reviewer_accounts SET ' + column + ' = ' + column), {code: '42501'});
  }
  await assert.rejects(service.query('DELETE FROM reviewer_case_dispositions'), {code: '42501'});
  await assert.rejects(directory.query('SELECT password_hash FROM reviewer_accounts'), {code: '42501'});
  await assert.rejects(directory.query('SELECT id FROM events'), {code: '42501'});
  await authority.revoke('installation', grant.token);
  await provision(directory, ['disable', 'alice']);
});

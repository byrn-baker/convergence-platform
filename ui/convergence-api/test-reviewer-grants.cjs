'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createHash, randomUUID} = require('node:crypto');
const {test} = require('node:test');
const bcrypt = require('bcrypt');
const {Pool} = require('pg');
const {createGrantAuthority, findReviewerAccount} = require('./src/reviewer/grants');
const {recordDisposition} = require('./src/reviewer/cases');

const url = process.env.REVIEWER_TEST_DATABASE_URL;
const schemaSql = fs.readFileSync(path.join(__dirname, 'src/reviewer/grants.sql'), 'utf8');
const errorStatus = code => error => error.status === code;
const read = {site: 'red', write: false};
const write = {site: 'red', write: true};
const getPrincipal = (_client, principal) => principal;

test('PostgreSQL reviewer grant authority', {skip: !url && 'Set REVIEWER_TEST_DATABASE_URL to an isolated PostgreSQL database'}, async t => {
  const schema = 'grant_test_' + randomUUID().replaceAll('-', '');
  const admin = new Pool({connectionString: url});
  await admin.query('CREATE SCHEMA ' + schema);
  const options = {connectionString: url, options: '-c search_path=' + schema, max: 8};
  const a = new Pool(options);
  const b = new Pool(options);
  t.after(async () => {
    await Promise.all([a.end(), b.end()]);
    try { await admin.query('DROP SCHEMA ' + schema + ' CASCADE'); }
    finally { await admin.end(); }
  });
  await a.query(schemaSql);
  await b.query(schemaSql);
  const first = createGrantAuthority(a);
  const second = createGrantAuthority(b);
  const password = randomUUID();
  const passwordHash = await bcrypt.hash(password, 4);

  async function add(username, role = 'operator') {
    await a.query(`INSERT INTO reviewer_accounts (id, username, name, password_hash, role, sites)
      VALUES ($1,$1,$1,$2,$3,ARRAY['red'])`, [username,passwordHash,role]);
    return {username,password};
  }
  async function readExpiry(token) {
    const {rows: [row]} = await a.query(`SELECT encode(token_digest,'hex') AS digest,
      idle_expires_at, expires_at FROM reviewer_grants WHERE token_digest = $1`,
    [createHash('sha256').update(token).digest()]);
    return row;
  }

  await t.test('grant uses a digest and explicit installation, and caps authority at both snapshots', async () => {
    const credentials = await add('alice', 'viewer');
    const grant = await first.issue('one', credentials);
    const stored = await readExpiry(grant.token);
    assert.match(grant.token, /^[A-Za-z0-9_-]{43}$/);
    assert.equal(stored.digest, createHash('sha256').update(grant.token).digest('hex'));
    assert.equal(+grant.expiresAt, +stored.expires_at);
    assert.ok(Math.abs(+stored.expires_at - Date.now() - 30 * 60_000) < 5000);
    assert.ok(Math.abs(+stored.idle_expires_at - Date.now() - 10 * 60_000) < 5000);
    assert.equal((await second.withGrant('one', grant.token, read, getPrincipal)).id, 'alice');
    await assert.rejects(second.withGrant('two', grant.token, read, getPrincipal), errorStatus(401));
    await second.revoke('two', grant.token);
    const grantColumns = (await a.query('SELECT * FROM reviewer_grants LIMIT 1')).rows[0];
    assert.ok(!Object.values(grantColumns).includes(grant.token));

    await b.query("UPDATE reviewer_accounts SET role='operator', sites=ARRAY['red','blue'] WHERE id='alice'");
    await assert.rejects(first.withGrant('one', grant.token, write, getPrincipal), errorStatus(403));
    await assert.rejects(first.withGrant('one', grant.token, {site:'blue',write:false}, getPrincipal), errorStatus(403));
    const summary = await second.withGrant('one', grant.token, {site:null,write:false}, getPrincipal);
    assert.equal(+grant.idleExpiresAt, +stored.idle_expires_at);
    assert.deepEqual(summary, {expiresAt:stored.expires_at,idleExpiresAt:stored.idle_expires_at,id:'alice',name:'alice',role:'viewer',sites:['red'],installation:'one'});

    const elevated = await second.issue('one', credentials);
    assert.equal((await first.withGrant('one', elevated.token, write, getPrincipal)).id, 'alice');
    await b.query("UPDATE reviewer_accounts SET role='viewer',sites=ARRAY['blue'] WHERE id='alice'");
    await assert.rejects(first.withGrant('one', elevated.token, read, getPrincipal), errorStatus(403));
    await assert.rejects(first.withGrant('one', elevated.token, {site:'blue',write:true}, getPrincipal), errorStatus(403));
    assert.equal((await first.withGrant('one', elevated.token, {site:'blue',write:false}, getPrincipal)).role, 'viewer');
  });

  await t.test('password rotation, disabling, and deleting an account invalidate earlier grants', async () => {
    const credentials = await add('bob');
    const issue = () => first.issue('one', credentials);
    const grant = await issue();
    await b.query("UPDATE reviewer_accounts SET password_hash=$1 WHERE id='bob'", [await bcrypt.hash('changed',4)]);
    await assert.rejects(second.withGrant('one', grant.token, read, getPrincipal), errorStatus(401));
    await assert.rejects(issue(), errorStatus(401));
    await b.query("UPDATE reviewer_accounts SET password_hash=$1 WHERE id='bob'", [passwordHash]);
    const renewed = await issue();
    await b.query("UPDATE reviewer_accounts SET enabled=false WHERE id='bob'");
    assert.equal(await findReviewerAccount(a, 'bob'), null);
    await assert.rejects(issue(), errorStatus(401));
    await b.query("UPDATE reviewer_accounts SET enabled=true WHERE id='bob'");
    await assert.rejects(second.withGrant('one', renewed.token, read, getPrincipal), errorStatus(401));
    const final = await issue();
    await b.query("DELETE FROM reviewer_accounts WHERE id='bob'");
    await add('bob');
    await assert.rejects(second.withGrant('one', final.token, read, getPrincipal), errorStatus(401));
  });

  await t.test('entitlement changes during password verification cancel issuance', async t => {
    const credentials = await add('rotating');
    const compare = bcrypt.compare;
    t.mock.method(bcrypt, 'compare', async function (...args) {
      const ok = await compare.apply(bcrypt, args);
      await b.query("UPDATE reviewer_accounts SET sites=ARRAY['blue'] WHERE id='rotating'");
      return ok;
    });
    await assert.rejects(first.issue('one', credentials), errorStatus(401));
    assert.equal((await a.query("SELECT * FROM reviewer_grants WHERE account_id='rotating'")).rowCount, 0);
  });

  await t.test('poll reads preserve idle expiry, writes renew only to the absolute ceiling', async () => {
    const grant = await first.issue('one', await add('idle'));
    await b.query("UPDATE reviewer_grants SET idle_expires_at=clock_timestamp()+interval '15 seconds' WHERE account_id='idle'");
    const before = await readExpiry(grant.token);
    await first.withGrant('one', grant.token, read, getPrincipal);
    assert.equal(+(await readExpiry(grant.token)).idle_expires_at, +before.idle_expires_at);
    await first.withGrant('one', grant.token, write, getPrincipal);
    assert.ok(+(await readExpiry(grant.token)).idle_expires_at > +before.idle_expires_at);
    await b.query("UPDATE reviewer_grants SET expires_at=clock_timestamp()+interval '10 seconds' WHERE account_id='idle'");
    await second.withGrant('one', grant.token, write, getPrincipal);
    const last = await readExpiry(grant.token);
    assert.equal(+last.idle_expires_at, +last.expires_at);
    await b.query("UPDATE reviewer_grants SET expires_at=clock_timestamp()-interval '1 second' WHERE account_id='idle'");
    await assert.rejects(first.withGrant('one', grant.token, read, getPrincipal), errorStatus(401));
    const other = await first.issue('one', await add('idle_expired'));
    await b.query("UPDATE reviewer_grants SET idle_expires_at=clock_timestamp()-interval '1 second' WHERE account_id='idle_expired'");
    await assert.rejects(first.withGrant('one', other.token, read, getPrincipal), errorStatus(401));
  });

  await t.test('expiry before commit rolls back the private operation', async () => {
    const grant = await first.issue('one', await add('timeout'));
    await b.query("UPDATE reviewer_grants SET idle_expires_at=clock_timestamp()+interval '250 milliseconds' WHERE account_id='timeout'");
    await assert.rejects(first.withGrant('one', grant.token, write, async client => {
      await client.query("UPDATE reviewer_accounts SET name='should roll back' WHERE id='timeout'");
      await client.query('SELECT pg_sleep(0.35)');
    }), errorStatus(401));
    assert.equal((await findReviewerAccount(b, 'timeout')).name, 'timeout');
  });

  await t.test('revoke serializes with the transaction owning a write, and isolates other sessions', async () => {
    const credentials = await add('serial');
    const firstGrant = await first.issue('one', credentials);
    const other = await first.issue('one', credentials);
    const started = Promise.withResolvers();
    const proceed = Promise.withResolvers();
    const work = first.withGrant('one', firstGrant.token, write, async client => {
      started.resolve();
      await proceed.promise;
      await client.query("UPDATE reviewer_accounts SET name='written' WHERE id='serial'");
    });
    await started.promise;
    let revoked = false;
    const revocation = second.revoke('one', firstGrant.token).then(() => { revoked = true; });
    // Wait for PostgreSQL to report a blocked DELETE rather than guessing
    // whether the second caller had reached the DB based on elapsed time.
    let waiting;
    try {
      for (let i=0; i<100; i++) {
        waiting = (await admin.query(`SELECT 1 FROM pg_stat_activity WHERE datname=current_database()
          AND wait_event_type='Lock' AND query LIKE 'DELETE FROM reviewer_grants WHERE installation%'`)).rowCount;
        if (waiting) break;
        await new Promise(resolve => setTimeout(resolve,10));
      }
      assert.equal(waiting, 1);
      assert.equal(revoked, false);
    } finally {
      proceed.resolve();
      await Promise.all([work, revocation]);
    }
    await second.revoke('one', firstGrant.token);
    await assert.rejects(first.withGrant('one', firstGrant.token, read, getPrincipal), errorStatus(401));
    assert.equal((await first.withGrant('one', other.token, read, getPrincipal)).name, 'written');
  });

  await t.test('blocked case write times out, releases the grant and records no audit or disposition', {timeout: 9000}, async () => {
    await a.query(fs.readFileSync(path.join(__dirname,'src/db/schema.sql'),'utf8'));
    await a.query(fs.readFileSync(path.join(__dirname,'src/reviewer/cases.sql'),'utf8'));
    const id = randomUUID();
    await a.query("INSERT INTO sites (id,name) VALUES ('red','Reviewer test')");
    await a.query("INSERT INTO events (id,site_id,message,alert_name) VALUES ($1,'red','test','SuricataIntrusion')", [id]);
    const grant = await first.issue('one', await add('blocked'));
    const item = {operationId:randomUUID(), kind:'false_positive', reason:'Reviewed isolated case'};
    const lock = await b.connect();
    try {
      await lock.query('BEGIN');
      await lock.query('SELECT id FROM events WHERE id=$1 FOR UPDATE', [id]);
      const began = Date.now();
      await assert.rejects(
        first.withGrant('one',grant.token,write,(client, actor) => recordDisposition(client,actor,'red',id,item)),
        {status:503, message:'Reviewer service unavailable'});
      assert.ok(Date.now()-began < 7000, 'case lock wait is bounded');
      assert.equal((await b.query('SELECT count(*)::int AS n FROM reviewer_case_dispositions')).rows[0].n, 0);
      assert.equal((await b.query('SELECT security_disposition FROM events WHERE id=$1', [id])).rows[0].security_disposition, null);
      await second.revoke('one',grant.token);
      await assert.rejects(first.withGrant('one',grant.token,read,getPrincipal), errorStatus(401));
    } finally {
      await lock.query('ROLLBACK');
      lock.release();
    }
  });

  await t.test('an idle operation times out and the pool can serve subsequent grants', {timeout: 12000}, async () => {
    const before = (await a.query('SHOW lock_timeout')).rows[0].lock_timeout;
    const credentials = await add('idling');
    const grant = await first.issue('one', credentials);
    const resume = Promise.withResolvers();
    let limits;
    try {
      await assert.rejects(first.withGrant('one',grant.token,read,async client => {
        limits = (await client.query(`SELECT current_setting('lock_timeout') AS lock,
          current_setting('statement_timeout') AS statement,
          current_setting('idle_in_transaction_session_timeout') AS idle`)).rows[0];
        await resume.promise;
      }), {status:503, message:'Reviewer service unavailable'});
    } finally {
      resume.resolve();
    }
    assert.deepEqual(limits, {lock:'2s',statement:'8s',idle:'5s'});
    assert.equal((await second.withGrant('one',grant.token,read,getPrincipal)).id, 'idling');
    assert.equal((await a.query('SHOW lock_timeout')).rows[0].lock_timeout, before);
  });

  await t.test('rate limits apply across replicas before bcrypt, with bounded anonymous buckets', async t => {
    await a.query('TRUNCATE reviewer_login_buckets');
    const credentials = await add('limited','viewer');
    const compare = t.mock.method(bcrypt,'compare');
    const attempts = await Promise.allSettled(Array.from({length: 12}, (_, i) =>
      (i % 2 ? first : second).issue('client-' + i, credentials)));
    assert.equal(attempts.filter(r => r.status === 'fulfilled').length, 10);
    assert.equal(attempts.filter(r => r.reason?.status === 429).length, 2);
    assert.equal(compare.mock.callCount(), 10);

    await a.query("UPDATE reviewer_login_buckets SET attempts=60 WHERE kind='installation' AND key='client-0'");
    const before = compare.mock.callCount();
    await assert.rejects(second.issue('client-0',{username:'new-user',password}), errorStatus(429));
    assert.equal(compare.mock.callCount(), before);
    await a.query("UPDATE reviewer_login_buckets SET attempts=120 WHERE kind='global'");
    await assert.rejects(first.issue('completely-new',{username:'new-user',password}), errorStatus(429));
    assert.equal(compare.mock.callCount(), before);
    assert.equal((await a.query("SELECT 1 FROM reviewer_login_buckets WHERE key='completely-new'")).rowCount, 0);

    await b.query("UPDATE reviewer_login_buckets SET started_at=clock_timestamp()-interval '70 seconds'");
    await first.issue('client-0', credentials);
    assert.equal((await a.query("SELECT 1 FROM reviewer_login_buckets WHERE started_at < clock_timestamp()-interval '1 minute'")).rowCount, 0);
    assert.equal((await a.query("SELECT 1 FROM reviewer_grants WHERE LEAST(expires_at,idle_expires_at) < clock_timestamp()")).rowCount, 0);
    await assert.rejects(first.issue('client-0',{username:'missing-user',password}), errorStatus(401));
  });
});

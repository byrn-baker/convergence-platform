'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {Pool} = require('pg');
const {readCase, readDispositionReceipt, recordDisposition} = require('./src/reviewer/cases');

test('reviewer dispositions are site-scoped, atomic, attributable, append-only and replayable',
  {skip: !process.env.REVIEWER_TEST_DATABASE_URL}, async t => {
    const connectionString = process.env.REVIEWER_TEST_DATABASE_URL;
    const schema = 'reviewer_cases_' + randomUUID().replaceAll('-', '');
    const admin = new Pool({connectionString, max: 1});
    const pool = new Pool({connectionString, options: '-c search_path=' + schema});
    t.after(async () => {
      await pool.end();
      try { await admin.query('DROP SCHEMA IF EXISTS ' + schema + ' CASCADE'); }
      finally { await admin.end(); }
    });
    await admin.query('CREATE SCHEMA ' + schema);
    for (const file of ['src/db/schema.sql', 'src/reviewer/grants.sql', 'src/reviewer/cases.sql']) {
      await pool.query(readFileSync(path.join(__dirname, file), 'utf8'));
    }

    const operator = {id: randomUUID(), name: 'Original reviewer', role: 'operator', sites: ['red', 'blue'], installation: 'A'};
    const operator2 = {...operator, id: randomUUID(), name: 'Second reviewer'};
    const viewer = {...operator, id: randomUUID(), name: 'Case viewer', sites: ['red'], role: 'viewer'};
    const item = {kind: 'false_positive', reason: 'Verified duplicate capture', operationId: randomUUID()};
    const [red, blue, nonIDS] = [randomUUID(), randomUUID(), randomUUID()];
    await pool.query("INSERT INTO sites (id,name) VALUES ('red','red'), ('blue','blue')");
    await pool.query(`INSERT INTO events (id, site_id, message, alert_name, source_evidence, investigation_notes, jev_review)
      VALUES ($1,'red',repeat('r',5000),'SuricataIntrusion',$4,repeat('n',8000),$5),
             ($2,'blue','blue-only','SuricataIntrusion',NULL,NULL,NULL), ($3,'red','wan','WanHardDown',NULL,NULL,NULL)`,
    [red, blue, nonIDS, {annotations: {description: 'Passive IDS observation; not proof of compromise. ' +
      JSON.stringify({alert: {signature: 'ET MALWARE signature'}})}, secret: 'private evidence'},
    {hidden: 'private analysis'}]);

    async function tx(callback) {
      const db = await pool.connect();
      try {
        await db.query('BEGIN');
        const result = await callback(db);
        await db.query('COMMIT');
        return result;
      } catch (err) {
        await db.query('ROLLBACK');
        throw err;
      } finally { db.release(); }
    }
    const write = (actor, site, id, input) => tx(db => recordDisposition(db, actor, site, id, input));
    const read = (actor, site, id) => tx(db => readCase(db, actor, site, id));
    const receipt = (actor, site, id, operation) => tx(db => readDispositionReceipt(db, actor, site, id, operation));
    const reject = (promise, status) => assert.rejects(promise, err => err.status === status);
    const count = async (sql, args = []) => (await pool.query(sql, args)).rows[0].count;

    await t.test('restricts source projection and rejects forged commands, viewer writes and other sites', async () => {
      const data = await read(viewer, 'red', red);
      assert.equal(data.security_assessment.bucket, 'attention');
      assert.equal(data.message.length, 2048);
      assert.equal(data.investigation_notes.length, 4096);
      assert.deepEqual(data.review_history, []);
      assert.doesNotMatch(JSON.stringify(data), /private evidence|private analysis|source_evidence|jev_review/);
      await reject(read(viewer, 'blue', blue), 403);
      await reject(read(viewer, 'red', blue), 404);
      await reject(write(viewer, 'red', red, item), 403);
      await reject(write({...operator, name: null}, 'red', red, item), 401);
      await reject(write(operator, 'red', blue, item), 404);
      await reject(write(operator, 'red', red, {...item, reviewed_by: 'spoof'}), 400);
      await reject(write(operator, 'red', red, {...item, operationId: 'invalid'}), 400);
      await reject(write(operator, 'red', red, {...item, reason: 'unencodable\0note'}), 400);
      await reject(write(operator, 'red', red, {...item, reason: 'unencodable\ud800note'}), 400);
      await reject(write(operator, 'red', nonIDS, item), 400);
      assert.equal(await count('SELECT count(*)::int FROM reviewer_case_dispositions'), 0);
    });

    await t.test('concurrent retries commit one event mutation and preserve the original receipt', async () => {
      const results = await Promise.all(Array.from({length: 8}, () => write(operator, 'red', red, item)));
      for (const r of results) assert.deepEqual(r, results[0]);
      assert.equal(results[0].reviewed_by, operator.id);
      assert.equal(results[0].case_id, red);
      assert.equal(results[0].site_id, 'red');
      assert.equal(await count('SELECT count(*)::int FROM reviewer_case_dispositions'), 1);
      assert.equal(await count('SELECT count(*)::int FROM event_history WHERE event_id = $1', [red]), 1);
      assert.deepEqual(await receipt(operator, 'red', red, item.operationId), results[0]);

      await reject(write(operator, 'red', red, {...item, reason: 'Different review evidence'}), 409);
      await reject(write(operator, 'blue', blue, item), 409);
      await reject(receipt(operator2, 'red', red, item.operationId), 404);
      await reject(receipt({...operator, installation: 'B'}, 'red', red, item.operationId), 404);
      await reject(receipt(operator, 'blue', blue, item.operationId), 404);

      const next = await write(operator2, 'red', red, {...item, kind: 'needs_review'});
      const data = await read(viewer, 'red', red);
      assert.equal(data.security_disposition.reviewed_by, operator2.id);
      assert.equal(data.security_disposition.reviewed_at, next.reviewed_at.toISOString());
      assert.deepEqual(data.review_history.map(r => r.reviewed_by), [operator2.id, operator.id]);
      assert.deepEqual(await write(operator, 'red', red, item), results[0]);
      assert.equal((await read(viewer, 'red', red)).security_disposition.kind, 'needs_review');
    });

    await t.test('preserves the actor name from the committed review through account rename and deletion', async () => {
      const renamed = 'Updated reviewer';
      const id = randomUUID();
      await pool.query(`INSERT INTO reviewer_accounts (id, username, name, password_hash, role, sites)
        VALUES ($1, 'reviewer-name-fixture', $2, 'not-for-login', 'operator', ARRAY['red'])`, [id, operator.name]);
      const currentActor = async () => {
        const {rows: [user]} = await pool.query('SELECT id, name, role, sites FROM reviewer_accounts WHERE id = $1', [id]);
        return {...user, installation: operator.installation};
      };
      const action = {...item, operationId: randomUUID()};
      const atReview = await write(await currentActor(), 'red', red, action);
      assert.equal(atReview.reviewed_by_name, operator.name);
      assert.equal(atReview.reviewed_by, id);
      await pool.query('UPDATE reviewer_accounts SET name = $1 WHERE id = $2', [renamed, id]);
      assert.deepEqual(await write(await currentActor(), 'red', red, action), atReview);
      assert.deepEqual(await receipt(await currentActor(), 'red', red, action.operationId), atReview);
      await pool.query('DELETE FROM reviewer_accounts WHERE id = $1', [id]);
      const data = await read(viewer, 'red', red);
      assert.equal(data.security_disposition.reviewed_by_name, operator.name);
      assert.equal(data.security_disposition.reviewed_by, id);
      assert.equal(data.review_history[0].reviewed_by_name, operator.name);
      assert.equal(data.review_history[0].reviewed_by, id);
    });

    await t.test('an existing audit row migrates without inventing a historical display name', async () => {
      // Recreate the prior column layout and place one historical receipt in it.
      await pool.query('ALTER TABLE reviewer_case_dispositions DROP COLUMN actor_name');
      const caseId = randomUUID(), operationId = randomUUID();
      const old = {version: 1, kind: item.kind, reason: item.reason, reviewed_at: new Date().toISOString(), reviewed_by: operator.id};
      await pool.query(`INSERT INTO events (id, site_id, message, alert_name, security_disposition)
        VALUES ($1, 'red', 'historical case', 'SuricataIntrusion', $2)`, [caseId, old]);
      await pool.query(`INSERT INTO reviewer_case_dispositions (installation_id, actor_id, operation_id, site_id, case_id, kind, reason)
        VALUES ($1,$2,$3,'red',$4,$5,$6)`, [operator.installation, operator.id, operationId, caseId, item.kind, item.reason]);
      const migration = readFileSync(path.join(__dirname, 'src/reviewer/cases.sql'), 'utf8');
      await pool.query(migration);
      await pool.query(migration);
      const historical = await read(viewer, 'red', caseId);
      assert.deepEqual(historical.security_disposition, old);
      assert.equal(historical.review_history[0].reviewed_by, operator.id);
      assert.equal(historical.review_history[0].reviewed_by_name, null);
      assert.equal((await receipt(operator, 'red', caseId, operationId)).reviewed_by_name, null);
    });

    await t.test('a receipt follows the case tenancy boundary when the case moves sites', async () => {
      const transferred = randomUUID();
      await pool.query(`INSERT INTO events (id, site_id, message, alert_name)
        VALUES ($1, 'red', 'soon transferred', 'SuricataIntrusion')`, [transferred]);
      const action = {...item, operationId: randomUUID()};
      await write(operator, 'red', transferred, action);
      await pool.query("UPDATE events SET site_id = 'blue' WHERE id = $1", [transferred]);
      await reject(receipt(operator, 'red', transferred, action.operationId), 404);
      await reject(receipt(operator, 'blue', transferred, action.operationId), 404);
    });

    await t.test('two conflicting submissions on different cases serialize on the shared operation ID', async () => {
      const conflict = {...item, operationId: randomUUID()};
      const attempts = await Promise.allSettled([write(operator, 'red', red, conflict), write(operator, 'blue', blue, conflict)]);
      const rejected = attempts.filter(a => a.status === 'rejected');
      assert.equal(rejected.length, 1);
      assert.equal(rejected[0].reason.status, 409);
      assert.equal(await count('SELECT count(*)::int FROM reviewer_case_dispositions WHERE operation_id = $1', [conflict.operationId]), 1);
    });

    await t.test('failure between disposition and transaction commit rolls back both records', async () => {
      const temporary = {...item, operationId: randomUUID(), kind: 'expected_activity'};
      const before = await read(viewer, 'red', red);
      await assert.rejects(tx(async db => {
        await recordDisposition(db, operator2, 'red', red, temporary);
        await db.query('SELECT 1 / 0');
      }), err => err.code === '22012');
      await reject(receipt(operator2, 'red', red, temporary.operationId), 404);
      assert.deepEqual(await read(viewer, 'red', red), before);
    });

    await t.test('database blocks historical mutation and reviewer history is bounded', async () => {
      for (const sql of ['UPDATE reviewer_case_dispositions SET reason = $1 WHERE operation_id = $2',
        'DELETE FROM reviewer_case_dispositions', 'TRUNCATE reviewer_case_dispositions']) {
        await assert.rejects(pool.query(sql, sql.startsWith('UPDATE') ? ['changed historical text', item.operationId] : []),
          err => err.code === '55000');
      }
      const detailed = 'x'.repeat(4000);
      for (let i = 0; i < 22; i++) {
        await write(operator, 'red', red, {...item, reason: detailed, operationId: randomUUID()});
      }
      const data = await read(viewer, 'red', red);
      assert.equal(data.review_history.length, 20);
      assert.equal(data.review_history_truncated, true);
      assert.equal(data.review_history[0].reason_preview.length, 256);
      assert.equal(data.review_history[0].reason_truncated, true);
      assert.equal((await receipt(operator, 'red', red, data.review_history[0].operation_id)).reason, detailed);
      assert.equal((await receipt(operator, 'red', red, item.operationId)).reason, item.reason);
    });
  });

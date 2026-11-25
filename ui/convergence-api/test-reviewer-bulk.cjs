'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {setTimeout: delay} = require('node:timers/promises');
const {Pool} = require('pg');
const {migrate} = require('./reviewer-service');
const {readCase, recordDisposition} = require('./src/reviewer/cases');
const {BULK_CEILING, parsePreviewQuery, previewBulk, recordBulk} = require('./src/reviewer/bulk');

test('the bulk preview query accepts only its closed set of parameters', () => {
  assert.deepEqual(parsePreviewQuery(undefined), {window: '24h', source: null});
  assert.deepEqual(parsePreviewQuery('window=7d&source=10.20.4.17'), {window: '7d', source: '10.20.4.17'});
  assert.equal(parsePreviewQuery('source=2001:db8::17').source, '2001:db8::17');
  for (const bad of ['window=1y', 'source=', 'source=10.20.4.0/24', 'source=host.example', 'source=fe80::1%25eth0',
    'source=10.20.4.17&source=10.20.4.18', 'site=blue', 'limit=5', "source=10.20.4.17' OR '1'='1", 'x'.repeat(200)]) {
    assert.throws(() => parsePreviewQuery(bad), err => err.status === 400, bad);
  }
});

test('a bulk decision covers exactly the previewed cases, once, under one name',
  {skip: !process.env.REVIEWER_TEST_DATABASE_URL}, async t => {
    const connectionString = process.env.REVIEWER_TEST_DATABASE_URL;
    const schema = 'reviewer_bulk_' + randomUUID().replaceAll('-', '');
    const admin = new Pool({connectionString, max: 1});
    const pool = new Pool({connectionString, options: '-c search_path=' + schema});
    t.after(async () => {
      await pool.end();
      try { await admin.query('DROP SCHEMA IF EXISTS ' + schema + ' CASCADE'); }
      finally { await admin.end(); }
    });
    await admin.query('CREATE SCHEMA ' + schema);
    await pool.query(readFileSync(path.join(__dirname, 'src/db/schema.sql'), 'utf8'));
    await migrate(pool);
    await pool.query("INSERT INTO sites (id,name) VALUES ('red','red'), ('blue','blue') ON CONFLICT DO NOTHING");

    const A = '10.20.4.17', B = '10.20.4.52';
    const sensor = source => 'Passive IDS observation; not proof of compromise. '
      + JSON.stringify({src_ip: source, src_port: 49000, dest_ip: '203.0.113.44', dest_port: 443, proto: 'TCP', alert: {signature: 'Beacon'}});
    const reviewed = kind => ({version: 1, kind, reason: 'Checked one by one', reviewed_by: randomUUID(), reviewed_at: new Date().toISOString()});
    async function add(site, signature, source, hours, disposition = null, description = sensor(source)) {
      const id = randomUUID();
      await pool.query(`INSERT INTO events (id, site_id, message, alert_name, severity, timestamp, source_evidence, security_disposition)
        VALUES ($1,$2,'m','SuricataIntrusion','watch', statement_timestamp() - make_interval(hours => $3), $4, $5)`,
      [id, site, hours, {labels: {signature_id: signature}, annotations: {summary: 'Beacon — a → b', description}}, disposition]);
      return id;
    }
    const fromA = [];
    for (let i = 0; i < 5; i++) fromA.push(await add('red', '100', A, 1 + i));
    const fromB = [await add('red', '100', B, 1), await add('red', '100', B, 2), await add('red', '100', B, 3, reviewed('needs_review'))];
    const unknown = await add('red', '100', A, 2, null, 'free text, not the sensor record');
    const already = await add('red', '100', A, 2, reviewed('false_positive'));
    const older = await add('red', '100', A, 72);
    const other = await add('red', '200', A, 1);
    const blue = await add('blue', '100', A, 1);

    async function run(operation) {
      const db = await pool.connect();
      try {
        await db.query('BEGIN');
        const result = await operation(db);
        await db.query('COMMIT');
        return result;
      } catch (err) {
        await db.query('ROLLBACK');
        throw err;
      } finally { db.release(); }
    }
    const operator = {id: randomUUID(), name: 'Red operator', role: 'operator', sites: ['red'], installation: 'A'};
    const viewer = {...operator, id: randomUUID(), name: 'Red viewer', role: 'viewer'};
    const preview = (actor, site, signature, search) => run(db => previewBulk(db, actor, site, signature, parsePreviewQuery(search)));
    const write = (actor, site, signature, input) => run(db => recordBulk(db, actor, site, signature, input));
    const command = (seen, extra = {}) => ({operationId: randomUUID(), kind: 'expected_activity',
      reason: 'Scheduled backup traffic to the known host', windowStart: seen.window_start, windowEnd: seen.window_end,
      source: seen.source_host, confirmedCount: seen.cases, ...extra});
    const reject = (promise, status) => assert.rejects(promise, err => err.status === status);
    const disposition = async id => (await pool.query('SELECT security_disposition AS d FROM events WHERE id = $1', [id])).rows[0].d;
    const count = async table => (await pool.query(`SELECT count(*)::int AS n FROM ${table}`)).rows[0].n;

    await t.test('the preview counts what still needs review, by window and source host', async () => {
      const all = await preview(operator, 'red', '100', undefined);
      assert.equal(all.cases, 9);
      assert.deepEqual(all.sources, [{source_host: A, cases: 5}, {source_host: B, cases: 3}]);
      assert.deepEqual([all.source_host, all.ceiling, all.sources_truncated, all.window], [null, BULK_CEILING, false, '24h']);
      assert.match(all.window_start, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/);
      assert.ok(Date.parse(all.window_end) - Date.parse(all.window_start) === 86400000);
      assert.equal((await preview(operator, 'red', '100', 'source=' + A)).cases, 5);
      assert.equal((await preview(operator, 'red', '100', 'source=10.9.9.9')).cases, 0);
      assert.equal((await preview(operator, 'red', '100', 'window=7d')).cases, 10);
      assert.equal((await preview(operator, 'red', '999', undefined)).cases, 0);
    });

    await t.test('viewers, other sites, missing sessions and forged input are refused; nothing is written', async () => {
      const seen = await preview(operator, 'red', '100', 'source=' + A);
      await reject(preview(viewer, 'red', '100', undefined), 403);
      await reject(preview(operator, 'blue', '100', undefined), 403);
      await reject(preview({...operator, id: null}, 'red', '100', undefined), 401);
      await reject(preview(operator, 'red', "100' OR '1'='1", undefined), 400);
      await reject(write(viewer, 'red', '100', command(seen)), 403);
      await reject(write(operator, 'blue', '100', command(seen)), 403);
      await reject(write({...operator, sites: []}, 'red', '100', command(seen)), 403);
      await reject(write({...operator, name: ''}, 'red', '100', command(seen)), 401);
      const future = new Date(Date.now() + 3600000).toISOString().replace('Z', '000Z');
      const long = new Date(Date.parse(seen.window_end) - 31 * 86400000).toISOString().replace('Z', '000Z');
      for (const bad of [{kind: 'needs_review'}, {kind: 'ignored'}, {reason: 'short'}, {reason: 'x'.repeat(4001)},
        {reason: 'Contains a null \u0000 character'}, {confirmedCount: 0}, {confirmedCount: 1.5}, {confirmedCount: '5'},
        {confirmedCount: BULK_CEILING + 1}, {operationId: 'not-a-uuid'}, {source: '10.20.4.0/24'}, {source: 17},
        {windowStart: seen.window_end}, {windowEnd: future}, {windowStart: long}, {windowStart: '2026-13-01T00:00:00.000000Z'},
        {windowStart: seen.window_start.slice(0, 23) + 'Z'}, {windowEnd: "2026-11-24' OR '1'='1"}, {extra: true}]) {
        await reject(write(operator, 'red', '100', command(seen, bad)), 400);
      }
      const short = command(seen); delete short.source;
      await reject(write(operator, 'red', '100', short), 400);
      await reject(write(operator, 'red', '', command(seen)), 400);
      assert.deepEqual([await count('reviewer_bulk_dispositions'), await count('event_history'), await count('reviewer_operation_keys')], [0, 0, 0]);
    });

    let first;
    await t.test('one source host: its cases get the decision and the reviewer; everything else is untouched', async s => {
      const seen = await preview(operator, 'red', '100', 'source=' + A);
      first = command(seen);
      const receipt = await write(operator, 'red', '100', first);
      assert.deepEqual([receipt.case_count, receipt.kind, receipt.reason, receipt.source_host, receipt.signature_id, receipt.site_id],
        [5, 'expected_activity', first.reason, A, '100', 'red']);
      assert.deepEqual([receipt.operation_id, receipt.reviewed_by, receipt.reviewed_by_name, receipt.window_start, receipt.window_end],
        [first.operationId, operator.id, 'Red operator', seen.window_start, seen.window_end]);
      for (const id of fromA) {
        const saved = await disposition(id);
        assert.deepEqual([saved.kind, saved.reason, saved.reviewed_by, saved.reviewed_by_name, saved.bulk_operation_id, saved.version],
          ['expected_activity', first.reason, operator.id, 'Red operator', first.operationId, 1]);
        assert.equal(saved.reviewed_at, receipt.reviewed_at.toISOString());
      }
      for (const id of [fromB[0], fromB[1], unknown, older, other, blue]) assert.equal(await disposition(id), null);
      assert.equal((await disposition(fromB[2])).kind, 'needs_review');
      assert.equal((await disposition(already)).kind, 'false_positive');
      // The previous state of each covered case is kept, and the queue follows.
      assert.deepEqual([await count('event_history'), await count('reviewer_bulk_dispositions'), await count('reviewer_case_dispositions')], [5, 1, 0]);
      const queued = await pool.query('SELECT needs_review, outcome FROM reviewer_case_queue WHERE case_id = ANY($1::uuid[])', [fromA]);
      assert.ok(queued.rows.every(row => row.needs_review === false && row.outcome === 'expected_activity'));
      assert.equal((await preview(operator, 'red', '100', undefined)).cases, 4);

      await s.test('a replay returns the original receipt and writes nothing', async () => {
        assert.deepEqual(await write(operator, 'red', '100', first), receipt);
        // The reviewer's confirmed count is not part of the operation's identity.
        assert.deepEqual(await write(operator, 'red', '100', {...first, confirmedCount: 1}), receipt);
        assert.deepEqual([await count('event_history'), await count('reviewer_bulk_dispositions')], [5, 1]);
        for (const changed of [{reason: 'A different reason for the same operation'}, {kind: 'false_positive'}, {source: null}, {source: B},
          {windowStart: new Date(Date.parse(seen.window_start) + 1000).toISOString().replace('Z', '000Z')}]) {
          await reject(write(operator, 'red', '100', {...first, ...changed}), 409);
        }
        await reject(write(operator, 'red', '200', first), 409);
        // Another reviewer's operation with the same ID is a separate operation; it finds nothing left to review.
        await reject(write({...operator, id: randomUUID()}, 'red', '100', first), 409);
      });

      await s.test('an operation ID belongs to one command', async () => {
        const single = {operationId: randomUUID(), kind: 'false_positive', reason: 'Reviewed on its own after the bulk decision'};
        await reject(run(db => recordDisposition(db, operator, 'red', fromA[0], {...single, operationId: first.operationId})), 409);
        // A covered case can still be reviewed on its own; that is an ordinary single disposition.
        await run(db => recordDisposition(db, operator, 'red', fromA[0], single));
        const saved = await disposition(fromA[0]);
        assert.deepEqual([saved.kind, saved.bulk_operation_id], ['false_positive', undefined]);
        const seenB = await preview(operator, 'red', '100', 'source=' + B);
        await reject(write(operator, 'red', '100', command(seenB, {operationId: single.operationId})), 409);
        assert.equal(await count('reviewer_bulk_dispositions'), 1);
      });
    });

    await t.test('more cases than confirmed is a conflict; fewer is accepted and the rest are left alone', async () => {
      const seen = await preview(operator, 'red', '100', 'source=' + B);
      assert.equal(seen.cases, 3);
      // A case that arrives late, with a time inside the window the reviewer saw.
      const late = await add('red', '100', B, 1);
      const history = await count('event_history');
      await reject(write(operator, 'red', '100', command(seen)), 409);
      assert.deepEqual([await count('event_history'), await count('reviewer_bulk_dispositions'), await disposition(late)], [history, 1, null]);
      // Someone reviews two of them one by one before the reviewer confirms the new count.
      const again = await preview(operator, 'red', '100', 'source=' + B);
      assert.equal(again.cases, 4);
      const single = {kind: 'confirmed_threat', reason: 'Confirmed on the endpoint by its owner'};
      const colleague = {...operator, id: randomUUID(), name: 'Second operator'};
      await run(db => recordDisposition(db, colleague, 'red', fromB[0], {...single, operationId: randomUUID()}));
      await run(db => recordDisposition(db, colleague, 'red', late, {...single, operationId: randomUUID()}));
      const receipt = await write(operator, 'red', '100', command(again));
      assert.equal(receipt.case_count, 2);
      assert.deepEqual([(await disposition(fromB[0])).reviewed_by_name, (await disposition(late)).kind], ['Second operator', 'confirmed_threat']);
      assert.deepEqual([(await disposition(fromB[1])).bulk_operation_id, (await disposition(fromB[2])).bulk_operation_id],
        [receipt.operation_id, receipt.operation_id]);
      // Nothing is left for this source: a further decision has no cases and is refused.
      const empty = await preview(operator, 'red', '100', 'source=' + B);
      assert.equal(empty.cases, 0);
      await reject(write(operator, 'red', '100', command(empty, {confirmedCount: 1})), 409);
    });

    await t.test('a single disposition in flight wins its case; the bulk decision covers the others', async () => {
      const cases = [await add('red', '300', A, 1), await add('red', '300', A, 1), await add('red', '300', A, 1)];
      const seen = await preview(operator, 'red', '300', undefined);
      assert.equal(seen.cases, 3);
      const colleague = {...operator, id: randomUUID(), name: 'Second operator'};
      const held = await pool.connect();
      let settled = false, pending;
      try {
        await held.query('BEGIN');
        await recordDisposition(held, colleague, 'red', cases[1],
          {operationId: randomUUID(), kind: 'confirmed_threat', reason: 'Confirmed while the bulk decision was being made'});
        pending = write(operator, 'red', '300', command(seen)).finally(() => { settled = true; });
        pending.catch(() => {});
        await delay(300);
        // The bulk write waits for the case lock instead of deciding on a stale read.
        assert.equal(settled, false);
        await held.query('COMMIT');
      } catch (err) {
        await held.query('ROLLBACK');
        throw err;
      } finally { held.release(); }
      const receipt = await pending;
      assert.equal(receipt.case_count, 2);
      assert.deepEqual([(await disposition(cases[1])).kind, (await disposition(cases[1])).reviewed_by_name, (await disposition(cases[1])).bulk_operation_id],
        ['confirmed_threat', 'Second operator', undefined]);
      assert.deepEqual([(await disposition(cases[0])).bulk_operation_id, (await disposition(cases[2])).bulk_operation_id],
        [receipt.operation_id, receipt.operation_id]);

      // Two submissions of one operation at the same time produce one decision and one receipt.
      for (let i = 0; i < 4; i++) await add('red', '400', A, 1);
      const twice = command(await preview(operator, 'red', '400', undefined));
      const before = await count('event_history');
      const [one, two] = await Promise.all([write(operator, 'red', '400', twice), write(operator, 'red', '400', twice)]);
      assert.deepEqual(one, two);
      assert.deepEqual([one.case_count, await count('event_history') - before], [4, 4]);
    });

    await t.test('the ceiling: one case over is refused whole, exactly at it is written', async () => {
      await pool.query(`INSERT INTO events (id, site_id, message, alert_name, severity, timestamp, source_evidence)
        SELECT gen_random_uuid(), 'red', 'm', 'SuricataIntrusion', 'info', statement_timestamp() - interval '1 hour',
               jsonb_build_object('labels', jsonb_build_object('signature_id', '900'),
                 'annotations', jsonb_build_object('summary', 'Sweep — a → b', 'description', $1::text))
          FROM generate_series(1, $2)`, [sensor(A), BULK_CEILING + 1]);
      const over = await preview(operator, 'red', '900', undefined);
      assert.equal(over.cases, BULK_CEILING + 1);
      const history = await count('event_history');
      await reject(write(operator, 'red', '900', command(over)), 400);
      await reject(write(operator, 'red', '900', command(over, {confirmedCount: BULK_CEILING})), 409);
      assert.equal(await count('event_history'), history);
      await pool.query(`DELETE FROM events WHERE id = (SELECT case_id FROM reviewer_case_queue WHERE signature_id = '900' LIMIT 1)`);
      const receipt = await write(operator, 'red', '900', command(await preview(operator, 'red', '900', undefined)));
      assert.equal(receipt.case_count, BULK_CEILING);
      assert.equal(await count('event_history') - history, BULK_CEILING);
      assert.equal((await pool.query("SELECT count(*)::int AS n FROM reviewer_case_queue WHERE signature_id = '900' AND needs_review")).rows[0].n, 0);
    });

    await t.test('a covered case shows the decision in its history, and stays reviewable on its own', async () => {
      const read = (actor, id) => run(db => readCase(db, actor, 'red', id));
      // Covered by the first decision and not touched since.
      const covered = await read(viewer, fromA[1]);
      assert.deepEqual([covered.security_disposition.kind, covered.security_disposition.bulk_operation_id,
        covered.security_disposition.reviewed_by_name], ['expected_activity', first.operationId, 'Red operator']);
      assert.equal(covered.review_history.length, 1);
      const [entry] = covered.review_history;
      assert.deepEqual([entry.operation_id, entry.kind, entry.bulk_case_count, entry.reviewed_by, entry.reviewed_by_name,
        entry.reason_preview, entry.reason_truncated],
      [first.operationId, 'expected_activity', 5, operator.id, 'Red operator', first.reason, false]);
      assert.equal(entry.reviewed_at.toISOString(), covered.security_disposition.reviewed_at);
      // Covered, then reviewed on its own: the single review is current and the decision stays in the history.
      const later = await read(viewer, fromA[0]);
      assert.deepEqual([later.security_disposition.kind, later.security_disposition.bulk_operation_id], ['false_positive', undefined]);
      assert.deepEqual(later.review_history.map(row => [row.kind, row.bulk_case_count]), [['false_positive', null], ['expected_activity', 5]]);
      // Reviewed by a colleague before the second decision, which left it alone: no bulk entry.
      const skipped = await read(viewer, fromB[0]);
      assert.deepEqual(skipped.review_history.map(row => [row.kind, row.reviewed_by_name, row.bulk_case_count]),
        [['confirmed_threat', 'Second operator', null]]);
      assert.equal(skipped.security_disposition.bulk_operation_id, undefined);
      assert.deepEqual((await read(viewer, fromB[1])).review_history.map(row => [row.kind, row.bulk_case_count]), [['expected_activity', 2]]);
      assert.deepEqual((await read(viewer, other)).review_history, []);
      // Each decision lists exactly the cases it covered, and the list cannot be changed.
      const listed = await pool.query(`SELECT b.case_count, count(c.case_id)::int AS listed FROM reviewer_bulk_dispositions b
        LEFT JOIN reviewer_bulk_disposition_cases c USING (installation_id, actor_id, operation_id) GROUP BY b.operation_id, b.case_count`);
      assert.ok(listed.rowCount >= 4 && listed.rows.every(row => row.case_count === row.listed));
      const mine = await pool.query('SELECT case_id FROM reviewer_bulk_disposition_cases WHERE operation_id = $1 ORDER BY 1', [first.operationId]);
      assert.deepEqual(mine.rows.map(row => row.case_id), [...fromA].sort());
      for (const statement of ['DELETE FROM reviewer_bulk_disposition_cases', "UPDATE reviewer_bulk_disposition_cases SET site_id = 'blue'",
        'TRUNCATE reviewer_bulk_disposition_cases']) {
        await assert.rejects(pool.query(statement), {code: '55000'});
      }
      // A disposition whose bulk reference is not a UUID is shown without it.
      await pool.query(`UPDATE events SET security_disposition = security_disposition || '{"bulk_operation_id":"<script>"}' WHERE id = $1`, [fromA[2]]);
      assert.equal((await read(viewer, fromA[2])).security_disposition.bulk_operation_id, undefined);
    });

    await t.test('the source address is read from the sensor record and never stops a case being stored', async () => {
      const source = async id => (await pool.query('SELECT host(source_ip) AS ip FROM reviewer_case_queue WHERE case_id = $1', [id])).rows[0].ip;
      assert.deepEqual([await source(fromA[1]), await source(unknown)], [A, null]);
      const prefix = 'Passive IDS observation; not proof of compromise. ';
      for (const [description, expected] of [[prefix + '{"src_ip":"2001:db8::17"}', '2001:db8::17'], [prefix + '{"src_ip":"10.0.0.0/8"}', null],
        [prefix + '{"src_ip":"not an address"}', null], [prefix + '{"src_ip":17}', null], [prefix + '{"src_ip":', null],
        [prefix + '"text"', null], [prefix + '[1]', null], [prefix + '{"tunnel":{"src_ip":"10.1.1.1"}}', null], [prefix, null], ['', null]]) {
        assert.equal(await source(await add('red', '500', A, 1, null, description)), expected, description);
      }
    });
  });

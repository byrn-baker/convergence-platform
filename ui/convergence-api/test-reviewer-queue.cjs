'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {Pool} = require('pg');
const {backfillQueue, listCases, listSignatures, parseCaseQuery, parseSignatureQuery} = require('./src/reviewer/queue');

test('the signature query accepts only its closed set of parameters', () => {
  assert.deepEqual(parseSignatureQuery(undefined), {window: '24h', state: 'needs_review', severity: null});
  assert.deepEqual(parseSignatureQuery(''), {window: '24h', state: 'needs_review', severity: null});
  assert.deepEqual(parseSignatureQuery('window=30d&state=reviewed&severity=watch'),
    {window: '30d', state: 'reviewed', severity: 'watch'});
  for (const bad of ['window=1y', 'state=open', 'severity=critical', 'site=blue', 'window=24h&window=7d',
    'window=', 'limit=5', 'window=24h%00', 'state=all;', 'x'.repeat(300)]) {
    assert.throws(() => parseSignatureQuery(bad), err => err.status === 400, bad);
  }
});

test('the signature queue is site-scoped, windowed, counted and ordered',
  {skip: !process.env.REVIEWER_TEST_DATABASE_URL}, async t => {
    const connectionString = process.env.REVIEWER_TEST_DATABASE_URL;
    const schema = 'reviewer_queue_' + randomUUID().replaceAll('-', '');
    const admin = new Pool({connectionString, max: 1});
    const pool = new Pool({connectionString, options: '-c search_path=' + schema});
    t.after(async () => {
      await pool.end();
      try { await admin.query('DROP SCHEMA IF EXISTS ' + schema + ' CASCADE'); }
      finally { await admin.end(); }
    });
    await admin.query('CREATE SCHEMA ' + schema);
    for (const file of ['src/db/schema.sql', 'src/reviewer/grants.sql', 'src/reviewer/cases.sql', 'src/reviewer/queue.sql']) {
      await pool.query(readFileSync(path.join(__dirname, file), 'utf8'));
    }
    await pool.query("INSERT INTO sites (id,name) VALUES ('red','red'), ('blue','blue')");

    const reviewed = kind => ({version: 1, kind, reason: 'checked', reviewed_by: randomUUID(), reviewed_at: new Date().toISOString()});
    // site, signature, name, severity, age in hours, disposition, alert kind
    const fixtures = [
      ['red', '100', 'Beacon to rare host', 'watch', 1, null, 'SuricataIntrusion'],
      ['red', '100', 'Beacon to rare host', 'watch', 2, null, 'SuricataIntrusion'],
      ['red', '100', 'Beacon to rare host', 'info', 3, reviewed('false_positive'), 'SuricataIntrusion'],
      ['red', '200', 'DNS to new domain', 'info', 1, null, 'SuricataIntrusion'],
      ['red', '200', 'DNS to new domain', 'info', 5, reviewed('needs_review'), 'SuricataIntrusion'],
      ['red', '300', 'Scripted user agent', 'info', 4, reviewed('expected_activity'), 'SuricataIntrusion'],
      ['red', '400', 'Old sweep', 'alert', 24 * 3, null, 'SuricataIntrusion'],
      ['red', '500', 'Very old sweep', 'alert', 24 * 40, null, 'SuricataIntrusion'],
      ['red', '100', 'Not an IDS case', 'alert', 1, null, 'WanHardDown'],
      ['blue', '900', 'Blue only', 'alert', 1, null, 'SuricataIntrusion'],
    ];
    for (const [site, signature, name, severity, hours, disposition, alert] of fixtures) {
      await pool.query(`INSERT INTO events (id, site_id, message, alert_name, severity, timestamp, source_evidence, security_disposition)
        VALUES ($1,$2,'m',$3,$4, statement_timestamp() - make_interval(hours => $5), $6, $7)`,
      [randomUUID(), site, alert, severity, hours,
        {labels: {signature_id: signature}, annotations: {summary: name + ' — 10.0.0.1 → 203.0.113.9', description: 'private evidence'}},
        disposition]);
    }

    async function list(actor, site, search) {
      const db = await pool.connect();
      try {
        await db.query('BEGIN');
        const result = await listSignatures(db, actor, site, parseSignatureQuery(search));
        await db.query('COMMIT');
        return result;
      } catch (err) {
        await db.query('ROLLBACK');
        throw err;
      } finally { db.release(); }
    }
    const red = {id: randomUUID(), name: 'Red viewer', role: 'viewer', sites: ['red'], installation: 'A'};
    const ids = result => result.signatures.map(row => row.signature_id);

    await t.test('default: needs review in 24 hours, highest severity first, with counts and names', async () => {
      const result = await list(red, 'red', undefined);
      assert.deepEqual(ids(result), ['100', '200']);
      const [beacon, dns] = result.signatures;
      assert.deepEqual([beacon.cases, beacon.needs_review, beacon.severity, beacon.name], [3, 2, 'watch', 'Beacon to rare host']);
      // A case reopened with "needs_review" still needs review.
      assert.deepEqual([dns.cases, dns.needs_review, dns.severity], [2, 2, 'info']);
      assert.ok(Date.parse(beacon.first_seen) < Date.parse(beacon.last_seen));
      assert.equal(result.truncated, false);
      assert.deepEqual(Object.keys(beacon).sort(),
        ['cases', 'first_seen', 'last_seen', 'name', 'needs_review', 'severity', 'signature_id']);
      assert.ok(!JSON.stringify(result).includes('private evidence') && !JSON.stringify(result).includes('203.0.113.9'));
    });

    await t.test('state, severity and window filters', async () => {
      assert.deepEqual(ids(await list(red, 'red', 'state=reviewed')), ['100', '300']);
      assert.deepEqual(ids(await list(red, 'red', 'state=all')), ['100', '200', '300']);
      assert.deepEqual(ids(await list(red, 'red', 'state=false_positive')), ['100']);
      assert.deepEqual(ids(await list(red, 'red', 'state=expected_activity')), ['300']);
      assert.deepEqual(ids(await list(red, 'red', 'state=confirmed_threat')), []);
      assert.deepEqual(ids(await list(red, 'red', 'severity=watch')), ['100']);
      assert.equal((await list(red, 'red', 'severity=watch')).signatures[0].cases, 2);
      assert.deepEqual(ids(await list(red, 'red', 'window=7d')), ['400', '100', '200']);
      assert.deepEqual(ids(await list(red, 'red', 'window=30d&state=all')), ['400', '100', '200', '300']);
    });

    await t.test('the queue follows inserts, dispositions, reclassification and deletes; backfill restores it', async () => {
      const row = async id => (await pool.query('SELECT * FROM reviewer_case_queue WHERE case_id = $1', [id])).rows[0];
      const id = randomUUID();
      await pool.query(`INSERT INTO events (id, site_id, message, alert_name, severity, source_evidence)
        VALUES ($1,'blue','m','SuricataIntrusion','watch',$2)`, [id, {labels: {signature_id: '901'}, annotations: {summary: 'Sync — a → b'}}]);
      assert.deepEqual([(await row(id)).signature_id, (await row(id)).needs_review, (await row(id)).severity], ['901', true, 'watch']);
      await pool.query('UPDATE events SET security_disposition = $2 WHERE id = $1', [id, reviewed('confirmed_threat')]);
      assert.deepEqual([(await row(id)).needs_review, (await row(id)).outcome], [false, 'confirmed_threat']);
      await pool.query('UPDATE events SET security_disposition = $2 WHERE id = $1', [id, reviewed('needs_review')]);
      assert.deepEqual([(await row(id)).needs_review, (await row(id)).outcome], [true, null]);
      await pool.query("UPDATE events SET status = 'resolved' WHERE id = $1", [id]);
      await pool.query("UPDATE events SET alert_name = 'WanHardDown' WHERE id = $1", [id]);
      assert.equal(await row(id), undefined);
      await pool.query("UPDATE events SET alert_name = 'SuricataIntrusion' WHERE id = $1", [id]);
      assert.equal((await row(id)).site_id, 'blue');
      const count = async () => (await pool.query('SELECT count(*)::int AS n FROM reviewer_case_queue')).rows[0].n;
      const before = await count();
      // Cases written before the triggers existed are copied by the backfill, and only once.
      const outcomes = async () => (await pool.query('SELECT case_id, outcome FROM reviewer_case_queue ORDER BY 1')).rows;
      const kept = await outcomes();
      await pool.query('DELETE FROM reviewer_case_queue');
      assert.equal(await backfillQueue(pool), before);
      assert.deepEqual(await outcomes(), kept);
      assert.equal(await backfillQueue(pool), 0);
      assert.equal(await count(), before);
      await pool.query('DELETE FROM events WHERE id = $1', [id]);
      assert.equal(await row(id), undefined);
      assert.equal(await count(), before - 1);
    });

    await t.test('other sites, missing sessions and forged queries are refused', async () => {
      const reject = (promise, status) => assert.rejects(promise, err => err.status === status);
      await reject(list(red, 'blue', undefined), 403);
      await reject(list({...red, sites: []}, 'red', undefined), 403);
      await reject(list({...red, id: null}, 'red', undefined), 401);
      assert.deepEqual(ids(await list({...red, sites: ['blue']}, 'blue', undefined)), ['900']);
      const db = await pool.connect();
      try {
        await db.query('BEGIN');
        await reject(listSignatures(db, red, 'red', {window: "24h'; DROP TABLE events; --", state: 'all', severity: null}), 400);
        await reject(listSignatures(db, red, 'red', {window: '24h', state: 'all', severity: "x' OR '1'='1"}), 400);
      } finally { await db.query('ROLLBACK'); db.release(); }
    });
  });

test('the case query accepts only its closed set of parameters', () => {
  const defaults = {window: '24h', state: 'needs_review', severity: null, signature: null, before: null, limit: 50};
  assert.deepEqual(parseCaseQuery(undefined), defaults);
  assert.deepEqual(parseCaseQuery(''), defaults);
  const cursor = Buffer.from('1795000000123456.0b0e7a52-7a0c-4c64-9d0e-3f6f0c1a2b3c').toString('base64url');
  assert.deepEqual(parseCaseQuery(`window=7d&state=false_positive&severity=watch&signature=2900417&limit=100&before=${cursor}`),
    {window: '7d', state: 'false_positive', severity: 'watch', signature: '2900417', before: cursor, limit: 100});
  assert.equal(parseCaseQuery('signature=').signature, '');
  const encode = text => Buffer.from(text).toString('base64url');
  for (const bad of ['window=1y', 'state=open', 'severity=critical', 'site=blue', 'limit=0', 'limit=101', 'limit=05',
    'limit=1e2', 'limit=-1', 'limit=', 'limit=5&limit=6', 'signature=1%27%20OR%201=1', 'signature=' + '1'.repeat(65),
    'before=', 'before=abc', 'before=' + cursor + '=', 'before=' + cursor + 'A', 'before=' + 'A'.repeat(97),
    'before=' + encode('1795000000123456.0B0E7A52-7A0C-4C64-9D0E-3F6F0C1A2B3C'),
    'before=' + encode('-5.0b0e7a52-7a0c-4c64-9d0e-3f6f0c1a2b3c'),
    'before=' + encode('1795000000123456.0b0e7a52-7a0c-4c64-9d0e-3f6f0c1a2b3c; DROP TABLE events'),
    'before=' + encode("2026-01-01'::timestamptz.0b0e7a52-7a0c-4c64-9d0e-3f6f0c1a2b3c"),
    'before=' + encode('99999999999999999.0b0e7a52-7a0c-4c64-9d0e-3f6f0c1a2b3c'),
    'x'.repeat(600)]) {
    assert.throws(() => parseCaseQuery(bad), err => err.status === 400, bad);
  }
});

test('the case list pages by keyset, is site-scoped and filtered',
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
    for (const file of ['src/db/schema.sql', 'src/reviewer/grants.sql', 'src/reviewer/cases.sql', 'src/reviewer/queue.sql']) {
      await pool.query(readFileSync(path.join(__dirname, file), 'utf8'));
    }
    await pool.query("INSERT INTO sites (id,name) VALUES ('red','red'), ('blue','blue')");

    const reviewed = kind => ({version: 1, kind, reason: 'checked', reviewed_by: randomUUID(), reviewed_at: new Date().toISOString()});
    const second = Math.floor(Date.now() / 1000) * 1000 - 3600000;
    // A timestamp `back` seconds before the base second, with an exact microsecond part.
    const at = (back, micros = '000000') => new Date(second - back * 1000).toISOString().replace('.000Z', '.' + micros + 'Z');
    const evidence = (signature, name, port) => ({
      labels: signature === null ? {} : {signature_id: signature},
      annotations: {summary: name + ' — 10.20.4.17 → 203.0.113.44',
        description: 'Passive IDS observation; not proof of compromise. ' + JSON.stringify({src_ip: '10.20.4.17', src_port: port,
          dest_ip: '203.0.113.44', dest_port: 443, proto: 'TCP', payload_printable: 'private evidence', alert: {signature: name}})},
    });
    const made = {};
    async function add(label, site, signature, severity, timestamp, disposition = null, alert = 'SuricataIntrusion') {
      made[label] = randomUUID();
      await pool.query(`INSERT INTO events (id, site_id, message, alert_name, severity, timestamp, source_evidence, security_disposition)
        VALUES ($1,$2,'m',$3,$4,$5::timestamptz,$6,$7)`,
      [made[label], site, alert, severity, timestamp, evidence(signature, 'Beacon to rare host', 49000), disposition]);
    }
    await add('newest', 'red', '100', 'watch', at(1));
    // Three cases in the same microsecond, and two one microsecond either side of it.
    await add('tieA', 'red', '100', 'watch', at(2, '123456'));
    await add('tieB', 'red', '100', 'info', at(2, '123456'));
    await add('tieC', 'red', '100', 'watch', at(2, '123456'), reviewed('false_positive'));
    await add('after', 'red', '100', 'watch', at(2, '123457'));
    await add('before', 'red', '100', 'watch', at(2, '123455'), reviewed('expected_activity'));
    await add('reopened', 'red', '100', 'watch', at(3), reviewed('needs_review'));
    await add('other', 'red', '200', 'alert', at(4));
    await add('unsigned', 'red', null, 'info', at(5));
    await add('old', 'red', '100', 'watch', new Date(second - 3 * 86400000).toISOString());
    await add('notIds', 'red', '100', 'alert', at(1), null, 'WanHardDown');
    await add('blue', 'blue', '100', 'alert', at(2, '123456'));
    const label = id => Object.keys(made).find(key => made[key] === id);
    const ties = ['tieA', 'tieB', 'tieC'].sort((a, b) => (made[a] < made[b] ? 1 : -1));
    const all = ['newest', 'after', ...ties, 'before', 'reopened', 'other', 'unsigned'];

    async function page(actor, site, search) {
      const db = await pool.connect();
      try {
        await db.query('BEGIN');
        const result = await listCases(db, actor, site, parseCaseQuery(search));
        await db.query('COMMIT');
        return result;
      } catch (err) {
        await db.query('ROLLBACK');
        throw err;
      } finally { db.release(); }
    }
    // Follow `next` to the end and return the labels seen and the size of each page.
    async function walk(actor, site, search) {
      const labels = [], sizes = [];
      for (let next = null, turns = 0; turns < 50; turns++) {
        const result = await page(actor, site, search + (next ? '&before=' + next : ''));
        labels.push(...result.cases.map(row => label(row.id)));
        sizes.push(result.cases.length);
        next = result.next;
        if (!next) return {labels, sizes};
      }
      throw new Error('the list did not end');
    }
    const red = {id: randomUUID(), name: 'Red viewer', role: 'viewer', sites: ['red'], installation: 'A'};

    await t.test('every page size returns each case once, in order, across equal timestamps', async () => {
      for (const limit of [1, 2, 3, 4, 100]) {
        const {labels, sizes} = await walk(red, 'red', 'state=all&limit=' + limit);
        assert.deepEqual(labels, all, 'limit ' + limit);
        assert.ok(sizes.slice(0, -1).every(size => size === limit), 'limit ' + limit);
      }
      // Timestamps one microsecond apart are neither merged nor skipped.
      const two = await page(red, 'red', 'state=all&limit=2');
      assert.deepEqual(two.cases.map(row => label(row.id)), ['newest', 'after']);
      assert.equal(label((await page(red, 'red', 'state=all&limit=1&before=' + two.next)).cases[0].id), ties[0]);
    });

    await t.test('page boundaries: an exact fit has no next page, and an empty list is not an error', async () => {
      const exact = await page(red, 'red', 'state=all&limit=' + all.length);
      assert.deepEqual([exact.cases.length, exact.next], [all.length, null]);
      const short = await page(red, 'red', 'state=all&limit=' + (all.length - 1));
      assert.equal(short.cases.length, all.length - 1);
      const last = await page(red, 'red', 'state=all&limit=' + (all.length - 1) + '&before=' + short.next);
      assert.deepEqual([last.cases.map(row => label(row.id)), last.next], [['unsigned'], null]);
      const none = await page(red, 'red', 'state=confirmed_threat');
      assert.deepEqual([none.cases, none.next], [[], null]);
      assert.deepEqual([exact.limit, (await page(red, 'red', undefined)).limit], [all.length, 50]);
    });

    await t.test('window, signature, state, outcome and severity filters hold across pages', async () => {
      const seen = async search => (await walk(red, 'red', search + '&limit=2')).labels;
      assert.deepEqual(await seen('state=needs_review'), all.filter(key => !['tieC', 'before'].includes(key)));
      assert.deepEqual(await seen('state=reviewed'), all.filter(key => ['tieC', 'before'].includes(key)));
      assert.deepEqual(await seen('state=false_positive'), ['tieC']);
      assert.deepEqual(await seen('state=expected_activity'), ['before']);
      assert.deepEqual(await seen('state=all&signature=100'), all.filter(key => !['other', 'unsigned'].includes(key)));
      assert.deepEqual(await seen('state=all&signature=200'), ['other']);
      assert.deepEqual(await seen('state=all&signature='), ['unsigned']);
      assert.deepEqual(await seen('state=all&signature=999'), []);
      assert.deepEqual(await seen('state=all&severity=info'), ['tieB', 'unsigned']);
      assert.deepEqual(await seen('state=all&window=7d'), [...all, 'old']);
      assert.deepEqual(await seen('state=all&window=7d&signature=100&severity=watch'),
        [...all.filter(key => !['tieB', 'other', 'unsigned'].includes(key)), 'old']);
    });

    await t.test('a row carries bounded traffic metadata and no evidence text', async () => {
      const result = await page(red, 'red', 'state=all&limit=100');
      const row = result.cases.find(item => item.id === made.tieC);
      assert.deepEqual(Object.keys(row).sort(), ['destination_ip', 'destination_port', 'id', 'name', 'occurred_at', 'protocol',
        'severity', 'signature_id', 'source_ip', 'source_port', 'state']);
      assert.deepEqual([row.name, row.signature_id, row.severity, row.state], ['Beacon to rare host', '100', 'watch', 'false_positive']);
      assert.deepEqual([row.source_ip, row.source_port, row.destination_ip, row.destination_port, row.protocol],
        ['10.20.4.17', 49000, '203.0.113.44', 443, 'TCP']);
      assert.equal(result.cases.find(item => item.id === made.reopened).state, 'needs_review');
      assert.equal(result.cases.find(item => item.id === made.unsigned).signature_id, null);
      assert.ok(!JSON.stringify(result).includes('private evidence') && !JSON.stringify(result).includes('checked'));
      assert.deepEqual(Object.keys(result).sort(), ['cases', 'limit', 'next', 'severity', 'signature', 'site', 'state', 'window']);
      // Evidence that is not the expected JSON leaves the traffic fields empty.
      await pool.query(`UPDATE events SET source_evidence = jsonb_set(source_evidence, '{annotations,description}', '"free text"') WHERE id = $1`, [made.other]);
      const plain = (await page(red, 'red', 'state=all&signature=200')).cases[0];
      assert.deepEqual([plain.source_ip, plain.destination_port, plain.protocol, plain.name], [null, null, null, 'Beacon to rare host']);
    });

    await t.test('other sites, missing sessions and forged queries or cursors are refused', async () => {
      const reject = (promise, status) => assert.rejects(promise, err => err.status === status);
      await reject(page(red, 'blue', undefined), 403);
      await reject(page({...red, sites: []}, 'red', undefined), 403);
      await reject(page({...red, id: null}, 'red', undefined), 401);
      const blue = {...red, sites: ['blue']};
      const first = await page(blue, 'blue', 'limit=1');
      assert.deepEqual([first.cases.map(row => label(row.id)), first.next], [['blue'], null]);
      // A cursor is a position, not a grant: one built from another site's case still lists only this site.
      const foreign = Buffer.from(`${BigInt(second - 2000) * 1000n + 123456n}.${made.blue}`).toString('base64url');
      const mine = await page(red, 'red', 'state=all&limit=100&before=' + foreign);
      assert.ok(mine.cases.length > 0 && mine.cases.every(row => all.includes(label(row.id))));
      const db = await pool.connect();
      try {
        await db.query('BEGIN');
        const base = parseCaseQuery('state=all');
        await reject(listCases(db, red, 'red', {...base, window: "24h'; DROP TABLE events; --"}), 400);
        await reject(listCases(db, red, 'red', {...base, signature: "1' OR '1'='1"}), 400);
        await reject(listCases(db, red, 'red', {...base, state: "all' OR '1'='1"}), 400);
        await reject(listCases(db, red, 'red', {...base, limit: 1000}), 400);
        await reject(listCases(db, red, 'red', {...base, limit: '5; DROP TABLE events'}), 400);
        await reject(listCases(db, red, 'red', {...base, before: "x' OR '1'='1"}), 400);
        await reject(listCases(db, red, 'red', {...base, before: {micros: '1', caseId: made.blue}}), 400);
      } finally { await db.query('ROLLBACK'); db.release(); }
      assert.equal((await pool.query('SELECT count(*)::int AS n FROM events')).rows[0].n, Object.keys(made).length);
    });
  });

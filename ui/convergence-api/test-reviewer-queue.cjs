'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {Pool} = require('pg');
const {listSignatures, parseSignatureQuery} = require('./src/reviewer/queue');

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
    for (const file of ['src/db/schema.sql', 'src/reviewer/grants.sql', 'src/reviewer/cases.sql']) {
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
      assert.deepEqual(ids(await list(red, 'red', 'severity=watch')), ['100']);
      assert.equal((await list(red, 'red', 'severity=watch')).signatures[0].cases, 2);
      assert.deepEqual(ids(await list(red, 'red', 'window=7d')), ['400', '100', '200']);
      assert.deepEqual(ids(await list(red, 'red', 'window=30d&state=all')), ['400', '100', '200', '300']);
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

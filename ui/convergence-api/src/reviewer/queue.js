'use strict';

const {requireScope} = require('./cases');
const {trafficContext} = require('../lib/trafficContext');

const WINDOWS = {'24h': '24 hours', '7d': '7 days', '30d': '30 days'};
const OUTCOMES = ['expected_activity', 'false_positive', 'confirmed_threat'];
const STATES = ['needs_review', 'reviewed', 'all', ...OUTCOMES];
const SEVERITIES = ['ok', 'info', 'watch', 'alert'];
const SIGNATURE_LIMIT = 500;
const CASE_LIMIT = 100;
const CASE_LIMIT_DEFAULT = 50;
const SIGNATURE_ID = /^[A-Za-z0-9_.:-]{0,64}$/;
const CURSOR = /^([0-9]{1,16})\.([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/;

function failure(status, message) {
  return Object.assign(new Error(message), {status});
}

// Accept only the named parameters, each at most once and from a closed set.
function parseSignatureQuery(search) {
  const query = {window: '24h', state: 'needs_review', severity: null};
  if (search === undefined || search === '') return query;
  if (typeof search !== 'string' || search.length > 256) throw failure(400, 'Invalid query');
  const seen = new Set();
  for (const [key, value] of new URLSearchParams(search)) {
    if (!Object.hasOwn(query, key) || seen.has(key)) throw failure(400, 'Invalid query');
    seen.add(key);
    if (key === 'window' && !Object.hasOwn(WINDOWS, value)) throw failure(400, 'Invalid query');
    if (key === 'state' && !STATES.includes(value)) throw failure(400, 'Invalid query');
    if (key === 'severity' && !SEVERITIES.includes(value)) throw failure(400, 'Invalid query');
    query[key] = value;
  }
  return query;
}

// One row per detection signature seen on the site in the window. The site comes from
// the route and is checked against the grant; nothing here trusts the browser for scope.
async function listSignatures(client, actor, site, query) {
  requireScope(actor, site);
  if (!query || !Object.hasOwn(WINDOWS, query.window) || !STATES.includes(query.state) ||
      (query.severity !== null && !SEVERITIES.includes(query.severity))) throw failure(400, 'Invalid query');
  // A bad plan fails instead of holding the database. The caller's transaction ends with this read.
  await client.query("SET LOCAL statement_timeout = '5s'");
  const having = query.state === 'needs_review' ? 'HAVING count(*) FILTER (WHERE needs_review) > 0'
    : query.state === 'reviewed' ? 'HAVING count(*) FILTER (WHERE NOT needs_review) > 0'
      : OUTCOMES.includes(query.state) ? 'HAVING count(*) FILTER (WHERE outcome = $4) > 0' : '';
  const values = [site, WINDOWS[query.window], query.severity];
  if (OUTCOMES.includes(query.state)) values.push(query.state);
  // Group the narrow queue rows, then read one case per signature for its name.
  const result = await client.query(`
    SELECT g.signature_id, g.cases, g.needs_review, g.rank, g.first_seen, g.last_seen,
           (SELECT left(split_part(e.source_evidence #>> '{annotations,summary}', ' — ', 1), 256)
              FROM events e
             WHERE e.id = (SELECT q.case_id FROM reviewer_case_queue q
                            WHERE q.site_id = $1 AND q.signature_id = g.signature_id
                            ORDER BY q.occurred_at DESC, q.case_id DESC LIMIT 1)) AS name
      FROM (SELECT signature_id, count(*)::int AS cases,
                   (count(*) FILTER (WHERE needs_review))::int AS needs_review,
                   max(CASE severity WHEN 'alert' THEN 3 WHEN 'watch' THEN 2 WHEN 'info' THEN 1 ELSE 0 END) AS rank,
                   min(occurred_at) AS first_seen, max(occurred_at) AS last_seen
              FROM reviewer_case_queue
             WHERE site_id = $1 AND occurred_at >= statement_timestamp() - $2::interval
               AND ($3::text IS NULL OR severity = $3)
             GROUP BY 1 ${having}
             ORDER BY rank DESC, needs_review DESC, last_seen DESC, signature_id
             LIMIT ${SIGNATURE_LIMIT + 1}) g
     ORDER BY g.rank DESC, g.needs_review DESC, g.last_seen DESC, g.signature_id`,
  values);
  const rows = result.rows.slice(0, SIGNATURE_LIMIT);
  return {
    site, window: query.window, state: query.state, severity: query.severity,
    truncated: result.rows.length > SIGNATURE_LIMIT,
    signatures: rows.map(row => ({
      signature_id: row.signature_id || null, name: row.name || null, cases: row.cases, needs_review: row.needs_review,
      severity: ['ok', 'info', 'watch', 'alert'][row.rank],
      first_seen: row.first_seen.toISOString(), last_seen: row.last_seen.toISOString(),
    })),
  };
}

// The cursor names the last row of a page: its time in microseconds, so that rows the
// database tells apart are never merged by a millisecond clock, and its case ID.
function encodeCursor(micros, caseId) {
  return Buffer.from(`${micros}.${caseId}`).toString('base64url');
}

function decodeCursor(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,96}$/.test(value)) throw failure(400, 'Invalid query');
  const match = CURSOR.exec(Buffer.from(value, 'base64url').toString('latin1'));
  // Only the exact text this service issued is accepted.
  if (!match || encodeCursor(match[1], match[2]) !== value) throw failure(400, 'Invalid query');
  return {micros: match[1], caseId: match[2]};
}

function parseCaseQuery(search) {
  const query = {window: '24h', state: 'needs_review', severity: null, signature: null, before: null, limit: CASE_LIMIT_DEFAULT};
  if (search === undefined || search === '') return query;
  if (typeof search !== 'string' || search.length > 512) throw failure(400, 'Invalid query');
  const seen = new Set();
  for (const [key, value] of new URLSearchParams(search)) {
    if (!Object.hasOwn(query, key) || seen.has(key)) throw failure(400, 'Invalid query');
    seen.add(key);
    if (key === 'window' && !Object.hasOwn(WINDOWS, value)) throw failure(400, 'Invalid query');
    if (key === 'state' && !STATES.includes(value)) throw failure(400, 'Invalid query');
    if (key === 'severity' && !SEVERITIES.includes(value)) throw failure(400, 'Invalid query');
    // An empty signature selects the cases that carry no signature ID.
    if (key === 'signature' && !SIGNATURE_ID.test(value)) throw failure(400, 'Invalid query');
    if (key === 'before') { decodeCursor(value); }
    if (key === 'limit') {
      if (!/^[1-9][0-9]{0,2}$/.test(value) || Number(value) > CASE_LIMIT) throw failure(400, 'Invalid query');
      query.limit = Number(value);
      continue;
    }
    query[key] = value;
  }
  return query;
}

// One page of a site's cases, newest first, by keyset on (time, case ID). The queue
// table picks the page; `events` is read only for the rows on it.
async function listCases(client, actor, site, query) {
  requireScope(actor, site);
  if (!query || !Object.hasOwn(WINDOWS, query.window) || !STATES.includes(query.state) ||
      (query.severity !== null && !SEVERITIES.includes(query.severity)) ||
      (query.signature !== null && (typeof query.signature !== 'string' || !SIGNATURE_ID.test(query.signature))) ||
      !Number.isInteger(query.limit) || query.limit < 1 || query.limit > CASE_LIMIT) throw failure(400, 'Invalid query');
  const before = query.before === null ? null : decodeCursor(query.before);
  await client.query("SET LOCAL statement_timeout = '5s'");
  // Each filter is a fixed fragment with a numbered value, so the plan can use the
  // signature index when a signature is named and the window index otherwise.
  const values = [site, WINDOWS[query.window]];
  const value = item => '$' + values.push(item);
  let where = '';
  if (query.signature !== null) where += ` AND signature_id = ${value(query.signature)}`;
  if (query.severity !== null) where += ` AND severity = ${value(query.severity)}`;
  if (query.state === 'needs_review') where += ' AND needs_review';
  else if (query.state === 'reviewed') where += ' AND NOT needs_review';
  else if (query.state !== 'all') where += ` AND outcome = ${value(query.state)}`;
  if (before) {
    where += ` AND (occurred_at, case_id) < ('epoch'::timestamptz + ${value(before.micros)}::bigint * interval '1 microsecond', ${value(before.caseId)}::uuid)`;
  }
  const result = await client.query(`
    SELECT p.case_id, p.occurred_at, p.micros::text AS micros, left(p.signature_id, 64) AS signature_id,
           left(p.severity, 64) AS severity, p.needs_review, left(p.outcome, 64) AS outcome,
           left(split_part(e.source_evidence #>> '{annotations,summary}', ' — ', 1), 256) AS name,
           left(e.source_evidence #>> '{annotations,description}', 16384) AS description
      FROM (SELECT case_id, occurred_at, (extract(epoch FROM occurred_at) * 1000000)::bigint AS micros,
                   signature_id, severity, needs_review, outcome
              FROM reviewer_case_queue
             WHERE site_id = $1 AND occurred_at >= statement_timestamp() - $2::interval${where}
             ORDER BY occurred_at DESC, case_id DESC
             LIMIT ${value(query.limit + 1)}) p
      JOIN events e ON e.id = p.case_id AND e.site_id = $1
     ORDER BY p.occurred_at DESC, p.case_id DESC`, values);
  const rows = result.rows.slice(0, query.limit);
  const last = rows[rows.length - 1];
  return {
    site, window: query.window, state: query.state, severity: query.severity, signature: query.signature,
    limit: query.limit,
    cases: rows.map(row => {
      // Bounded traffic metadata only; the evidence text itself stays on the case page.
      const traffic = trafficContext({annotations: {description: row.description}});
      return {
        id: row.case_id, occurred_at: row.occurred_at.toISOString(), signature_id: row.signature_id || null,
        name: row.name || null, severity: row.severity, state: row.needs_review ? 'needs_review' : row.outcome || 'reviewed',
        source_ip: traffic?.source_ip ?? null, source_port: traffic?.source_port ?? null,
        destination_ip: traffic?.destination_ip ?? null, destination_port: traffic?.destination_port ?? null,
        protocol: traffic?.protocol ?? null,
      };
    }),
    next: result.rows.length > query.limit ? encodeCursor(last.micros, last.case_id) : null,
  };
}

// Copy existing cases into the queue, one site and one day per statement, so no
// statement holds a long lock or snapshot. Safe to stop and run again: rows the
// triggers already wrote are left alone.
async function backfillQueue(pool, log = () => {}) {
  const {rows: sites} = await pool.query('SELECT id FROM sites ORDER BY id');
  let copied = 0;
  for (const {id: site} of sites) {
    const {rows: [range]} = await pool.query(
      `SELECT date_trunc('day', min(timestamp)) AS first, max(timestamp) AS last FROM events WHERE site_id = $1`, [site]);
    if (!range.first) continue;
    for (let day = range.first; day <= range.last; day = new Date(day.getTime() + 86400000)) {
      const result = await pool.query(`
        INSERT INTO reviewer_case_queue (case_id, site_id, occurred_at, signature_id, severity, needs_review, outcome)
        SELECT id, site_id, timestamp, coalesce(left(source_evidence #>> '{labels,signature_id}', 64), ''),
               severity, reviewer_case_needs_review(security_disposition), reviewer_case_outcome(security_disposition)
          FROM events
         WHERE site_id = $1 AND timestamp >= $2 AND timestamp < $2::timestamptz + interval '1 day'
           AND alert_name = 'SuricataIntrusion'
        ON CONFLICT (case_id) DO NOTHING`, [site, day]);
      copied += result.rowCount;
      log({site, day: day.toISOString().slice(0, 10), copied: result.rowCount});
    }
  }
  return copied;
}

module.exports = {backfillQueue, listCases, listSignatures, parseCaseQuery, parseSignatureQuery};

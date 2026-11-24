'use strict';

const {isIP} = require('node:net');
const {requireScope, uuid} = require('./cases');
const {OUTCOMES, WINDOWS} = require('./queue');
const {securityDisposition, validDisposition} = require('../lib/securityAssessment');

// The most cases one decision may cover. Past it the write is refused and nothing is saved.
const BULK_CEILING = 5000;
const SOURCE_LIMIT = 10;
const SIGNATURE_ID = /^[A-Za-z0-9_.:-]{1,64}$/;
// Window bounds travel as text with microseconds, exactly as the preview issued them.
const INSTANT = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$/;
const INSTANT_SQL = `'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'`;
const FIELDS = ['operationId', 'kind', 'reason', 'windowStart', 'windowEnd', 'source', 'confirmedCount'];
// The cases a decision covers: one site and signature, a fixed window, still needing
// review, optionally from one source address. The preview and the write both use it.
// $1 site, $2 signature, $3 window start, $4 window end, $5 source or NULL.
const SCOPE = `q.site_id = $1 AND q.signature_id = $2 AND q.occurred_at >= $3::timestamptz
  AND q.occurred_at < $4::timestamptz AND q.needs_review AND ($5::inet IS NULL OR q.source_ip = $5::inet)`;
const RECEIPT = `operation_id, site_id, signature_id,
  to_char(window_start AT TIME ZONE 'UTC', ${INSTANT_SQL}) AS window_start,
  to_char(window_end AT TIME ZONE 'UTC', ${INSTANT_SQL}) AS window_end,
  host(source_host) AS source_host, kind, reason, actor_id AS reviewed_by, actor_name AS reviewed_by_name,
  reviewed_at, case_count`;

function failure(status, message) {
  return Object.assign(new Error(message), {status});
}

function signatureId(value) {
  if (typeof value !== 'string' || !SIGNATURE_ID.test(value)) throw failure(400, 'Invalid signature');
  return value;
}

// One address in plain form: no network, zone or surrounding text.
function sourceHost(value) {
  if (value === null) return null;
  if (typeof value !== 'string' || value.length > 45 || !isIP(value) || value.includes('%')) throw failure(400, 'Invalid source');
  return value;
}

function instant(value) {
  if (typeof value !== 'string' || !INSTANT.test(value)) throw failure(400, 'Invalid window');
  const time = Date.parse(value);
  // Reject text that only looks like a date, such as month 13.
  if (Number.isNaN(time) || new Date(time).toISOString().slice(0, 23) !== value.slice(0, 23)) throw failure(400, 'Invalid window');
  return value;
}

function parsePreviewQuery(search) {
  const query = {window: '24h', source: null};
  if (search === undefined || search === '') return query;
  if (typeof search !== 'string' || search.length > 128) throw failure(400, 'Invalid query');
  const seen = new Set();
  for (const [key, value] of new URLSearchParams(search)) {
    if (!Object.hasOwn(query, key) || seen.has(key)) throw failure(400, 'Invalid query');
    seen.add(key);
    if (key === 'window' && !Object.hasOwn(WINDOWS, value)) throw failure(400, 'Invalid query');
    if (key === 'source') sourceHost(value);
    query[key] = value;
  }
  return query;
}

// What a bulk decision would cover right now. The bounds it returns are fixed points
// in time; the write takes them back, so a case that arrives later is outside them.
async function previewBulk(client, actor, site, signature, query) {
  requireScope(actor, site, true);
  signatureId(signature);
  if (!query || !Object.hasOwn(WINDOWS, query.window)) throw failure(400, 'Invalid query');
  const source = sourceHost(query.source);
  await client.query("SET LOCAL statement_timeout = '5s'");
  const {rows: [bounds]} = await client.query(`
    SELECT to_char((statement_timestamp() - $1::interval) AT TIME ZONE 'UTC', ${INSTANT_SQL}) AS window_start,
           to_char(statement_timestamp() AT TIME ZONE 'UTC', ${INSTANT_SQL}) AS window_end`, [WINDOWS[query.window]]);
  const scope = [site, signature, bounds.window_start, bounds.window_end];
  const {rows: [count]} = await client.query(
    `SELECT count(*)::int AS cases FROM reviewer_case_queue q WHERE ${SCOPE}`, [...scope, source]);
  // The busiest sources of the whole signature, so the page can offer "only this host".
  const sources = await client.query(`
    SELECT host(q.source_ip) AS source_host, count(*)::int AS cases FROM reviewer_case_queue q
     WHERE ${SCOPE} AND q.source_ip IS NOT NULL
     GROUP BY q.source_ip ORDER BY cases DESC, q.source_ip LIMIT ${SOURCE_LIMIT + 1}`, [...scope, null]);
  return {
    site, signature_id: signature, window: query.window, window_start: bounds.window_start, window_end: bounds.window_end,
    source_host: source, cases: count.cases, ceiling: BULK_CEILING,
    sources: sources.rows.slice(0, SOURCE_LIMIT), sources_truncated: sources.rowCount > SOURCE_LIMIT,
  };
}

// One decision for every case in the scope that still needs review. Everything happens
// in the caller's transaction: on any failure nothing is written.
async function recordBulk(client, actor, site, signature, input) {
  requireScope(actor, site, true);
  signatureId(signature);
  if (!input || typeof input !== 'object' || Object.keys(input).length !== FIELDS.length
      || !FIELDS.every(key => Object.hasOwn(input, key))
      || !OUTCOMES.includes(input.kind) || !validDisposition(input.kind, input.reason)
      || /[\u0000\uD800-\uDFFF]/u.test(input.reason)
      || !Number.isInteger(input.confirmedCount) || input.confirmedCount < 1 || input.confirmedCount > BULK_CEILING) {
    throw failure(400, 'Invalid bulk disposition');
  }
  const operationId = uuid(input.operationId);
  const reason = input.reason.trim();
  const source = sourceHost(input.source);
  const scope = [site, signature, instant(input.windowStart), instant(input.windowEnd), source];
  await client.query("SET LOCAL statement_timeout = '15s'");
  const {rows: [window]} = await client.query(`
    SELECT $1::timestamptz < $2::timestamptz AND $2::timestamptz <= statement_timestamp()
       AND $2::timestamptz - $1::timestamptz <= interval '30 days' AS valid`, [scope[2], scope[3]]);
  if (!window.valid) throw failure(400, 'Invalid window');

  // Claim the operation ID first. A replay waits here for the original to finish, and
  // an ID already used for another command is refused.
  const identity = [actor.installation, actor.id, operationId];
  await client.query(`INSERT INTO reviewer_operation_keys (installation_id, actor_id, operation_id, command)
    VALUES ($1, $2, $3, 'bulk_disposition') ON CONFLICT DO NOTHING`, identity);
  const claimed = await client.query(`SELECT command FROM reviewer_operation_keys
    WHERE installation_id = $1 AND actor_id = $2 AND operation_id = $3`, identity);
  if (claimed.rows[0]?.command !== 'bulk_disposition') throw failure(409, 'Conflicting operation');
  const prior = await client.query(`
    SELECT ${RECEIPT}, (site_id = $4 AND signature_id = $5 AND window_start = $6::timestamptz AND window_end = $7::timestamptz
           AND source_host IS NOT DISTINCT FROM $8::inet AND kind = $9 AND reason = $10) AS same
      FROM reviewer_bulk_dispositions WHERE installation_id = $1 AND actor_id = $2 AND operation_id = $3`,
  [...identity, ...scope, input.kind, reason]);
  if (prior.rowCount) {
    const {same, ...receipt} = prior.rows[0];
    if (!same) throw failure(409, 'Operation ID already used with different inputs');
    return receipt;
  }

  // Lock the cases, in a fixed order, and read their state again under the lock: a case
  // that someone reviewed after the preview no longer matches and is left alone.
  const locked = await client.query(`
    SELECT e.id FROM events e
     WHERE e.id IN (SELECT q.case_id FROM reviewer_case_queue q WHERE ${SCOPE})
       AND e.site_id = $1 AND e.alert_name = 'SuricataIntrusion' AND reviewer_case_needs_review(e.security_disposition)
     ORDER BY e.id LIMIT ${BULK_CEILING + 1} FOR UPDATE OF e`, scope);
  const count = locked.rowCount;
  // Fewer than confirmed is expected when others reviewed some meanwhile. More means
  // cases the reviewer has not seen, so the reviewer must look again.
  if (count === 0 || count > BULK_CEILING || count > input.confirmedCount) throw failure(409, 'The cases changed since the preview');

  let inserted;
  try {
    inserted = await client.query(`
      INSERT INTO reviewer_bulk_dispositions (installation_id, actor_id, operation_id, actor_name, site_id, signature_id,
             window_start, window_end, source_host, kind, reason, case_count)
      VALUES ($1, $2, $3, $4, $5, $6, $7::timestamptz, $8::timestamptz, $9::inet, $10, $11, $12) RETURNING ${RECEIPT}`,
    [...identity, actor.name, ...scope, input.kind, reason, count]);
  } catch (error) {
    if (error.constraint === 'reviewer_command_identity') throw failure(409, 'Conflicting operation');
    throw error;
  }
  const receipt = inserted.rows[0];
  const disposition = securityDisposition(receipt.kind, receipt.reason, actor.id, receipt.reviewed_at.toISOString());
  disposition.reviewed_by_name = receipt.reviewed_by_name;
  disposition.bulk_operation_id = receipt.operation_id;
  const updated = await client.query('UPDATE events SET security_disposition = $1 WHERE id = ANY($2::uuid[]) AND site_id = $3',
    [disposition, locked.rows.map(row => row.id), site]);
  if (updated.rowCount !== count) throw new Error('Bulk disposition covered an unexpected number of cases');
  return receipt;
}

module.exports = {BULK_CEILING, parsePreviewQuery, previewBulk, recordBulk};

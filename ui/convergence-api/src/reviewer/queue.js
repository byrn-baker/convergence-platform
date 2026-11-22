'use strict';

const {requireScope} = require('./cases');

const WINDOWS = {'24h': '24 hours', '7d': '7 days', '30d': '30 days'};
const STATES = ['needs_review', 'reviewed', 'all'];
const SEVERITIES = ['ok', 'info', 'watch', 'alert'];
const SIGNATURE_LIMIT = 500;

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
    : query.state === 'reviewed' ? 'HAVING count(*) FILTER (WHERE NOT needs_review) > 0' : '';
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
  [site, WINDOWS[query.window], query.severity]);
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
        INSERT INTO reviewer_case_queue (case_id, site_id, occurred_at, signature_id, severity, needs_review)
        SELECT id, site_id, timestamp, coalesce(left(source_evidence #>> '{labels,signature_id}', 64), ''),
               severity, reviewer_case_needs_review(security_disposition)
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

module.exports = {backfillQueue, listSignatures, parseSignatureQuery};

'use strict';

const {requireScope} = require('./cases');

const WINDOWS = {'24h': '24 hours', '7d': '7 days', '30d': '30 days'};
const STATES = ['needs_review', 'reviewed', 'all'];
const SEVERITIES = ['ok', 'info', 'watch', 'alert'];
const SIGNATURE_LIMIT = 500;

function failure(status, message) {
  return Object.assign(new Error(message), {status});
}

// A case needs review until it carries a disposition other than "needs_review".
const NEEDS_REVIEW = `(security_disposition IS NULL OR jsonb_typeof(security_disposition) <> 'object'
  OR security_disposition->>'kind' IS NULL OR security_disposition->>'kind' = 'needs_review')`;

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
  const having = query.state === 'needs_review' ? `HAVING count(*) FILTER (WHERE ${NEEDS_REVIEW}) > 0`
    : query.state === 'reviewed' ? `HAVING count(*) FILTER (WHERE NOT ${NEEDS_REVIEW}) > 0` : '';
  const result = await client.query(`
    SELECT left(source_evidence #>> '{labels,signature_id}', 64) AS signature_id,
           left(split_part(max(source_evidence #>> '{annotations,summary}'), ' — ', 1), 256) AS name,
           count(*)::int AS cases,
           (count(*) FILTER (WHERE ${NEEDS_REVIEW}))::int AS needs_review,
           max(CASE severity WHEN 'alert' THEN 3 WHEN 'watch' THEN 2 WHEN 'info' THEN 1 ELSE 0 END) AS rank,
           min(timestamp) AS first_seen, max(timestamp) AS last_seen
      FROM events
     WHERE site_id = $1 AND alert_name = 'SuricataIntrusion'
       AND timestamp >= statement_timestamp() - $2::interval
       AND ($3::text IS NULL OR severity = $3)
     GROUP BY 1 ${having}
     ORDER BY rank DESC, needs_review DESC, last_seen DESC, signature_id
     LIMIT ${SIGNATURE_LIMIT + 1}`, [site, WINDOWS[query.window], query.severity]);
  const rows = result.rows.slice(0, SIGNATURE_LIMIT);
  return {
    site, window: query.window, state: query.state, severity: query.severity,
    truncated: result.rows.length > SIGNATURE_LIMIT,
    signatures: rows.map(row => ({
      signature_id: row.signature_id, name: row.name || null, cases: row.cases, needs_review: row.needs_review,
      severity: ['ok', 'info', 'watch', 'alert'][row.rank],
      first_seen: row.first_seen.toISOString(), last_seen: row.last_seen.toISOString(),
    })),
  };
}

module.exports = {listSignatures, parseSignatureQuery};

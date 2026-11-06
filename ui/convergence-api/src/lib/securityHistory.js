'use strict';

const WINDOWS = {'1h':3600, '6h':21600, '24h':86400, '7d':604800};

// Older annotations store IP context as a JSON string. Invalid or absent
// enrichment must remain unknown, without breaking the entire history query.
const DESTINATION_SQL = `CASE
  WHEN jsonb_typeof(source_evidence #> '{annotations,ip_context}') = 'object'
    THEN source_evidence #> '{annotations,ip_context,destination}'
  WHEN pg_input_is_valid(source_evidence #>> '{annotations,ip_context}', 'jsonb')
    THEN (source_evidence #>> '{annotations,ip_context}')::jsonb -> 'destination'
  ELSE NULL END`;
const COUNTRY_SQL = `CASE WHEN destination->>'scope' = 'internal' THEN 'internal'
  WHEN destination->>'scope' = 'external' AND destination->>'country_code' ~ '^[A-Z]{2}$'
    THEN destination->>'country_code' ELSE 'unknown' END`;

function historyWindow(input = {}, now = Date.now()) {
  const range = input.range || '24h';
  if (!Object.hasOwn(WINDOWS, range)) throw new Error('Choose a 1h, 6h, 24h or 7d time window');
  return {range, start:new Date(now - WINDOWS[range]*1000).toISOString(), end:new Date(now).toISOString(),
    bucket_seconds:WINDOWS[range] / (range === '7d' ? 28 : 24)};
}

function caseHistoryFilters(input, now = Date.now()) {
  const filtered = input.start !== undefined || input.end !== undefined || input.country !== undefined;
  if (!filtered) return {};
  if (input.type !== 'security') throw new Error('Historical filters require security cases');
  const result = {};
  if (input.start !== undefined || input.end !== undefined) {
    const start=Date.parse(input.start), end=Date.parse(input.end);
    if (!Number.isFinite(start) || !Number.isFinite(end) || start>=end || end-start>604800000 || end>now+60000)
      throw new Error('Choose a valid interval of up to seven days');
    result.start=new Date(start).toISOString(); result.end=new Date(end).toISOString();
  }
  if (input.country !== undefined) {
    if (typeof input.country !== 'string' || !/^(?:[A-Z]{2}|internal|unknown)$/.test(input.country))
      throw new Error('Invalid destination country');
    result.country=input.country;
  }
  return result;
}

async function securityHistory(site, input, query, now = Date.now()) {
  const window=historyWindow(input, now);
  // One statement gives the chart and geography the same database snapshot.
  // Aggregate before limiting: the diary's latest-100 cap never applies here.
  const {rows} = await query(`WITH observations AS MATERIALIZED (
    SELECT timestamp, ${DESTINATION_SQL} AS destination FROM events
    WHERE site_id = $1 AND alert_name = 'SuricataIntrusion'
      AND timestamp >= $2::timestamptz AND timestamp < $3::timestamptz
  ), classified AS (
    SELECT timestamp, ${COUNTRY_SQL} AS country FROM observations
  ), timeline AS (
    SELECT floor(extract(epoch FROM timestamp) / $4)::bigint AS bucket,
      count(*)::int AS count FROM classified GROUP BY 1
  ), geography AS (
    SELECT country, count(*)::int AS count FROM classified GROUP BY 1
  ) SELECT 'bucket' AS kind, bucket::text AS key, count FROM timeline
    UNION ALL SELECT 'country', country, count FROM geography`,
  [site, window.start, window.end, window.bucket_seconds]);
  const counts=new Map(rows.filter(r=>r.kind==='bucket').map(r=>[Number(r.key),Number(r.count)]));
  const countries=rows.filter(r=>r.kind==='country').map(r=>({code:r.key,count:Number(r.count)}))
    .sort((a,b)=>b.count-a.count || a.code.localeCompare(b.code));
  const startMs=Date.parse(window.start),endMs=Date.parse(window.end),step=window.bucket_seconds*1000;
  const firstBucket=Math.floor(startMs/step),bucketCount=Math.ceil(endMs/step)-firstBucket;
  const buckets=Array.from({length:bucketCount},(_,i)=>({
    start:new Date(Math.max(startMs,(firstBucket+i)*step)).toISOString(),
    end:new Date(Math.min(endMs,(firstBucket+i+1)*step)).toISOString(), count:counts.get(firstBucket+i)||0,
  }));
  return {site,...window,total:countries.reduce((n,c)=>n+c.count,0),buckets,countries,
    basis:'All saved IDS cases by recorded case time; not raw detections, traffic volume or unique incidents.'};
}

module.exports={securityHistory,caseHistoryFilters,DESTINATION_SQL,COUNTRY_SQL};

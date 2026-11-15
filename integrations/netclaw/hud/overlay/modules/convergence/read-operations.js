import { isIP } from 'node:net';

const eventId = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const sites = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
const timeWindow = value => ['1h', '6h', '24h', '7d'].includes(value);
const utc = value => /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{3})?Z$/.test(value) && Number.isFinite(Date.parse(value));
const fields = {
  health: 'timestamp healthScore uptime wanLatency wanLoss speedtest alertCount sources devices unavailable reason',
  wifi: 'clients txRetries',
  security: 'status source observedWindow blockedConnections topSources intrusionDetection hostTelemetry hypervisorTelemetry unavailable reason',
  'security/vms': 'status generated_at window_seconds sources synthetic hostTelemetry unavailable reason',
  'security/history': 'range start end bucket_seconds total buckets countries basis',
  flows: 'coverage status reason range start end read_at exporter exporters total sources destinations conversations sampling basis limitations',
  alerts: 'firing resolved',
  events: 'limit range historical events unavailable reason',
  'isp/providers': 'version checked_at scope providers',
};
const eventFields = ('id site_id timestamp message severity category source status alert_name alert_fingerprint root_cause ' +
  'investigation_notes expert_feedback feedback_quality rag_document_id incident_key alert_cleared_at ip_context ' +
  'jev_review security_disposition isp_report_generated_at capture_context traffic_context security_assessment ' +
  'escalated_at investigation_started_at investigation_completed_at feedback_provided_at created_at updated_at').split(' ');
const filters = {
  flows: {range: v => ['15m', '1h', '6h', '24h'].includes(v), exporter: v => v === '' || !!isIP(v)},
  'security/history': {range: timeWindow},
  events: {
    range: timeWindow, limit: v => /^(?:[1-9][0-9]?|100)$/.test(v),
    type: v => v === 'security' || v === 'wan', status: v => v === 'escalated',
    start: utc, end: utc, country: v => /^(?:[A-Z]{2}|internal|unknown)$/.test(v),
  },
};

export class ReadFailure extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export function validSite(value) {
  return typeof value === 'string' && sites.test(value) && !Object.hasOwn(Object.prototype, value) && value !== 'prototype';
}

function invalid() { throw new ReadFailure(400, 'Invalid Convergence read request.'); }
function unavailable() { throw new ReadFailure(502, 'Scoped Convergence data is unavailable.'); }

export function parseTarget(raw) {
  if (typeof raw !== 'string' || raw.length > 2048 || /[#\\\s\u0000-\u001f]/.test(raw)) invalid();
  const [path, query, ...extra] = raw.split('?');
  if (extra.length || !/^\/api\/home\/[A-Za-z0-9/-]+$/.test(path) || path.includes('//') || path.endsWith('/')) invalid();
  const params = new URLSearchParams(query);
  const seen = new Set();
  for (const [key, value] of params) {
    if (seen.size >= 10 || seen.has(key) || value.length > 128) invalid();
    seen.add(key);
  }
  return {resource: path.slice('/api/home/'.length), params};
}

export function readOperation({resource, params}, site) {
  if (resource === 'sites') {
    if (params.size) invalid();
    return {value: {sites: [{id: site, name: site, healthy: null}]}};
  }
  const escalated = resource === 'events/escalated';
  const id = resource.startsWith('events/') && eventId.test(resource.slice(7)) ? resource.slice(7) : null;
  if (!escalated && !id && !Object.hasOwn(fields, resource) && resource !== 'devices') {
    throw new ReadFailure(404, 'Unknown Convergence read.');
  }

  const query = new URLSearchParams({site});
  for (const [key, value] of params) {
    if (key === 'site') {
      if (value !== site) throw new ReadFailure(403, 'Outside the configured site.');
      continue;
    }
    if (!Object.hasOwn(filters, resource) || !Object.hasOwn(filters[resource], key) || !filters[resource][key](value)) invalid();
    query.set(key, value);
  }

  if (resource === 'devices') {
    // The baseline device API includes unscoped metrics. Do not dispatch it.
    return {value: {site, devices: [], unavailable: true, reason: 'Site-scoped inventory is unavailable.'}};
  }
  if (resource === 'events') {
    const start = params.get('start'), end = params.get('end');
    if (start || end || params.has('country')) {
      if (params.get('type') !== 'security') invalid();
      if (start || end) {
        const duration = Date.parse(end) - Date.parse(start);
        if (!start || !end || duration <= 0 || duration > 7 * 86400000 || Date.parse(end) > Date.now() + 60000) invalid();
      }
    }
    if (!query.has('limit')) query.set('limit', '100');
  }
  if (escalated) {
    query.set('status', 'escalated');
    query.set('limit', '100');
  }
  const apiResource = escalated ? 'events' : resource;
  return {resource: apiResource, path: '/api/' + apiResource, query, id};
}

function select(record, keys) {
  if (!record || typeof record !== 'object' || Array.isArray(record)) unavailable();
  return Object.fromEntries(keys.filter(key => Object.hasOwn(record, key)).map(key => [key, record[key]]));
}

// Subservices may carry their own site tags. Never publish a partially mixed
// response, including an apparently correct envelope containing foreign rows.
function inspect(value, site, depth = 0) {
  if (!value || typeof value !== 'object') return;
  if (depth > 32) unavailable();
  for (const [key, child] of Object.entries(value)) {
    if ((key === 'site' || key === 'site_id') && child !== site) unavailable();
    if (['__proto__', 'constructor', 'prototype'].includes(key) ||
        /(?:^|_)(?:token|password|credentials?|authorization|api_?key|secret)(?:_|$)/i.test(key)) unavailable();
    inspect(child, site, depth + 1);
  }
}

export function projectRead(op, data, site) {
  if (!data || typeof data !== 'object' || Array.isArray(data)) unavailable();
  let result;
  if (op.id) {
    if (data.id !== op.id || data.site_id !== site) unavailable();
    result = select(data, eventFields);
  } else if (op.resource === 'isp/providers') {
    if (!Array.isArray(data.providers) || data.providers.length > 100) unavailable();
    result = select(data, fields['isp/providers'].split(' '));
    // This read-only view shows the provider's name and guidance. Reports and
    // outbound support links belong to the case reviewer.
    result.providers = data.providers.map(p => select(p, ['id', 'name', 'channel', 'guidance']));
  } else {
    // The VM service omits the site for an unavailable or unconfigured
    // answer. Never pass the accompanying telemetry through that envelope.
    if (op.resource === 'security/vms' && data.site === undefined && ['unavailable', 'not_configured'].includes(data.status)) {
      return {site, status: data.status, unavailable: true, sources: [], reason: 'VM security observations unavailable.'};
    }
    if (data.site !== site) unavailable();
    result = select(data, ['site', ...fields[op.resource].split(' ')]);
    if (op.resource === 'wifi') {
      if (!Array.isArray(result.txRetries)) unavailable();
      // The API includes the controller's management URL in each retry row.
      // Operators need the telemetry, not a credential-bearing admin link.
      result.txRetries = result.txRetries.map(row => select(row, ['device', 'band', 'value', 'status']));
    }
    if (op.resource === 'alerts') {
      for (const key of ['firing', 'resolved']) {
        if (!Array.isArray(result[key]) || !result[key].every(row => row?.labels?.site === site)) unavailable();
      }
    }
    if (op.resource === 'events') {
      if (!Array.isArray(result.events) || result.events.length > Number(op.query.get('limit'))) unavailable();
      result.events = result.events.map(row => select(row, eventFields));
    }
  }
  inspect(result, site);
  return result;
}

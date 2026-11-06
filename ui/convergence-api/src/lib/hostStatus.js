'use strict';

const unavailable = () => ({status: 'unavailable', reason: 'Guest heartbeat status unavailable; visibility is unknown', agents: []});
const empty = () => ({status: 'not_configured', reason: 'No guest heartbeat enrollments configured', agents: []});
const states = new Set(['unknown', 'reporting', 'stale', 'degraded', 'inactive', 'revoked']);
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = value => typeof value === 'string' && value.length > 0 && value.length <= 512;
const counter = value => Number.isSafeInteger(value) && value >= 0;
const timestamp = value => value === null || (typeof value === 'number' && Number.isFinite(value) && value > 0 && value <= 253402300799);
function expiry(value) {
  if (typeof value !== 'string' || value.length > 40) return false;
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-](\d{2}):(\d{2}))$/.exec(value);
  if (!m) return false;
  const [year, month, day, hour, minute, second, offsetHour, offsetMinute] = m.slice(1).map(value => Number(value ?? 0));
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1]
    && hour < 24 && minute < 60 && second < 60 && offsetHour < 24 && offsetMinute < 60
    && Number.isFinite(Date.parse(value));
}

function displayAgent(agent, site) {
  if (!object(agent) || agent.site !== site || !text(agent.id) || !text(agent.vm_id)
      || !states.has(agent.status) || !text(agent.reason) || !expiry(agent.valid_until)
      || agent.evidence_trust !== 'guest_reported' || !timestamp(agent.last_contact)
      || !timestamp(agent.heartbeat_observed) || !counter(agent.pending_events) || !counter(agent.pending_limit)) {
    throw new Error('Invalid host status');
  }
  let heartbeat = null;
  if (agent.heartbeat !== null) {
    const h = agent.heartbeat;
    if (!object(h) || typeof h.collection_ok !== 'boolean'
        || !['queue_events', 'queue_bytes', 'dropped_events'].every(key => counter(h[key]))) throw new Error('Invalid heartbeat');
    heartbeat = {collection_ok: h.collection_ok, queue_events: h.queue_events, queue_bytes: h.queue_bytes, dropped_events: h.dropped_events};
  }
  if ((heartbeat === null) !== (agent.heartbeat_observed === null)
      || (['reporting', 'stale', 'degraded'].includes(agent.status) && (!heartbeat || agent.last_contact === null))
      || (agent.status === 'reporting' && (!heartbeat.collection_ok || heartbeat.queue_events || heartbeat.dropped_events))) {
    throw new Error('Inconsistent heartbeat');
  }
  return {id: agent.id, vm_id: agent.vm_id, status: agent.status, reason: agent.reason,
    last_contact: agent.last_contact, heartbeat_observed: agent.heartbeat_observed, heartbeat,
    pending_events: agent.pending_events, pending_limit: agent.pending_limit,
    valid_until: agent.valid_until, evidence_trust: 'guest_reported'};
}

// This operator credential never becomes part of the browser projection.
async function hostStatus(site) {
  const base = process.env.IDS_STATUS_URL;
  const token = process.env.IDS_STATUS_TOKEN;
  if (!base || !token) return empty();
  try {
    if (!text(site)) throw new Error('Missing authorized site');
    const url = new URL('/v1/host-status', base);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('Invalid status URL');
    url.searchParams.set('site', site);
    const response = await fetch(url, {headers: {Authorization: `Bearer ${token}`},
      signal: AbortSignal.timeout(5000), redirect: 'error'});
    if (!response.ok) throw new Error('Host status unavailable');
    const data = await response.json();
    if (!object(data) || !['configured', 'not_configured'].includes(data.status) || !Array.isArray(data.agents)
        || (data.status === 'not_configured' && data.agents.length)) throw new Error('Invalid host status');
    const agents = data.agents.map(agent => displayAgent(agent, site));
    if (new Set(agents.map(agent => agent.id)).size !== agents.length) throw new Error('Duplicate enrollment');
    return agents.length ? {status: 'configured', agents} : empty();
  } catch {
    return unavailable();
  }
}

module.exports = {hostStatus};

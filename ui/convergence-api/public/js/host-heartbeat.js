'use strict';

// Consumes only the display allowlist returned by authenticated /api/security.
function renderHostHeartbeat(telemetry) {
  var root = document.getElementById('host-heartbeat');
  if (!root) return;
  root.replaceChildren();
  function add(parent, tag, text) {
    var el = document.createElement(tag);
    el.textContent = text;
    parent.appendChild(el);
    return el;
  }
  function count(value) { return Number.isSafeInteger(value) && value >= 0 ? String(value) : 'Unknown'; }
  function timestamp(value) {
    return typeof value === 'number' && value > 0 && Number.isFinite(new Date(value * 1000).getTime()) ? new Date(value * 1000).toISOString() : 'Unknown';
  }
  var agents = telemetry && telemetry.status === 'configured' && Array.isArray(telemetry.agents) ? telemetry.agents : [];
  if (telemetry && (telemetry.status === 'not_configured' || (telemetry.status === 'configured' && Array.isArray(telemetry.agents) && !agents.length))) {
    add(root, 'p', 'No VM heartbeat enrollments configured for this site.');
    return;
  }
  if (!telemetry || telemetry.status !== 'configured' || !Array.isArray(telemetry.agents)) {
    add(root, 'p', 'VM heartbeat status unavailable.');
    if (telemetry && telemetry.reason) add(root, 'p', telemetry.reason);
    return;
  }
  var states = {unknown: 'Unknown', reporting: 'Reporting', stale: 'Stale · visibility lost', degraded: 'Degraded', inactive: 'Inactive / expired', revoked: 'Revoked'};
  var table = document.createElement('table');
  table.className = 'w-full text-sm';
  add(table, 'caption', 'Guest-reported VM heartbeat observations');
  var head = document.createElement('thead');
  var titles = document.createElement('tr');
  ['VM / enrollment', 'Heartbeat state', 'Heartbeat observed (UTC)', 'Last receiver contact (UTC)', 'Collection', 'Guest queue / drops', 'Receiver pending', 'Enrollment expiry (UTC)'].forEach(function(title) {
    add(titles, 'th', title).scope = 'col';
  });
  head.appendChild(titles);
  table.appendChild(head);
  var body = document.createElement('tbody');
  agents.forEach(function(agent) {
    var row = document.createElement('tr');
    add(row, 'th', (agent.vm_id || 'Unknown VM') + ' / ' + (agent.id || 'Unknown enrollment')).scope = 'row';
    add(row, 'td', (states[agent.status] || 'Unknown') + (agent.reason ? ' · ' + agent.reason : ''));
    var observed = timestamp(agent.heartbeat_observed);
    add(row, 'td', observed + ' · ' + (observed === 'Unknown' ? 'Age unknown' : Math.max(0, Math.floor((Date.now() - agent.heartbeat_observed * 1000) / 1000)) + 's ago'));
    add(row, 'td', timestamp(agent.last_contact));
    var heartbeat = agent.heartbeat || {};
    add(row, 'td', heartbeat.collection_ok === true ? 'Reporting OK' : heartbeat.collection_ok === false ? 'Collection failed' : 'Unknown');
    add(row, 'td', 'Events: ' + count(heartbeat.queue_events) + ' · Bytes: ' + count(heartbeat.queue_bytes) + ' · Dropped: ' + count(heartbeat.dropped_events));
    add(row, 'td', count(agent.pending_events) + ' / ' + count(agent.pending_limit));
    add(row, 'td', typeof agent.valid_until === 'string' && Number.isFinite(Date.parse(agent.valid_until)) ? new Date(agent.valid_until).toISOString() : 'Unknown');
    body.appendChild(row);
  });
  table.appendChild(body);
  root.appendChild(table);
}

/** Operator visibility only: the guest's claims are not a host security verdict. */
const esc = value => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
const count = value => Number.isSafeInteger(value) && value >= 0 ? String(value) : 'Unknown';
const time = value => typeof value === 'number' && Number.isFinite(new Date(value * 1000).getTime()) && value > 0 ? new Date(value * 1000).toISOString() : 'Unknown';
const expiry = value => typeof value === 'string' && Number.isFinite(Date.parse(value)) ? new Date(value).toISOString() : 'Unknown';
const states = {unknown: 'Unknown', reporting: 'Reporting', stale: 'Stale · visibility lost', degraded: 'Degraded', inactive: 'Inactive / expired', revoked: 'Revoked'};

export function hostHeartbeatHTML(telemetry, now = Date.now()) {
  const agents = telemetry?.status === 'configured' && Array.isArray(telemetry.agents) ? telemetry.agents : [];
  let body;
  if (telemetry?.status === 'not_configured' || (telemetry?.status === 'configured' && Array.isArray(telemetry.agents) && !agents.length)) {
    body = '<p>No VM heartbeat enrollments configured for this site.</p>';
  } else if (telemetry?.status !== 'configured' || !Array.isArray(telemetry.agents)) {
    body = `<p>VM heartbeat status unavailable.</p>${telemetry?.reason ? `<p>${esc(telemetry.reason)}</p>` : ''}`;
  } else {
    body = `<div class="home-table-wrap"><table class="home-table"><caption>Guest-reported VM heartbeat observations</caption><thead><tr>${['VM / enrollment', 'Heartbeat state', 'Heartbeat observed (UTC)', 'Last receiver contact (UTC)', 'Collection', 'Guest queue / drops', 'Receiver pending', 'Enrollment expiry (UTC)'].map(label => `<th scope="col">${label}</th>`).join('')}</tr></thead><tbody>${agents.map(agent => {
      const observed = time(agent.heartbeat_observed);
      const age = observed === 'Unknown' ? 'Age unknown' : `${Math.max(0, Math.floor((now - agent.heartbeat_observed * 1000) / 1000))}s ago`;
      const heartbeat = agent.heartbeat;
      return `<tr><th scope="row">${esc(agent.vm_id || 'Unknown VM')}<br><small>${esc(agent.id || 'Unknown enrollment')}</small></th><td>${states[agent.status] || 'Unknown'}${agent.reason ? `<br>${esc(agent.reason)}` : ''}</td><td>${observed}<br>${age}</td><td>${time(agent.last_contact)}</td><td>${heartbeat?.collection_ok === true ? 'Reporting OK' : heartbeat?.collection_ok === false ? 'Collection failed' : 'Unknown'}</td><td>Events: ${count(heartbeat?.queue_events)}<br>Bytes: ${count(heartbeat?.queue_bytes)}<br>Dropped: ${count(heartbeat?.dropped_events)}</td><td>${count(agent.pending_events)} / ${count(agent.pending_limit)}</td><td>${expiry(agent.valid_until)}</td></tr>`;
    }).join('')}</tbody></table></div>`;
  }
  return `<section class="home-section" aria-labelledby="host-heartbeat-title"><h3 id="host-heartbeat-title">VM heartbeat health</h3><p class="home-muted">Guest-reported visibility only (guest_reported). Stale or expired heartbeats mean lost visibility, not proof of compromise. Reporting does not establish host safety.</p>${body}</section>`;
}

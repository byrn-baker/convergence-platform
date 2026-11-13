const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));

export function caseCaptureContext(event) {
  const context = event?.capture_context;
  if (!context || typeof context !== 'object' || Array.isArray(context)) {
    return ['SuricataIntrusion', 'SuricataCanary'].includes(event?.alert_name)
      ? {status: 'unavailable', vlan_tags: []} : null;
  }
  const tags = context.vlan_tags;
  if (!Array.isArray(tags) || tags.length > 3
      || tags.some(tag => !Number.isInteger(tag) || tag < 0 || tag > 4094)
      || !['recorded', 'not_recorded', 'unavailable'].includes(context.status)
      || (context.status === 'recorded') !== (tags.length > 0)) {
    return {...context, status: 'unavailable', vlan_tags: []};
  }
  return context;
}

export function observedVlanHTML(context) {
  if (!context) return '<span class="home-muted" title="No IDS capture context">—</span>';
  if (context.status === 'recorded') {
    return [...new Set(context.vlan_tags)].map(tag =>
      `<span class="home-badge">${tag === 0 ? '0 (priority tag)' : tag}</span>`).join(' ');
  }
  return context.status === 'not_recorded'
    ? '<span class="home-muted" title="No VLAN tag in this observation; no VLAN is inferred from IP addresses.">Not recorded</span>'
    : '<span class="home-muted" title="Capture evidence is missing or could not be read.">Unavailable</span>';
}

export function captureContextHTML(context) {
  if (!context) return '';
  const explanation = context.status === 'recorded'
    ? `Raw VLAN tags: <code>${esc(JSON.stringify(context.vlan_tags))}</code>. Tag order and duplicates are preserved from the sensor. These tags do not establish each endpoint's VLAN or a routed path.`
    : context.status === 'not_recorded'
      ? 'No VLAN tag was recorded for this alert. Untagged or inner tunnel traffic can lack tags; no VLAN is inferred from IP addresses.'
      : 'The stored capture evidence is missing or could not be read.';
  return `<details class="home-capture-context"><summary>Capture context</summary>
    <p class="home-muted">Sensor: <strong>${esc(context.sensor_id || 'Not recorded')}</strong></p>
    <p>Observed VLAN: ${observedVlanHTML(context)}</p>
    <p class="home-muted">${explanation}${context.vlan_tags.includes(0) ? ' Tag 0 carries priority information, not VLAN membership.' : ''}</p>
  </details>`;
}

const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));

export function caseIPContext(event) {
  let context = event?.ip_context ?? event?.source_evidence?.annotations?.ip_context;
  if (typeof context === 'string') {
    try { context = JSON.parse(context); } catch { return null; }
  }
  return context && typeof context === 'object' && !Array.isArray(context) ? context : null;
}

export function ipContextHTML(context) {
  if (!context) return '';
  const rows = ['source', 'destination'].filter(key => context[key]?.ip).map(key => {
    const endpoint = context[key];
    const details = endpoint.scope === 'external'
      ? [endpoint.country, endpoint.country_code, endpoint.asn, endpoint.as_name].filter(Boolean).join(' · ') || 'Lookup unavailable'
      : endpoint.scope === 'internal' ? 'Internal network' : 'Non-public address';
    return `<tr><td>${key === 'source' ? 'Source' : 'Destination'}</td><td>${esc(endpoint.ip)}</td><td>${esc(details)}${endpoint.as_domain ? `<br><span class="home-muted">Network organization domain: ${esc(endpoint.as_domain)}</span>` : ''}</td></tr>`;
  }).join('');
  if (!rows) return '';
  const date = new Date(Number(context.databaseBuiltAt) * 1000);
  const built = context.databaseBuiltAt && Number.isFinite(date.getTime()) ? date.toISOString().slice(0, 10) : 'unknown';
  return `<details class="home-ip-context"><summary>Source and destination context</summary>
    <div class="home-table-wrap"><table class="home-table"><thead><tr><th>Endpoint</th><th>Address</th><th>Network context</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="home-muted">IP address data powered by <a href="https://ipinfo.io" target="_blank" rel="noopener noreferrer">IPinfo</a> · database ${esc(built)} · ${esc(context.status || 'unknown')}. Country and ASN identify network context; they do not establish who initiated the traffic or whether it is malicious. The organization domain is not the requested hostname.</p>
  </details>`;
}

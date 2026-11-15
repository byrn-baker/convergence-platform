// A finite transport contract shared by the HTTPS adapter and its mTLS client.
const names = new Set(['health','wifi','devices','alerts','security','security/vms','security/history',
  'events','events/escalated','flows','flows/records','isp/providers']);
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
export function telemetryTarget(site, resource, query = '') {
  const bad = () => { throw Object.assign(new Error('Invalid telemetry request'), {status:400}); };
  if (typeof site !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(site) ||
      typeof resource !== 'string' || (!names.has(resource) && !(resource.startsWith('events/') && uuid.test(resource.slice(7))))) bad();
  const params = new URLSearchParams(query);
  const seen = new Set();
  // Resource-specific values are checked by the authority service. This boundary
  // rejects authority parameters and ambiguous/oversized input before transport.
  const allowed = new Set(['range','limit','type','status','start','end','country','exporter','address']);
  for (const [key, value] of params) {
    if (!allowed.has(key) || seen.has(key) || value.length > 128) bad();
    seen.add(key);
  }
  return {site, resource, query: params.toString()};
}
export function parseTelemetryTarget(raw) {
  if (typeof raw !== 'string' || raw.length > 2048 || /[#\\\s\u0000-\u001f]/.test(raw)) return null;
  const [path, query, ...extra] = raw.split('?');
  if (extra.length) return null;
  const match = /^\/review\/sites\/([A-Za-z0-9_.-]+)\/telemetry\/([A-Za-z0-9/-]+)$/.exec(path);
  return match ? telemetryTarget(match[1], match[2], query) : null;
}

'use strict';
const {isIP} = require('node:net');

const unavailable = () => ({status: 'unavailable', reason: 'Hypervisor observations unavailable; visibility is unknown', sources: []});
const empty = () => ({status: 'not_configured', sources: []});
const object = v => v !== null && typeof v === 'object' && !Array.isArray(v);
const text = v => typeof v === 'string' && v.length > 0 && v.length <= 256;
const integer = v => Number.isSafeInteger(v) && v >= 0;
const positive = v => integer(v) && v > 0;
const uuid = v => typeof v === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v);
const nullable = check => v => v === null || check(v);
const timestamp = v => typeof v === 'number' && Number.isFinite(v) && v > 0 && v <= 253402300799;
const list = (v, limit) => Array.isArray(v) && v.length <= limit;
const bool = v => typeof v === 'boolean';
function project(value, fields) {
  if (!object(value)) throw Error('Invalid observation');
  return Object.fromEntries(Object.entries(fields).map(([key, check]) => {
    if (!check(value[key])) throw Error('Invalid observation');
    return [key, value[key]];
  }));
}
function snapshot(value, node) {
  const result = project(value, {schema_version: v => v === 1, sequence: positive, observed_at: timestamp,
    collector_started_at: timestamp, node: v => v === node, boot_id: uuid});
  result.collection = project(value.collection, {inventory_ok: bool, packet_capture_ok: bool,
    sampled: v => v === true, capture_drops: integer, events_discarded: integer,
    limitations: v => list(v, 64) && v.every(text)});
  if (!list(value.vms, 256) || !list(value.connections, 256)) throw Error('Invalid bounds');
  let nicCount = 0;
  result.vms = value.vms.map(vm => {
    const out = project(vm, {vm_id: positive, vm_uuid: nullable(uuid), power_state: v => ['running', 'stopped', 'unknown'].includes(v)});
    if (!list(vm.nics, 1024) || (nicCount += vm.nics.length) > 1024) throw Error('Invalid NICs');
    out.nics = vm.nics.map(nic => project(nic, {name: v => /^net\d+$/.test(v) && text(v),
      tap: v => text(v) && new RegExp(`^tap${vm.vm_id}i\\d+$`).test(v), ifindex: nullable(positive),
      mac: v => typeof v === 'string' && /^([0-9a-f]{2}:){5}[0-9a-f]{2}$/i.test(v), bridge: v => typeof v === 'string' && (v === '' || /^[A-Za-z0-9_.:-]{1,15}$/.test(v)),
      vlan: nullable(v => integer(v) && v <= 4094), present: bool,
      rx_bytes: nullable(integer), tx_bytes: nullable(integer), rx_packets: nullable(integer), tx_packets: nullable(integer),
      capture_state: v => ['observing', 'unsupported', 'absent', 'error'].includes(v), firewall_drop_packets: nullable(integer)}));
    if (out.nics.some(n => n.bridge === '' && (n.present || !['unsupported', 'absent'].includes(n.capture_state)))) throw Error('Invalid unsupported NIC');
    if (new Set(out.nics.map(n => n.name)).size !== out.nics.length) throw Error('Duplicate NIC');
    return out;
  });
  if (new Set(result.vms.map(vm => vm.vm_id)).size !== result.vms.length) throw Error('Duplicate VM');
  result.connections = value.connections.map(connection => {
    const out = project(connection, {vm_id: positive, vm_uuid: nullable(uuid), tap: text, ifindex: positive,
      observed_at: timestamp, direction: v => v === 'guest_egress', ip_version: v => v === 4 || v === 6,
      protocol: v => ['tcp', 'udp', 'icmp', 'icmpv6'].includes(v), src_ip: v => isIP(v) === connection.ip_version, dst_ip: v => isIP(v) === connection.ip_version,
      src_port: nullable(v => integer(v) && v <= 65535), dst_port: nullable(v => integer(v) && v <= 65535),
      packet_count: positive, evidence: v => v === 'packet_metadata', verdict: v => v === 'unknown'});
    const vm = result.vms.find(vm => vm.vm_id === out.vm_id && vm.vm_uuid === out.vm_uuid);
    if (!vm?.nics.some(nic => nic.tap === out.tap && nic.ifindex === out.ifindex)) throw Error('Unattributed connection');
    return out;
  });
  return result;
}

// Uses the shared status-reader token and the platform's verified TLS/private CA transport.
async function hypervisorStatus(site) {
  if (!text(site)) return unavailable();
  const base = process.env.IDS_STATUS_URL, token = process.env.IDS_STATUS_TOKEN;
  if (!base || !token) return empty();
  try {
    const url = new URL('/v1/hypervisor-status', base);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw Error('Invalid URL');
    url.searchParams.set('site', site);
    const response = await fetch(url, {headers: {Authorization: `Bearer ${token}`}, signal: AbortSignal.timeout(5000), redirect: 'error'});
    if (!response.ok) throw Error('Unavailable');
    const data = await response.json();
    if (!object(data) || !['configured', 'not_configured'].includes(data.status) || !list(data.sources, 256)
        || (data.status === 'not_configured' && data.sources.length)) throw Error('Invalid status');
    const sources = data.sources.map(source => {
      if (!object(source) || source.site !== site || !text(source.id) || !text(source.node)
          || !['reporting', 'stale', 'unknown'].includes(source.status) || !nullable(timestamp)(source.last_contact)) throw Error('Invalid source');
      const value = source.snapshot === null ? null : snapshot(source.snapshot, source.node);
      const latest = Date.now() / 1000 + 30;
      if (source.last_contact > latest || (value && (value.observed_at > latest || value.collector_started_at > value.observed_at
          || value.connections.some(c => c.observed_at > value.observed_at + 30)))) throw Error('Invalid observation time');
      if (value !== null && source.last_contact === null) throw Error('Inconsistent source');
      const stale = value && (Date.now() / 1000 - Math.min(value.observed_at, source.last_contact) > 60);
      return {id: source.id, node: source.node, status: value ? (stale || source.status === 'stale' ? 'stale' : source.status) : (source.last_contact === null ? 'unknown' : 'stale'),
        last_contact: source.last_contact, snapshot: value};
    });
    if (new Set(sources.map(s => s.id)).size !== sources.length || new Set(sources.map(s => s.node)).size !== sources.length) throw Error('Duplicate source');
    return sources.length ? {status: 'configured', sources} : empty();
  } catch { return unavailable(); }
}
module.exports = {hypervisorStatus};

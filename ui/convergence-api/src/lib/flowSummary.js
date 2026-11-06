'use strict';
const {isIP} = require('node:net');
const WINDOWS = {'15m':900,'1h':3600,'6h':21600,'24h':86400};
const quote = value => JSON.stringify(String(value));
const COVERAGE = {
  demo: {label:'Synthetic demonstration', description:'Synthetic demonstration traffic only; these records do not describe activity on your home network.'},
  host: {label:'Local interface traffic', description:'Only traffic visible on the explicitly selected local interface is observed; this is not whole-network coverage.'},
  network: {label:'Configured flow exporters', description:'Only traffic visible to the configured exporters is observed; collection points determine network coverage.'},
};
function flowCoverage(config) {
  const selected = config?.flows?.coverage;
  const mode = typeof selected?.mode === 'string' && Object.hasOwn(COVERAGE,selected.mode) ? selected.mode : 'network';
  const detail = typeof selected?.description === 'string' ? selected.description.trim().slice(0,240) : '';
  return {mode,...COVERAGE[mode],...(detail ? {detail} : {})};
}

async function queryLogs(query) {
  const base = process.env.FLOW_VICTORIALOGS_URL || process.env.VICTORIALOGS_URL || 'http://victorialogs:9428';
  const response = await fetch(`${base.replace(/\/$/,'')}/select/logsql/query`, {
    method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:new URLSearchParams({query}), signal:AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error('Flow archive query failed');
  const reader = response.body.getReader();
  let text = '', size = 0; const decoder = new TextDecoder();
  try {
    for (;;) { const {done,value} = await reader.read(); if(done) break; size += value.length;
      if(size>256000) throw Error('Flow aggregate response exceeds limit'); text += decoder.decode(value,{stream:true}); }
    text += decoder.decode();
  } finally { await reader.cancel(); }
  return text.trim() ? text.trim().split('\n').map(line=>JSON.parse(line)) : [];
}

async function flowSummary(site, config, input = {}, query = queryLogs, now = Date.now()) {
  const range = input.range || '1h';
  const coverage = flowCoverage(config);
  if (!Object.hasOwn(WINDOWS,range)) throw Object.assign(Error('Choose 15m, 1h, 6h or 24h'),{status:400});
  const exporters = [...new Set((config?.flows?.exporters || []).filter(ip=>typeof ip==='string' && isIP(ip)))];
  if (!exporters.length) return {site,coverage,status:'not_configured',reason:'Configure this site’s flow exporters and a VictoriaLogs flow collector.',exporters:[]};
  const exporter = input.exporter || '';
  if (exporter && !exporters.includes(exporter)) throw Object.assign(Error('Exporter not configured for this site'),{status:400});
  const start = new Date(now-WINDOWS[range]*1000).toISOString(), end = new Date(now).toISOString();
  const filter = `_time:[${quote(start)},${quote(end)}] job:=${quote('netflow')} site:=${quote(site)} sampler_address:in(${(exporter?[exporter]:exporters).map(quote).join(',')})`;
  const sum = 'sum(bytes) as bytes, sum(packets) as packets, count() as records';
  const [total,sources,destinations,conversations,sampling] = await Promise.all([
    query(`${filter} | stats ${sum}, max(_time) as last_received`),
    query(`${filter} | stats by (src_addr) ${sum} | sort by (bytes desc) | limit 20`),
    query(`${filter} | stats by (dst_addr) ${sum} | sort by (bytes desc) | limit 20`),
    query(`${filter} | stats by (src_addr, dst_addr) ${sum} | sort by (bytes desc) | limit 20`),
    query(`${filter} | stats by (sampling_rate) count() as records | sort by (records desc) | limit 10`),
  ]);
  const number = v => {const n=Number(v);return Number.isFinite(n)&&n>=0?n:null;};
  const values = row => ({bytes:number(row?.bytes),packets:number(row?.packets),records:number(row?.records)});
  const rows = (list,fields) => list.slice(0,20).filter(r=>fields.every(f=>isIP(String(r[f]||'')))).map(r=>({...Object.fromEntries(fields.map(f=>[f,r[f]])),...values(r)}));
  return {site,coverage,status:Number(total[0]?.records)>0?'active':'empty',range,start,end,read_at:new Date(now).toISOString(),exporter:exporter||null,exporters,
    total:{...values(total[0]),last_received:total[0]?.last_received||null},
    sources:rows(sources,['src_addr']),destinations:rows(destinations,['dst_addr']),conversations:rows(conversations,['src_addr','dst_addr']),
    sampling:sampling.map(r=>({rate:number(r.sampling_rate),records:number(r.records)})),
    basis:'Exported bytes and packets by collector receipt time; sampling is not extrapolated. Source and destination describe record direction, not connection initiator or upload/download.',
    limitations:[coverage.description,'Only configured exporters and retained records are included; traffic outside their observation points is absent.',
      'Ingress/egress observation, NAT, repeated exports and at-least-once log delivery can count traffic more than once. Totals are observed exports, not billing usage.',
      'Flow volume does not establish a threat, application identity, or a firewall allow/block verdict. New collection has no earlier history.'],
  };
}
async function flowEvidence(site, config, input, query = queryLogs, now = Date.now()) {
  const {address,start,end,exporter} = input;
  const exporters = (config?.flows?.exporters || []).filter(ip=>typeof ip==='string'&&isIP(ip));
  if (!exporters.length) throw Object.assign(Error('Flow exporters not configured for this site'),{status:400});
  const from = typeof start==='string' ? Date.parse(start) : NaN, to = typeof end==='string' ? Date.parse(end) : NaN;
  if (!isIP(String(address||'')) || !Number.isFinite(from) || !Number.isFinite(to) || to<=from || to-from>86400000 || to>now+60000)
    throw Object.assign(Error('Supply an IP address and a valid time interval of at most 24 hours'),{status:400});
  if (exporter && !exporters.includes(exporter)) throw Object.assign(Error('Exporter not configured for this site'),{status:400});
  const fields = ['_time','sampler_address','src_addr','dst_addr','src_port','dst_port','proto','bytes','packets','sampling_rate','in_if','out_if','src_vlan','dst_vlan','vlan_id'];
  const records = await query(`_time:[${quote(new Date(from).toISOString())},${quote(new Date(to).toISOString())}] job:="netflow" site:=${quote(site)} sampler_address:in(${(exporter?[exporter]:exporters).map(quote).join(',')}) (src_addr:=${quote(address)} OR dst_addr:=${quote(address)}) | sort by (_time desc) | limit 100 | fields ${fields.join(', ')}`);
  return {site,coverage:flowCoverage(config),address,start:new Date(from).toISOString(),end:new Date(to).toISOString(),limit:100,
    records:records.slice(0,100).filter(r=>(r.src_addr===address||r.dst_addr===address)&&exporters.includes(r.sampler_address)&&(!exporter||r.sampler_address===exporter)).map(r=>Object.fromEntries(fields.filter(f=>r[f]!=null).map(f=>[f,r[f]]))),
    basis:'Latest 100 retained matching flow exports by collector receipt time. Interface indices and VLAN fields are exporter observations, not inferred asset membership. Bytes are not sampling-extrapolated; duplicate observation/NAT may apply.'};
}
module.exports = {flowSummary,flowEvidence,queryLogs};

'use strict';
const {isIP} = require('node:net');
const invalid = (status = 400) => { throw Object.assign(new Error('Invalid telemetry request'),{status}); };
const uuid = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const sitePattern = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
const window = value => ['1h','6h','24h','7d'].includes(value);
const timestamp = value => /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,3})?Z$/.test(value) && Number.isFinite(Date.parse(value));
const resources = new Set(['health','wifi','devices','alerts','security','security/vms','security/history','events','events/escalated','flows','flows/records','isp/providers']);
const filters = {
  'security/history':{range:window},
  events:{range:window,limit:v=>/^(?:[1-9][0-9]?|100)$/.test(v),type:v=>['security','wan'].includes(v),
    status:v=>['logged','investigating','resolved','escalated','needs_more_context'].includes(v),
    start:timestamp,end:timestamp,country:v=>/^(?:[A-Z]{2}|internal|unknown)$/.test(v)},
  flows:{range:v=>['15m','1h','6h','24h'].includes(v),exporter:v=>v==='' || !!isIP(v)},
  'flows/records':{address:v=>!!isIP(v),start:timestamp,end:timestamp,exporter:v=>v==='' || !!isIP(v)},
};
function telemetryOperation(site,resource,params = new URLSearchParams()) {
  if (typeof site !== 'string' || !sitePattern.test(site) || Object.hasOwn(Object.prototype,site) || site==='prototype') invalid();
  const id = typeof resource === 'string' && resource.startsWith('events/') && uuid.test(resource.slice(7)) ? resource.slice(7) : null;
  if (!resources.has(resource) && !id) invalid(404);
  const query = {};
  for (const [key,value] of params) {
    if (Object.hasOwn(query,key) || typeof value !== 'string' || value.length > 128 ||
        !Object.hasOwn(filters,resource) || !Object.hasOwn(filters[resource],key) || !filters[resource][key](value)) invalid();
    query[key] = value;
  }
  if (resource === 'events' && (query.start || query.end || query.country)) {
    if (query.type !== 'security') invalid();
    if (query.start || query.end) interval(query,7*86400000);
  }
  if (resource === 'flows/records') {
    if (!query.address) invalid();
    interval(query,86400000);
  }
  return {site,resource,id,query};
}
function interval(query,limit) {
  if (!query.start || !query.end) invalid();
  const span = Date.parse(query.end)-Date.parse(query.start);
  if (span <= 0 || span > limit || Date.parse(query.end) > Date.now()+60000) invalid();
}
function parseTelemetryTarget(raw) {
  if (typeof raw !== 'string' || raw.length > 2048 || /[#\\\s\u0000-\u001f]/.test(raw)) invalid();
  const [path,query,...extra] = raw.split('?');
  const match = /^\/v1\/sites\/([A-Za-z0-9_.-]+)\/telemetry\/([A-Za-z0-9/-]+)$/.exec(path);
  if (!match || extra.length || path.includes('//') || path.endsWith('/')) invalid(404);
  return telemetryOperation(match[1],match[2],new URLSearchParams(query));
}
const fields = {
  health:'timestamp healthScore uptime wanLatency wanLoss speedtest alertCount sources devices unavailable reason',
  wifi:'clients txRetries status unavailable reason',
  devices:'wanProbes edge firewall accessPoints switches summary sources unavailable reason',
  alerts:'firing resolved status unavailable reason',
  security:'status source observedWindow blockedConnections topSources intrusionDetection hostTelemetry hypervisorTelemetry unavailable reason',
  'security/vms':'status generated_at window_seconds sources synthetic hostTelemetry unavailable reason',
  'security/history':'range start end bucket_seconds total buckets countries basis unavailable reason',
  events:'limit range historical events unavailable reason',
  'events/escalated':'events unavailable reason',
  flows:'coverage status reason range start end read_at exporter exporters total sources destinations conversations sampling basis limitations unavailable',
  'flows/records':'coverage address start end limit records basis unavailable reason',
  'isp/providers':'version checked_at scope providers',
};
const eventFields = ('id site_id timestamp message severity category source status alert_name alert_fingerprint root_cause '+
 'investigation_notes expert_feedback feedback_quality rag_document_id incident_key alert_cleared_at ip_context '+
 'jev_review security_disposition isp_report isp_report_generated_at capture_context traffic_context security_assessment '+
 'escalated_at investigation_started_at investigation_completed_at feedback_provided_at created_at updated_at').split(' ');
function select(record,names) {
  if (!record || typeof record!=='object' || Array.isArray(record)) invalid(503);
  return Object.fromEntries(names.filter(key=>Object.hasOwn(record,key)).map(key=>[key,record[key]]));
}
function inspect(value,site,depth = 0) {
  if (!value || typeof value!=='object') return;
  if (depth > 32) invalid(503);
  for (const [key,child] of Object.entries(value)) {
    if ((key==='site' || key==='site_id') && child!==site) invalid(503);
    if (['__proto__','prototype','constructor'].includes(key) || /(?:^|_)(?:token|password|credentials?|authorization|api_?key|secret)(?:_|$)/i.test(key)) invalid(503);
    inspect(child,site,depth+1);
  }
}
function projectTelemetry(op,data) {
  let result;
  if (op.id) {
    if (data?.id!==op.id || data.site_id!==op.site) invalid(503);
    result=select(data,eventFields);
  } else {
    if (op.resource!=='isp/providers' && data?.site!==op.site) invalid(503);
    result=select(data,['site',...fields[op.resource].split(' ')]);
    if (op.resource==='events' || op.resource==='events/escalated') {
      if (!Array.isArray(result.events) || result.events.length>100) invalid(503);
      result.events=result.events.map(row=>select(row,eventFields));
    }
    if (op.resource==='wifi') result.txRetries=result.txRetries.map(row=>select(row,['device','band','value','status']));
    if (op.resource==='devices') {
      for (const key of ['wanProbes','edge','firewall','accessPoints','switches']) {
        if (!Array.isArray(result[key])) invalid(503);
        result[key]=result[key].map(({mgmtUrl,...row})=>row);
      }
    }
    if (op.resource==='alerts') for (const key of ['firing','resolved']) {
      if (!Array.isArray(result[key]) || result[key].some(row=>row?.labels?.site!==op.site)) invalid(503);
    }
    if (op.resource==='isp/providers') {
      if (!Array.isArray(result.providers) || result.providers.length>100) invalid(503);
      result.providers=result.providers.map(row=>select(row,['id','name','channel','guidance']));
    }
  }
  inspect(result,op.site);
  if (Buffer.byteLength(JSON.stringify(result)) > 1024*1024) invalid(503);
  return result;
}
module.exports={parseTelemetryTarget,telemetryOperation,projectTelemetry};

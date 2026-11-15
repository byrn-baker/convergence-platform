'use strict';
const {withReadContext} = require('../services/read-context');
const {telemetryOperation,projectTelemetry} = require('./telemetry-contract');
const {ReadError} = require('../services/read-error');

function createTelemetryReader({query, readers = {}} = {}) {
  const database = require('../db');
  // Bound the database client wait outside grant transactions; deployment statement_timeout bounds server work.
  const readQuery = query || ((text,values)=>database.query({text,values,query_timeout:12000}));
  const {readEvents,readEscalatedEvents,readEvent} = require('../services/events');
  const {readSecurity,readSecurityHistory,readVmSecurity} = require('../services/security');
  const table = {
    health:require('../services/health').readHealth,
    wifi:require('../services/wifi').readWifi,
    devices:require('../services/devices').readDevices,
    alerts:require('../services/alerts').readAlerts,
    security:readSecurity,
    'security/vms':readVmSecurity,
    'security/history':(site,filters)=>readSecurityHistory(site,filters,{},readQuery),
    events:(site,filters)=>readEvents(site,filters,{},readQuery),
    'events/escalated':site=>readEscalatedEvents(site,{limit:100}, {},readQuery),
    flows:require('../services/flows').readFlows,
    'flows/records':require('../services/flows').readFlowRecords,
    'isp/providers':()=>require('../lib/ispReport').catalog,
    ...readers,
  };
  return async function readTelemetry(site,resource,query = {},{signal} = {}) {
    const op=telemetryOperation(site,resource,new URLSearchParams(query));
    return withReadContext(signal,async()=>{
      let data;
      try {
        data = op.id ? await readEvent(site,{}, {id:op.id},readQuery) : await table[resource](site,op.query);
      } catch(error) {
        // Expected unavailable reads keep their public shape without internal source diagnostics.
        if (error instanceof ReadError && error.status>=500) {
          data={site,unavailable:true,status:'unavailable',reason:'Telemetry source unavailable'};
          if (resource==='events' || resource==='events/escalated') data.events=[];
          else if (resource==='devices') for (const key of ['wanProbes','edge','firewall','accessPoints','switches']) data[key]=[];
          else if (resource==='wifi') Object.assign(data,{clients:{},txRetries:[]});
          else if (resource==='alerts') Object.assign(data,{firing:[],resolved:[]});
        } else throw error;
      }
      if (resource==='security/vms' && !data.site && ['unavailable','not_configured'].includes(data.status)) {
        data={site,status:data.status,unavailable:true,sources:[],hostTelemetry:data.hostTelemetry,reason:'VM security observations unavailable'};
      }
      if (signal?.aborted) throw signal.reason;
      return projectTelemetry(op,data);
    });
  };
}
module.exports={createTelemetryReader};

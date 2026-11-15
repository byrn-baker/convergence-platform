'use strict';
const {getSiteConfig} = require('../lib/config');
const {flowSummary,flowEvidence} = require('../lib/flowSummary');
const {fail} = require('./read-error');
const {currentRead} = require('./read-context');
const cache = new Map();
function validSite(site) {
  if (typeof site !== 'string' || !site || site.length > 128) fail(400,{error:'Valid site required'});
}
async function readFlowRecords(site, filters = {}) {
  validSite(site);
  try { return await flowEvidence(site,getSiteConfig(site),filters); }
  catch(error) { fail(error.status || 502,{unavailable:true,error:error.status ? error.message : 'Flow archive unavailable'}); }
}
async function readFlows(site, filters = {}) {
  validSite(site);
  const range = filters.range || '1h', exporter = filters.exporter || '';
  if (typeof range !== 'string' || typeof exporter !== 'string' || exporter.length > 64) fail(400,{error:'Invalid flow filter'});
  const key = JSON.stringify([site,range,exporter]), now = Date.now();
  try {
    if (currentRead()) return await flowSummary(site,getSiteConfig(site),{range,exporter});
    let hit = cache.get(key);
    if (!hit || hit.until < now) {
      if (cache.size >= 100) cache.delete(cache.keys().next().value);
      hit = {until:now+30000,promise:flowSummary(site,getSiteConfig(site),{range,exporter})};
      cache.set(key,hit);
    }
    return await hit.promise;
  } catch(error) {
    cache.delete(key);
    fail(error.status || 502,{site,status:'unavailable',unavailable:true,error:error.status ? error.message : 'Flow archive unavailable; traffic totals are unknown.'});
  }
}
module.exports = {readFlows,readFlowRecords};

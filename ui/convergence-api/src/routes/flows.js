'use strict';
const router = require('express').Router();
const {getSiteConfig} = require('../lib/config');
const {flowSummary,flowEvidence} = require('../lib/flowSummary');
const cache = new Map();
router.get('/records', async (req,res) => {
  if (typeof req.site!=='string' || !req.site || req.site.length>128) return res.status(400).json({error:'Valid site required'});
  try { res.json(await flowEvidence(req.site,getSiteConfig(req.site),req.query)); }
  catch(err) { res.status(err.status||502).json({unavailable:true,error:err.status?err.message:'Flow archive unavailable'}); }
});
router.get('/', async (req,res) => {
  const {site} = req;
  if (typeof site !== 'string' || !site || site.length>128) return res.status(400).json({error:'Valid site required'});
  const range = req.query.range || '1h', exporter = req.query.exporter || '';
  if (typeof range !== 'string' || typeof exporter !== 'string' || exporter.length>64) return res.status(400).json({error:'Invalid flow filter'});
  const key = JSON.stringify([site,range,exporter]), now = Date.now();
  try {
    let hit = cache.get(key);
    if (!hit || hit.until<now) {
      if(cache.size>=100) cache.delete(cache.keys().next().value);
      hit = {until:now+30000,promise:flowSummary(site,getSiteConfig(site),{range,exporter})};
      cache.set(key,hit);
    }
    res.json(await hit.promise);
  } catch(err) {
    cache.delete(key);
    res.status(err.status||502).json({site,status:'unavailable',unavailable:true,error:err.status?err.message:'Flow archive unavailable; traffic totals are unknown.'});
  }
});
module.exports = router;

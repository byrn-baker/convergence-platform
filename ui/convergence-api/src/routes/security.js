'use strict';
const express = require('express');
const router = express.Router();
const { LOKI_URL, promqlLabelValue } = require('../lib/config');
const { idsStatus } = require('../lib/idsStatus');
const { hostStatus } = require('../lib/hostStatus');
const { hypervisorStatus } = require('../lib/hypervisorStatus');
const {query, DbUnavailableError} = require('../db');
const {securityHistory} = require('../lib/securityHistory');

router.get('/vms', async (req, res) => {
  const [assessment, hosts] = await Promise.all([
    require('../lib/vmSecurity').vmSecurity(req.site), hostStatus(req.site),
  ]);
  res.json({...assessment, hostTelemetry: hosts});
});

router.get('/history', async (req, res) => {
  try {
    res.json(await securityHistory(req.site, req.query, query));
  } catch (err) {
    if (/^Choose /.test(err.message)) return res.status(400).json({error:err.message});
    const down=err instanceof DbUnavailableError || err.code==='DB_UNAVAILABLE';
    if (!down) console.error('Security history error:',err.message);
    res.status(503).json({site:req.site,unavailable:true,reason:'Security history unavailable'});
  }
});

async function metric(query) {
  const r = await fetch(`${LOKI_URL}/loki/api/v1/query?${new URLSearchParams({query})}`, {signal: AbortSignal.timeout(10000)});
  if (!r.ok) throw new Error(`Log store HTTP ${r.status}`);
  const data = await r.json();
  if (data.status !== 'success') throw new Error('Log query failed');
  return data.data.result;
}
const scalar = rows => rows.length ? Number(rows[0].value[1]) : 0;

router.get('/', async (req, res) => {
  const ids = Promise.resolve().then(() => idsStatus(req.site)).catch(() => ({
    status: 'unavailable', reason: 'IDS sensor status service unavailable; detection coverage is unknown', sensors: [], alertsLast24h: null,
  }));
  const hosts = hostStatus(req.site);
  const hypervisors = hypervisorStatus(req.site);
  const site = promqlLabelValue(req.site);
  const source = `{job="device-syslog",site="${site}"} | json | attributes_appname="filterlog"`;
  const blocked = `${source} | attributes_action="block"`;
  try {
    const [coverage, day, hour, top] = await Promise.all([
      metric(`sum(count_over_time(${source} [15m]))`),
      metric(`sum(count_over_time(${blocked} [24h]))`),
      metric(`sum(count_over_time(${blocked} [1h]))`),
      metric(`topk(5, sum by (attributes_src_ip) (count_over_time(${blocked} | attributes_src_ip!="" [1h])))`),
    ]);
    const observed = scalar(coverage) > 0;
    res.json({site: req.site, status: observed ? 'active' : 'unknown',
      source: 'Structured firewall logs', observedWindow: '15m',
      reason: observed ? undefined : 'No recent firewall logs; security visibility is unknown',
      blockedConnections: {last24h: observed ? scalar(day) : null, lastHour: observed ? scalar(hour) : null},
      topSources: top.map(r => ({ip: r.metric.attributes_src_ip, count: Number(r.value[1]), classification: 'unclassified'})).sort((a, b) => b.count - a.count),
      // Firewall drops alone are not evidence that an intrusion was detected.
      intrusionDetection: await ids,
      hostTelemetry: await hosts,
      hypervisorTelemetry: await hypervisors,
    });
  } catch (err) {
    res.json({site: req.site, status: 'unavailable', unavailable: true, reason: err.message,
      blockedConnections: {last24h: null, lastHour: null}, topSources: [], intrusionDetection: await ids, hostTelemetry: await hosts, hypervisorTelemetry: await hypervisors});
  }
});
module.exports = router;

'use strict';

// Sensor status is independent of firewall logs and model availability.
async function idsStatus(site) {
  const base = process.env.IDS_STATUS_URL;
  const token = process.env.IDS_STATUS_TOKEN;
  if (!base || !token) return {status: 'not_configured', reason: 'No IDS sensor service configured', sensors: []};
  try {
    const url = new URL('/v1/status', base);
    url.searchParams.set('site', site);
    const response = await fetch(url, {
      headers: {Authorization: `Bearer ${token}`}, signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!Array.isArray(data.sensors) || typeof data.status !== 'string') throw new Error('Invalid sensor status');
    return data;
  } catch {
    return {status: 'unavailable', reason: 'IDS sensor status service unavailable; detection coverage is unknown', sensors: [], alertsLast24h: null};
  }
}

module.exports = {idsStatus};

'use strict';

const { instantQuery, extractScalar } = require('../lib/queryEngine');
const { getSiteConfig, getThresholdStatus, getMgmtUrls } = require('../lib/config');

/**
 * GET /api/wifi?site=X
 * Wi-Fi client counts and quality metrics.
 */
const {fail} = require('./read-error');

async function readWifi(site, filters = {}) {
  const config = getSiteConfig(site);
  const thresholds = config?.thresholds?.txRetries || { green: 20, yellow: 35 };
  const mgmt = getMgmtUrls(site);

  try {
    const [wireless, wired, guest, total, retries] = await Promise.allSettled([
      instantQuery(`unifi_site_clients_wireless{site=${JSON.stringify(site)}}`),
      instantQuery(`unifi_site_clients_wired{site=${JSON.stringify(site)}}`),
      instantQuery(`unifi_site_clients_guest{site=${JSON.stringify(site)}}`),
      instantQuery(`unifi_site_clients_total{site=${JSON.stringify(site)}}`),
      instantQuery(`unifi_radio_tx_retries_pct{site=${JSON.stringify(site)}}`)
    ]);

    // null when series missing (exporter down) — do not coerce empty to 0
    const scalarOrNull = (settled) => {
      if (settled.status !== 'fulfilled') return null;
      const v = extractScalar(settled.value);
      return v == null ? null : v;
    };
    const clients = {
      wireless: scalarOrNull(wireless),
      wired: scalarOrNull(wired),
      guest: scalarOrNull(guest),
      total: scalarOrNull(total)
    };

    // TX retries per AP/band
    const txRetries = [];
    if (retries.status === 'fulfilled') {
      for (const r of retries.value) {
        const value = parseFloat(r.value[1]);
        txRetries.push({
          device: r.metric.device || 'unknown',
          band: r.metric.band || 'unknown',
          value: Math.round(value * 10) / 10,
          status: getThresholdStatus(value, thresholds),
          mgmtUrl: mgmt.unifi || null
        });
      }
    }

    return ({ site, clients, txRetries, mgmt });
  } catch (err) {
    console.error('WiFi endpoint error:', err.message);
    fail(502, { error: 'Failed to fetch WiFi data', detail: err.message });
  }
}




module.exports = {readWifi};

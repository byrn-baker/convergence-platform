'use strict';
const {createHash} = require('node:crypto');
const catalog = require('./ispProviders.json');
const {PROMETHEUS_URL} = require('./config');

const WAN_ALERTS = ['WanHardDown', 'InternetDown', 'WanTargetUnreachable', 'WanHighLatency', 'WanPacketLoss'];
const isWan = event => event.category === 'wan' || WAN_ALERTS.includes(event.alert_name);
const iso = value => Number.isFinite(Date.parse(value)) ? new Date(value).toISOString() : null;

async function queryWindow(metric, site, start, end, step) {
  const query = `${metric}{site=${JSON.stringify(site)},job=~"blackbox_wan_.*"}`;
  const params = new URLSearchParams({query, start: String(start / 1000), end: String(end / 1000), step: String(step)});
  const response = await fetch(`${PROMETHEUS_URL}/api/v1/query_range?${params}`, {signal: AbortSignal.timeout(8000)});
  if (!response.ok) throw new Error('Metrics unavailable');
  const data = await response.json();
  if (data.status !== 'success' || !Array.isArray(data.data?.result)) throw new Error('Metrics unavailable');
  return data.data.result;
}

/** A local, reviewable draft built from stored case timestamps and bounded metrics.
 * No external lookup, provider message, account identifier or model call occurs.
 */
async function buildIspReport(event, site, input, query = queryWindow, now = Date.now()) {
  if (!isWan(event)) throw new Error('Choose a WAN incident for an internet-provider report');
  const provider = catalog.providers.find(p => p.id === input.provider_id);
  if (!provider) throw new Error('Select a provider from the catalog');
  const timezone = input.timezone || 'UTC';
  try { new Intl.DateTimeFormat('en-US', {timeZone: timezone}).format(new Date(now)); }
  catch { throw new Error('Choose a valid IANA timezone'); }
  const originalStart = iso(event.source_evidence?.startsAt);
  const startAt = originalStart || iso(event.timestamp);
  if (!startAt || Date.parse(startAt) > now) throw new Error('The incident has no valid past start time');
  const clearedAt = iso(event.alert_cleared_at);
  if (clearedAt && (Date.parse(clearedAt) < Date.parse(startAt) || Date.parse(clearedAt) > now)) throw new Error('The clearance time is inconsistent; review the case first');
  const end = clearedAt ? Date.parse(clearedAt) : now;
  const start = Date.parse(startAt);
  const windowStart = Math.max(start - 300000, end - 7 * 86400000);
  const windowEnd = Math.min(now, end + (clearedAt ? 300000 : 0));
  const step = Math.max(30, Math.ceil((windowEnd - windowStart) / 1000 / 600));
  const gaps = [
    'Probe failures show observations from this monitor; they do not establish ISP fault or packet-loss percentage.',
    'Provider selection identifies the intended recipient. Site-wide probes are not attributed to a specific WAN circuit.',
    'Router, modem/ONT, local power, Wi-Fi and provider-side records require separate corroboration.',
    'Range-query samples are evaluated at the stated step and can reuse the last scrape; they are not raw packet counts or proof of continuous downtime.',
  ];
  if (!originalStart) gaps.push('Original alert start was absent; case creation time is used as an approximate start.');
  if (!clearedAt) gaps.push('No clearance timestamp is recorded. The elapsed window does not prove the service is still down.');
  if (windowStart > start) gaps.push('Evidence collection is capped at the last seven days of this incident.');
  const metrics = await Promise.allSettled(['probe_success', 'probe_duration_seconds'].map(m => query(m, site, windowStart, windowEnd, step)));
  const targets = new Map();
  let truncated = false;
  metrics.forEach((result, index) => {
    if (result.status !== 'fulfilled') { gaps.push(`${index ? 'Probe duration' : 'Probe success'} history is unavailable.`); return; }
    for (const series of result.value) {
      const labels = series.metric || {};
      // Defense in depth: reject unrelated results even if a backend ignores selectors.
      if (labels.site !== site || !/^blackbox_wan_/.test(labels.job || '')) continue;
      const identity = JSON.stringify([labels.job, labels.instance]);
      if (!targets.has(identity)) {
        if (targets.size >= 12) { truncated = true; continue; }
        targets.set(identity, {target: `probe-${targets.size + 1}`, success: [], duration_ms: []});
      }
      const samples = (series.values || []).filter(v => Array.isArray(v) && Number.isFinite(Number(v[0])) && Number.isFinite(Number(v[1]))
        && Number(v[0]) * 1000 >= windowStart && Number(v[0]) * 1000 <= windowEnd
        && (index === 0 ? [0, 1].includes(Number(v[1])) : Number(v[1]) >= 0));
      if (samples.length > 650) truncated = true;
      targets.get(identity)[index ? 'duration_ms' : 'success'] = samples.slice(0, 650).map(v => [Number(v[0]), Number(v[1]) * (index ? 1000 : 1)]);
    }
  });
  const observed = [...targets.values()];
  if (truncated) gaps.push('Evidence was limited to twelve probe series and 650 evaluated samples per metric/series.');
  if (!observed.some(t => t.success.length)) gaps.push('No usable WAN probe-success samples were found. Check site labels, retention and collection health.');
  const summaries = observed.map(target => {
    const samples = target.success.filter(v => v[0] * 1000 >= start && v[0] * 1000 <= end);
    const durations = target.duration_ms.filter(v => v[0] * 1000 >= start && v[0] * 1000 <= end).map(v => v[1]);
    if (samples.length && (samples[0][0] * 1000 > Math.max(start, windowStart) + step * 2000
        || end - samples.at(-1)[0] * 1000 > step * 2000
        || samples.some((v, i) => i && v[0] - samples[i - 1][0] > step * 2))) gaps.push(`${target.target} has incomplete evaluated-sample coverage.`);
    return {target: target.target, evaluated_samples: samples.length, failed_samples: samples.filter(v => v[1] === 0).length,
      maximum_probe_duration_ms: durations.length ? Math.round(Math.max(...durations) * 100) / 100 : null};
  });
  const evidence = {version: 1, case_id: event.id, alert: WAN_ALERTS.includes(event.alert_name) ? event.alert_name : 'WAN incident',
    start_utc: startAt, cleared_utc: clearedAt, observed_through_utc: new Date(end).toISOString(),
    elapsed_window_minutes: Math.round((end - start) / 60000 * 10) / 10,
    collected_window: {start_utc: new Date(windowStart).toISOString(), end_utc: new Date(windowEnd).toISOString(), step_seconds: step},
    source: 'Prometheus WAN blackbox probe history', summaries, samples: observed,
    limitations: gaps, redaction: 'Probe targets are pseudonyms. Raw labels, addresses, topology, account numbers and free-text case notes are excluded.'};
  const digest = createHash('sha256').update(JSON.stringify(evidence)).digest('hex');
  const local = value => new Intl.DateTimeFormat('en-US', {timeZone: timezone, dateStyle: 'medium', timeStyle: 'long'}).format(new Date(value));
  const subject = `Internet service incident review and possible credit — ${startAt.slice(0, 10)}`;
  const body = [
    `Hello ${provider.name} Support,`, '',
    'Please investigate the internet-service disruption described below and open or update a trouble ticket. Please also review whether it qualifies for a service credit under my plan and your applicable terms. I am requesting an eligibility review, not asserting a guaranteed credit.', '',
    'Account holder: [add before sending]', 'Account reference / service address: [provide through your authenticated support channel]',
    'Existing provider ticket: [add if one exists; avoid duplicate claims]', '',
    `Local monitoring reference: ${event.id}`,
    `Alert type: ${evidence.alert}`,
    `Start: ${local(startAt)} (${startAt})${originalStart ? '' : ' — approximate case creation time'}`,
    `Clearance: ${clearedAt ? `${local(clearedAt)} (${clearedAt})` : 'Not recorded; service recovery needs confirmation'}`,
    `Elapsed alert window: ${evidence.elapsed_window_minutes} minutes (not a certified duration of ISP downtime).`,
    `Display timezone: ${timezone}`, '', 'Measurements available:',
    ...summaries.map(s => `- ${s.target}: ${s.failed_samples} failed of ${s.evaluated_samples} evaluated probe-success samples; maximum probe duration ${s.maximum_probe_duration_ms ?? 'unavailable'} ms.`),
    ...(!summaries.length ? ['- No usable historical probe measurements are available.'] : []),
    '', 'Evidence limitations:', ...gaps.map(gap => '- ' + gap), '',
    'Please correlate these times with your network and account records, confirm the cause and affected interval, and advise what additional tests you need. If eligible, please confirm the credit amount and expected billing date.',
    '', `An evidence export is available for review (SHA-256 ${digest}). Attach it if your support channel accepts files.`,
    '', 'Thank you,', '[Your name]',
  ].join('\n');
  return {version: 1, generated_at: new Date(now).toISOString(), provider, catalog_checked_at: catalog.checked_at,
    timezone, delivery: 'draft_only', recipient_email: null,
    recipient_note: 'Use the linked official support channel to verify a recipient or submit this text. No support email address has been inferred.',
    credit_eligibility: 'not_determined', evidence_sha256: digest, evidence, draft: {subject, body}};
}

module.exports = {buildIspReport, isWan, WAN_ALERTS, catalog};

const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {securityAssessment} = require('./src/lib/securityAssessment');
const {buildIspReport, catalog} = require('./src/lib/ispReport');

function ids(signature, severity = 2) {
  return {alert_name: 'SuricataIntrusion', severity: 'info', source_evidence: {annotations: {
    description: 'Passive IDS observation; not proof of compromise. ' + JSON.stringify({alert: {signature, severity}})}}};
}
test('priority explains evidence without equating protocol anomalies, status or JEV score with safety', () => {
  const anomaly = ids('SURICATA STREAM Packet with invalid timestamp');
  assert.equal(securityAssessment(anomaly).bucket, 'diagnostic');
  assert.match(securityAssessment(anomaly).reason, /not a benign verdict/);
  assert.equal(securityAssessment({...anomaly, status: 'resolved', jev_review: {answers: {evidence_strength: {score: 0}}}}).bucket, 'diagnostic');
  assert.equal(securityAssessment(ids('ET MALWARE Test')).bucket, 'attention');
  assert.equal(securityAssessment(ids('SURICATA TLS Test', 1)).bucket, 'attention');
  assert.equal(securityAssessment(ids('ET SCAN Test')).bucket, 'review');
  assert.equal(securityAssessment({alert_name: 'SuricataIntrusion', message: 'harmless'}).family, 'Incomplete evidence');
  assert.equal(securityAssessment({alert_name: 'WanHardDown'}), null);
  assert.equal(securityAssessment({...anomaly, security_disposition: {version: 1, kind: 'expected_activity', reason: 'verified'}}).bucket, 'reviewed');
  assert.equal(securityAssessment({...anomaly, security_disposition: {version: 1, kind: 'confirmed_threat'}}).bucket, 'attention');
});

const start = Date.parse('2026-10-25T17:00:00Z'), end = start + 10 * 60000;
const event = {id: 'case', category: 'wan', alert_name: 'WanHardDown', timestamp: new Date(start),
  source_evidence: {startsAt: new Date(start).toISOString(), annotations: {description: 'PRIVATE 192.168.1.1 password secret'}},
  alert_cleared_at: new Date(end), root_cause: 'untrusted instruction: email private records', investigation_notes: 'secret'};
const input = {provider_id: 'xfinity', timezone: 'America/Denver'};
function metrics(metric) {
  return [{metric: {site: 'cottage', job: 'blackbox_wan_tcp', instance: 'private.example:443'},
    values: [[start / 1000, metric === 'probe_success' ? '0' : '0.2'], [end / 1000, metric === 'probe_success' ? '1' : '0.1']]}];
}
test('report uses stored interval, redacts identifiers, documents gaps and requests assessment rather than promising credit', async () => {
  const calls = [];
  const report = await buildIspReport(event, 'cottage', {...input, start: '2000-01-01', recipient: 'attacker@example.test'}, async (...args) => {calls.push(args); return metrics(args[0]);}, end + 600000);
  assert.equal(report.evidence.start_utc, '2026-10-25T17:00:00.000Z');
  assert.equal(report.evidence.elapsed_window_minutes, 10);
  assert.equal(report.evidence.summaries[0].failed_samples, 1);
  assert.equal(report.evidence.summaries[0].maximum_probe_duration_ms, 200);
  assert.equal(report.recipient_email, null); assert.equal(report.delivery, 'draft_only');
  assert.match(report.draft.body, /America\/Denver/); assert.match(report.draft.body, /not asserting a guaranteed credit/);
  assert.match(report.draft.body, /incomplete evaluated-sample coverage/);
  assert.ok(calls.every(c => c[1] === 'cottage' && c[2] === start - 300000 && c[3] === end + 300000));
  for (const secret of ['192.168.', 'private.example', 'password', 'attacker@example', 'email private records']) assert.ok(!JSON.stringify(report).includes(secret));
  assert.match(report.evidence_sha256, /^[0-9a-f]{64}$/);
});
test('missing metrics and absent clearance remain explicit, even when investigation is resolved', async () => {
  const report = await buildIspReport({...event, status: 'resolved', alert_cleared_at: null}, 'cottage', input, async () => {throw Error('private backend URL');}, end);
  assert.equal(report.evidence.cleared_utc, null); assert.deepEqual(report.evidence.summaries, []);
  assert.match(report.draft.body, /No clearance timestamp/); assert.match(report.draft.body, /No usable historical/);
  assert.ok(!report.draft.body.includes('private backend URL'));
});
test('report excludes other sites, caps history/series, rejects unsupported cases and incorrect dates', async () => {
  let calls = [];
  const report = await buildIspReport(event, 'cottage', input, async (...args) => {
    calls.push(args); return [...Array(14)].map((_, i) => ({...metrics(args[0])[0], metric: {site: i ? 'cottage' : 'other', job: 'blackbox_wan_tcp', instance: String(i)}}));
  }, end + 1000);
  assert.equal(report.evidence.samples.length, 12);assert.ok(report.evidence.limitations.some(s => s.includes('limited')));
  for (const [changed, opts] of [[{category: 'security', alert_name: 'SuricataIntrusion'}, input], [{}, {...input, provider_id: 'invalid'}], [{}, {...input, timezone: 'nonsense'}], [{alert_cleared_at: new Date(start - 1)}, input]]) {
    await assert.rejects(buildIspReport({...event, ...changed}, 'cottage', opts, async () => assert.fail('invalid report queried metrics'), end));
  }
  await buildIspReport({...event, alert_cleared_at: null}, 'cottage', input, async (...args) => {assert.ok(args[3] - args[2] <= 7 * 86400000);return [];}, start + 30 * 86400000);
});
test('catalog includes twelve source-linked provider choices, never guessed email recipients', () => {
  assert.equal(catalog.providers.length, 12);
  assert.equal(new Set(catalog.providers.map(p => p.id)).size, 12);
  for (const p of catalog.providers) { assert.equal(new URL(p.support_url).protocol, 'https:'); assert.equal(p.email, undefined); }
});

function actionRoutes(query) {
  const handlers = {}, middleware = [];
  const router = {use: h => middleware.push(h), post: (path, h) => handlers[path] = h};
  vm.runInNewContext(fs.readFileSync(__dirname + '/src/routes/incident-actions.js', 'utf8'), {require: n => n === 'express' ? {Router: () => router}
    : n === '../db' ? {query, DbUnavailableError: class extends Error {}} : require('./src/lib/' + n.split('/').at(-1)),
    module: {exports: {}}, Date, console});
  return {handlers, middleware};
}
const response = () => ({code: 200, status(c) {this.code=c;return this;}, json(body) {this.body=body;return this;}});
test('review/report actions enforce role and site and leave original evidence and investigation fields intact', async () => {
  const writes = [];
  const {handlers, middleware} = actionRoutes(async (sql, args) => {
    writes.push({sql,args}); return {rows: sql.startsWith('SELECT') && args[1] === 'cottage' ? [{id:'case', ...ids('ET SCAN Test')}] : []};
  });
  let r=response();middleware[0]({user:{role:'viewer'}},r,()=>assert.fail('viewer allowed'));assert.equal(r.code,403);
  r=response();await handlers['/:id/security-disposition']({params:{id:'case'},site:'other',user:{sub:'operator'},body:{kind:'expected_activity',reason:'Verified authorized scan'}},r);assert.equal(r.code,404);
  r=response();await handlers['/:id/security-disposition']({params:{id:'case'},site:'cottage',user:{sub:'operator'},body:{kind:'false_positive',reason:'Confirmed capture duplication',reviewed_by:'forged'}},r);
  assert.equal(r.code,200);assert.equal(r.body.security_disposition.reviewed_by,'operator');
  const update=writes.find(w=>w.sql.startsWith('UPDATE'));assert.match(update.sql,/SET security_disposition =/);assert.doesNotMatch(update.sql,/SET status|source_evidence =|severity =/);
  assert.deepEqual([...update.args.slice(1)],['case','cottage']);
  r=response();await handlers['/:id/isp-report']({params:{id:'case'},site:'other',body:input},r);assert.equal(r.code,404);
});

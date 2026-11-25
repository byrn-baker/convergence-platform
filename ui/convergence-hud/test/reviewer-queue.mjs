import {test} from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID, webcrypto} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
import {startReviewer} from '../reviewer/public/reviewer.js';
import {ago, parseRoute, routeFragment} from '../reviewer/public/queue.js';

const html = readFileSync(new URL('../reviewer/public/index.html', import.meta.url), 'utf8');
const identity = {id: randomUUID(), role: 'operator', name: 'Riley Reviewer', sites: ['red', 'blue']};
const caseIds = [randomUUID(), randomUUID(), randomUUID()];
const now = Date.now();
const signatures = [
  {signature_id: '2900417', name: '<b>MALWARE</b> beacon', cases: 1418, needs_review: 418, severity: 'watch',
    first_seen: new Date(now - 7200e3).toISOString(), last_seen: new Date(now - 12e3).toISOString()},
  {signature_id: '2900533', name: 'DNS query for new domain', cases: 12, needs_review: 3, severity: 'info',
    first_seen: new Date(now - 7200e3).toISOString(), last_seen: new Date(now - 180e3).toISOString()},
  {signature_id: null, name: null, cases: 2, needs_review: 2, severity: 'info',
    first_seen: new Date(now - 7200e3).toISOString(), last_seen: new Date(now - 4000e3).toISOString()},
];
const caseRow = (id, minute) => ({id, occurred_at: `2026-11-24T05:${minute}:00.000Z`, signature_id: '2900417',
  name: '<b>MALWARE</b> beacon', severity: 'watch', state: 'needs_review', source_ip: '10.20.4.17', source_port: 49822,
  destination_ip: '2001:db8::44', destination_port: 443, protocol: 'TCP'});
const caseData = id => ({id, site_id: 'red', status: 'investigating', alert_name: 'SuricataIntrusion',
  timestamp: '2026-11-24T05:29:00Z', message: 'Observed', severity: 'watch', review_history: [],
  security_assessment: {label: 'Review', reason: 'Reason', next_check: 'Next', signature: '<i>ET MALWARE</i> beacon'},
  traffic_context: {source_ip: '10.20.4.17', source_port: 49822, destination_ip: '203.0.113.44', destination_port: 443,
    protocol: 'TCP', signature_id: 2900417, signature: 'ET MALWARE beacon', action: 'allowed'}});
const response = (status, value) => ({ok: status >= 200 && status < 300, status, json: async () => value});
const wait = (ms = 15) => new Promise(resolve => setTimeout(resolve, ms));

function page(t, {hash = '', onList, onCase, onBulk, user = identity} = {}) {
  const dom = new JSDOM(html, {url: 'https://review.test/' + hash, pretendToBeVisual: true});
  const {window} = dom;
  Object.defineProperty(window, 'crypto', {value: webcrypto, configurable: true});
  const calls = [];
  const state = {session: {csrf: 's', user}, posts: [], bulk: [], failWrite: false};
  window.fetch = async (path, options) => {
    calls.push(path);
    if (path === '/review/session') return response(200, state.session);
    const bulk = /^\/review\/sites\/red\/signatures\/2900417\/dispositions(?:\/preview\?(.*))?$/.exec(path);
    if (bulk) {
      const body = options.body ? JSON.parse(options.body) : null;
      state.bulk.push({path, body, csrf: options.headers['X-Review-CSRF']});
      return onBulk(bulk[1] === undefined ? null : new URLSearchParams(bulk[1]), body);
    }
    const list = /^\/review\/sites\/([a-z]+)\/(signatures|cases)\?(.*)$/.exec(path);
    if (list) {
      const query = new URLSearchParams(list[3]);
      const custom = onList?.(list[1], list[2], query);
      if (custom) return custom;
      if (list[2] === 'signatures') return response(200, {site: list[1], window: query.get('window'), signatures, truncated: false});
      const more = query.has('before');
      return response(200, {site: list[1], signature: query.get('signature'),
        cases: more ? [caseRow(caseIds[2], '27')] : [caseRow(caseIds[0], '29'), caseRow(caseIds[1], '28')],
        next: more ? null : 'bmV4dA'});
    }
    const single = /^\/review\/sites\/red\/cases\/([0-9a-f-]{36})$/.exec(path);
    if (single) return response(200, onCase?.(single[1]) || caseData(single[1]));
    const write = /^\/review\/sites\/red\/cases\/([0-9a-f-]{36})\/disposition$/.exec(path);
    if (write) {
      const body = JSON.parse(options.body);
      state.posts.push({id: write[1], ...body});
      if (state.failWrite) throw Error('unknown outcome');
      return response(200, {operation_id: body.operationId, case_id: write[1], site_id: 'red', reviewed_by: user.id,
        reviewed_by_name: user.name, kind: body.kind, reason: body.reason, reviewed_at: '2026-11-24T06:00:00Z'});
    }
    return response(404, {});
  };
  const close = startReviewer(window.document, window);
  t.after(() => { close(); window.close(); });
  const byId = id => window.document.getElementById(id);
  const key = (value, target = window.document.body) => target.dispatchEvent(
    new window.KeyboardEvent('keydown', {key: value, bubbles: true, cancelable: true}));
  const rows = () => Array.from(byId('list-rows').rows);
  const lists = () => calls.filter(path => /\/(signatures|cases)\?/.test(path));
  return {window, byId, key, rows, calls, lists, state};
}

test('routes parse to safe defaults and print without them', () => {
  const plain = {view: 'queue', signature: null, site: null, window: '24h', state: 'needs_review', severity: null};
  assert.deepEqual(parseRoute(''), plain);
  assert.deepEqual(parseRoute('#queue'), plain);
  assert.deepEqual(parseRoute('#queue&window=1y&state=open&severity=high'), plain);
  assert.deepEqual(parseRoute('#signature=2900417&window=7d&state=false_positive&severity=watch&site=blue'),
    {view: 'signature', signature: '2900417', site: 'blue', window: '7d', state: 'false_positive', severity: 'watch'});
  assert.equal(parseRoute('#signature=').view, 'signature');
  assert.equal(parseRoute('#signature=a%20b').view, 'queue');
  assert.deepEqual(parseRoute('#case=' + caseIds[0].toUpperCase() + '&site=red'), {view: 'case', id: caseIds[0], site: 'red'});
  assert.deepEqual(parseRoute('#case=nope&site=red'), plain);
  assert.deepEqual(parseRoute('#case=' + caseIds[0]), plain);
  assert.equal(routeFragment(plain, 'red'), 'queue');
  assert.equal(routeFragment({...plain, site: 'red'}, 'red'), 'queue');
  assert.equal(routeFragment({...plain, view: 'signature', signature: 'a:1', window: '30d', state: 'all', severity: 'alert', site: 'blue'}, 'red'),
    'signature=a:1&window=30d&state=all&severity=alert&site=blue');
  assert.equal(routeFragment({view: 'case', id: caseIds[0], site: 'red'}, 'red'), `case=${caseIds[0]}&site=red`);
  for (const text of ['#queue&window=7d', '#signature=2900417&state=all&site=blue']) {
    assert.equal('#' + routeFragment(parseRoute(text), 'red'), text);
  }
  assert.equal(ago(new Date(1000e3 - 12e3).toISOString(), 1000e3), '12 s ago');
  assert.equal(ago(new Date(0).toISOString(), 185e3), '3 min ago');
  assert.equal(ago(new Date(0).toISOString(), 7300e3), '2 h ago');
  assert.equal(ago(new Date(0).toISOString(), 200000e3), '2 d ago');
  assert.equal(ago('never', 1), '');
});

test('the queue loads after sign-in, filters by fragment and never renders markup from a name', async t => {
  const api = page(t);
  const {byId, rows, lists, window} = api;
  await wait();
  assert.equal(byId('list-view').hidden, false);
  assert.deepEqual(lists(), ['/review/sites/red/signatures?window=24h&state=needs_review']);
  assert.equal(rows().length, 3);
  assert.equal(byId('list-rows').querySelector('b'), null);
  assert.match(rows()[0].textContent, /<b>MALWARE<\/b> beacon/);
  assert.match(rows()[0].textContent, /1,418.*418.*12 s ago/);
  assert.equal(rows()[0].querySelector('a').getAttribute('href'), '#signature=2900417');
  assert.equal(rows()[2].querySelector('a').getAttribute('href'), '#signature=');
  assert.match(byId('list-summary').textContent, /423 need review across 3 signatures/);
  assert.equal(rows()[0].classList.contains('selected'), true);

  byId('filter-text').value = 'dns';
  byId('filter-text').dispatchEvent(new window.Event('input', {bubbles: true}));
  assert.equal(rows().length, 1);
  byId('filter-text').value = '';
  byId('filter-text').dispatchEvent(new window.Event('input', {bubbles: true}));

  byId('filter-window').querySelector('[data-window="7d"]').click(); await wait();
  assert.equal(window.location.hash, '#queue&window=7d');
  byId('filter-state').value = 'confirmed_threat';
  byId('filter-state').dispatchEvent(new window.Event('change', {bubbles: true})); await wait();
  byId('filter-severity').value = 'watch';
  byId('filter-severity').dispatchEvent(new window.Event('change', {bubbles: true})); await wait();
  assert.equal(window.location.hash, '#queue&window=7d&state=confirmed_threat&severity=watch');
  assert.equal(lists().at(-1), '/review/sites/red/signatures?window=7d&state=confirmed_threat&severity=watch');
  assert.equal(byId('filter-window').querySelector('[data-window="7d"]').getAttribute('aria-pressed'), 'true');
  assert.ok(lists().every(path => /^[A-Za-z0-9_=&.:\/?-]+$/.test(path)));

  const sites = byId('lookup').elements.site;
  sites.value = 'blue'; sites.dispatchEvent(new window.Event('change', {bubbles: true})); await wait();
  assert.equal(window.location.hash, '#queue&window=7d&state=confirmed_threat&severity=watch&site=blue');
  assert.match(lists().at(-1), /^\/review\/sites\/blue\/signatures\?/);

  window.history.back(); await wait(30);
  assert.equal(window.location.hash, '#queue&window=7d&state=confirmed_threat&severity=watch');
  assert.equal(sites.value, 'red');
  assert.match(lists().at(-1), /^\/review\/sites\/red\/signatures\?window=7d/);
});

test('keys move through the queue, a signature and a case, and back up', async t => {
  const api = page(t);
  const {byId, rows, lists, key, window, calls} = api;
  await wait();
  key('j');
  assert.equal(rows()[1].classList.contains('selected'), true);
  assert.equal(window.document.activeElement, rows()[1].querySelector('a'));
  key('j'); key('j'); key('j');
  assert.equal(rows()[2].classList.contains('selected'), true);
  key('k'); key('k'); key('k');
  assert.equal(rows()[0].classList.contains('selected'), true);
  assert.equal(rows()[0].querySelector('a').getAttribute('aria-current'), 'true');

  // Typing in a field is never taken as a key.
  byId('filter-text').focus(); key('j', byId('filter-text'));
  assert.equal(rows()[0].classList.contains('selected'), true);
  window.document.activeElement.blur();
  key('/');
  assert.equal(window.document.activeElement, byId('filter-text'));
  window.document.activeElement.blur();

  key('?');
  assert.equal(byId('keys').hidden, false);
  key('Escape');
  assert.equal(byId('keys').hidden, true);

  key('Enter'); await wait();
  assert.equal(window.location.hash, '#signature=2900417');
  assert.equal(lists().at(-1), '/review/sites/red/cases?window=24h&state=needs_review&signature=2900417');
  assert.equal(rows().length, 2);
  assert.equal(byId('list-title').textContent, '<b>MALWARE</b> beacon');
  assert.equal(byId('list-view').querySelector('h2 b'), null);
  assert.match(byId('list-summary').textContent, /sid 2900417 · 1,418 cases, 418 need review/);
  assert.match(rows()[0].textContent, /10\.20\.4\.17:49822.*\[2001:db8::44\]:443.*TCP.*Needs review/);
  assert.equal(byId('filter-text-label').hidden, true);
  assert.equal(byId('list-more').hidden, false);
  byId('list-more').click(); await wait();
  assert.equal(lists().at(-1), '/review/sites/red/cases?window=24h&state=needs_review&signature=2900417&before=bmV4dA');
  assert.equal(rows().length, 3);
  assert.equal(byId('list-more').hidden, true);

  key('j'); key('Enter'); await wait();
  assert.equal(window.location.hash, `#case=${caseIds[1]}&site=red`);
  assert.equal(calls.at(-1), '/review/sites/red/cases/' + caseIds[1]);
  assert.equal(byId('detail').hidden, false);
  assert.equal(byId('list-view').hidden, true);
  assert.equal(byId('lookup').elements.caseId.value, caseIds[1]);
  assert.equal(byId('trail-position').textContent, 'Case 2 of 3 in this list');

  key('u'); await wait();
  assert.equal(window.location.hash, '#signature=2900417');
  assert.equal(byId('detail').hidden, true);
  assert.equal(rows().length, 2);
  key('u'); await wait();
  assert.equal(window.location.hash, '#queue');
  assert.equal(rows().length, 3);
  key('u'); await wait();
  assert.equal(window.location.hash, '#queue');

  // The back button retraces the views.
  window.history.back(); await wait(30);
  assert.equal(window.location.hash, '#signature=2900417');
  assert.equal(rows().length, 2);
  window.history.back(); await wait(30);
  assert.equal(window.location.hash, `#case=${caseIds[1]}&site=red`);
  assert.equal(byId('detail').hidden, false);
});

test('a linked signature opens directly; a site the account lacks falls back to the queue', async t => {
  const linked = page(t, {hash: '#signature=2900533&window=30d&site=blue'});
  await wait();
  assert.deepEqual(linked.lists(), ['/review/sites/blue/cases?window=30d&state=needs_review&signature=2900533']);
  assert.equal(linked.byId('lookup').elements.site.value, 'blue');
  assert.equal(linked.byId('crumb-signature').textContent, 'sid 2900533');
  assert.equal(linked.byId('crumb-queue').getAttribute('href'), '#queue&window=30d&site=blue');

  const denied = page(t, {hash: '#signature=2900533&site=green'});
  await wait();
  assert.deepEqual(denied.lists(), ['/review/sites/red/signatures?window=24h&state=needs_review']);
  assert.match(denied.byId('status').textContent, /does not have access to the linked site/);

  const unsigned = page(t, {hash: '#signature='});
  await wait();
  assert.deepEqual(unsigned.lists(), ['/review/sites/red/cases?window=24h&state=needs_review&signature=']);
  assert.equal(unsigned.byId('list-title').textContent, 'Cases with no signature');
});

test('answers for another site or signature, late answers and failures are not shown', async t => {
  const wrongSite = page(t, {onList: (site, kind) => kind === 'signatures' ? response(200, {site: 'blue', signatures}) : null});
  await wait();
  assert.equal(wrongSite.rows().length, 0);
  assert.match(wrongSite.byId('list-status').textContent, /could not be loaded/);

  const wrongSignature = page(t, {hash: '#signature=2900417',
    onList: (site, kind) => kind === 'cases' ? response(200, {site, signature: '2900533', cases: [caseRow(caseIds[0], '29')], next: null}) : null});
  await wait();
  assert.equal(wrongSignature.rows().length, 0);

  let release;
  const late = page(t, {onList: (site, kind, query) => query.get('window') === '24h' && kind === 'signatures'
    ? new Promise(resolve => { release = () => resolve(response(200, {site, signatures, truncated: false})); }) : null});
  await wait();
  late.byId('filter-window').querySelector('[data-window="7d"]').click(); await wait();
  assert.equal(late.rows().length, 3);
  late.window.location.hash = '#signature=2900417'; await wait(30);
  assert.equal(late.rows().length, 2);
  release(); await wait();
  assert.equal(late.rows().length, 2);

  let fail = 500, attempts = 0;
  const failing = page(t, {onList: () => { attempts++; return fail ? response(fail, {}) : null; }});
  await wait();
  assert.match(failing.byId('list-status').textContent, /could not be loaded/);
  fail = 0; failing.byId('list-refresh').click(); await wait();
  assert.equal(failing.rows().length, 3);

  // A refused list means the session's scope changed: everything private goes.
  fail = 403; failing.state.session = {csrf: 'pre', user: null};
  failing.byId('list-refresh').click(); await wait();
  assert.equal(failing.rows().length, 0);
  assert.equal(failing.byId('list-view').hidden, true);
  assert.equal(failing.byId('workspace').hidden, true);
  assert.ok(attempts >= 3);
});

test('lists are removed when the tab is hidden or the session ends, and a case link does not outlive the session', async t => {
  const api = page(t);
  const {byId, rows, window, lists, key} = api;
  await wait();
  key('Enter'); await wait();
  assert.equal(rows().length, 2);
  let hidden = true;
  Object.defineProperty(window.document, 'hidden', {get: () => hidden, configurable: true});
  window.document.dispatchEvent(new window.Event('visibilitychange'));
  assert.equal(rows().length, 0);
  assert.equal(byId('list-view').hidden, true);
  const before = lists().length;
  hidden = false;
  window.document.dispatchEvent(new window.Event('visibilitychange')); await wait();
  assert.equal(rows().length, 2);
  assert.equal(lists().length, before + 1);
  assert.equal(window.location.hash, '#signature=2900417');

  // Regular session checks do not reload a list that is already shown.
  window.dispatchEvent(new window.Event('focus')); await wait();
  assert.equal(lists().length, before + 1);

  key('Enter'); await wait();
  assert.match(window.location.hash, /^#case=/);
  api.state.session = {csrf: 'pre', user: null};
  window.dispatchEvent(new window.Event('focus')); await wait();
  assert.equal(window.location.hash, '');
  assert.equal(byId('detail').hidden, true);
  assert.equal(byId('list-view').hidden, true);
});

test('a viewer reads the queue; an account with no site asks for nothing', async t => {
  const viewer = page(t, {user: {...identity, role: 'viewer'}});
  await wait();
  assert.equal(viewer.rows().length, 3);
  const none = page(t, {user: {...identity, sites: []}});
  await wait();
  assert.deepEqual(none.lists(), []);
  assert.match(none.byId('status').textContent, /no sites available/);
});

test('the case page leads with what fired and words a bulk decision as one shared entry', async t => {
  const api = page(t, {hash: '#signature=2900417', onCase: id => ({...caseData(id),
    security_disposition: {kind: 'expected_activity', reason: 'Backup agent check-in', reviewed_by: identity.id,
      reviewed_by_name: identity.name, reviewed_at: '2026-11-24T06:00:00Z', bulk_operation_id: randomUUID()},
    review_history: [
      {operation_id: randomUUID(), kind: 'expected_activity', reason_preview: 'Backup agent check-in', reason_truncated: false,
        reviewed_by: identity.id, reviewed_by_name: identity.name, reviewed_at: '2026-11-24T06:00:00Z', bulk_case_count: 418},
      {operation_id: randomUUID(), kind: 'needs_review', reason_preview: 'Reopened for a check', reason_truncated: false,
        reviewed_by: identity.id, reviewed_by_name: identity.name, reviewed_at: '2026-11-23T06:00:00Z', bulk_case_count: null},
    ]})});
  const {byId, rows, window} = api;
  await wait();
  rows()[0].querySelector('a').click(); await wait(30);
  assert.equal(byId('detail').hidden, false);
  assert.equal(byId('case-title').textContent, '<i>ET MALWARE</i> beacon');
  assert.equal(byId('detail').querySelector('i'), null);
  assert.equal(byId('flow').hidden, false);
  assert.equal(byId('flow-source').textContent, '10.20.4.17:49822');
  assert.equal(byId('flow-source-kind').textContent, 'internal address');
  assert.equal(byId('flow-protocol').textContent, 'TCP');
  assert.equal(byId('flow-destination').textContent, '203.0.113.44:443');
  assert.equal(byId('flow-destination-kind').textContent, 'external address');
  assert.match(byId('case-when').textContent, /watch severity/);
  assert.equal(byId('fired-signature').querySelector('a').getAttribute('href'), '#signature=2900417');
  assert.match(byId('fired-signature').textContent, /sensor action: allowed/);
  // Order: what fired, evidence, current disposition, history, then the form.
  const order = ['fired', 'facts', 'current', 'history', 'disposition', 'jev-panel', 'investigation-panel'].map(id => byId(id));
  for (let at = 1; at < order.length; at++) {
    assert.ok(order[at - 1].compareDocumentPosition(order[at]) & window.Node.DOCUMENT_POSITION_FOLLOWING, order[at].id);
  }
  assert.match(byId('current').textContent, /recorded by a bulk decision/);
  const entries = Array.from(byId('history').querySelectorAll('li strong')).map(item => item.textContent);
  assert.deepEqual(entries, ['Expected activity · bulk decision covering 418 cases', 'Needs review']);
});

test('a case without parsed traffic shows no hosts, and an opened-by-ID case has no list to step through', async t => {
  const api = page(t, {onCase: id => ({...caseData(id), traffic_context: null, security_assessment: null, alert_name: 'WanHardDown'})});
  const {byId, key, window} = api;
  await wait();
  byId('lookup').elements.caseId.value = caseIds[0];
  byId('lookup').dispatchEvent(new window.Event('submit', {bubbles: true, cancelable: true})); await wait();
  assert.equal(byId('case-title').textContent, 'WanHardDown');
  assert.equal(byId('flow').hidden, true);
  assert.equal(byId('fired-signature').hidden, true);
  assert.equal(byId('trail').hidden, true);
  const before = api.calls.length;
  key('j'); await wait();
  assert.equal(api.calls.length, before);
  assert.equal(byId('detail').hidden, false);
});

test('saving can open the next case; an unconfirmed save stays; the list is read on when it runs out', async t => {
  const api = page(t, {hash: '#signature=2900417'});
  const {byId, rows, key, window, state, calls, lists} = api;
  await wait();
  rows()[0].querySelector('a').click(); await wait(30);
  assert.equal(byId('trail-position').textContent, 'Case 1 of 2+ in this list');
  assert.equal(byId('case-previous').disabled, true);
  assert.equal(byId('save-next').hidden, false);
  const reason = byId('disposition').elements.reason;

  // An unknown outcome keeps the reviewer on the case with its receipt controls.
  state.failWrite = true;
  reason.value = 'Checked against the backup schedule';
  byId('disposition').elements.kind.value = 'expected_activity';
  byId('save-next').click(); await wait(40);
  assert.equal(state.posts.length, 1);
  assert.equal(window.location.hash, `#case=${caseIds[0]}&site=red`);
  assert.equal(byId('check-receipt').hidden, false);
  state.failWrite = false;
  byId('check-receipt').click(); await wait(40);
  assert.equal(byId('retry').hidden, false);
  byId('retry').click(); await wait(40);
  assert.equal(state.posts.length, 2);
  assert.equal(state.posts[1].operationId, state.posts[0].operationId);
  assert.match(byId('operation-note').textContent, /Expected activity recorded by Riley Reviewer/);
  assert.equal(window.location.hash, `#case=${caseIds[0]}&site=red`);

  // Too short a reason is not submitted and does not move on.
  reason.value = 'short';
  byId('save-next').click(); await wait();
  assert.equal(state.posts.length, 2);

  key('j'); await wait(30);
  assert.equal(window.location.hash, `#case=${caseIds[1]}&site=red`);
  assert.equal(byId('trail-position').textContent, 'Case 2 of 2+ in this list');
  assert.equal(byId('operation').hidden, true);

  // A confirmed save moves on, reading the list's next page first.
  byId('disposition').elements.reason.value = 'Confirmed with the device owner';
  byId('disposition').elements.kind.value = 'false_positive';
  byId('save-next').click(); await wait(60);
  assert.equal(state.posts.length, 3);
  assert.equal(state.posts[2].id, caseIds[1]);
  assert.equal(lists().at(-1), '/review/sites/red/cases?window=24h&state=needs_review&signature=2900417&before=bmV4dA');
  assert.equal(window.location.hash, `#case=${caseIds[2]}&site=red`);
  assert.equal(byId('trail-position').textContent, 'Case 3 of 3 in this list');
  assert.equal(byId('case-next').disabled, true);
  assert.equal(byId('save-next').hidden, true);
  assert.equal(byId('disposition').elements.reason.value, '');

  key('j'); await wait();
  assert.match(byId('status').textContent, /last case in this list/);
  assert.equal(window.location.hash, `#case=${caseIds[2]}&site=red`);
  byId('case-previous').click(); await wait(30);
  assert.equal(window.location.hash, `#case=${caseIds[1]}&site=red`);
  key('k'); await wait(30);
  assert.equal(window.location.hash, `#case=${caseIds[0]}&site=red`);
  key('u'); await wait();
  assert.equal(window.location.hash, '#signature=2900417');
  assert.ok(calls.every(path => path.startsWith('/review/')));
});

const previewOf = (query, count = 418) => ({site: 'red', signature_id: '2900417', window: query.get('window'),
  window_start: '2026-11-23T05:30:00.123456Z', window_end: '2026-11-24T05:30:00.123456Z', source_host: query.get('source'),
  cases: query.get('source') ? 391 : count, ceiling: 5000,
  sources: [{source_host: '10.20.4.17', cases: 391}, {source_host: '10.20.4.52', cases: 27}], sources_truncated: false});
const receiptOf = (body, count = body.confirmedCount) => ({operation_id: body.operationId, site_id: 'red', signature_id: '2900417',
  window_start: body.windowStart, window_end: body.windowEnd, source_host: body.source, kind: body.kind, reason: body.reason,
  reviewed_by: identity.id, reviewed_by_name: identity.name, reviewed_at: '2026-11-24T05:31:00Z', case_count: count});

test('bulk review counts first, asks for confirmation and shows one receipt', async t => {
  const api = page(t, {hash: '#signature=2900417', onBulk: (query, body) => response(200, query ? previewOf(query) : receiptOf(body, 389))});
  const {byId, key, window, state, lists} = api;
  const fire = (id, event) => byId(id).dispatchEvent(new window.Event(event, {bubbles: true, cancelable: true}));
  await wait();
  assert.equal(byId('bulk').hidden, false);
  assert.equal(byId('bulk-form').hidden, true);
  assert.equal(state.bulk.length, 0);
  key('b'); await wait();
  assert.equal(state.bulk[0].path, '/review/sites/red/signatures/2900417/dispositions/preview?window=24h');
  const form = byId('bulk-form').elements;
  assert.deepEqual(Array.from(form.scope.options, option => option.textContent), ['All that need review in the last 24 hours',
    'Only source 10.20.4.17 (391)', 'Only source 10.20.4.52 (27)']);
  assert.equal(byId('bulk-review').textContent, 'Review 418 cases as one decision');
  assert.equal(Array.from(form.scope.options).some(option => /future|7 days/.test(option.textContent)), false);

  form.kind.value = 'false_positive'; form.reason.value = 'short';
  fire('bulk-form', 'submit');
  assert.match(byId('bulk-status').textContent, /10 to 4000 characters/);
  assert.equal(byId('bulk-confirm').hidden, true);

  form.reason.value = '  Backup agent check-in, confirmed with the owner  ';
  form.scope.value = '10.20.4.17'; fire('bulk-form', 'change'); form.scope.dispatchEvent(new window.Event('change', {bubbles: true})); await wait();
  assert.equal(state.bulk.at(-1).path, '/review/sites/red/signatures/2900417/dispositions/preview?window=24h&source=10.20.4.17');
  assert.equal(byId('bulk-review').textContent, 'Review 391 cases as one decision');
  assert.equal(form.kind.value, 'false_positive');
  assert.equal(form.scope.value, '10.20.4.17');

  fire('bulk-form', 'submit');
  assert.equal(byId('bulk-confirm').hidden, false);
  assert.match(byId('bulk-confirm-text').textContent, /Record "False positive" for 391 cases of signature 2900417 from source 10\.20\.4\.17/);
  assert.equal(byId('bulk-confirm-yes').textContent, 'Confirm 391 cases');
  assert.equal(form.reason.disabled, true);
  assert.equal(state.bulk.filter(call => call.body).length, 0);
  byId('bulk-cancel').click();
  assert.equal(byId('bulk-confirm').hidden, true);
  assert.equal(form.reason.disabled, false);
  byId('bulk-confirm-yes').click(); await wait();
  assert.equal(state.bulk.filter(call => call.body).length, 0);

  fire('bulk-form', 'submit');
  const listsBefore = lists().length;
  byId('bulk-confirm-yes').click(); await wait(30);
  const write = state.bulk.at(-1);
  assert.equal(write.path, '/review/sites/red/signatures/2900417/dispositions');
  assert.equal(write.csrf, 's');
  assert.deepEqual(Object.keys(write.body), ['operationId', 'kind', 'reason', 'windowStart', 'windowEnd', 'source', 'confirmedCount']);
  assert.deepEqual({...write.body, operationId: null}, {operationId: null, kind: 'false_positive',
    reason: 'Backup agent check-in, confirmed with the owner', windowStart: '2026-11-23T05:30:00.123456Z',
    windowEnd: '2026-11-24T05:30:00.123456Z', source: '10.20.4.17', confirmedCount: 391});
  assert.match(byId('bulk-receipt-note').textContent, /False positive recorded for 389 cases by Riley Reviewer.*Fewer than previewed/);
  assert.equal(byId('bulk-operation').textContent, write.body.operationId);
  assert.equal(byId('bulk-form').hidden, true);
  assert.equal(byId('bulk-retry').hidden, true);
  assert.equal(lists().length, listsBefore + 1);
  byId('bulk-again').click(); await wait();
  assert.equal(byId('bulk-form').hidden, false);
  assert.equal(form.reason.value, '');
});

test('bulk review: changed cases record nothing, an unknown outcome retries the same decision', async t => {
  let mode = 'conflict', total = 418;
  const api = page(t, {hash: '#signature=2900417', onBulk: (query, body) => {
    if (query) return response(200, previewOf(query, total));
    if (mode === 'conflict') return response(409, {});
    if (mode === 'lost') throw Error('connection lost');
    return response(200, receiptOf(body));
  }});
  const {byId, window, state} = api;
  const submit = () => byId('bulk-form').dispatchEvent(new window.Event('submit', {bubbles: true, cancelable: true}));
  const writes = () => state.bulk.filter(call => call.body);
  await wait();
  byId('bulk-start').click(); await wait();
  byId('bulk-form').elements.reason.value = 'Scheduled scanner, see change 1234';
  submit(); total = 430;
  byId('bulk-confirm-yes').click(); await wait(40);
  assert.equal(writes().length, 1);
  assert.match(byId('bulk-status').textContent, /changed since the preview\. Nothing was recorded/);
  assert.equal(byId('bulk-review').textContent, 'Review 430 cases as one decision');
  assert.equal(byId('bulk-form').elements.reason.value, 'Scheduled scanner, see change 1234');
  assert.equal(byId('bulk-receipt').hidden, true);

  mode = 'lost';
  submit(); byId('bulk-confirm-yes').click(); await wait(40);
  assert.equal(writes().length, 2);
  assert.notEqual(writes()[1].body.operationId, writes()[0].body.operationId);
  assert.equal(writes()[1].body.confirmedCount, 430);
  assert.match(byId('bulk-receipt-note').textContent, /outcome of this decision .* is unknown/);
  assert.equal(byId('bulk-retry').hidden, false);
  assert.equal(byId('bulk-form').hidden, true);

  // A hidden tab drops the panel but not the undecided operation.
  let hidden = true;
  Object.defineProperty(window.document, 'hidden', {get: () => hidden, configurable: true});
  window.document.dispatchEvent(new window.Event('visibilitychange'));
  assert.equal(byId('bulk').hidden, true);
  assert.equal(byId('bulk-operation').textContent, '');
  hidden = false;
  window.document.dispatchEvent(new window.Event('visibilitychange')); await wait();
  assert.equal(byId('bulk-retry').hidden, false);
  assert.equal(byId('bulk-start').hidden, true);
  assert.equal(byId('bulk-operation').textContent, writes()[1].body.operationId);

  mode = 'ok';
  byId('bulk-retry').click(); await wait(40);
  assert.equal(writes().length, 3);
  assert.deepEqual(writes()[2].body, writes()[1].body);
  assert.match(byId('bulk-receipt-note').textContent, /Expected activity recorded for 430 cases by Riley Reviewer/);
  assert.equal(byId('bulk-retry').hidden, true);
});

test('bulk review is not offered to a viewer, on the queue, or for cases with no signature; limits block the write', async t => {
  const viewer = page(t, {hash: '#signature=2900417', user: {...identity, role: 'viewer'}, onBulk: () => response(403, {})});
  await wait();
  assert.equal(viewer.byId('bulk').hidden, true);
  viewer.key('b'); await wait();
  assert.equal(viewer.state.bulk.length, 0);

  const unsigned = page(t, {hash: '#signature='});
  await wait();
  assert.equal(unsigned.byId('bulk').hidden, true);
  const queue = page(t);
  await wait();
  assert.equal(queue.byId('bulk').hidden, true);

  let count = 5001;
  const big = page(t, {hash: '#signature=2900417', onBulk: query => response(200, previewOf(query, count))});
  await wait();
  big.byId('bulk-start').click(); await wait();
  assert.equal(big.byId('bulk-review').disabled, true);
  assert.match(big.byId('bulk-status').textContent, /More than 5,000 cases/);
  count = 0;
  big.byId('filter-window').querySelector('[data-window="7d"]').click(); await wait();
  assert.equal(big.byId('bulk-form').hidden, true);
  big.byId('bulk-start').click(); await wait();
  assert.equal(big.state.bulk.at(-1).path, '/review/sites/red/signatures/2900417/dispositions/preview?window=7d');
  assert.equal(big.byId('bulk-review').disabled, true);
  assert.match(big.byId('bulk-status').textContent, /No case in this scope needs review/);

  // An answer for another signature is not shown.
  const wrong = page(t, {hash: '#signature=2900417', onBulk: query => response(200, {...previewOf(query), signature_id: '2900533'})});
  await wait();
  wrong.byId('bulk-start').click(); await wait();
  assert.equal(wrong.byId('bulk-form').hidden, true);
  assert.match(wrong.byId('bulk-status').textContent, /could not be loaded/);
});

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
  security_assessment: {label: 'Review', reason: 'Reason', next_check: 'Next'}});
const response = (status, value) => ({ok: status >= 200 && status < 300, status, json: async () => value});
const wait = (ms = 15) => new Promise(resolve => setTimeout(resolve, ms));

function page(t, {hash = '', onList, user = identity} = {}) {
  const dom = new JSDOM(html, {url: 'https://review.test/' + hash, pretendToBeVisual: true});
  const {window} = dom;
  Object.defineProperty(window, 'crypto', {value: webcrypto, configurable: true});
  const calls = [];
  const state = {session: {csrf: 's', user}};
  window.fetch = async path => {
    calls.push(path);
    if (path === '/review/session') return response(200, state.session);
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
    if (single) return response(200, caseData(single[1]));
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
  key('j');
  assert.equal(byId('detail').hidden, false);

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

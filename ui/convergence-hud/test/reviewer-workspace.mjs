import {test} from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID, webcrypto} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
import {startReviewer} from '../reviewer/public/reviewer.js';

const html = readFileSync(new URL('../reviewer/public/index.html', import.meta.url), 'utf8');
const id = randomUUID(), secondId = randomUUID();
const identity = {id: randomUUID(), role: 'operator', name: 'Riley Reviewer', sites: ['red', 'blue']};
const casePath = '/review/sites/red/cases/' + id;
const caseData = {
  id, site_id: 'red', status: 'investigating', alert_name: 'SuricataIntrusion', timestamp: '2026-11-13T00:00:00Z',
  message: '<script>private investigation</script>', severity: 'watch', root_cause: 'Root cause still open',
  review_history: [], security_assessment: {label: 'Review', reason: 'Review reason', next_check: 'Review next'},
};
function response(status, value) {
  return {ok: status >= 200 && status < 300, status, json: async () => value};
}
function wait(ms = 15) { return new Promise(resolve => setTimeout(resolve, ms)); }
function endpoint(t, onCall, configure = () => {}) {
  const dom = new JSDOM(html, {url: 'https://review.test/', pretendToBeVisual: true});
  const {window} = dom;
  Object.defineProperty(window, 'crypto', {value: webcrypto, configurable: true});
  configure(window);
  const calls = [];
  window.fetch = async (path, options) => {
    calls.push({path, options});
    return onCall(path, options);
  };
  const close = startReviewer(window.document, window);
  t.after(() => { close(); window.close(); });
  const byId = id => window.document.getElementById(id);
  const emit = (element, event) => element.dispatchEvent(new window.Event(event, {bubbles: true, cancelable: true}));
  const submit = form => emit(byId(form), 'submit');
  const open = (value = id) => { byId('lookup').elements.caseId.value = value; submit('lookup'); };
  return {window, byId, emit, submit, open, calls};
}

test('sign-in, two-site lookup, escaped case text, viewer role and removal on entitlement changes', async t => {
  let session = {user: null, csrf: 'pre'};
  const api = endpoint(t, (path, options) => {
    if (path === '/review/session') return response(200, session);
    if (path === '/review/sign-in') {
      assert.equal(options.headers['X-Review-CSRF'], 'pre');
      assert.deepEqual(JSON.parse(options.body), {username: 'reviewer', password: 'synthetic'});
      session = {csrf: 'viewer', user: {...identity, role: 'viewer'}};
      return response(200, session);
    }
    assert.equal(path, casePath); return response(200, caseData);
  });
  const {byId, submit, window, open} = api;
  await wait();
  assert.equal(byId('login').hidden, false);
  const login = byId('login').elements;
  login.username.value = 'reviewer'; login.password.value = 'synthetic'; submit('login');
  assert.equal(login.password.value, '');
  await wait();
  assert.equal(byId('workspace').hidden, false);
  assert.equal(byId('lookup').elements.site.length, 2);
  open(); await wait();
  assert.equal(byId('detail').hidden, false);
  assert.equal(byId('facts').querySelector('script'), null);
  assert.match(byId('facts').textContent, /private investigation/);
  assert.equal(byId('disposition').hidden, true);
  session = {csrf: 'viewer', user: {...identity, role: 'viewer', sites: ['blue']}};
  window.dispatchEvent(new window.Event('focus')); await wait();
  assert.equal(byId('detail').hidden, true);
  assert.equal(byId('facts').textContent, '');
  assert.equal(byId('lookup').elements.caseId.value, '');
  assert.equal(byId('lookup').elements.site.value, 'blue');
  assert.ok(api.calls.every(c => c.path.startsWith('/review/')));
});

test('late reads are discarded after a site change or a loss of session', async t => {
  let resolveRead;
  let session = {csrf: 's', user: identity};
  const api = endpoint(t, (path) => {
    if (path === '/review/session') return response(200, session);
    return new Promise(resolve => { resolveRead = resolve; });
  });
  await wait();
  const {byId, open, emit, window} = api;
  open();
  byId('lookup').elements.site.value = 'blue'; emit(byId('lookup').elements.site, 'change');
  resolveRead(response(200, caseData)); await wait();
  assert.equal(byId('detail').hidden, true); assert.equal(byId('facts').textContent, '');
  byId('lookup').elements.site.value = 'red'; emit(byId('lookup').elements.site, 'change');
  open(secondId);
  session = {csrf: 'signed-out', user: null};
  window.dispatchEvent(new window.Event('focus')); await wait();
  resolveRead(response(200, {...caseData, id: secondId})); await wait();
  assert.equal(byId('login').hidden, false);
  assert.equal(byId('workspace').hidden, true);
  assert.equal(byId('facts').textContent, '');
});

test('ambiguous write reconciles before a manual retry, retaining its operation ID and original author', async t => {
  const posts = [], checks = [];
  const current = {...caseData, review_history: []};
  const api = endpoint(t, (path, options) => {
    if (path === '/review/session') return response(200, {csrf: 'op', user: identity});
    if (path === casePath) return response(200, current);
    if (path === casePath + '/disposition') {
      assert.equal(options.headers['X-Review-CSRF'], 'op');
      posts.push(JSON.parse(options.body));
      if (posts.length === 1) throw Error('response lost');
      const input = posts[1];
      const receipt = {...input, operation_id: input.operationId, case_id: id, site_id: 'red', reviewed_by: identity.id,
        reviewed_by_name: identity.name, reviewed_at: caseData.timestamp};
      current.security_disposition = receipt;
      current.review_history = [{...receipt, reason_preview: input.reason}];
      return response(200, receipt);
    }
    checks.push(path); return response(404, {});
  });
  const {byId, open, submit} = api;
  await wait(); open(); await wait();
  byId('disposition').elements.reason.value = 'Review matched archived evidence'; submit('disposition');
  await wait();
  assert.equal(posts.length, 1);
  assert.equal(byId('check-receipt').hidden, false);
  assert.equal(byId('retry').hidden, true);
  assert.equal(byId('disposition').elements.reason.disabled, true);
  byId('check-receipt').click(); await wait();
  assert.deepEqual(checks, [casePath + '/disposition/' + posts[0].operationId]);
  byId('retry').click(); await wait();
  assert.deepEqual(posts, [posts[0], posts[0]]);
  assert.match(byId('operation-note').textContent, /Riley Reviewer/);
  assert.ok(byId('operation-note').textContent.includes(identity.id));
  assert.ok(byId('history').textContent.includes(identity.id));
  assert.ok(byId('current').textContent.includes(identity.name));
  assert.equal(byId('disposition').elements.reason.value, '');
  assert.equal(byId('retry').hidden, true);
  assert.equal(byId('check-receipt').hidden, true);
});

test('failed sign-out keeps private data cleared and retries the original session', async t => {
  let session = {csrf: 'operator', user: identity}, logouts = 0;
  const api = endpoint(t, (path, options) => {
    if (path === casePath) return response(200, caseData);
    if (path === '/review/session') return response(200, session);
    assert.equal(path, '/review/sign-out');
    assert.equal(options.headers['X-Review-CSRF'], session.csrf);
    if (!logouts++) throw Error('disconnected');
    session = {csrf: 'pre', user: null};
    return response(204, null);
  });
  const {byId, open, window} = api;
  await wait(); open(); await wait();
  byId('logout').click(); await wait();
  assert.equal(byId('workspace').hidden, true);
  assert.equal(byId('facts').textContent, '');
  window.dispatchEvent(new window.Event('focus')); await wait();
  assert.equal(byId('workspace').hidden, true);
  byId('reconnect').click(); await wait();
  assert.equal(logouts, 2);
  assert.equal(byId('login').hidden, false);
});

test('pending receipts survive reload and recover only in the original principal, site and case', async t => {
  const storage = new Map(), posts = [], lookups = [];
  let user = identity, failPoll = false, saved;
  const original = 'Observed an <internal> packet path';
  const configure = window => Object.defineProperty(window, 'sessionStorage', {value: {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key),
  }});
  const handler = (path, options) => {
    if (path === '/review/session') {
      if (failPoll) throw Error('network loss');
      return response(200, {user, csrf: user.id});
    }
    if (path === casePath || path === casePath.replace('red','blue')) {
      return response(200, {...caseData, site_id: path === casePath ? 'red' : 'blue'});
    }
    if (path === casePath + '/disposition') {
      posts.push(JSON.parse(options.body));
      if (posts.length === 1) throw Error('unknown outcome');
      saved = {operation_id: posts[1].operationId, case_id: id, reviewed_by: identity.id, reviewed_by_name: identity.name,
        site_id: 'red', kind: posts[1].kind, reason: original, reviewed_at: caseData.timestamp};
      return response(200, saved);
    }
    lookups.push(path);
    return saved ? response(200, saved) : response(404, {});
  };
  const first = endpoint(t, handler, configure);
  await wait(); first.open(); await wait();
  first.byId('disposition').elements.reason.value = original;
  first.submit('disposition'); await wait();
  assert.equal(storage.size, 1);
  const [[key,value]] = storage;
  assert.match(key, /^review:[\da-f]{64}$/);
  assert.equal(value.split('.')[0], posts[0].operationId);
  for (const secret of [original, identity.id, identity.name, id, 'red', 'needs_review']) {
    assert.equal((key + value).includes(secret), false);
  }
  failPoll = true;
  first.window.dispatchEvent(new first.window.Event('focus')); await wait();
  assert.equal(first.byId('detail').hidden, true);
  failPoll = false;
  first.byId('reconnect').click(); await wait(); first.open(); await wait(); await wait();
  assert.equal(first.byId('operation-id').textContent, posts[0].operationId);
  first.window.dispatchEvent(new first.window.Event('pagehide'));

  user = {...identity, id: randomUUID(), name: 'Someone else'};
  const second = endpoint(t, handler, configure);
  await wait();
  const before = lookups.length;
  second.open(); await wait();
  assert.equal(lookups.length, before);
  assert.equal(second.byId('operation').hidden, true);
  user = identity;
  second.window.dispatchEvent(new second.window.Event('focus')); await wait();
  const sites = second.byId('lookup').elements.site;
  sites.value = 'blue'; second.emit(sites, 'change'); second.open(); await wait();
  assert.equal(second.byId('operation').hidden, true); assert.equal(lookups.length, before);
  sites.value = 'red'; second.emit(sites, 'change'); second.open(); await wait(); await wait();
  assert.equal(lookups.at(-1), casePath + '/disposition/' + posts[0].operationId);
  assert.equal(second.byId('disposition').elements.reason.value, '');
  assert.match(second.byId('operation-note').textContent, /original outcome and evidence/);
  second.byId('disposition').elements.reason.value = 'A different reason must not be submitted';
  second.submit('disposition'); await wait();
  assert.equal(posts.length, 1);
  second.byId('disposition').elements.reason.value = original;
  second.submit('disposition'); await wait(); await wait();
  assert.deepEqual(posts[1], posts[0]);
  assert.equal(storage.size, 0);
  assert.match(second.byId('operation-note').textContent, /recorded by/);
});

test('an ambiguous write with a committed receipt restores on reload without posting again', async t => {
  let receipt;
  const api = endpoint(t, (path, options) => {
    if (path === '/review/session') return response(200, {user: identity, csrf: 's'});
    if (path === casePath) return response(200, caseData);
    if (options.method === 'POST') {
      const input = JSON.parse(options.body);
      receipt = {operation_id: input.operationId, kind: input.kind, reason: input.reason, reviewed_by: identity.id,
        reviewed_by_name: identity.name, reviewed_at: caseData.timestamp, site_id: 'red', case_id: id};
      throw Error('Response lost');
    }
    return response(200, receipt);
  });
  await wait(); api.open(); await wait();
  api.byId('disposition').elements.reason.value = 'Synthetic committed review';
  api.submit('disposition'); await wait();
  api.open(); await wait(); await wait();
  assert.match(api.byId('operation-note').textContent, /recorded by/);
  assert.equal(api.calls.filter(c => c.options.method === 'POST').length, 1);
  assert.equal(api.window.sessionStorage.length, 0);
});

test('retry sign-out never revokes the replacement account, including after cross-tab notification', async t => {
  let session = {csrf: 'original', user: identity}, channel;
  const posted = [];
  const api = endpoint(t, (path, options) => {
    if (path === '/review/session') return response(200, session);
    if (path === casePath) return response(200, caseData);
    posted.push(options.headers['X-Review-CSRF']);
    session = {csrf: 'replacement', user: {...identity, id: 'B', name: 'Other reviewer'}};
    throw Error('Lost original response');
  }, window => {
    window.BroadcastChannel = class {constructor(){ channel = this; } postMessage(){} close(){}};
  });
  await wait(); api.open(); await wait();
  api.byId('logout').click(); await wait();
  channel.onmessage(); await wait();
  assert.equal(api.byId('reconnect').hidden, false);
  assert.equal(api.byId('workspace').hidden, true);
  api.byId('reconnect').click(); await wait();
  assert.deepEqual(posted, ['original']);
  assert.match(api.byId('identity').textContent, /Other reviewer/);
  assert.equal(api.byId('facts').textContent, '');
});

test('effective idle expiry clears private data without polling and visibility restoration waits for revalidation', async t => {
  let session = {user: identity, csrf: 's', expiresAt: new Date(Date.now() + 60000).toISOString()};
  let hold, paused = false;
  const api = endpoint(t, path => {
    if (path === casePath) {
      const result = response(200, caseData);
      result.headers = {get: name => name === 'X-Review-Expires-At' ? new Date(Date.now() + 120).toISOString() : null};
      return result;
    }
    if (paused) return new Promise(resolve => {hold = resolve;});
    return response(200, session);
  });
  await wait(); api.open(); await wait();
  assert.equal(api.byId('detail').hidden, false);
  session = {user: null, csrf: 'expired'};
  await wait(150);
  assert.equal(api.byId('facts').textContent, '');
  assert.equal(api.byId('login').hidden, false);
  session = {user: identity, csrf:'new', expiresAt: new Date(Date.now()+60000).toISOString()};
  api.window.dispatchEvent(new api.window.Event('focus')); await wait();
  api.open(); await wait();
  paused = true;
  api.window.document.dispatchEvent(new api.window.Event('visibilitychange'));
  assert.equal(api.byId('facts').textContent, '');
  assert.equal(api.byId('workspace').hidden, true);
  hold(response(200, session)); await wait();
  assert.equal(api.byId('workspace').hidden, false);
  assert.match(api.byId('facts').textContent, /private investigation/);
});

test('unsent draft returns after switching tabs only for the same unchanged authority', async t => {
  let hidden = false, session = {user: identity, csrf: 's'};
  const api = endpoint(t, path => response(200, path === '/review/session' ? session : caseData),
    window => Object.defineProperty(window.document, 'hidden', {get: () => hidden}));
  const visibility = value => {
    hidden = value;
    api.window.document.dispatchEvent(new api.window.Event('visibilitychange'));
  };
  await wait(); api.open(); await wait();
  api.byId('disposition').elements.kind.value = 'expected_activity';
  api.byId('disposition').elements.reason.value = 'Unsent draft for original reviewer';
  visibility(true);
  assert.equal(api.byId('workspace').hidden, true);
  assert.equal(api.byId('facts').textContent, '');
  assert.equal(api.byId('disposition').elements.reason.value, '');
  visibility(false);
  assert.equal(api.byId('workspace').hidden, true);
  await wait(); await wait();
  assert.equal(api.byId('disposition').elements.reason.value, 'Unsent draft for original reviewer');
  assert.equal(api.byId('disposition').elements.kind.value, 'expected_activity');
  visibility(true);
  session = {user: {...identity, id: 'replacement'}, csrf:'new-session'};
  visibility(false);
  await wait();
  assert.equal(api.byId('workspace').hidden, false);
  assert.equal(api.byId('detail').hidden, true);
  assert.equal(api.byId('disposition').elements.reason.value, '');
});

test('storage denial preserves case reads and blocks new writes without a durable receipt pointer', async t => {
  const api = endpoint(t, path => response(200, path === '/review/session' ? {csrf:'s', user:identity} : caseData),
    window => Object.defineProperty(window, 'sessionStorage', {get() { throw Error('unavailable'); }}));
  await wait(); api.open(); await wait();
  assert.equal(api.byId('detail').hidden, false);
  assert.match(api.byId('facts').textContent, /private investigation/);
  assert.equal(api.byId('disposition').querySelector('button').disabled, true);
  assert.match(api.byId('status').textContent, /cannot access pending receipts/);
  api.submit('disposition'); await wait();
  assert.equal(api.calls.filter(c => c.options.method === 'POST').length, 0);
});

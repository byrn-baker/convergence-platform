import {test} from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID, webcrypto} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
import {startReviewer} from '../reviewer/public/reviewer.js';

const html = readFileSync(new URL('../reviewer/public/index.html', import.meta.url), 'utf8');
const caseId = randomUUID();
const user = {id: randomUUID(), role: 'operator', name: 'Riley Reviewer', sites: ['red']};
const response = (status, value) => ({ok: status >= 200 && status < 300, status, json: async () => value});
const wait = (ms = 15) => new Promise(resolve => setTimeout(resolve, ms));

// jsdom cannot navigate, so the page gets a window whose location records it.
function page(t, {hash = '', held, onCall}) {
  const {window} = new JSDOM(html, {url: 'https://review.test/', pretendToBeVisual: true});
  Object.defineProperty(window, 'crypto', {value: webcrypto, configurable: true});
  if (held) window.sessionStorage.setItem('review-linked-case', held);
  const navigations = [];
  const location = {hash, pathname: '/', search: '', assign: url => navigations.push(url)};
  window.fetch = async (path, options) => onCall(path, options);
  const browser = new Proxy(window, {get(target, key) {
    if (key === 'location') return location;
    const value = target[key];
    return typeof value === 'function' && !value.prototype ? value.bind(target) : value;
  }});
  const close = startReviewer(window.document, browser);
  t.after(() => { close(); window.close(); });
  return {window, navigations, byId: id => window.document.getElementById(id)};
}

test('the page offers the sign-in methods that the server reports', async t => {
  const methods = async signIn => {
    const view = page(t, {onCall: () => response(200, {user: null, csrf: 'pre', ...(signIn && {signIn})})});
    await wait();
    return [view.byId('sso').hidden, view.byId('login').hidden];
  };
  assert.deepEqual(await methods({oidc: true, password: false}), [false, true]);
  assert.deepEqual(await methods({oidc: true, password: true}), [false, false], 'break-glass address');
  assert.deepEqual(await methods({oidc: false, password: true}), [true, false]);
  assert.deepEqual(await methods(undefined), [true, false], 'a server from before single sign-on');
});

test('single sign-on keeps the linked case across the round trip', async t => {
  const anonymous = {user: null, csrf: 'pre', signIn: {oidc: true, password: false}};
  const leaving = page(t, {hash: '#case=' + caseId.toUpperCase() + '&site=red', onCall: () => response(200, anonymous)});
  await wait();
  leaving.byId('sso-start').click();
  assert.deepEqual(leaving.navigations, ['/oidc/login']);
  const held = leaving.window.sessionStorage.getItem('review-linked-case');
  assert.deepEqual(JSON.parse(held), {id: caseId, site: 'red'});

  const calls = [];
  const returning = page(t, {held, onCall: path => {
    calls.push(path);
    if (path === '/review/session') return response(200, {user, csrf: 'live', capabilities: {telemetry: false}});
    return response(200, {id: caseId, site_id: 'red', status: 'investigating', alert_name: 'SuricataIntrusion',
      timestamp: '2026-11-13T00:00:00Z', message: 'Synthetic', severity: 'watch', review_history: []});
  }});
  assert.equal(returning.window.sessionStorage.getItem('review-linked-case'), null, 'consumed once');
  await wait(40);
  assert.equal(returning.byId('lookup').elements.caseId.value, caseId);
  // The reference only fills in the lookup. Opening the case stays a deliberate act.
  assert.deepEqual(calls, ['/review/session']);

  for (const junk of ['{', '{"id":"x","site":"red"}', JSON.stringify({id: caseId, site: ''}), '[]']) {
    const ignored = page(t, {held: junk, onCall: () => response(200, {user, csrf: 'live'})});
    await wait(30);
    assert.equal(ignored.byId('lookup').elements.caseId.value, '', junk);
    assert.equal(ignored.window.sessionStorage.getItem('review-linked-case'), null);
  }
});

test('sign-out follows the server to the identity provider, over HTTPS only', async t => {
  const signedOut = async reply => {
    let session = {user, csrf: 'live', capabilities: {telemetry: false}};
    const view = page(t, {onCall: path => {
      if (path === '/review/sign-out') { session = {user: null, csrf: 'next', signIn: {oidc: true, password: false}}; return reply; }
      return response(200, session);
    }});
    await wait();
    view.byId('logout').click();
    await wait(30);
    return view;
  };
  const end = 'https://sso.test/realms/home/protocol/openid-connect/logout?id_token_hint=x';
  assert.deepEqual((await signedOut(response(200, {location: end}))).navigations, [end]);
  const local = await signedOut(response(204));
  assert.deepEqual(local.navigations, []);
  assert.equal(local.byId('sso').hidden, false);
  for (const location of ['javascript:alert(1)', 'http://sso.test/logout', '//sso.test/logout', 7]) {
    assert.deepEqual((await signedOut(response(200, {location}))).navigations, [], String(location));
  }
});

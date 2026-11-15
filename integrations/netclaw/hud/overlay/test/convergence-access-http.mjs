// Run inside the staged ui/netclaw-visual with node --test test/convergence-access-http.mjs.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtemp, rm } from 'node:fs/promises';
import http from 'node:http';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import express from 'express';
import { register } from '../modules/convergence/server.js';
import { mountAssessmentRoutes } from '../src/hud-server/assessment-routes.js';
import { Bindings } from '../src/hud-server/bindings.js';
import { createLocalAccess } from '../src/security/local-access.js';

const site = 'lab-west';
const event = randomUUID();
const token = 'synthetic-viewer-' + randomUUID();
const reviewer = 'https://review.example.test';

async function listen(t, app) {
  const server = http.createServer(app);
  await new Promise((resolve, reject) => server.once('error', reject).listen(0, '127.0.0.1', resolve));
  t.after(async () => {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  });
  return server.address().port;
}

async function fixture(t) {
  const home = await mkdtemp(path.join(tmpdir(), 'convergence-access-'));
  t.after(() => rm(home, {recursive: true, force: true}));
  const calls = [];
  const service = {reply: (_req, res) => res.json({site, clients: {total: 3}, txRetries: []})};
  const api = express();
  api.use((req, res) => {
    calls.push({method: req.method, path: req.path, query: {...req.query}, headers: {...req.headers}});
    if (req.get('Authorization') !== 'Bearer ' + token || req.query.site !== site) return res.sendStatus(403);
    return service.reply(req, res);
  });
  const apiPort = await listen(t, api);
  const env = {
    CONVERGENCE_HUD_LOCAL_OWNER: '1', CONVERGENCE_SITE: site, CONVERGENCE_API_URL: 'http://127.0.0.1:' + apiPort,
    CONVERGENCE_HUD_READ_TOKEN: token, CONVERGENCE_REVIEWER_ORIGIN: reviewer,
  };
  const installation = {installationId: randomUUID(), kind: 'openclaw', home};
  const hudBindings = new Bindings(path.join(home, 'hud-bindings'), Date.now, installation);
  const app = express();
  const ports = {api: 0, ui: 0};
  const ctx = {env: name => env[name], installation, hudBindings, ports, hudHost: '127.0.0.1',
    openLabWorkspace: null, protectedProfile: false};
  // Match the upstream server's cookie normalization. Private Convergence reads
  // must verify the original namespaced cookie even after this rewrite.
  app.use((req, _res, next) => {
    const cookies = (req.headers.cookie || '').split(';').map(c => c.trim());
    const scoped = cookies.find(c => c.startsWith(hudBindings.cookieName + '='));
    req.headers.cookie = scoped ? 'nc_hud=' + scoped.slice(hudBindings.cookieName.length + 1)
      : cookies.find(c => c.startsWith('nc_hud=')) || '';
    next();
  });
  mountAssessmentRoutes(app, {bindings: hudBindings});
  register(app, ctx);
  ports.api = await listen(t, app);
  ctx.allowedLocalRequest = createLocalAccess(ports, {host: '127.0.0.1'});
  const base = 'http://127.0.0.1:' + ports.api;
  const session = await fetch(base + '/api/hud/session', {method: 'POST', headers: {Origin: base}});
  assert.equal(session.status, 200);
  const cookie = session.headers.get('set-cookie').split(';')[0];
  assert.match(cookie, /^nc_hud_[a-f0-9]{32}=[a-f0-9]{48}$/);
  const owner = {'X-NetClaw-Installation': installation.installationId, Cookie: cookie};
  const get = (resource, headers = owner, method = 'GET') =>
    fetch(base + '/api/home/' + resource, {method, redirect: 'manual', headers});
  return {base, get, calls, service, owner, ctx, cookie, env};
}

test('public discovery exposes only the configured HTTPS reviewer and local bootstrap state', async t => {
  const f = await fixture(t);
  const first = await f.get('status', {});
  assert.equal(first.headers.get('cache-control'), 'no-store');
  assert.deepEqual(await first.json(), {configured: false, reviewerOrigin: reviewer, localOwnerMode: true});
  assert.deepEqual(await (await f.get('status')).json(), {configured: true, reviewerOrigin: reviewer, localOwnerMode: true});
  f.ctx.hudHost = '192.0.2.10'; // Simulate the host/Vite forwarder binding a LAN interface.
  assert.deepEqual(await (await f.get('status')).json(), {configured: false, reviewerOrigin: reviewer, localOwnerMode: false});
  assert.equal((await f.get('wifi')).status, 403);
  for (const link of ['http://review.example.test', 'https://a:b@review.example.test', reviewer + '?key=bad',
    reviewer + '/token', reviewer + '#private', 'https://127.0.0.1', 'https://review.localhost', 'javascript:alert(1)']) {
    f.env.CONVERGENCE_REVIEWER_ORIGIN = link;
    assert.equal((await (await f.get('status', {})).json()).reviewerOrigin, null);
  }
  assert.equal((await f.get('status?redirect=https://attacker.example', {})).status, 400);
  assert.equal(f.calls.length, 0);
});

test('each allowed HTTP read uses a fixed GET with the server-selected endpoint, site and read credential', async t => {
  const f = await fixture(t);
  assert.deepEqual(await (await f.get('sites')).json(), {sites: [{id:site, name:site, healthy:null}]});
  assert.equal((await (await f.get('devices?site=' + site)).json()).unavailable, true);
  assert.equal(f.calls.length, 0);
  const cases = [
    ['health', {site, healthScore:{value:93}}, 'healthScore'],
    ['wifi', {site, clients:{total:3}, txRetries:[{device:'ap', value:1, mgmtUrl:'https://hidden'}], mgmt:{secret_url:'https://hidden'}}, 'clients'],
    ['security', {site, status:'active', hostTelemetry:{agents:[]}}, 'hostTelemetry'],
    ['security/vms', {site, status:'configured', sources:[{site,id:'local'}]}, 'sources'],
    ['security/history?range=7d', {site, range:'7d', buckets:[], countries:[]}, 'buckets'],
    ['flows?range=15m&exporter=192.0.2.1', {site, status:'active', total:{records:2}}, 'total'],
    ['alerts', {site, firing:[{labels:{site},severity:'critical'}], resolved:[]}, 'firing'],
    ['events?type=security&limit=1&range=1h', {site, events:[{id:event}]}, 'events'],
    ['events/escalated', {site, events:[]}, 'events'],
    ['events/' + event, {id:event, site_id:site, message:'case', source_evidence:{do_not_forward:'hidden'}}, 'message'],
    ['isp/providers', {version:1, providers:[{id:'demo',name:'Official provider',support_url:'javascript:hidden'}]}, 'providers'],
  ];
  for (const [target, data, knownField] of cases) {
    f.service.reply = (_req, res) => res.json({...data, unknown:'never_return'});
    const response = await f.get(target, {...f.owner, 'X-Spoofed-Header':'never_forward'});
    assert.equal(response.status, 200, target);
    const value = await response.json();
    assert.ok(Object.hasOwn(value, knownField), target);
    assert.doesNotMatch(JSON.stringify(value), /never_return|hidden|synthetic-viewer/);
    const call = f.calls.at(-1);
    assert.equal(call.path, '/api/' + target.split('?')[0].replace('events/escalated', 'events'));
    assert.equal(call.query.site, site);
    if (target === 'events/escalated') assert.deepEqual(call.query, {site, status:'escalated', limit:'100'});
    assert.equal(call.method, 'GET');
    assert.equal(call.headers.authorization, 'Bearer ' + token);
    for (const name of ['cookie','x-netclaw-installation','x-spoofed-header','origin']) assert.equal(call.headers[name], undefined);
  }
  assert.equal(f.calls.length, cases.length);
});

test('mutations, unknown paths, external URLs, malformed queries and scope changes never reach the service', async t => {
  const f = await fixture(t);
  for (const method of ['POST','PATCH','PUT','DELETE','HEAD','OPTIONS']) {
    assert.equal((await f.get('events/' + event, f.owner, method)).status, 405, method);
  }
  for (const target of ['events/' + event + '/reinvestigate','events/' + event + '/jev-review','auth/login',
    'auth/me','metrics','flows/records','events/not-a-case','models','ssh','flows//summary','%66lows',
    'sites?site=' + site, 'flows?site=other', 'flows?site=' + site + '&site=other', 'flows?site%5B%5D=other',
    'flows?range=all','flows?exporter=https://elsewhere','security?url=http://elsewhere','health?__proto__=pollute',
    'events?limit=101','events?status=closed','events?type=security&start=2025-01-01T00:00:00Z',
    'events?country=US','events?type=security&country=all','events?type=security&start=2025-01-01T00:00:00Z&end=2025-02-01T00:00:00Z',
    'status?next=https://elsewhere','alerts?token=a']) {
    assert.ok([400,403,404].includes((await f.get(target)).status), target);
  }
  assert.equal(f.calls.length, 0);
});

test('native namespaced owner, installation identity, trusted origin and ordinary loopback profile are required', async t => {
  const f = await fixture(t);
  const rawCookie = f.cookie.split('=')[1];
  for (const headers of [
    {}, {Cookie:f.cookie}, {'X-NetClaw-Installation':f.ctx.installation.installationId},
    {...f.owner,Cookie:'nc_hud=' + rawCookie}, {...f.owner,Cookie:f.cookie + '; nc_hud=' + rawCookie},
    {...f.owner,Cookie:f.cookie + '; ' + f.cookie}, {...f.owner,Cookie:'nc_hud_other=' + rawCookie},
    {...f.owner,'X-NetClaw-Installation':randomUUID()}, {...f.owner,'X-NetClaw-Installation':f.ctx.installation.installationId + ', ' + f.ctx.installation.installationId},
    {...f.owner,Origin:'https://attacker.invalid'}, {...f.owner,'Sec-Fetch-Site':'same-site'}, {...f.owner,'X-Forwarded-For':'127.0.0.1'},
    {...f.owner,Forwarded:'for=localhost'}, {...f.owner,Authorization:'Bearer ' + token},
  ]) assert.equal((await f.get('wifi', headers)).status, 403);
  for (const [key, value] of [['protectedProfile',true], ['openLabWorkspace',{}], ['hudHost','0.0.0.0'],
    ['hudHost',undefined], ['installation',{kind:'hermes',installationId:randomUUID()}]]) {
    const prior = f.ctx[key]; f.ctx[key] = value;
    assert.equal((await f.get('wifi')).status,403,key);
    f.ctx[key] = prior;
  }
  delete f.env.CONVERGENCE_HUD_LOCAL_OWNER;
  assert.equal((await f.get('wifi')).status,403);
  f.env.CONVERGENCE_HUD_LOCAL_OWNER = '1';
  f.ctx.hudBindings.revoke(rawCookie);
  assert.equal((await f.get('wifi')).status,403);
  assert.equal(f.calls.length, 0);
});

test('incomplete or unsafe backend settings fail closed with no broad token alias fallback', async t => {
  const f = await fixture(t);
  f.env.CONVERGENCE_API_TOKEN = token;
  for (const [name, value] of [
    ['CONVERGENCE_API_URL','http://192.0.2.1:3080'], ['CONVERGENCE_API_URL','https://a:b@svc.example'],
    ['CONVERGENCE_API_URL','https://svc.example/internal'], ['CONVERGENCE_API_URL','https://svc.example/?token=leak'],
    ['CONVERGENCE_SITE',''], ['CONVERGENCE_SITE','__proto__'], ['CONVERGENCE_SITE','east|west'],
    ['CONVERGENCE_HUD_READ_TOKEN',''], ['CONVERGENCE_HUD_READ_TOKEN','short'], ['CONVERGENCE_HUD_READ_TOKEN','abc\nxyz'],
  ]) {
    const old = f.env[name]; f.env[name] = value;
    assert.equal((await f.get('wifi')).status, 503, name + ' ' + value);
    assert.equal((await (await f.get('status')).json()).configured, false);
    f.env[name] = old;
  }
  assert.equal(f.calls.length, 0);
});

test('rejects foreign or unbound responses, raw credentials, redirects and oversized data', async t => {
  const f = await fixture(t);
  for (const [target, data] of [
    ['wifi',{site:'other',clients:{total:20}}], ['wifi',{clients:{total:2}}],
    ['security/vms',{site,sources:[{site:'other'}]}], ['alerts',{site,firing:[{labels:{}}],resolved:[]}],
    ['alerts',{site,firing:[{labels:{site:'other'}}],resolved:[]}],
    ['events',{site, events:[{site_id:'other', message:'private'}]}],
    ['events/'+event,{id:randomUUID(),site_id:site,message:'private'}],
    ['events/'+event,{id:event,site_id:'other',message:'private'}],
    ['security',{site,hostTelemetry:{token:'private'}}],
    ['events/'+event,{id:event,site_id:site,message:'Echoed credential ' + token}],
  ]) {
    f.service.reply = (_req, res) => res.json(data);
    const result = await f.get(target);
    assert.equal(result.status, 502, target);
    assert.doesNotMatch(await result.text(), /private|other|synthetic-viewer/);
  }
  f.service.reply = (_req, res) => res.json({status:'not_configured',sources:[{site:'other'}],hostTelemetry:{agents:['private']}});
  assert.deepEqual(await (await f.get('security/vms')).json(),
    {site,status:'not_configured',unavailable:true,sources:[],reason:'VM security observations unavailable.'});
  f.service.reply = (_req, res) => res.type('text/plain').send(JSON.stringify({site,clients:{total:3}}));
  assert.equal((await f.get('wifi')).status,502);
  f.service.reply = (_req, res) => res.json({site,clients:{label:'x'.repeat(1024*1024)}});
  assert.equal((await f.get('wifi')).status,502);
  const before = f.calls.length;
  f.service.reply = (_req, res) => res.redirect(302, '/api/evil?site=' + site);
  const result = await f.get('wifi');
  assert.equal(result.status,502);
  assert.equal(result.headers.get('location'),null);
  assert.equal(f.calls.length, before + 1);
});

test('revoking the owner or changing the pinned site during a read suppresses delivery', async t => {
  for (const change of ['revoke','scope']) {
    const f = await fixture(t);
    const {promise: started, resolve: onStart} = Promise.withResolvers();
    const {promise: release, resolve: finish} = Promise.withResolvers();
    f.service.reply = async (_req,res) => {onStart(); await release; res.json({site,clients:{label:'private'}, txRetries:[]});};
    const response = f.get('wifi');
    await started;
    if (change === 'revoke') f.ctx.hudBindings.revoke(f.cookie.split('=')[1]);
    else f.env.CONVERGENCE_SITE = 'other';
    finish();
    const result = await response;
    assert.equal(result.status,403);
    assert.doesNotMatch(await result.text(), /private/);
    assert.equal(f.calls.length,1);
  }
});

test('raw duplicate claims, forged Host and body-bearing GET are rejected before dispatch', async t => {
  const f = await fixture(t);
  const authority = new URL(f.base).host;
  const id = f.owner['X-NetClaw-Installation'];
  function raw(path, headers, body = '') {
    return new Promise((resolve, reject) => {
      const req = http.request(f.base + '/api/home/' + path, {
        method:'GET', headers, setHost:false,
      }, res => {
        res.resume();
        res.once('end', () => resolve(res.statusCode));
      });
      req.once('error', reject);
      req.end(body);
    });
  }
  const credentials = ['Cookie', f.cookie, 'X-NetClaw-Installation', id];
  assert.equal(await raw('wifi',['Host',authority,...credentials, 'Cookie',f.cookie]),403);
  assert.equal(await raw('wifi',['Host',authority,...credentials, 'X-NetClaw-Installation',id]),403);
  assert.equal(await raw('wifi',['Host','evil.invalid',...credentials]),403);
  assert.equal(await raw('wifi',['Host',authority,'Host',authority,...credentials]),403);
  assert.equal(await raw('wifi',['Host',authority,...credentials,'Origin',f.base,'Origin',f.base]),403);
  assert.equal(await raw('wifi',['Host',authority,...credentials,'Content-Length','3'],'abc'),400);
  assert.equal(f.calls.length,0);
  assert.equal(await raw('wifi',['Host',authority,...credentials,'Origin',f.base,'Sec-Fetch-Site','same-origin']),200);
  assert.equal(f.calls.length,1);
});

test('the Vite HTTP proxy can bootstrap a loopback owner but cannot upgrade a LAN HUD to local-owner mode', async t => {
  const f = await fixture(t);
  const {createServer} = await import('vite');
  const root = f.ctx.installation.home;
  const proxy = await createServer({configFile:false, root, cacheDir:path.join(root,'vite-cache'), logLevel:'error',
    server:{host:'127.0.0.1', port:0, proxy:{'/api':{target:f.base}}}});
  t.after(() => proxy.close());
  await proxy.listen();
  f.ctx.ports.ui = proxy.httpServer.address().port;
  f.ctx.allowedLocalRequest = createLocalAccess(f.ctx.ports, {host:'127.0.0.1'});
  const ui = 'http://127.0.0.1:' + f.ctx.ports.ui;
  const session = await fetch(ui + '/api/hud/session', {method:'POST',headers:{Origin:ui}});
  assert.equal(session.status,200);
  const cookie = session.headers.get('set-cookie').split(';')[0];
  const headers = {...f.owner, Origin:ui, Cookie:cookie};
  assert.equal((await fetch(ui + '/api/home/wifi', {headers})).status,200);
  f.ctx.hudHost = '192.0.2.10';
  assert.equal((await fetch(ui + '/api/home/wifi', {headers})).status,403);
  const metadata = await (await fetch(ui + '/api/home/status', {headers})).json();
  assert.deepEqual(metadata, {configured:false, localOwnerMode:false, reviewerOrigin:reviewer});
  assert.equal(f.calls.length,1);
});

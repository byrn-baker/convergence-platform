import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {randomBytes, randomUUID} from 'node:crypto';
import {once} from 'node:events';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import https from 'node:https';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {createReviewerServer} from '../reviewer/server.mjs';
import {cookieName, ReviewerSessions} from '../reviewer/sessions.mjs';

const origin = 'https://reviewer.test';
const caseId = randomUUID();
const caseURL = '/review/sites/red/cases/' + caseId;
const result = {id:caseId, site_id:'red', message:'Private investigation'};
const nonce = () => randomBytes(32).toString('base64url');
const user = {id:'alice', name:'Alice', role:'operator', sites:['red']};
const viewer = {id:'bob', name:'Bob', role:'viewer', sites:['red']};

test('HTTPS reviewer routes only release protected data to the current authorized session', async t => {
  const dir = mkdtempSync(path.join(os.tmpdir(),'reviewer-tests-'));
  t.after(() => rmSync(dir,{recursive:true,force:true}));
  const pem = name => path.join(dir,name + '.pem');
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-out',pem('cert'),
    '-keyout',pem('key'),'-subj','/CN=reviewer.test','-addext','subjectAltName=DNS:reviewer.test'],{stdio:'ignore'});
  const cert = readFileSync(pem('cert'));
  const grants = new Map();
  const deadlines = new Map();
  const calls = [];
  let pauseLogin, pauseRead, pauseRevoke, failRevoke, wrongResult;
  const client = {
    async issue(credentials) {
      calls.push('issue');
      if (pauseLogin) { pauseLogin.started.resolve(); await pauseLogin.resume.promise; }
      if (credentials.password !== 'fixture') throw Object.assign(new Error('private diagnostic'), {status:401});
      const token = nonce();
      const subject = credentials.username === 'alice' ? user : viewer;
      grants.set(token,subject);
      // Simulate elapsed time between DB issuance and the host binding.
      const clock = {expiresAt:new Date(Date.now()+1700000).toISOString(),
        idleExpiresAt:new Date(Date.now()+450000).toISOString()};
      deadlines.set(token,clock);
      return {...subject, token, ...clock};
    },
    async current(token) {
      calls.push('current');
      if (!grants.has(token)) throw Object.assign(new Error('revoked token ' + token),{status:401});
      return {...grants.get(token), ...deadlines.get(token)};
    },
    async revoke(token) {
      calls.push('revoke');
      if (pauseRevoke) { pauseRevoke.started.resolve(); await pauseRevoke.resume.promise; }
      if (failRevoke) throw new Error('private backend offline details');
      grants.delete(token);
    },
    async readCase(token,site) {
      calls.push('read');
      if (!grants.get(token)?.sites.includes(site)) throw Object.assign(new Error('wrong site'),{status:403});
      if (pauseRead) { pauseRead.started.resolve(); await pauseRead.resume.promise; }
      return wrongResult || result;
    },
    async readReceipt(token,site,id,operationId) {
      calls.push('receipt'); return wrongResult || {case_id:id.toLowerCase(),site_id:site,operation_id:operationId.toLowerCase(),reviewed_by:grants.get(token).id};
    },
    async requestInvestigation(token,site,id,data) {
      calls.push('investigate');
      if (grants.get(token)?.role === 'viewer') throw Object.assign(new Error('wrong role'),{status:403});
      return wrongResult || {case_id:id,site_id:site,operation_id:data.operationId.toLowerCase(),requested_by:grants.get(token).id};
    },
    async readInvestigationReceipt(token,site,id,operationId) {
      return wrongResult || {case_id:id,site_id:site,operation_id:operationId.toLowerCase(),requested_by:grants.get(token).id};
    },
    async recordDisposition(token,_site,_id,data) {
      calls.push('write');
      if (grants.get(token)?.role === 'viewer') throw Object.assign(new Error('wrong role'),{status:403});
      deadlines.get(token).idleExpiresAt = new Date(Date.now() + 540000).toISOString();
      return wrongResult || {case_id:caseId,site_id:_site,operation_id:data.operationId.toLowerCase(),reviewed_by:grants.get(token).id};
    },
  };
  const assets = new Map([['/',{type:'text/html',content:Buffer.from('<p>Reviewer</p>')}]]);
  const options = {tls:{cert,key:readFileSync(pem('key'))},origin,client,assets};
  assert.throws(() => createReviewerServer({...options,assets:new Map([['/api/ssh',{}]])}));
  assert.throws(() => createReviewerServer({...options,origin:'http://reviewer.test'}));
  const server = createReviewerServer(options);
  server.listen(0,'127.0.0.1');
  await once(server,'listening');
  t.after(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }));
  const request = (target, {method='GET', state={}, data, headers={}, raw}={}) => new Promise((resolve,reject) => {
    const body = raw ?? (data === undefined ? undefined : JSON.stringify(data));
    const req = https.request({host:'127.0.0.1',port:server.address().port,servername:'reviewer.test',ca:cert,
      path:target,method,agent:false,headers:{host:'reviewer.test',...(state.cookie && {cookie:state.cookie}),
        ...(state.csrf && {'x-review-csrf':state.csrf}),...(method === 'POST' && {origin, 'content-type':'application/json'}),
        ...(body !== undefined && {'content-length':Buffer.byteLength(body)}),...headers}}, res => {
      const chunks = [];
      res.on('data',chunk => chunks.push(chunk));
      res.on('end',() => {
        const text = Buffer.concat(chunks).toString();
        resolve({status:res.statusCode, headers:res.headers, cookie:res.headers['set-cookie']?.[0]?.split(';')[0],
          body:res.headers['content-type'] === 'application/json' && text ? JSON.parse(text) : text});
      });
    });
    req.on('error',reject); req.end(body);
  });
  async function bootstrap() {
    const r = await request('/review/session');
    assert.equal(r.status,200);
    assert.equal(r.body.user,null);
    return {cookie:r.cookie,csrf:r.body.csrf};
  }
  const signIn = (username, state) => request('/review/sign-in',{method:'POST',state,data:{username,password:'fixture'}});
  const signOut = state => request('/review/sign-out',{method:'POST',state,data:{}});
  async function authenticated(username) {
    const pre = await bootstrap();
    const r = await signIn(username,pre);
    assert.equal(r.status,200);
    assert.notEqual(r.cookie,pre.cookie);
    assert.notEqual(r.body.csrf,pre.csrf);
    assert.deepEqual(Object.keys(r.body).sort(),['csrf','expiresAt','user']);
    assert.match(r.headers['set-cookie'][0], /; Secure; HttpOnly; SameSite=Strict$/);
    assert.ok(!r.headers['set-cookie'][0].includes('Domain='));
    return {cookie:r.cookie,csrf:r.body.csrf};
  }

  await t.test('origin, method, cookie, CSRF, native routes and request limits fail closed', async () => {
    const shell = await request('/');
    assert.equal(shell.status,200);
    assert.match(shell.headers['content-security-policy'], /frame-ancestors 'none'/);
    assert.equal(shell.headers['cache-control'],'no-store');
    for (const target of ['/api/ssh','/api/hud/owner/session','/review/sign-in?alt','/../api/hud','/review/%73ession']) {
      assert.equal((await request(target)).status,404);
    }
    assert.equal((await request('/review/session',{method:'HEAD'})).status,404);
    assert.equal((await request('/review/session',{headers:{host:'foreign.test'}})).status,403);
    assert.equal((await request('/review/session',{headers:{origin:'https://reviewer.test.evil'}})).status,403);
    assert.equal((await request('/review/session',{headers:{'sec-fetch-site':'cross-site'}})).status,403);
    assert.equal((await request('/review/session',{headers:{'x-forwarded-for':'127.0.0.1'}})).status,403);
    await assert.rejects(request('/review/session',{headers:{upgrade:'websocket',connection:'Upgrade'}}));
    const pre = await bootstrap();
    const args = {method:'POST',state:pre,data:{username:'alice',password:'fixture'}};
    assert.equal((await request('/review/sign-in',{...args,headers:{origin:'null'}})).status,403);
    assert.equal((await request('/review/sign-in',{...args,headers:{'x-review-csrf':'spoof'}})).status,403);
    assert.equal((await request('/review/sign-in',{...args,headers:{'x-review-csrf':[pre.csrf,pre.csrf]}})).status,400);
    assert.equal((await request('/review/sign-in',{...args,headers:{cookie:pre.cookie+'; '+pre.cookie}})).status,400);
    assert.equal((await request('/review/sign-in',{...args,headers:{'content-type':'text/plain'}})).status,415);
    assert.equal((await request('/review/sign-in',{...args,data:{...args.data,actor:'spoof'}})).status,400);
    assert.equal((await request('/review/sign-in',{...args,raw:'x'.repeat(33000)})).status,413);
    assert.equal(calls.length,0);
  });

  await t.test('two accounts remain separate and only an authorized operator can act', async () => {
    const alice = await authenticated('alice');
    const bob = await authenticated('bob');
    const item = {kind:'false_positive',reason:'Reviewed synthetic case',operationId:randomUUID()};
    assert.deepEqual((await request(caseURL,{state:alice})).body,result);
    assert.equal((await request(caseURL.replace('red','blue'),{state:alice})).status,403);
    assert.equal((await request(caseURL+'/disposition',{method:'POST',state:bob,data:item})).status,403);
    const before = calls.length;
    assert.equal((await request(caseURL+'/disposition',{method:'POST',state:{...alice,csrf:bob.csrf},data:item})).status,403);
    assert.equal(calls.length,before);
    const initial = (await request('/review/session',{state:alice})).body.expiresAt;
    const aliceToken = [...grants.keys()].find(token => grants.get(token).id === 'alice');
    assert.equal(initial, deadlines.get(aliceToken).idleExpiresAt);
    const polled = await request(caseURL,{state:alice});
    assert.equal(polled.headers['x-review-expires-at'],initial);
    const action = await request(caseURL+'/disposition',{method:'POST',state:alice,data:item});
    assert.equal(action.body.reviewed_by,'alice');
    assert.equal(action.headers['x-review-expires-at'],deadlines.get(aliceToken).idleExpiresAt);
    assert.ok(Date.parse(action.headers['x-review-expires-at']) > Date.parse(initial));
    assert.equal((await request(caseURL+'/disposition/'+item.operationId,{state:alice})).status,200);
    assert.equal((await signOut(alice)).status,204);
    assert.equal((await request(caseURL,{state:alice})).status,401);
    assert.equal((await request(caseURL,{state:bob})).status,200);
    await signOut(bob);
  });

  await t.test('investigation uses the same identity/CSRF boundaries and rejects forged receipts', async () => {
    const alice = await authenticated('alice'), bob = await authenticated('bob');
    const input = {operationId:randomUUID(),feedback:'Inspect this synthetic case again'};
    const url = caseURL+'/investigation';
    assert.equal((await request(url,{method:'POST',state:bob,data:input})).status,403);
    assert.equal((await request(url,{method:'POST',state:{...alice,csrf:bob.csrf},data:input})).status,403);
    assert.equal((await request(url,{method:'POST',state:alice,data:{...input,actor:'spoof'}})).status,400);
    assert.equal((await request(url,{method:'POST',state:alice,data:input})).body.requested_by,'alice');
    assert.equal((await request(url+'/'+input.operationId,{state:alice})).status,200);
    try {
      wrongResult={case_id:caseId,site_id:'red',operation_id:input.operationId,requested_by:'bob'};
      assert.equal((await request(url+'/'+input.operationId,{state:alice})).status,503);
      assert.equal((await request(url,{method:'POST',state:alice,data:input})).status,503);
    } finally {wrongResult=null;}
    await signOut(alice); await signOut(bob);
  });

  await t.test('response scope must match the requested case, site, operation and reviewer', async () => {
    const alice = await authenticated('alice');
    const operationId = randomUUID();
    const path = caseURL.replace(caseId,caseId.toUpperCase());
    assert.equal((await request(path,{state:alice})).status,200);
    assert.equal((await request(path+'/disposition/'+operationId.toUpperCase(),{state:alice})).status,200);
    const receipt = {case_id:caseId,site_id:'red',operation_id:operationId,reviewed_by:'alice',secret:'private'};
    try {
      for (const mismatch of [{...result,site_id:'blue'}, {...result,id:randomUUID()}]) {
        wrongResult = mismatch;
        const r = await request(path,{state:alice});
        assert.equal(r.status,503);
        assert.deepEqual(r.body,{error:'Reviewer service unavailable'});
      }
      for (const mismatch of [{...receipt,reviewed_by:'bob'},{...receipt,site_id:'blue'},
        {...receipt,case_id:randomUUID()},{...receipt,operation_id:randomUUID()}]) {
        wrongResult = mismatch;
        const r = await request(path+'/disposition/'+operationId,{state:alice});
        assert.equal(r.status,503);
        assert.deepEqual(r.body,{error:'Reviewer service unavailable'});
      }
    } finally { wrongResult = null; }
    await signOut(alice);
  });

  await t.test('sign-out invalidates a pending login and disposes of the late grant', async () => {
    const pre = await bootstrap();
    pauseLogin = {started:Promise.withResolvers(),resume:Promise.withResolvers()};
    const login = signIn('alice',pre);
    await pauseLogin.started.promise;
    assert.equal((await signIn('alice',pre)).status,409);
    assert.equal((await signOut(pre)).status,204);
    pauseLogin.resume.resolve(); pauseLogin = null;
    assert.equal((await login).status,401);
    assert.equal(grants.size,0);
    assert.equal((await request(caseURL,{state:pre})).status,401);
  });

  await t.test('post-read authorization withholds private results after site downgrade or grant revocation', async () => {
    const alice = await authenticated('alice');
    const [token] = grants.keys();
    pauseRead = {started:Promise.withResolvers(),resume:Promise.withResolvers()};
    const data = request(caseURL,{state:alice});
    await pauseRead.started.promise;
    grants.set(token,{...user,sites:[]});
    pauseRead.resume.resolve(); pauseRead = null;
    const denied = await data;
    assert.deepEqual(denied.body,{error:'Access denied'});
    assert.equal(denied.status,403);

    grants.set(token,user);
    pauseRead = {started:Promise.withResolvers(),resume:Promise.withResolvers()};
    const late = request(caseURL,{state:alice});
    await pauseRead.started.promise;
    grants.delete(token);
    pauseRead.resume.resolve(); pauseRead = null;
    const revoked = await late;
    assert.equal(revoked.status,401);
    assert.ok(!JSON.stringify(revoked.body).includes(token));
    assert.equal((await request(caseURL,{state:alice})).status,401);
  });

  await t.test('local logout remains final if the backend cannot confirm revocation', async () => {
    const alice = await authenticated('alice');
    failRevoke = true;
    const logout = await signOut(alice);
    assert.equal(logout.status,503);
    assert.equal(logout.body.error,'Reviewer service unavailable');
    assert.match(logout.headers['set-cookie'][0], /Max-Age=0/);
    assert.equal((await request(caseURL,{state:alice})).status,401);
    failRevoke = false;
  });

  await t.test('graceful shutdown invalidates pending login and drains upstream revocations',async () => {
    // The previous test deliberately made an upstream grant unreachable.
    grants.clear();
    await authenticated('alice');
    const pre = await bootstrap();
    pauseLogin = {started:Promise.withResolvers(),resume:Promise.withResolvers()};
    const pending = signIn('bob',pre);
    await pauseLogin.started.promise;
    pauseRevoke = {started:Promise.withResolvers(),resume:Promise.withResolvers()};
    const closed = new Promise(resolve => server.close(resolve));
    await pauseRevoke.started.promise;
    let drained = false;
    server.grantsDrained.then(() => { drained = true; });
    await Promise.resolve();
    assert.equal(drained,false);
    pauseLogin.resume.resolve(); pauseLogin = null;
    pauseRevoke.resume.resolve(); pauseRevoke = null;
    assert.equal((await pending).status,401);
    await Promise.all([closed,server.grantsDrained]);
    assert.equal(grants.size,0);
    assert.equal(drained,true);
  });
});

test('anonymous and authenticated reviewer sessions expire without polling prolonging them', () => {
  let now = 1700000000000;
  const sessions = new ReviewerSessions(() => now);
  const pre = sessions.bootstrap();
  now += 300000;
  assert.equal(sessions.get(pre.id),undefined);
  const exp = new Date(now+3600000).toISOString();
  const snapshot = {expiresAt:exp,idleExpiresAt:new Date(now+600000).toISOString()};
  const active = sessions.authenticated(sessions.bootstrap(),{token:nonce(),...snapshot},user,snapshot);
  assert.equal(active.expires,now+1800000);
  assert.equal(sessions.get(active.id),active);
  now += 600000;
  assert.equal(sessions.get(active.id),undefined);
  const all = Array.from({length:512},() => sessions.bootstrap());
  assert.throws(() => sessions.bootstrap(), {status:429});
  for (const item of all) assert.ok(item.id);
  assert.match(cookieName,/^__Host-/);
});

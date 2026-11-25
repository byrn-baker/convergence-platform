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
  let pauseLogin, pauseRead, pauseRevoke, failRevoke, wrongResult, telemetryEnabled = true;
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
      return {...grants.get(token), ...deadlines.get(token), capabilities:{telemetry:telemetryEnabled}};
    },
    async revoke(token) {
      calls.push('revoke');
      if (pauseRevoke) { pauseRevoke.started.resolve(); await pauseRevoke.resume.promise; }
      if (failRevoke) throw new Error('private backend offline details');
      grants.delete(token);
    },
    async readTelemetry(token,site,resource,query) {
      calls.push('telemetry:' + resource);
      if (pauseRead) { pauseRead.started.resolve(); await pauseRead.resume.promise; }
      return wrongResult || {site, resource, query, healthScore:{value:97}};
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
    async requestJevReview(token,site,id,data) {
      calls.push('jev');
      if (grants.get(token)?.role === 'viewer') throw Object.assign(Error('wrong role'),{status:403});
      return wrongResult || {case_id:id,site_id:site,operation_id:data.operationId.toLowerCase(),requested_by:grants.get(token).id};
    },
    async readJevReceipt(token,site,id,operationId) {
      return wrongResult || {case_id:id,site_id:site,operation_id:operationId.toLowerCase(),requested_by:grants.get(token).id};
    },
    async listSignatures(token,site,query) {
      calls.push('signatures:' + (query ?? ''));
      if (!grants.get(token)?.sites.includes(site)) throw Object.assign(new Error('wrong site'),{status:403});
      if (pauseRead) { pauseRead.started.resolve(); await pauseRead.resume.promise; }
      return wrongResult || {site, signatures:[{signature_id:'2900417', cases:418}]};
    },
    async listCases(token,site,query) {
      calls.push('cases:' + (query ?? ''));
      if (!grants.get(token)?.sites.includes(site)) throw Object.assign(new Error('wrong site'),{status:403});
      return wrongResult || {site, cases:[{id:caseId}], next:null};
    },
    async previewBulk(token,site,signature,query) {
      calls.push('preview:' + signature + ':' + (query ?? ''));
      if (grants.get(token)?.role === 'viewer') throw Object.assign(new Error('wrong role'),{status:403});
      return wrongResult || {site, signature_id:signature, cases:418, window_start:'2026-11-23T00:00:00.000000Z', window_end:'2026-11-24T00:00:00.000000Z'};
    },
    async recordBulk(token,site,signature,data) {
      calls.push('bulk');
      if (grants.get(token)?.role === 'viewer') throw Object.assign(new Error('wrong role'),{status:403});
      deadlines.get(token).idleExpiresAt = new Date(Date.now() + 540000).toISOString();
      return wrongResult || {site_id:site, signature_id:signature, operation_id:data.operationId.toLowerCase(),
        reviewed_by:grants.get(token).id, case_count:data.confirmedCount};
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
    assert.deepEqual(Object.keys(r.body).sort(),['capabilities','csrf','expiresAt','user']);
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

  await t.test('external top-level links can open the public sign-in shell only', async () => {
    const navigation = {'sec-fetch-site':'cross-site', 'sec-fetch-mode':'navigate', 'sec-fetch-dest':'document'};
    const before = calls.length;
    const shell = await request('/', {headers:navigation});
    assert.equal(shell.status,200);
    assert.equal(shell.cookie,undefined);
    assert.equal(calls.length,before);
    for (const target of ['/review/session',caseURL]) {
      assert.equal((await request(target,{headers:navigation})).status,403);
    }
    for (const headers of [
      {...navigation,'sec-fetch-mode':'cors','sec-fetch-dest':'empty'},
      {...navigation,'sec-fetch-dest':'iframe'},
      {...navigation,host:'foreign.test'},
      {...navigation,origin:'https://foreign.test'},
      {...navigation,'x-forwarded-for':'127.0.0.1'},
    ]) assert.equal((await request('/',{headers})).status,403);
    assert.equal((await request('/review/sign-in',{method:'POST',headers:navigation,data:{}})).status,403);
    assert.equal(calls.length,before);
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

  await t.test('JEV uses a separate operation and preserves role, CSRF and receipt identity checks', async () => {
    const alice=await authenticated('alice'),bob=await authenticated('bob');
    const input={operationId:randomUUID()},url=caseURL+'/jev-review';
    assert.equal((await request(url,{method:'POST',state:bob,data:input})).status,403);
    assert.equal((await request(url,{method:'POST',state:{...alice,csrf:bob.csrf},data:input})).status,403);
    assert.equal((await request(url,{method:'POST',state:alice,data:{...input,feedback:'Not an investigation'}})).status,400);
    assert.equal((await request(url,{method:'POST',state:alice,data:input})).body.requested_by,'alice');
    assert.equal((await request(url+'/'+input.operationId,{state:alice})).status,200);
    try {
      wrongResult={case_id:caseId,site_id:'red',operation_id:input.operationId,requested_by:'bob'};
      assert.equal((await request(url+'/'+input.operationId,{state:alice})).status,503);
    } finally {wrongResult=null;}
    await signOut(alice);await signOut(bob);
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

  await t.test('the case queue and bulk review keep the session, site, role, CSRF and response-scope rules', async () => {
    const alice = await authenticated('alice');
    const bob = await authenticated('bob');
    const queueURL = '/review/sites/red/signatures', listURL = '/review/sites/red/cases';
    const bulkURL = '/review/sites/red/signatures/2900417/dispositions';
    const decision = {operationId:randomUUID().toUpperCase(), kind:'false_positive', reason:'Synthetic duplicate capture on the mirror port',
      windowStart:'2026-11-23T00:00:00.000000Z', windowEnd:'2026-11-24T00:00:00.000000Z', source:null, confirmedCount:418};

    // Reads: any reviewer of the site, with the query passed through as written.
    const queue = await request(queueURL + '?window=7d&state=all',{state:bob});
    assert.deepEqual([queue.status,queue.body.signatures[0].cases],[200,418]);
    assert.equal(calls.at(-2),'signatures:window=7d&state=all');
    assert.ok(queue.headers['x-review-expires-at']);
    const list = await request(listURL + '?signature=2900417&limit=50&before=MTc5NS4w_-',{state:alice});
    assert.deepEqual([list.status,list.body.cases.length],[200,1]);
    assert.equal(calls.at(-2),'cases:signature=2900417&limit=50&before=MTc5NS4w_-');
    assert.equal((await request(queueURL,{state:alice})).status,200);
    assert.equal((await request(listURL + '?source=2001:db8::17',{state:alice})).status,200);
    // No session, another site, and anything but plain name=value text never reach the service.
    const before = calls.length;
    assert.equal((await request(queueURL)).status,401);
    assert.equal((await request(listURL,{state:await bootstrap()})).status,401);
    for (const target of [queueURL + '?window=7d%26site=blue', queueURL + '?a=b#c', queueURL + '?x=' + 'a'.repeat(520), listURL + '?q=a+b',
      listURL + "?q='", listURL + '?q=a/b', '/review/sites/red/signatures/', '/review/sites/red/queue', '/review/sites/../signatures',
      bulkURL + '/preview?' + 'a'.repeat(130), '/review/sites/red/signatures/a%2Fb/dispositions/preview', bulkURL + '/preview/x']) {
      assert.ok([400,404].includes((await request(target,{state:alice})).status),target);
    }
    assert.equal((await request(queueURL,{method:'POST',state:alice,data:{}})).status,404);
    assert.equal((await request(listURL,{method:'DELETE',state:alice})).status,404);
    assert.equal(calls.length,before);
    assert.equal((await request('/review/sites/blue/signatures',{state:alice})).status,403);
    assert.equal((await request('/review/sites/blue/cases',{state:alice})).status,403);

    // Bulk review: operators only, and the write needs this session's CSRF value.
    const preview = await request(bulkURL + '/preview?window=24h&source=10.20.4.17',{state:alice});
    assert.deepEqual([preview.status,preview.body.cases,preview.body.signature_id],[200,418,'2900417']);
    assert.equal(calls.at(-2),'preview:2900417:window=24h&source=10.20.4.17');
    assert.equal((await request(bulkURL + '/preview',{state:bob})).status,403);
    assert.equal((await request(bulkURL,{method:'POST',state:bob,data:decision})).status,403);
    const quiet = calls.length;
    assert.equal((await request(bulkURL,{method:'POST',state:{...alice,csrf:bob.csrf},data:decision})).status,403);
    assert.equal((await request(bulkURL,{method:'POST',state:{cookie:alice.cookie},data:decision})).status,403);
    assert.equal((await request(bulkURL,{method:'POST',state:alice,data:decision,headers:{origin:'https://evil.test'}})).status,403);
    assert.equal((await request(bulkURL,{method:'POST',state:alice,data:{...decision,actor:'bob'}})).status,400);
    assert.equal((await request(bulkURL,{method:'POST',state:alice,data:{...decision,operationId:17}})).status,400);
    assert.equal((await request(bulkURL,{method:'POST',state:alice,data:{...decision,confirmedCount:'418'}})).status,400);
    assert.equal((await request(bulkURL,{method:'POST',state:alice,data:{...decision,confirmedCount:1.5}})).status,400);
    assert.equal((await request(bulkURL,{method:'POST',state:alice,data:{...decision,source:17}})).status,400);
    assert.equal((await request(bulkURL,{method:'POST',state:alice,data:{...decision,reason:null}})).status,400);
    assert.equal((await request(bulkURL,{method:'POST',state:alice,raw:'{',headers:{'content-type':'application/json'}})).status,400);
    assert.equal((await request(bulkURL,{method:'POST',state:alice,data:decision,headers:{'content-type':'text/plain'}})).status,415);
    assert.equal((await request(bulkURL,{state:alice})).status,404);
    assert.equal((await request(bulkURL + '/preview',{method:'POST',state:alice,data:decision})).status,404);
    assert.equal(calls.length,quiet);
    const aliceToken = [...grants.keys()].find(token => grants.get(token).id === 'alice');
    const written = await request(bulkURL,{method:'POST',state:alice,data:decision});
    assert.deepEqual([written.status,written.body.reviewed_by,written.body.case_count,written.body.operation_id],
      [200,'alice',418,decision.operationId.toLowerCase()]);
    assert.equal(written.headers['x-review-expires-at'],deadlines.get(aliceToken).idleExpiresAt);

    // A response for another site, signature, operation or reviewer is withheld.
    try {
      for (const [target,wrong] of [[queueURL,{site:'blue',signatures:[]}], [queueURL,{site:'red'}], [listURL,{site:'blue',cases:[]}],
        [listURL,{site:'red',cases:'private'}], [bulkURL + '/preview',{site:'blue',signature_id:'2900417',cases:1}],
        [bulkURL + '/preview',{site:'red',signature_id:'1',cases:1}]]) {
        wrongResult = wrong;
        const r = await request(target,{state:alice});
        assert.deepEqual([r.status,r.body],[503,{error:'Reviewer service unavailable'}],target);
      }
      const receipt = {site_id:'red',signature_id:'2900417',operation_id:decision.operationId.toLowerCase(),reviewed_by:'alice'};
      for (const wrong of [{...receipt,site_id:'blue'},{...receipt,signature_id:'1'},{...receipt,operation_id:randomUUID()},{...receipt,reviewed_by:'bob'}]) {
        wrongResult = wrong;
        assert.equal((await request(bulkURL,{method:'POST',state:alice,data:decision})).status,503);
      }
    } finally { wrongResult = undefined; }

    // A reviewer who loses the site while the read is in flight does not receive it.
    pauseRead = {started:Promise.withResolvers(),resume:Promise.withResolvers()};
    try {
      const pending = request(queueURL,{state:alice});
      await pauseRead.started.promise;
      grants.set(aliceToken,{...user,sites:['blue']});
      pauseRead.resume.resolve();
      assert.equal((await pending).status,403);
    } finally { pauseRead = undefined; grants.set(aliceToken,user); }
    assert.equal((await signOut(alice)).status,204);
    assert.equal((await request(queueURL,{state:alice})).status,401);
    assert.equal((await request(bulkURL,{method:'POST',state:alice,data:decision})).status,401);
    await signOut(bob);
  });

  await t.test('telemetry is finite, site scoped and rechecked after reads', async () => {
    const state = await authenticated('alice');
    const url = '/review/sites/red/telemetry/health';
    assert.equal((await request(url)).status,401);
    telemetryEnabled = false;
    assert.equal((await request(url,{state})).status,403);
    assert.equal((await request('/review/session',{state})).body.capabilities.telemetry,false);
    telemetryEnabled = true;
    const result = await request(url,{state});
    assert.equal(result.status,200);
    assert.equal(result.body.healthScore.value,97);
    assert.ok(result.headers['x-review-expires-at']);
    for (const target of ['/review/sites/blue/telemetry/health', url+'?site=blue',
      url+'?range=1h&range=24h', '/review/sites/red/telemetry/models', '/review/sites/red/telemetry/../health']) {
      assert.notEqual((await request(target,{state})).status,200);
    }
    assert.equal((await request(url,{method:'POST',state,data:{}})).status,404);
    wrongResult = {site:'blue', secret:'other site'};
    assert.equal((await request(url,{state})).status,503);
    wrongResult = null;
    pauseRead = {started:Promise.withResolvers(),resume:Promise.withResolvers()};
    const pending = request(url,{state});
    await pauseRead.started.promise;
    await signOut(state);
    pauseRead.resume.resolve();
    assert.equal((await pending).status,401);
    pauseRead = null;
    const replacement = await authenticated('alice');
    pauseRead = {started:Promise.withResolvers(),resume:Promise.withResolvers()};
    const revoked = request(url,{state:replacement});
    await pauseRead.started.promise;
    for (const [token, subject] of grants) if (subject.id === 'alice') grants.set(token,{...subject,sites:['blue']});
    pauseRead.resume.resolve();
    assert.equal((await revoked).status,403);
    pauseRead = null;
    await signOut(replacement);
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

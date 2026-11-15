'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const https=require('node:https');
const {X509Certificate}=require('node:crypto');
const {once}=require('node:events');
const {certificates}=require('./test-support/tls-fixture.cjs');
const {createReviewerService}=require('./src/reviewer/transport');
const {parseTelemetryTarget,projectTelemetry}=require('./src/reviewer/telemetry-contract');
const {createTelemetryReader}=require('./src/reviewer/telemetry');
const token='a'.repeat(43);
const endpoint='/v1/sites/red/telemetry/health';

test('finite telemetry route rejects aliases, duplicate/unknown filters and unbounded windows',()=>{
  assert.equal(parseTelemetryTarget(endpoint).site,'red');
  for (const raw of [endpoint+'?site=blue',endpoint+'?range=1h',endpoint+'/../events',endpoint+'/',endpoint+'?x=1',
    '/v1/sites/red/telemetry/events?limit=101','/v1/sites/red/telemetry/events?limit=1&limit=2',
    '/v1/sites/red/telemetry/events?country=US','/v1/sites/red/telemetry/security/history?range=30d',
    '/v1/sites/red/telemetry/flows/records?address=1.1.1.1','/v1/sites/red/telemetry/%68ealth']) assert.throws(()=>parseTelemetryTarget(raw),raw);
  assert.equal(parseTelemetryTarget('/v1/sites/red/telemetry/events?type=wan&limit=100').query.type,'wan');
});

test('real mTLS telemetry brackets slow work with current authorization and cancels late reads',async t=>{
  const tls=certificates(t);let valid=true,inTransaction=false,reads=0,checks=0,mode='ok',release,entered;
  const authority={withGrant:async(installation,value,scope,operation)=>{
    assert.equal(installation,'fixture');assert.equal(value,token);assert.equal(scope.site,'red');assert.equal(scope.write,false);
    checks++;if(!valid)throw Object.assign(Error(),{status:401});
    inTransaction=true;
    try{return await operation(null,{id:'alice',role:'viewer',sites:['red']});}finally{inTransaction=false;}
  }};
  const telemetry=async(site,resource,query,{signal})=>{
    assert.equal(inTransaction,false,'slow source reads cannot hold grant locks');reads++;
    assert.equal(site,'red');assert.equal(resource,'health');assert.deepEqual(query,{});
    if(mode==='pause'){entered();await new Promise(resolve=>release=resolve);}
    if(mode==='timeout')await new Promise(resolve=>signal.addEventListener('abort',resolve,{once:true}));
    return {site,healthScore:{value:99}};
  };
  const server=createReviewerService({tls:{ca:tls.ca,...tls.server},authority,telemetry,telemetryTimeoutMs:100,
    enrollments:[{installation:'fixture',fingerprint256:new X509Certificate(tls.client.cert).fingerprint256}]});
  server.listen(0,'127.0.0.1');await once(server,'listening');t.after(()=>new Promise(resolve=>server.close(resolve)));
  const request=(url=endpoint,{method='GET',headers={}}={})=>new Promise((resolve,reject)=>{
    const req=https.request({hostname:'127.0.0.1',port:server.address().port,servername:'localhost',ca:tls.ca,...tls.client,agent:false,
      path:url,method,headers:{authorization:'Bearer '+token,...headers}},res=>{let body='';res.on('data',chunk=>body+=chunk);res.on('end',()=>resolve({status:res.statusCode,body:JSON.parse(body)}));});
    req.on('error',reject);req.end();
  });
  assert.equal((await request()).status,200);assert.equal(checks,2);assert.equal(reads,1);
  for(const [url,opts] of [[endpoint+'?site=blue',{}],[endpoint,{method:'POST'}],[endpoint,{headers:{origin:'https://hostile.test'}}]]) {
    assert.ok((await request(url,opts)).status>=400);assert.equal(reads,1);
  }
  valid=false;assert.equal((await request()).status,401);assert.equal(reads,1);valid=true;
  mode='pause';let pending;await new Promise(resolve=>{entered=resolve;pending=request();});valid=false;release();
  const denied=await pending;assert.equal(denied.status,401);assert.equal(denied.body.healthScore,undefined);valid=true;
  mode='timeout';const timed=await request();assert.equal(timed.status,503);assert.equal(timed.body.healthScore,undefined);
});

test('telemetry projections refuse mixed sites, secret fields, wrong cases and oversized responses',()=>{
  const op=parseTelemetryTarget('/v1/sites/red/telemetry/security/vms');
  for(const data of [{site:'blue'}, {site:'red',sources:[{site:'blue'}]}, {site:'red',sources:[{token:'private'}]},
    {site:'red',sources:[{description:'x'.repeat(1024*1024)}]}]) assert.throws(()=>projectTelemetry(op,data));
  const vm=projectTelemetry(op,{site:'red',sources:[],hostTelemetry:{status:'configured',agents:[{id:'guest'}]},status:'configured'});
  assert.equal(vm.hostTelemetry.agents[0].id,'guest');
  const id='11111111-1111-4111-8111-111111111111';
  const caseOp=parseTelemetryTarget('/v1/sites/red/telemetry/events/'+id);
  assert.throws(()=>projectTelemetry(caseOp,{id,site_id:'blue'}));
  assert.deepEqual(projectTelemetry(caseOp,{id,site_id:'red',isp_report:{summary:'saved'},source_evidence:{private:'omit'}}),
    {id,site_id:'red',isp_report:{summary:'saved'}});
});

test('shared services preserve legacy HTTP data and authorize database reads by site',async t=>{
  const nativeFetch=global.fetch;const requests=[];
  process.env.CONVERGENCE_DB='off';process.env.SITES_CONFIG=JSON.stringify({red:{},blue:{}});
  global.fetch=async(url,options)=>{
    requests.push(String(url));
    return {ok:true,json:async()=>({status:'success',data:{result:[]}})};
  };
  t.after(()=>{global.fetch=nativeFetch;});
  const calls=[];
  const query=async(sql,args)=>{calls.push({sql,args});return {rows:[]};};
  const read=createTelemetryReader({query});
  const device=await read('red','devices');assert.equal(device.site,'red');
  assert.ok(requests.length>20);assert.ok(requests.every(url=>new URL(url).searchParams.get('query').includes('site="red"')));
  const express=require('express');
  const legacy=express().use((req,res,next)=>{req.site='red';next();}).use(require('./src/routes/devices')).listen(0,'127.0.0.1');
  await once(legacy,'listening');t.after(()=>new Promise(resolve=>legacy.close(resolve)));
  const response=await nativeFetch('http://127.0.0.1:'+legacy.address().port+'/');
  const old=await response.json();assert.equal(response.status,200);
  assert.deepEqual(device.switches,old.switches);assert.deepEqual(device.summary,old.summary);assert.equal(device.mgmt,undefined);
  const diary=await read('red','events',{type:'wan',limit:'100'});assert.equal(diary.site,'red');
  assert.equal(calls[0].args[0],'red');assert.match(calls[0].sql,/site_id = \$1/);assert.equal(calls[0].args.at(-1),100);
  await read('blue','events');assert.equal(calls[1].args[0],'blue');
  await assert.rejects(read('red','events',{site:'blue'}));
});

test('failed Wi-Fi and alert reads retain explicit unavailable status through the service projection',async()=>{
  const {ReadError}=require('./src/services/read-error');
  const unavailable=async()=>{throw new ReadError(503,{error:'private source error'});};
  const read=createTelemetryReader({query:async()=>({rows:[]}),readers:{wifi:unavailable,alerts:unavailable}});
  for(const resource of ['wifi','alerts']) {
    const result=await read('red',resource);
    assert.equal(result.site,'red');assert.equal(result.unavailable,true);assert.equal(result.status,'unavailable');
    assert.equal(result.reason,'Telemetry source unavailable');assert.ok(!JSON.stringify(result).includes('private'));
  }
});

test('PostgreSQL grants isolate telemetry users and revocation completes during a pending source read',{
  skip:!process.env.REVIEWER_TEST_DATABASE_URL,timeout:30000,
},async t=>{
  const {Pool}=require('pg');const {randomUUID}=require('node:crypto');const {Readable}=require('node:stream');
  const fs=require('node:fs');const {migrate}=require('./reviewer-service');const {provision}=require('./reviewer-accounts');
  const {createGrantAuthority}=require('./src/reviewer/grants');
  const admin=new Pool({connectionString:process.env.REVIEWER_TEST_DATABASE_URL});
  const schema='telemetry_'+randomUUID().replaceAll('-','');await admin.query('CREATE SCHEMA '+schema);
  const pool=new Pool({connectionString:process.env.REVIEWER_TEST_DATABASE_URL,options:'-c search_path='+schema});
  t.after(async()=>{await pool.end();await admin.query('DROP SCHEMA '+schema+' CASCADE');await admin.end();});
  await pool.query(fs.readFileSync(__dirname+'/src/db/schema.sql','utf8'));await migrate(pool);
  await pool.query("INSERT INTO sites(id,name) VALUES ('red','Red'),('blue','Blue')");
  for(const [user,site] of [['alice','red'],['bob','blue']]) await provision(pool,['create',user,'viewer',site],Readable.from([Buffer.from('fixture-password\n')]));
  const authority=createGrantAuthority(pool);
  const alice=await authority.issue('fixture',{username:'alice',password:'fixture-password'});
  const bob=await authority.issue('fixture',{username:'bob',password:'fixture-password'});
  let entered,release,paused=false;
  const reader=createTelemetryReader({query:(text,values)=>pool.query(text,values),readers:{
    health:async site=>{if(paused){entered();await new Promise(resolve=>release=resolve);}return {site,healthScore:{value:98}};},
  }});
  const tls=certificates(t);
  const server=createReviewerService({tls:{ca:tls.ca,...tls.server},authority,telemetry:reader,
    enrollments:[{installation:'fixture',fingerprint256:new X509Certificate(tls.client.cert).fingerprint256}]});
  server.listen(0,'127.0.0.1');await once(server,'listening');
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const request=(grant,site)=>new Promise((resolve,reject)=>{
    const req=https.request({hostname:'127.0.0.1',port:server.address().port,servername:'localhost',ca:tls.ca,...tls.client,agent:false,
      path:'/v1/sites/'+site+'/telemetry/health',headers:{authorization:'Bearer '+grant.token}},res=>{
      let body='';res.on('data',x=>body+=x);res.on('end',()=>resolve({status:res.statusCode,body:JSON.parse(body)}));});
    req.on('error',reject);req.end();
  });
  assert.equal((await request(alice,'red')).status,200);assert.equal((await request(alice,'blue')).status,403);
  assert.equal((await request(bob,'blue')).status,200);assert.equal((await request(bob,'red')).status,403);
  paused=true;let pending;await new Promise(resolve=>{entered=resolve;pending=request(alice,'red');});
  // If a grant transaction held its lock during acquisition, revoke would wait
  // for release and this check would time out instead of completing.
  await Promise.race([authority.revoke('fixture',alice.token),new Promise((_,reject)=>setTimeout(()=>reject(Error('Grant lock held across source read')),1000))]);
  release();const denied=await pending;assert.equal(denied.status,401);assert.equal(denied.body.healthScore,undefined);
  paused=false;assert.equal((await request(bob,'blue')).status,200);
});

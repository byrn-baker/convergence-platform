const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const agent = () => ({id:'enrollment-1', site:'authorized', vm_id:'vm-1', tenant_id:'private-tenant',
  allocation_id:'private-allocation', mapping_version:'private-map', token:'secret', token_sha256:'private-hash',
  valid_from:'2026-01-01T00:00:00Z', valid_until:'2027-01-01T00:00:00Z', status:'reporting', reason:'Recent guest report',
  last_contact:1790000000, heartbeat_observed:1790000000,
  heartbeat:{collection_ok:true, queue_events:0, queue_bytes:0, dropped_events:0, private:'omit'},
  pending_events:0, pending_limit:100, evidence_trust:'guest_reported'});
function reader(fetch, env={IDS_STATUS_URL:'https://receiver.example/internal', IDS_STATUS_TOKEN:'operator-secret'}) {
  const module={exports:{}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/src/lib/hostStatus.js','utf8'), {
    module, process:{env}, URL, AbortSignal, fetch,
  });
  return module.exports.hostStatus;
}
const reply = data => async()=>({ok:true, json:async()=>data});
const plain = value => JSON.parse(JSON.stringify(value));

test('status uses only authorized site, keeps credentials server-side and projects allowlisted fields', async()=>{
  let request;
  const read=reader(async(url,options)=>{request={url,options};return {ok:true,json:async()=>({status:'configured',agents:[agent()],token:'secret'})};});
  const result=plain(await read('authorized'));
  assert.equal(request.url.href,'https://receiver.example/v1/host-status?site=authorized');
  assert.equal(request.options.headers.Authorization,'Bearer operator-secret');
  assert.equal(request.options.redirect,'error');assert.ok(request.options.signal instanceof AbortSignal);
  assert.equal(request.options.dispatcher,undefined,'TLS must use the verified default transport');
  assert.equal(result.status,'configured');assert.equal(result.agents[0].heartbeat.collection_ok,true);
  assert.deepEqual(Object.keys(result.agents[0]).sort(),['id','vm_id','status','reason','last_contact','heartbeat_observed','heartbeat','pending_events','pending_limit','valid_until','evidence_trust'].sort());
  assert.deepEqual(Object.keys(result.agents[0].heartbeat).sort(),['collection_ok','queue_events','queue_bytes','dropped_events'].sort());
  assert.doesNotMatch(JSON.stringify(result),/private|secret|tenant_id|allocation_id|mapping_version|token|valid_from/);
});

test('all receiver states preserve unknown values and guest trust', async()=>{
  for(const state of ['unknown','reporting','stale','degraded','inactive','revoked']) {
    const a=agent();a.status=state;
    if(state==='unknown'){a.last_contact=null;a.heartbeat_observed=null;a.heartbeat=null;}
    if(state==='degraded')a.heartbeat.dropped_events=3;
    const result=await reader(reply({status:'configured',agents:[a]}))('authorized');
    assert.equal(result.status,'configured',state);assert.equal(result.agents[0].status,state);
    assert.equal(result.agents[0].evidence_trust,'guest_reported');
    if(state==='unknown')assert.equal(result.agents[0].heartbeat,null);
  }
});

test('missing configuration and empty enrollments are explicitly not configured', async()=>{
  for(const env of [{},{IDS_STATUS_URL:'https://receiver.example'},{IDS_STATUS_TOKEN:'secret'}]) {
    assert.equal((await reader(()=>assert.fail('unconfigured fetch'),env)('authorized')).status,'not_configured');
  }
  for(const status of ['configured','not_configured']) {
    assert.deepEqual(plain(await reader(reply({status,agents:[]}))('authorized')),
      {status:'not_configured',reason:'No guest heartbeat enrollments configured',agents:[]});
  }
});

test('404, other HTTP failures, timeouts, bad JSON and transport errors fail explicitly', async()=>{
  for(const fetch of [async()=>({ok:false,status:404}),async()=>({ok:false,status:503}),
    async()=>{throw new DOMException('timeout','TimeoutError');},async()=>{throw Error('private service details');},
    async()=>({ok:true,json:async()=>{throw Error('bad JSON');}})]) {
    const result=plain(await reader(fetch)('authorized'));
    assert.equal(result.status,'unavailable');assert.deepEqual(result.agents,[]);
    assert.doesNotMatch(JSON.stringify(result),/private service|bad JSON/);
  }
});

test('malformed or cross-site records reject the complete response', async()=>{
  const changes=[{site:'other'},{site:'Authorized'},{site:undefined},{status:'healthy'},{reason:null},
    {evidence_trust:'trusted'}, {last_contact:'2026-01-01T00:00:00Z'}, {last_contact:Infinity},
    {last_contact:0},{heartbeat_observed:0},{last_contact:0,heartbeat_observed:0},
    {heartbeat_observed:-1},{valid_until:'tomorrow'},{valid_until:'2027-13-99T00:00:00Z'},
    {valid_until:'2027-02-30T00:00:00Z'},{valid_until:'2027-01-01T24:00:00Z'},
    {pending_events:-1},{pending_limit:Number.MAX_SAFE_INTEGER+1},{heartbeat:null},
    {heartbeat:{collection_ok:true,queue_events:NaN,queue_bytes:0,dropped_events:0}},
    {heartbeat:{collection_ok:'true',queue_events:0,queue_bytes:0,dropped_events:0}},
    {heartbeat:{collection_ok:false,queue_events:0,queue_bytes:0,dropped_events:0}}];
  for(const change of changes) {
    const result=plain(await reader(reply({status:'configured',agents:[agent(),{...agent(),id:'second',...change}]}))('authorized'));
    assert.equal(result.status,'unavailable',JSON.stringify(change));assert.deepEqual(result.agents,[]);
  }
  for(const data of [null,{}, {status:'healthy',agents:[]}, {status:'not_configured',agents:[agent()]},
    {status:'configured',agents:[agent(),agent()]}, {status:'configured',agents:[null]}]) {
    assert.equal((await reader(reply(data))('authorized')).status,'unavailable');
  }
});

test('invalid authorized site and URL cannot trigger a request', async()=>{
  const fetch=()=>assert.fail('invalid request');
  assert.equal((await reader(fetch)(undefined)).status,'unavailable');
  for(const url of ['file:///private','https://name:password@receiver.example','bad']) {
    assert.equal((await reader(fetch,{IDS_STATUS_URL:url,IDS_STATUS_TOKEN:'secret'})('authorized')).status,'unavailable');
  }
});

test('security retains host status independently of firewall and IDS failures',async()=>{
  for(const logsFail of [false,true])for(const idsFail of [false,true]) {
    const handlers={},router={get:(path,handler)=>handlers[path]=handler};
    const host={status:'configured',agents:[{id:'vm'}]};let requestedSite;
    const dependencies={express:{Router:()=>router},'../lib/config':{LOKI_URL:'http://logs',promqlLabelValue:s=>s},
      '../lib/hypervisorStatus':{hypervisorStatus:async()=>({status:'not_configured',sources:[]})},
      '../lib/hostStatus':{hostStatus:async site=>{requestedSite=site;return host;}},
      '../lib/idsStatus':{idsStatus:async()=>{if(idsFail)throw Error('IDS offline');return {status:'configured'};}},
      '../db':{},'../lib/securityHistory':{}};
    vm.runInNewContext(fs.readFileSync(__dirname+'/src/routes/security.js','utf8'),{
      require:name=>dependencies[name],module:{exports:{}},URLSearchParams,AbortSignal,console,
      fetch:async()=>{if(logsFail)throw Error('logs offline');return {ok:true,json:async()=>({status:'success',data:{result:[]}})};},
    });
    const res={json(body){this.body=body;}};
    await handlers['/']({site:'authorized',query:{site:'other'}},res);
    assert.equal(requestedSite,'authorized');assert.equal(res.body.hostTelemetry,host);
    assert.equal(res.body.status,logsFail?'unavailable':'unknown');
    assert.equal(res.body.intrusionDetection.status,idsFail?'unavailable':'configured');
  }
});

'use strict';
const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
const epoch=Date.now()/1000,uuid='11111111-1111-4111-8111-111111111111';
function fixture(){return {status:'configured',generated_at:epoch,window_seconds:300,token:'secret-token',sources:[{id:'pve-source',site:'home',node:'pve-node',status:'reporting',last_contact:epoch,observed_at:epoch,token_sha256:'secret-token',coverage:{fresh:true,sampled:true,inventory_ok:true,packet_capture_ok:true,capture_drops_total:0,capture_drops_recent_delta:0,events_discarded_total:3,events_discarded_recent_delta:1,counter_delta_seconds:15,snapshot_count:3,snapshot_span_seconds:30,max_snapshot_gap_seconds:15,history_count:20,history_limited:false,missing_uuid_vm_count:0,excluded_identity_events:0,metadata_count:20,metadata_cap_reached:false,policy_configured:true,allowlisted_tuple_count:0,limitations:['Sampled evidence; intent unknown'],credential:'secret-token'},summary:{finding_count:1,affected_vm_count:1,findings_truncated:false,assessment:'review'},vms:[{vm_id:6095,vm_uuid:uuid,power_state:'running',capture_state:'observing',finding_count:1,nics:[{name:'net0',tap:'tap6095i0',ifindex:664,capture_state:'observing',raw_payload:'secret-token'}],tenant_id:'private-owner'}],findings:[{id:'a'.repeat(20),kind:'port_scan',severity:'review',vm_id:6095,vm_uuid:uuid,first_seen:epoch-20,last_seen:epoch-1,evidence:{unique_tcp_five_tuples:20,unique_destination_ports:20,unique_destination_ips:1,destination_ip:'198.51.100.1',destination_port:null,examples:[{src_ip:'10.30.1.105',dst_ip:'198.51.100.1',src_port:12345,dst_port:22,protocol:'tcp',tap:'tap6095i0',ifindex:664,observed_at:epoch-1,payload:'secret-token'}]},caveat:'Advisory pattern, not proof of attack',token:'secret-token'}]}]};}
const response=data=>({ok:true,body:(async function*(){yield Buffer.from(JSON.stringify(data));})()});
function reader(fetch,env={IDS_STATUS_URL:'https://receiver.example',IDS_STATUS_TOKEN:'reader-secret'}){const module={exports:{}};vm.runInNewContext(fs.readFileSync(__dirname+'/src/lib/vmSecurity.js','utf8'),{module,require,process:{env},fetch,URL,AbortSignal,Buffer});return module.exports.vmSecurity;}
const plain=x=>JSON.parse(JSON.stringify(x));
test('authenticated authorized-site request projects nested evidence without credentials/tenant fields',async()=>{
 let req;const data=plain(await reader(async(url,opts)=>{req={url,opts};return response(fixture());})('home'));
 assert.equal(data.status,'configured');assert.equal(data.sources[0].findings[0].kind,'port_scan');
 assert.equal(req.url.href,'https://receiver.example/v1/vm-security?site=home');assert.equal(req.opts.headers.Authorization,'Bearer reader-secret');assert.equal(req.opts.redirect,'error');assert.ok(req.opts.signal instanceof AbortSignal);
 assert.doesNotMatch(JSON.stringify(data),/secret-token|private-owner|token_sha256|raw_payload|tenant_id/);
});
test('invalid/missing site, config, failed or oversized responses remain unknown',async()=>{
 assert.equal((await reader(()=>assert.fail('fetch'))(null)).status,'unavailable');
 assert.equal((await reader(()=>assert.fail('fetch'),{})('home')).status,'not_configured');
 for(const call of [async()=>{throw Error('reader-secret');},async()=>({ok:false}),async()=>response({}),async()=>({ok:true,body:(async function*(){yield Buffer.alloc(4*1024*1024+1);})()})]){
  const result=await reader(call)('home');assert.equal(result.status,'unavailable');assert.equal(result.unavailable,true);assert.doesNotMatch(JSON.stringify(result),/reader-secret/);
 }
});
test('site spoof, identity mismatch, fake certainty, invalid scan thresholds and future times fail closed',async()=>{
 const mutations=[s=>s.site='other',s=>s.findings[0].vm_uuid='22222222-2222-4222-8222-222222222222',s=>s.findings[0].severity='confirmed_attack',s=>s.findings[0].evidence.examples[0].ifindex=999,s=>s.findings[0].evidence.unique_destination_ports=19,s=>s.findings[0].last_seen=epoch+60,s=>s.vms.push(s.vms[0]),s=>s.findings.push(s.findings[0]),s=>s.summary.finding_count=2];
 for(const mutate of mutations){const data=fixture();mutate(data.sources[0]);assert.equal((await reader(async()=>response(data))('home')).status,'unavailable');}
});
test('freshness overrides stale reporting without deleting historical findings',async()=>{
 const data=fixture();data.sources[0].last_contact=epoch-61;data.sources[0].observed_at=epoch-61;
 const result=await reader(async()=>response(data))('home');assert.equal(result.sources[0].status,'stale');assert.equal(result.sources[0].coverage.fresh,false);assert.equal(result.sources[0].findings.length,1);
});
test('empty stale observations never become an all clear',async()=>{
 const data=fixture();const s=data.sources[0];s.last_contact=null;s.observed_at=null;s.findings=[];s.vms[0].finding_count=0;s.summary={finding_count:0,affected_vm_count:0,findings_truncated:false,assessment:'no_patterns_observed'};
 const result=await reader(async()=>response(data))('home');assert.equal(result.sources[0].summary.assessment,'insufficient_evidence');assert.equal(result.sources[0].status,'unknown');
});
test('dedicated route uses req.site and keeps guest diagnostics independent',async()=>{
 const handlers={};const sites=[];const assessment={status:'configured',sources:[]},hosts={status:'configured',agents:[]};
 const deps={express:{Router:()=>({get:(p,h)=>handlers[p]=h})},'../lib/config':{},'../lib/idsStatus':{},'../lib/hostStatus':{hostStatus:async s=>{sites.push(s);return hosts;}},'../lib/hypervisorStatus':{},'../db':{},'../lib/securityHistory':{},'../lib/vmSecurity':{vmSecurity:async s=>{sites.push(s);return assessment;}}};
 vm.runInNewContext(fs.readFileSync(__dirname+'/src/routes/security.js','utf8'),{module:{exports:{}},require:n=>deps[n]});const res={json(x){this.data=x;}};
 await handlers['/vms']({site:'home',query:{site:'other'}},res);assert.deepEqual(sites,['home','home']);assert.equal(res.data.hostTelemetry,hosts);assert.equal(res.data.status,'configured');
});

test('bounded analyzer-added coverage explanations and gap fields remain available',async()=>{
 const data=fixture();data.sources[0].coverage.limitations=Array(71).fill('Coverage limitation');
 const result=await reader(async()=>response(data))('home');assert.equal(result.status,'configured');assert.equal(result.sources[0].coverage.snapshot_count,3);assert.equal(result.sources[0].coverage.max_snapshot_gap_seconds,15);assert.equal(result.sources[0].coverage.limitations.length,71);
});

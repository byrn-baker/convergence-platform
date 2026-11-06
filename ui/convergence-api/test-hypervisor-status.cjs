'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const epoch = Date.now() / 1000;
const uuid = '11111111-1111-4111-8111-111111111111';
const nic = () => ({name:'net0',tap:'tap100i0',ifindex:12,mac:'12:34:56:78:90:ab',bridge:'vmbr0',vlan:null,present:true,rx_bytes:1,tx_bytes:2,rx_packets:3,tx_packets:4,capture_state:'observing',firewall_drop_packets:null,token:'private-secret'});
const source = () => ({id:'observer-1',site:'authorized',node:'node-1',status:'reporting',last_contact:epoch,token:'private-secret',tenant_id:'private-customer',snapshot:{schema_version:1,sequence:1,observed_at:epoch,collector_started_at:epoch-10,node:'node-1',boot_id:uuid,token:'private-secret',collection:{inventory_ok:true,packet_capture_ok:true,sampled:true,capture_drops:0,events_discarded:0,limitations:[],raw_payload:'private-secret'},vms:[{vm_id:100,vm_uuid:uuid,power_state:'running',nics:[nic()],customer_id:'private-customer'}],connections:[{vm_id:100,vm_uuid:uuid,tap:'tap100i0',ifindex:12,observed_at:epoch,direction:'guest_egress',ip_version:4,protocol:'tcp',src_ip:'192.0.2.1',dst_ip:'192.0.2.2',src_port:10,dst_port:443,packet_count:1,evidence:'packet_metadata',verdict:'unknown',payload:'private-secret'}]}});
function reader(fetch, env={IDS_STATUS_URL:'https://receiver.example',IDS_STATUS_TOKEN:'private-secret'}) {
  const module = {exports:{}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/src/lib/hypervisorStatus.js','utf8'), {module,require,process:{env},fetch,URL,AbortSignal});
  return module.exports.hypervisorStatus;
}
const response = source => async()=>({ok:true,json:async()=>({status:'configured',sources:[source]})});
const plain = data => JSON.parse(JSON.stringify(data));
test('authorized site, verified transport, bounded request and recursive projection keep secrets server-side',async()=>{
  let request;
  const read=reader(async(url,options)=>{request={url,options};return response(source())();});
  const data=plain(await read('authorized'));
  assert.equal(data.status,'configured');assert.equal(data.sources[0].status,'reporting');
  assert.equal(request.url.href,'https://receiver.example/v1/hypervisor-status?site=authorized');
  assert.equal(request.options.headers.Authorization,'Bearer private-secret');assert.equal(request.options.redirect,'error');assert.ok(request.options.signal instanceof AbortSignal);assert.equal(request.options.dispatcher,undefined);
  assert.doesNotMatch(JSON.stringify(data),/private|token|payload|tenant|customer|site/);
  assert.equal(data.sources[0].snapshot.vms[0].nics[0].rx_packets,3);
});
test('invalid site cannot make a request; missing config and failures are explicit',async()=>{
  assert.equal((await reader(()=>assert.fail('fetch'))(undefined)).status,'unavailable');
  assert.equal((await reader(()=>assert.fail('fetch'),{})('authorized')).status,'not_configured');
  for(const fetch of [async()=>{throw Error('secret');},async()=>({ok:false}),async()=>({ok:true,json:async()=>null})]) {
    const result=await reader(fetch)('authorized');assert.equal(result.status,'unavailable');assert.doesNotMatch(JSON.stringify(result),/secret/);
  }
});
test('cross-site and mismatched node, attribution, malformed evidence and duplicate identities fail closed',async()=>{
  for(const mutate of [s=>s.site='other',s=>delete s.site,s=>s.snapshot.node='spoof',s=>s.snapshot.connections[0].ifindex=99,s=>s.snapshot.connections[0].vm_uuid=null,s=>s.snapshot.collection.sampled=false,s=>s.snapshot.vms[0].nics[0].rx_bytes=-1,s=>s.snapshot.vms.push(s.snapshot.vms[0]),s=>s.snapshot.vms[0].nics.push(s.snapshot.vms[0].nics[0]),s=>s.snapshot.connections[0].verdict='safe',s=>s.last_contact=null,s=>s.snapshot.observed_at=epoch+100,s=>s.snapshot.connections[0].src_ip='not-an-IP']) {
    const s=source();mutate(s);const result=await reader(response(s))('authorized');assert.equal(result.status,'unavailable');assert.deepEqual(plain(result.sources),[]);
  }
  const result=await reader(async()=>({ok:true,json:async()=>({status:'configured',sources:[source(),source()]})}))('authorized');assert.equal(result.status,'unavailable');
});
test('stale timestamps override reporting and no observations stay unknown',async()=>{
  const s=source();s.snapshot.observed_at=epoch-61;s.snapshot.collector_started_at=epoch-71;s.snapshot.connections[0].observed_at=epoch-61;
  assert.equal((await reader(response(s))('authorized')).sources[0].status,'stale');
  s.snapshot=null;s.last_contact=null;
  assert.equal((await reader(response(s))('authorized')).sources[0].status,'unknown');
});
test('security API includes hypervisor status independently of firewall failures using req.site',async()=>{
  for(const fail of [false,true]) {
    const handlers={};let requestedSite;
    const status={status:'configured',sources:[]};
    const deps={express:{Router:()=>({get:(path,handler)=>handlers[path]=handler})},'../lib/config':{LOKI_URL:'https://logs',promqlLabelValue:s=>s},'../lib/hostStatus':{hostStatus:async()=>({})},'../lib/idsStatus':{idsStatus:async()=>({})},'../lib/hypervisorStatus':{hypervisorStatus:async site=>{requestedSite=site;return status;}},'../db':{},'../lib/securityHistory':{}};
    vm.runInNewContext(fs.readFileSync(__dirname+'/src/routes/security.js','utf8'),{module:{exports:{}},require:name=>deps[name],AbortSignal,URLSearchParams,fetch:async()=>{if(fail)throw Error('logs down');return {ok:true,json:async()=>({status:'success',data:{result:[]}})};}});
    const res={json(body){this.body=body;}};await handlers['/']({site:'authorized',query:{site:'spoof'}},res);
    assert.equal(requestedSite,'authorized');assert.equal(res.body.hypervisorTelemetry,status);
  }
});

test('unsupported user-network NIC does not hide other VM inventory',async()=>{
  const s=source();s.snapshot.connections=[];
  Object.assign(s.snapshot.vms[0].nics[0],{bridge:'',present:false,ifindex:null,capture_state:'unsupported'});
  const good=await reader(response(s))('authorized');assert.equal(good.status,'configured');
  s.snapshot.vms[0].nics[0].present=true;
  assert.equal((await reader(response(s))('authorized')).status,'unavailable');
});
test('expired snapshot preserves historical source contact as stale',async()=>{
  const s=source();s.snapshot=null;s.last_contact=epoch-86401;s.status='stale';
  const data=await reader(response(s))('authorized');assert.equal(data.status,'configured');
  assert.equal(data.sources[0].status,'stale');assert.equal(data.sources[0].last_contact,s.last_contact);
  assert.equal(data.sources[0].snapshot,null);
});

'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {JSDOM}=require('../netclaw-visual/node_modules/jsdom');
const code=fs.readFileSync(__dirname+'/../netclaw-visual/modules/convergence/ProxmoxObserver.js','utf8').replace('export function','function')+'\nthis.render=proxmoxObserverHTML;';
const context={};vm.runInNewContext(code,context);
const now=1800000000000;
const nic={name:'net0',tap:'tap100i0',ifindex:12,present:true,capture_state:'observing',rx_bytes:10,tx_bytes:20,rx_packets:1,tx_packets:2,firewall_drop_packets:4};
const data=()=>({status:'configured',sources:[{id:'observer',node:'node-1',status:'reporting',last_contact:now/1000,snapshot:{observed_at:now/1000,collection:{inventory_ok:true,packet_capture_ok:true,capture_drops:0,events_discarded:0,limitations:[]},vms:[{vm_id:100,vm_uuid:'uuid',power_state:'running',nics:[nic]},{vm_id:101,vm_uuid:null,power_state:'stopped',nics:[nic]},{vm_id:102,vm_uuid:null,power_state:'running',nics:[{...nic,capture_state:'unsupported'}]}],connections:[]}}]});
function dom(data){return new JSDOM(context.render(data,now));}
test('all VMs inventory separates discovered, running, currently observed and shows cumulative counters',()=>{
  const d=dom(data());const text=d.window.document.body.textContent;
  assert.match(text,/VMs discovered: 3 · Running: 2 · Currently observed: 1/);assert.match(text,/101/);assert.match(text,/stopped/);assert.match(text,/unsupported/);assert.match(text,/RX \/ TX bytes: 10 \/ 20; packets: 1 \/ 2/);assert.match(text,/Firewall drops \(aggregate\): 4/);assert.match(text,/rental allocation is unresolved/);assert.match(text,/No recent packet observations/);assert.match(text,/2027-01-15T08:00:00.000Z/);d.window.close();
});
test('stale, capture failure, empty and unknown states never imply current coverage',()=>{
  for(const change of [s=>s.last_contact-=61,s=>s.status='stale',s=>s.snapshot.collection.packet_capture_ok=false]) {
    const value=data();change(value.sources[0]);const d=dom(value);assert.match(d.window.document.body.textContent,/Currently observed: 0/);assert.doesNotMatch(d.window.document.body.textContent,/sampled metadata available/);d.window.close();
  }
  for(const value of [undefined,{status:'unavailable'},{status:'configured',sources:[{id:'new',node:'node-1',status:'unknown',last_contact:null,snapshot:null}]}]){const d=dom(value);assert.match(d.window.document.body.textContent,/unknown/i);d.window.close();}
  const value=data();value.sources[0].snapshot.vms=[];const d=dom(value);assert.match(d.window.document.body.textContent,/No VM observations; inventory visibility unknown/);d.window.close();
  for(const value of [{status:'not_configured'},{status:'configured',sources:[]}])assert.match(context.render(value,now),/No Proxmox observers configured/);
});
test('all dynamic text including limited connection details is escaped',()=>{
  const hostile='<img src=x onerror="bad()"><script>bad()</script>';
  const value=data(),s=value.sources[0];s.id=hostile;s.node=hostile;s.snapshot.collection.limitations=[hostile];s.snapshot.vms[0].vm_uuid=hostile;s.snapshot.vms[0].nics=[{...nic,name:hostile,tap:hostile,capture_state:hostile}];s.snapshot.connections=[{vm_id:hostile,tap:hostile,protocol:hostile,src_ip:hostile,dst_ip:hostile,src_port:hostile,dst_port:hostile,packet_count:1,observed_at:now/1000,payload:'private-secret'}];
  const d=dom(value);assert.equal(d.window.document.querySelector('img,script'),null);assert.ok(d.window.document.body.textContent.includes(hostile));assert.doesNotMatch(d.window.document.body.textContent,/private-secret/);assert.ok(d.window.document.querySelector('details'));d.window.close();
});
test('Security no longer renders VM observer tables; VM Security owns collapsed heartbeat diagnostics',async()=>{
  const {HomeView}=await import('../netclaw-visual/modules/convergence/HomeView.js');
  const {vmSecurityHTML}=await import('../netclaw-visual/modules/convergence/VMSecurity.js');
  for(const embedded of [false,true]) {
    const context={cache:{security:{status:'unavailable',hostTelemetry:{status:'not_configured'},hypervisorTelemetry:data()},securityCases:{unavailable:true}},options:{embedded},securityState:()=>({}),securityDrafts:new Map(),incidentPending:new Set(),incidentErrors:new Map()};
    const d=new JSDOM(HomeView.prototype.htmlSecurity.call(context));
    assert.equal(d.window.document.querySelector('[aria-labelledby="proxmox-observer-title"]'),null);
    assert.equal(d.window.document.querySelector('[aria-labelledby="host-heartbeat-title"]'),null);d.window.close();
    const vmDoc=new JSDOM(vmSecurityHTML({site:'home',sources:[],hostTelemetry:{status:'not_configured'}},{site:'home'}));
    const card=vmDoc.window.document.querySelector('[aria-labelledby="host-heartbeat-title"]');
    assert.ok(card.closest('details'));assert.equal(card.closest('details').open,false);vmDoc.window.close();
  }
});

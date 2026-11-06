'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {JSDOM}=require('jsdom');
const dashboard=fs.readFileSync(path.join(__dirname,'views/pages/dashboard.ejs'),'utf8');
const renderer=fs.readFileSync(path.join(__dirname,'public/js/host-heartbeat.js'),'utf8');
const app=fs.readFileSync(path.join(__dirname,'public/js/app.js'),'utf8');
const agent={id:'enrollment-example',vm_id:'vm-example',status:'reporting',heartbeat_observed:1800000000,last_contact:1800000030,heartbeat:{collection_ok:true,queue_events:0,queue_bytes:0,dropped_events:3},pending_events:1,pending_limit:64,valid_until:'2028-01-01T00:00:00Z'};
function setup(){
  const dom=new JSDOM(dashboard,{runScripts:'outside-only',url:'https://example.invalid/'});
  dom.window.localStorage.setItem('guardian_token','synthetic-test-session');
  vm.runInContext(renderer,dom.getInternalVMContext());
  // Drive the existing refresh function explicitly, without starting dashboard timers.
  const listen=dom.window.document.addEventListener;
  dom.window.document.addEventListener=()=>{};
  vm.runInContext(app,dom.getInternalVMContext());
  dom.window.document.addEventListener=listen;
  return dom.window;
}
test('installed dashboard renders VM status via existing security response before firewall rendering',async()=>{
  const w=setup();const calls=[];
  w.API.fetch=async route=>{calls.push(route);return {hostTelemetry:{status:'configured',agents:[agent]}};};
  // A malformed/absent firewall section must not prevent heartbeat visibility.
  await w.updateSecurity();
  await new Promise(resolve=>setImmediate(resolve));
  const card=w.document.getElementById('host-heartbeat');
  assert.match(card.textContent,/vm-example/);assert.match(card.textContent,/Reporting/);assert.match(card.textContent,/Dropped: 3/);
  assert.match(card.textContent,/2027-01-15T08:00:00.000Z/);assert.match(card.textContent,/2027-01-15T08:00:30.000Z/);
  assert.match(card.textContent,/2028-01-01/);assert.deepEqual(calls,['/security']);
  w.API.fetch=async()=>null;w.updateSecurity();await new Promise(resolve=>setImmediate(resolve));
  assert.match(card.textContent,/status unavailable/);assert.doesNotMatch(card.textContent,/vm-example/);
  w.close();
});
test('renderer distinguishes all states and treats absent health data as unknown; strings stay text',()=>{
  const w=setup();const card=w.document.getElementById('host-heartbeat');
  for(const status of ['reporting','unknown','stale','degraded','inactive','revoked']){
    w.renderHostHeartbeat({status:'configured',agents:[{...agent,status,heartbeat:null,heartbeat_observed:null,last_contact:null}]});
    assert.match(card.textContent,new RegExp(status==='inactive'?'Inactive / expired':status,'i'));
    assert.match(card.textContent,/Events: Unknown/);assert.match(card.textContent,/Age unknown/);assert.doesNotMatch(card.textContent,/Reporting OK/);
  }
  for(const data of [{status:'not_configured',agents:[]},{status:'configured',agents:[]}]){w.renderHostHeartbeat(data);assert.match(card.textContent,/No VM heartbeat enrollments/);}
  for(const data of [undefined,{status:'unavailable'},{}]){w.renderHostHeartbeat(data);assert.match(card.textContent,/status unavailable/);}
  const hostile='<img src=x onerror="bad()"><script>bad()</script>';
  for(const data of [{status:'configured',agents:[{...agent,vm_id:hostile,id:hostile,reason:hostile}]},{status:'unavailable',reason:hostile}]){
    w.renderHostHeartbeat(data);assert.equal(card.querySelector('img,script'),null);assert.ok(card.textContent.includes(hostile));
  }
  assert.ok(w.document.querySelector('section[aria-labelledby="host-heartbeat-title"]'));
  w.close();
});

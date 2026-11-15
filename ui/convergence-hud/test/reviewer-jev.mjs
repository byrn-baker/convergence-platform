import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {randomUUID,webcrypto} from 'node:crypto';
import {JSDOM} from 'jsdom';
import {jevPanel} from '../reviewer/public/jev.js';
const html=readFileSync(new URL('../reviewer/public/index.html',import.meta.url),'utf8');
const entry={id:randomUUID(),site_id:'red',jev_enabled:true};
const user={id:'alice',name:'Alice',role:'operator'};
const tick=()=>new Promise(resolve=>setTimeout(resolve,25));
async function until(check) { const deadline=Date.now()+2000; while(!check() && Date.now()<deadline)await tick(); assert.ok(check(),'Expected async state'); }
function fixture(t,onCall,storage={}) {
  const dom=new JSDOM(html,{url:'https://review.test/'}),browser=dom.window;
  Object.defineProperty(browser,'crypto',{value:webcrypto});
  for(const [key,value] of Object.entries(storage))browser.sessionStorage.setItem(key,value);
  let losses=0;
  const calls=[];
  const panel=jevPanel({document:browser.document,browser,scopeLost:()=>{losses++;panel.clear();},
    request:async(path,body)=>{calls.push({path,body});return onCall(path,body);}});
  t.after(()=>{panel.clear();browser.close();});
  return {panel,browser,calls,losses:()=>losses,el:id=>browser.document.getElementById('jev-'+id)};
}
const receipt=(id,extra={})=>({operation_id:id,case_id:entry.id,site_id:'red',requested_by:'alice',requested_by_name:'Alice',state:'completed',participation:'reused',result:{status:'budget_exhausted',advisory_only:true,message:'<script>not executed</script>'},...extra});

test('JEV sends only one operation identity, renders canonical result and recovers without repeating submission',async t=>{
  let saved;
  const first=fixture(t,(_path,body)=>{saved=receipt(body.operationId);throw Error('Lost acknowledgement');});
  await first.panel.show(entry,user);
  first.el('request').click();await until(()=>first.calls.length===1 && /unknown/.test(first.el('status').textContent));
  assert.equal(first.calls.length,1);assert.deepEqual(Object.keys(first.calls[0].body),['operationId']);
  assert.match(first.el('status').textContent,/unknown/);
  const storage=Object.fromEntries(Object.keys(first.browser.sessionStorage).map(key=>[key,first.browser.sessionStorage.getItem(key)]));
  assert.equal(Object.keys(storage).length,1);assert.match(Object.keys(storage)[0],/^jev:/);
  assert.ok(!JSON.stringify(storage).includes(entry.id));
  const second=fixture(t,(path,body)=>{assert.equal(body,undefined);assert.ok(path.endsWith(saved.operation_id));return saved;},storage);
  await second.panel.show(entry,user);await tick();
  assert.equal(second.calls.length,1);
  assert.match(second.el('status').textContent,/Reused the retained assessment/);
  assert.match(second.el('result').textContent,/budget_exhausted/);
  assert.equal(second.el('result').querySelector('script'),null);
  second.panel.clear();assert.equal(second.el('result').textContent,'');
});

test('missing receipt permits only same-ID retry and an observed receipt never grants retry permission',async t=>{
  let id,found=false;
  const f=fixture(t,(_path,body)=>{
    if(body){id ||= body.operationId;assert.equal(body.operationId,id);if(!found)throw Error('lost');return receipt(id);}
    if(!found)throw Object.assign(Error('absent'),{status:404});
    return receipt(id);
  });
  await f.panel.show(entry,user);f.el('request').click();await until(()=>f.calls.length===1);
  f.el('check').click();await tick();assert.equal(f.el('retry').hidden,false);
  found=true;f.el('retry').click();await until(()=>/shared assessment request has finished/.test(f.el('status').textContent));
  assert.equal(f.calls.filter(c=>c.body).length,2);
  found=false;f.el('check').click();await tick();assert.equal(f.el('retry').hidden,true);
});

test('viewer, disabled feature and changed session cannot submit or display late JEV results',async t=>{
  let finish;
  const f=fixture(t,()=>new Promise(resolve=>{finish=resolve;}));
  await f.panel.show(entry,{...user,role:'viewer'});f.el('request').click();await tick();assert.equal(f.calls.length,0);
  await f.panel.show({...entry,jev_enabled:false},user);f.el('request').click();await tick();assert.equal(f.calls.length,0);
  await f.panel.show(entry,user);f.el('request').click();await until(()=>f.calls.length===1);
  const id=f.calls[0].body.operationId;f.panel.clear();finish(receipt(id));await tick();
  assert.equal(f.el('panel').hidden,true);assert.equal(f.el('result').textContent,'');
});

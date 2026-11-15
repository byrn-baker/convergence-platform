import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {HomeView} from '../modules/convergence/HomeView.js';
import {telemetryWorkspace} from '../reviewer/public/telemetry.js';
import {reviewerAssets} from '../reviewer/start.mjs';
import {telemetryTarget,parseTelemetryTarget} from '../reviewer/telemetry-contract.mjs';

const user = {id:'alice', name:'Alice', role:'viewer', sites:['red','blue']};
const wait = () => new Promise(resolve => setTimeout(resolve,30));
function setup(t, fetch) {
  const dom = new JSDOM('<section id="site-workspace" class="convergence-workspace"></section>',
    {url:'https://review.test/', pretendToBeVisual:true});
  const browser = dom.window;
  browser.Response = Response;
  let session = {csrf:'alice-session',user,capabilities:{telemetry:true}};
  const previous = Object.fromEntries(['document','window','localStorage'].map(key => [key,globalThis[key]]));
  Object.assign(globalThis,{document:browser.document,window:browser,localStorage:browser.localStorage});
  let scopeLosses = 0;
  const calls = [];
  browser.fetch = async (url, options) => { calls.push(url); return fetch(url, options); };
  const workspace = telemetryWorkspace({document:browser.document,browser,session:()=>session,
    scopeLost:()=>{ scopeLosses++; session = null; workspace.hide(); },openCase:()=>{},updateExpiry:()=>{},loadView:async()=>({HomeView})});
  t.after(()=>{workspace.hide();dom.window.close();for(const [key,value] of Object.entries(previous)) {
    if(value === undefined) delete globalThis[key]; else globalThis[key]=value;
  }});
  return {workspace,browser,calls,setSession:value=>{session=value;},scopeLosses:()=>scopeLosses};
}
const response = value => new Response(JSON.stringify(value),{headers:{'content-type':'application/json'}});
const health = {site:'red',healthScore:{value:97,status:'healthy'},wanLatency:{value:2},wanLoss:{value:0},alertCount:{firing:0},sources:{metrics:'available'}};

test('flow record transport preserves its address and bounded evidence interval', () => {
  const query = {address:'192.0.2.1',start:'2026-11-15T15:00:00Z',end:'2026-11-15T16:00:00Z'};
  const target = telemetryTarget('red','flows/records',query);
  assert.deepEqual(Object.fromEntries(new URLSearchParams(target.query)),query);
  assert.deepEqual(parseTelemetryTarget('/review/sites/red/telemetry/flows/records?'+target.query),target);
  assert.throws(()=>telemetryTarget('red','flows/records',target.query+'&address=192.0.2.2'));
  assert.throws(()=>telemetryTarget('red','flows/records',target.query+'&site=blue'));
});

test('read-only Internet and Wi-Fi views distinguish unavailable sources from empty observations', () => {
  const wan = data => HomeView.prototype.htmlWanCases.call({site:'red',cache:{wanCases:data}});
  for (const data of [{site:'red',events:[],unavailable:true},{site:'blue',events:[]}]) {
    assert.match(wan(data),/history unavailable/);
    assert.doesNotMatch(wan(data),/No recorded WAN incidents/);
  }
  assert.match(wan({site:'red',events:[]}),/No recorded WAN incidents/);
  const wifi = HomeView.prototype.htmlWifi.call({cache:{wifi:{site:'red',unavailable:true,clients:{},txRetries:[]}}});
  assert.match(wifi,/observations unavailable/);
  assert.doesNotMatch(wifi,/No unifi_|Waiting/);
});

test('shared site workspace renders telemetry, hides host capabilities and clears on sign-out', async t => {
  const f = setup(t,url => {
    assert.match(url,/^\/review\/sites\/red\/telemetry\//);
    if (url.endsWith('/health')) return response(health);
    if (url.endsWith('/wifi')) return response({site:'red',clients:{wireless:4},txRetries:[]});
    if (url.endsWith('/security/vms')) return response({site:'red',status:'not_configured',sources:[],hostTelemetry:{status:'unavailable',agents:[]}});
    throw Error('Unexpected path '+url);
  });
  await f.workspace.show(); await wait();
  const root=f.browser.document.getElementById('site-workspace');
  assert.match(root.textContent,/97/);
  assert.equal(root.querySelector('[data-home-sub="models"]').hidden,true);
  root.querySelector('[data-home-sub="vm-security"]').click(); await wait();
  assert.match(root.textContent,/VM Security/);
  assert.match(root.textContent,/No observers configured|unavailable|Insufficient evidence/);
  assert.equal(f.browser.localStorage.length,0);
  assert.ok(f.calls.every(path=>path.startsWith('/review/')));
  f.setSession(null); f.workspace.hide();
  assert.equal(root.textContent,'');
  assert.equal(root.hidden,true);
});

test('telemetry never releases an old users delayed body and revocation clears the workspace', async t => {
  let finish;
  const f=setup(t,()=>new Promise(resolve=>{finish=resolve;}));
  const pending=f.workspace.request('/api/home/health?site=red');
  f.setSession({csrf:'bob-session',user:{...user,id:'bob'},capabilities:{telemetry:true}});
  finish(response(health));
  await assert.rejects(pending,error=>error.status===401);
  await assert.rejects(f.workspace.request('/api/home/health?site=other'),error=>error.status===403);
  f.browser.fetch=async()=>new Response('{}',{status:401});
  await assert.rejects(f.workspace.request('/api/home/health?site=red'),error=>error.status===401);
  assert.equal(f.scopeLosses(),1);
});

test('static asset graph contains only approved view dependencies and no host modules', () => {
  const assets=reviewerAssets();
  assert.ok(assets.has('/site-assets/HomeView.js'));
  for(const [path,asset] of assets) {
    if(!path.endsWith('.js') || !path.startsWith('/site-assets/'))continue;
    for(const match of asset.content.toString().matchAll(/from\s+['"]([^'"]+)['"]/g)) {
      const target=new URL(match[1],'https://review.test'+path).pathname;
      assert.ok(assets.has(target),'Missing dependency '+target);
    }
  }
  assert.ok([...assets.keys()].every(path=>!/(server|terminal|model-config|owner-access)/.test(path)));
  assert.equal(telemetryTarget('red','security/vms').resource,'security/vms');
  for(const [resource,query] of [['models',''],['health','site=blue'],['events','limit=1&limit=2']]) {
    assert.throws(()=>telemetryTarget('red',resource,query));
  }
});


test('a granted second site remains selected across refresh and does not persist in browser storage', async t => {
  const f=setup(t,url=>{
    const site=url.split('/')[3];
    if(url.endsWith('/health'))return response({...health,site});
    if(url.endsWith('/wifi'))return response({site,clients:{wireless:4},txRetries:[]});
    if(url.endsWith('/security/vms'))return response({site,status:'not_configured',sources:[]});
    if(url.endsWith('/security'))return response({site,status:'unavailable',intrusionDetection:{sensors:[]}});
    if(url.includes('/security/history'))return response({site,unavailable:true});
    if(url.includes('/events'))return response({site,events:[]});
    if(url.endsWith('/devices'))return response({site,unavailable:true,reason:'Unavailable'});
    if(url.includes('/flows'))return response({site,unavailable:true});
    if(url.endsWith('/isp/providers'))return response({providers:[]});
    if(url.endsWith('/alerts'))return response({site,firing:[],resolved:[]});
    throw Error(url);
  });
  await f.workspace.show();await wait();
  const picker=f.browser.document.getElementById('home-site-select');
  picker.value='blue';picker.dispatchEvent(new f.browser.Event('change',{bubbles:true}));
  await wait();
  await f.workspace.show();await wait();
  assert.equal(f.browser.document.getElementById('home-site-select').value,'blue');
  assert.equal(f.browser.localStorage.length,0);
});

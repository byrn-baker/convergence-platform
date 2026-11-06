const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
function routes(file, dependencies, globals={}){
 dependencies={'../lib/hypervisorStatus':{hypervisorStatus:async()=>({status:'not_configured',sources:[]})},'../db':{query:()=>assert.fail('unexpected database query'),DbUnavailableError:class extends Error{}},'../lib/hostStatus':{hostStatus:async()=>({status:'not_configured',agents:[]})},...dependencies};
 const handlers={},middleware=[];
 const router={use:h=>middleware.push(h)};
 for(const m of ['get','post','patch'])router[m]=(p,h)=>{handlers[m+' '+p]=h};
 vm.runInNewContext(fs.readFileSync(__dirname+'/src/routes/'+file,'utf8'),{
 require:n=>n==='express'?{Router:()=>router}:['../lib/captureContext','../lib/securityAssessment','../lib/ispReport','../lib/trafficContext','../lib/securityHistory'].includes(n)?require('./src/lib/'+n.split('/').at(-1)):dependencies[n],module:{exports:{}},process:{env:{}},console,Date,AbortSignal,Promise,URLSearchParams,...globals,
 });
 return {handlers,middleware};
}
function response(){return {code:200,status(c){this.code=c;return this},json(b){this.body=b;return this}}}
test('case routes deny cross-site lookup and viewer mutation',async()=>{
 const {handlers,middleware}=routes('events.js',{'../db':{query:async(sql,args)=>({rows:args.includes('home')?[]:[{id:'other-case',site_id:'other'}]}),DbUnavailableError:class extends Error{},dbStatus:()=>({available:true})}});
 let r=response();await handlers['get /:id']({params:{id:'other-case'},site:'home'},r);assert.equal(r.code,404);
 r=response();middleware[0]({method:'PATCH',user:{role:'viewer'}},r,()=>assert.fail('viewer write passed'));assert.equal(r.code,403);
 r=response();await handlers['patch /:id']({params:{id:'other-case'},site:'home',body:{status:'resolved'}},r);assert.equal(r.code,404);
});
test('JEV review uses the authorized case and site, ignoring caller overrides',async()=>{
 const sent=[],queries=[];
 let exists=true;
 const {handlers,middleware}=routes('events.js',{'../db':{query:async(sql,args)=>{queries.push({sql,args});return {rows:exists?[{id:'case'}]:[]}},DbUnavailableError:class extends Error{},dbStatus:()=>({available:true})}}, {
  process:{env:{ALERT_RECEIVER_URL:'http://receiver/webhook',ALERT_RECEIVER_TOKEN:'internal-token'}},
  fetch:async(url,options)=>{sent.push({url,options});return {status:202,json:async()=>({job_id:'job'})}},
 });
 let r=response();middleware[0]({method:'POST',user:{role:'viewer'}},r,()=>assert.fail('viewer review passed'));assert.equal(r.code,403);
 r=response();await handlers['post /:id/jev-review']({params:{id:'case'},site:'home',body:{site:'other',event_id:'other',state:'private'}},r);
 assert.equal(r.code,202);assert.equal(sent[0].url,'http://receiver/jev-review');
 assert.deepEqual(JSON.parse(sent[0].options.body),{event_id:'case',site:'home'});
 assert.equal(sent[0].options.headers.Authorization,'Bearer internal-token');
 assert.ok(queries[0].sql.includes('site_id = $2'));assert.equal(queries[0].args[1],'home');
 exists=false;r=response();await handlers['post /:id/jev-review']({params:{id:'case'},site:'other'},r);
 assert.equal(r.code,404);assert.equal(sent.length,1);
});
test('saving an advisory review preserves evidence and case disposition without queuing learning',async()=>{
 const calls=[];
 const {handlers}=routes('events.js',{'../db':{query:async(sql,args)=>{calls.push({sql,args});return {rows:[{id:'case',status:'logged'}]}},DbUnavailableError:class extends Error{},dbStatus:()=>({available:true})}}, {
  fetch:async()=>assert.fail('review must not queue learning'),process:{env:{ALERT_RECEIVER_URL:'http://receiver'}},
 });
 const review={version:1,advisory_only:true,status:'ok',assessment_id:'a'};
 let r=response();await handlers['patch /:id']({params:{id:'case'},site:'home',body:{jev_review:review}},r);
 assert.equal(r.code,200);assert.equal(calls.length,1);
 assert.match(calls[0].sql,/SET jev_review = \$1 WHERE/);
 assert.doesNotMatch(calls[0].sql,/source_evidence =|status =|severity =/);
 assert.deepEqual(calls[0].args[0],review);assert.equal(calls[0].args[2],'home');
 r=response();await handlers['patch /:id']({params:{id:'case'},site:'home',body:{jev_review:{status:'ok'}}},r);
 assert.equal(r.code,400);assert.equal(calls.length,1);
});
test('diary and case detail expose the same capture context without leaking full evidence into the diary',async()=>{
 const evidence={labels:{source:'suricata',sensor_id:'home-ids-01'},annotations:{
  description:'Passive IDS observation; not proof of compromise. '+JSON.stringify({vlan:[100,100]})}};
 const event={id:'case',alert_name:'SuricataIntrusion',source_evidence:evidence};
 const calls=[];
 const {handlers}=routes('events.js',{'../db':{query:async(sql,args)=>{calls.push({sql,args});return {rows:[event]}},DbUnavailableError:class extends Error{},dbStatus:()=>({available:true})}});
 for(const path of ['/', '/escalated', '/:id']){
  const r=response();await handlers['get '+path]({site:'home',query:{limit:30},params:{id:'case'}},r);
  assert.equal(r.code,200);
  const row=path==='/:id'?r.body:r.body.events[0];
  assert.deepEqual(row.capture_context,{sensor_id:'home-ids-01',status:'recorded',vlan_tags:[100,100]});
  assert.equal(row.source_evidence,path==='/'?undefined:evidence);
 }
 assert.ok(calls.every(c=>c.sql.includes('site_id = $')&&c.args.includes('home')));
 assert.equal(event.capture_context,undefined,'read projection must not rewrite the stored case');
});
test('security aggregates structured logs and ranks observed sources',async()=>{
 const queries=[];
 const {handlers}=routes('security.js',{'../lib/config':{LOKI_URL:'http://logs',promqlLabelValue:s=>s},'../lib/idsStatus':{idsStatus:async()=>({status:'not_configured'})}}, {
  fetch:async url=>{
   const q=new URL(url).searchParams.get('query');queries.push(q);
   const rows=q.startsWith('topk')?[{metric:{attributes_src_ip:'one'},value:[0,'2']},{metric:{attributes_src_ip:'two'},value:[0,'9']}]:[{value:[0,'11']}];
   return {ok:true,json:async()=>({status:'success',data:{result:rows}})};
  },
 });
 const r=response();await handlers['get /']({site:'home'},r);
 assert.equal(r.body.status,'active');assert.equal(r.body.topSources[0].ip,'two');
 assert.equal(r.body.intrusionDetection.status,'not_configured');
 assert.ok(queries.every(q=>q.includes('attributes_appname="filterlog"')&&q.includes('site="home"')));
});
test('security does not report zero attacks when logs are unavailable',async()=>{
 const {handlers}=routes('security.js',{'../lib/config':{LOKI_URL:'http://logs',promqlLabelValue:s=>s},'../lib/idsStatus':{idsStatus:async()=>({status:'active',sensors:[{id:'sensor'}]})}}, {
  fetch:async()=>{throw new Error('offline')},
 });
 const r=response();await handlers['get /']({site:'home'},r);
 assert.equal(r.body.status,'unavailable');assert.equal(r.body.blockedConnections.lastHour,null);
 assert.equal(r.body.intrusionDetection.status,'active');
});
test('Alertmanager failure reports unknown counts and degraded source',async()=>{
 const q={instantQuery:async()=>[{value:[0,'99']}],extractScalar:r=>Number(r[0].value[1]),queryAlertmanager:async()=>{throw Error('down')}};
 const {handlers}=routes('health.js',{'../lib/queryEngine':q,'../lib/config':{getSiteConfig:()=>({}),getPrimaryProvider:()=>null,getThresholdStatus:()=> 'healthy',promqlLabelValue:s=>s}});
 const r=response();await handlers['get /']({site:'home'},r);
 assert.equal(r.body.alertCount.firing,null);assert.equal(r.body.unavailable,true);assert.equal(r.body.sources.alertmanager,'unavailable');
});

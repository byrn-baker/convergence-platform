const {test} = require('node:test');
const assert = require('node:assert/strict');
const {flowSummary,flowEvidence} = require('./src/lib/flowSummary');
const {trafficContext} = require('./src/lib/trafficContext');

test('diary traffic metadata comes only from the complete IDS envelope without payload fields',()=>{
 const raw={src_ip:'192.0.2.1',dest_ip:'2001:db8::1',src_port:1234,dest_port:443,proto:'TCP',payload:'PRIVATE',alert:{signature_id:123,signature:'Rule',action:'allowed'}};
 const result=trafficContext({annotations:{description:'Passive IDS observation; not proof of compromise. '+JSON.stringify(raw)}});
 assert.equal(result.source_ip,'192.0.2.1');assert.equal(result.destination_ip,'2001:db8::1');assert.equal(result.signature_id,123);
 assert.ok(!JSON.stringify(result).includes('PRIVATE'));assert.equal(trafficContext({annotations:{description:JSON.stringify(raw)}}),null);
 assert.equal(trafficContext({annotations:{description:'Passive IDS observation; not proof of compromise. {'}}),null);
});
test('flow aggregation is scoped to authorized site/exporters, uses full-window server aggregates and reports sampling honestly',async()=>{
 const queries=[];
 const d=await flowSummary('cottage',{flows:{exporters:['192.0.2.1','192.0.2.2']}},{range:'1h',exporter:'192.0.2.1'},async q=>{
   queries.push(q);
   if(q.includes('by (sampling_rate)'))return [{sampling_rate:'100',records:'10'}];
   if(q.includes('max(_time)'))return [{bytes:'12000',packets:'20',records:'10',last_received:'2026-10-25T10:00:00Z'}];
   return [{src_addr:'10.0.0.2',dst_addr:'198.51.100.1',bytes:'12000',packets:'20',records:'10'},{src_addr:'<img>',dst_addr:'bad',bytes:'5'}];
 },Date.parse('2026-10-25T10:00:00Z'));
 assert.equal(d.total.bytes,12000);assert.equal(d.sampling[0].rate,100);assert.equal(d.sources.length,1);assert.equal(d.conversations.length,1);
 assert.equal(d.start,'2026-10-25T09:00:00.000Z');assert.equal(d.end,'2026-10-25T10:00:00.000Z');
 assert.ok(queries.every(q=>q.includes('site:="cottage"')&&q.includes('sampler_address:in("192.0.2.1")')&&q.includes('job:="netflow"')));
 assert.match(d.basis,/sampling is not extrapolated/);assert.ok(d.limitations.some(s=>s.includes('NAT')));
});
test('flow inputs fail closed; missing configuration and empty archive are distinct from backend failure',async()=>{
 const cfg={flows:{exporters:['192.0.2.1']}},never=()=>assert.fail('unexpected query');
 assert.equal((await flowSummary('home',{}, {},never)).status,'not_configured');
 await assert.rejects(flowSummary('home',cfg,{range:'1000d'},never),/Choose/);
 await assert.rejects(flowSummary('home',cfg,{exporter:'192.0.2.2'},never),/not configured/);
 const empty=await flowSummary('home',cfg,{},async()=>[]);assert.equal(empty.status,'empty');assert.equal(empty.total.bytes,null);
 await assert.rejects(flowSummary('home',cfg,{},async()=>{throw Error('offline')}),/offline/);
});
test('flow evidence limits query scope, refuses arbitrary selectors and projects only traffic fields',async()=>{
 const config={flows:{exporters:['192.0.2.1']}},input={address:'10.0.0.1',start:'2026-10-25T10:00:00Z',end:'2026-10-25T11:00:00Z'};
 let query;
 const data=await flowEvidence('cottage',config,input,async q=>{query=q;return [
  {src_addr:input.address,dst_addr:'198.51.100.1',sampler_address:'192.0.2.1',bytes:'1234',private_payload:'must not export'},
  {src_addr:input.address,sampler_address:'192.0.2.9'},
  {src_addr:'10.0.0.9',sampler_address:'192.0.2.1'}];},Date.parse('2026-10-25T12:00:00Z'));
 assert.equal(data.records.length,1);assert.equal(data.records[0].private_payload,undefined);assert.match(query,/limit 100/);assert.match(query,/site:="cottage"/);
 for(const bad of [{address:'* OR site:other'},{start:'yesterday'},{end:'2026-10-27T10:00:00Z'},{exporter:'192.0.2.9'}])
  await assert.rejects(flowEvidence('cottage',config,{...input,...bad},()=>assert.fail('invalid query executed'),Date.parse('2026-10-25T12:00:00Z')));
});
test('demo and host coverage remain explicit in summaries and evidence, even when the archive is empty',async()=>{
 const inputs={address:'192.0.2.10',start:'2026-10-25T10:00:00Z',end:'2026-10-25T11:00:00Z'};
 for(const mode of ['demo','host']) {
  const config={flows:{exporters:['127.0.0.1'],coverage:{mode,description:'Selected source'}}};
  const summary=await flowSummary('lab',config,{},async()=>[],Date.parse('2026-10-25T12:00:00Z'));
  const evidence=await flowEvidence('lab',config,inputs,async()=>[],Date.parse('2026-10-25T12:00:00Z'));
  assert.equal(summary.status,'empty');assert.equal(summary.coverage.mode,mode);
  assert.deepEqual(evidence.coverage,summary.coverage);assert.equal(summary.coverage.detail,'Selected source');
  assert.ok(summary.limitations.includes(summary.coverage.description));
  assert.match(summary.coverage.description,mode==='demo'?/do not describe activity/:/not whole-network/);
 }
 const unknown=await flowSummary('lab',{flows:{coverage:{mode:'invalid',description:'x'.repeat(1000)}}},{},()=>assert.fail('must not query'));
 assert.equal(unknown.status,'not_configured');assert.equal(unknown.coverage.mode,'network');assert.equal(unknown.coverage.detail.length,240);
 assert.doesNotMatch(unknown.coverage.description,/firewall/i);
});

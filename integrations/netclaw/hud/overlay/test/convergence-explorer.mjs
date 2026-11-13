import {test} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {diaryHTML,groupDiary,caseHandoff,MAX_SELECTION} from '../modules/convergence/DiaryExplorer.js';
import {flowHTML,flowHandoff} from '../modules/convergence/FlowExplorer.js';
import {HomeView} from '../modules/convergence/HomeView.js';
const fixtures=[0,1,2].map(n=>({id:`case-${n}`,timestamp:`2026-10-25T10:0${n}:00Z`,alert_name:'SuricataIntrusion',message:'<img src=x> scan',
 traffic_context:{signature_id:n===2?2:1,signature:'Signature '+(n===2?2:1),source_ip:'10.0.0.1',destination_ip:`198.51.100.${n}`},
 ip_context:{destination:{ip:`198.51.100.${n}`,scope:'external',asn:n===2?'AS2':'AS1',as_name:'Example network',country:'United States',country_code:'US'}},
 capture_context:{status:'recorded',vlan_tags:[13],sensor_id:'sensor'}}));
test('diary groups by signature, destination context, IP, country or observed tags and retains each case',()=>{
 assert.deepEqual(groupDiary(fixtures,'signature').map(g=>g.events.length),[2,1]);
 assert.deepEqual(groupDiary(fixtures,'network').map(g=>g.events.length),[2,1]);
 assert.equal(groupDiary(fixtures,'destination').length,3);assert.equal(groupDiary(fixtures,'country').length,1);assert.equal(groupDiary(fixtures,'vlan').length,1);
 assert.equal(groupDiary(fixtures,'signature','AS2')[0].events[0].id,'case-2');
 const d=new JSDOM(diaryHTML({events:fixtures},{mode:'network',canInvestigate:true,selected:new Set(['case-1'])})).window.document;
 assert.equal(d.querySelectorAll('[data-diary-canvas]').length,3);assert.equal(d.querySelectorAll('[data-diary-group-canvas]').length,2);
 assert.equal(d.querySelectorAll('input:checked').length,1);assert.equal(d.querySelector('img'),null);assert.match(d.body.textContent,/not all detections/);
});
test('Canvas receives only selected same-site cases, bounded excerpts, exact IDs and a read-only investigation brief',()=>{
 const many=Array.from({length:100},(_,i)=>({...fixtures[0],id:`case-${i}`,message:'x'.repeat(40000)}));
 const context=caseHandoff(many,'cottage','2026-10-25','group'.repeat(10000));
 assert.equal(context.case_ids.length,MAX_SELECTION);assert.ok(JSON.stringify(context,null,2).length<12000);
 assert.ok(context.case_sources.every(s=>s.endsWith('site=cottage')));assert.match(context.task,/untrusted evidence/);assert.match(context.task,/formal read-only/);
 let called;
 HomeView.prototype.investigateDiary.call({site:'cottage',diaryRange:'24h',cache:{events:{events:fixtures}},readTimes:{events:'now'},options:{investigate:(...args)=>called=args}},['case-1'],'selected');
 assert.deepEqual(called[1].case_ids,['case-1']);assert.equal(called[1].site,'cottage');
});
test('case handoff includes compact JEV advice and transport context without raw review payloads',()=>{
 const event={...fixtures[0],traffic_context:{...fixtures[0].traffic_context,protocol:'TCP',source_port:54000,destination_port:80},
  security_assessment:{bucket:'attention',next_check:'Verify application and transport'},
  jev_review:{status:'ok',assessment_id:'review-1',model:'jev-example',
   answers:{next_check:{answer:'local_evidence'},evidence_strength:{score:0.66}},
   questions:{next_check:{criteria:{local_evidence:'Retrieve the complete local alert'}},evidence_strength:{criteria:['Insufficient','Weak','Corroborated','Verified']}},
   input_summary:{raw_review_marker:'excluded'},unused_payload:'excluded'}};
 const handoff=caseHandoff([event],'home','now','Selected Security review case');
 const excerpt=handoff.cases[0];
 assert.equal(excerpt.signature,'Signature 1');assert.equal(excerpt.destination_port,80);
 assert.equal(excerpt.next_check,'Verify application and transport');
 assert.deepEqual(excerpt.jev_advisory,{advisory_only:true,status:'ok',assessment_id:'review-1',model:'jev-example',
  next_check:'Retrieve the complete local alert',evidence_strength:0.66,rubric:['Insufficient','Weak','Corroborated','Verified']});
 assert.doesNotMatch(JSON.stringify(handoff),/excluded|input_summary|unused_payload/);
 assert.match(handoff.task,/redact credentials and authorization headers/);
 assert.match(handoff.task,/not a verdict or threat probability/);
 const oversized={...event,jev_review:{...event.jev_review,questions:{evidence_strength:{criteria:Array(100).fill('x'.repeat(40000))}}}};
 const bounded=caseHandoff(Array.from({length:20},(_,i)=>({...oversized,id:`case-${i}`})),'home','now','selection');
 assert.equal(bounded.case_ids.length,20);assert.ok(bounded.excerpts_omitted>0);
 assert.ok(JSON.stringify(bounded,null,2).length<12000);
});
test('traffic renders measured volume, context and unavailable state; deep dive preserves window/exporter scope',()=>{
 const raw={site:'cottage',status:'active',start:'2026-10-25T10:00:00Z',end:'2026-10-25T11:00:00Z',exporters:['192.0.2.1'],total:{bytes:5000,records:2},sampling:[{rate:1}],
 sources:[{src_addr:'10.0.0.1',bytes:5000,records:2,packets:3}],destinations:[],conversations:[],basis:'Exported bytes',limitations:['Coverage limited']};
 const d=new JSDOM(flowHTML(raw,{range:'1h',canInvestigate:true})).window.document;
 assert.match(d.body.textContent,/5 KB/);assert.equal(d.querySelector('[data-flow-canvas]').dataset.flowCanvas,'sources:0');
 const ctx=flowHandoff(raw,'sources',0);assert.equal(ctx.start,raw.start);assert.deepEqual(ctx.exporters,raw.exporters);assert.equal(ctx.observation.src_addr,'10.0.0.1');
 assert.match(flowHTML({unavailable:true}),/totals are unknown/);assert.doesNotMatch(flowHTML({unavailable:true}),/0 B/);
});
test('demo and host coverage is visible, escaped, and retained in investigation context',()=>{
 const raw={site:'demo',status:'active',start:'2026-10-25T10:00:00Z',end:'2026-10-25T11:00:00Z',exporters:['127.0.0.1'],
  sources:[{src_addr:'192.0.2.10',bytes:1200}],coverage:{mode:'demo',detail:'<img src=x onerror=alert(1)>'}};
 const document=new JSDOM(flowHTML(raw,{canInvestigate:true})).window.document;
 assert.match(document.body.textContent,/Synthetic demonstration/);
 assert.match(document.body.textContent,/do not describe activity on your home network/);
 assert.equal(document.querySelector('img'),null);
 const context=flowHandoff(raw,'sources',0);
 assert.equal(context.coverage.mode,'demo');assert.match(context.task,/Do not infer activity or threats/);
 assert.match(context.coverage.description,/Synthetic demonstration/);
 const host=new JSDOM(flowHTML({...raw,coverage:{mode:'host'},status:'empty'})).window.document;
 assert.match(host.body.textContent,/Local interface traffic/);assert.match(host.body.textContent,/not whole-network coverage/);
 assert.match(host.body.textContent,/No retained flow records/);
 assert.doesNotMatch(flowHTML({...raw,coverage:undefined}),/Firewall flow exports/);
});

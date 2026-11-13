import {test} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {securityModel,securityExplorerHTML} from '../modules/convergence/SecurityExplorer.js';
import {HomeView} from '../modules/convergence/HomeView.js';

const event=(id,bucket='review',changes={})=>({id,timestamp:'2026-10-26T00:00:00Z',alert_name:'SuricataIntrusion',status:'logged',message:'Observation',
  security_assessment:{bucket,signature:'ET INFO Outgoing Basic Auth Base64 HTTP Password detected unencrypted',reason:'Corroborate evidence',next_check:'Check the service',rule_priority:1},
  traffic_context:{signature_id:1,signature:'ET INFO Outgoing Basic Auth Base64 HTTP Password detected unencrypted',source_ip:'10.0.13.2',destination_ip:'198.51.100.5',protocol:'TCP',destination_port:80},
  capture_context:{status:'recorded',vlan_tags:[13],sensor_id:'sensor'},
  ip_context:{source:{ip:'10.0.13.2',scope:'internal'},destination:{ip:'198.51.100.5',scope:'external',asn:'AS64501',as_name:'Example Network',country:'Example country',country_code:'XX'}},...changes});
const cases=[event('urgent','attention'),event('expected','reviewed'),event('confirmed','attention',{security_assessment:{bucket:'attention',disposition:{kind:'confirmed_threat',reason:'Verified endpoint evidence'},signature:'Confirmed case'}}),
  event('diagnostic','diagnostic',{traffic_context:{signature_id:2,signature:'SURICATA STREAM Test'},capture_context:{status:'not_recorded',vlan_tags:[]}}),event('unknown','invalid',{capture_context:null})];
const data={site:'home',events:cases,limit:100};
const telemetry={status:'active',intrusionDetection:{status:'active',sensors:[{id:'sensor',status:'active',coverage:[{vlan:13,status:'observed'},{vlan:301,status:'unverified'}]}]}};
const document=html=>new JSDOM(html).window.document;

test('summary distinguishes reviewer-confirmed findings, review priority and diagnostic cases',()=>{
  const m=securityModel(data);
  assert.deepEqual(m.counts,{all:5,attention:1,review:1,confirmed:1,diagnostic:1,reviewed:1});
  assert.equal(m.selected.id,'confirmed');assert.equal(m.groups.length,2);
  assert.equal(m.selectedGroup.outcomes.size,4);
  assert.equal(securityModel(data,{filter:'attention'}).filtered[0].id,'urgent');
  assert.equal(securityModel(data,{filter:'confirmed'}).filtered[0].id,'confirmed');
  assert.equal(cases[0].id,'urgent'); // sorting cannot mutate source evidence
});
test('facets intersect, preserve original case identity and do not infer VLAN membership',()=>{
  assert.deepEqual(securityModel(data,{vlan:'13'}).filtered.map(e=>e.id),['urgent','expected','confirmed']);
  assert.deepEqual(securityModel(data,{vlan:'untagged'}).filtered.map(e=>e.id),['diagnostic']);
  assert.deepEqual(securityModel(data,{vlan:'unknown'}).filtered.map(e=>e.id),['unknown']);
  assert.equal(securityModel(data,{network:'asn:AS64501',country:'XX',filter:'reviewed',search:'198.51.100.5'}).selected.id,'expected');
  assert.equal(securityModel(data,{network:'asn:other'}).selected,undefined);
  assert.equal(securityModel(data,{group:'network',selectedId:'urgent'}).selected.id,'urgent');
  assert.equal(securityModel(data,{group:'none'}).groups.length,5);
  assert.equal(securityModel(data,{group:'vlan'}).groups.length,3);
});
test('detail pane keeps the chosen case and review form beside the grouped list; hostile evidence stays text',()=>{
  const malicious=event('case-"<img src=x>','attention',{message:'<script>bad()</script>',root_cause:'<img src=y>',investigation_notes:'<script>findings</script>',
    jev_review:{status:'ok',assessment_id:'review-1',answers:{evidence_strength:{score:0.66}},questions:{evidence_strength:{criteria:['Insufficient','Weak','Corroborated','Verified']}}}});
  const d=document(securityExplorerHTML({site:'home',events:[malicious]},telemetry,{site:'home',range:'24h',canInvestigate:true}));
  assert.equal(d.querySelector('img'),null);assert.equal(d.querySelector('script'),null);
  assert.equal(d.querySelector('[data-security-detail]').dataset.securityDetail,malicious.id);
  assert.equal(d.querySelector('[data-security-canvas]').dataset.securityCanvas,malicious.id);
  assert.equal(d.querySelector('[data-security-canvas]').disabled,false);
  assert.ok(d.querySelector('[data-security-case] textarea').required);
  assert.equal(d.querySelectorAll('.security-case-table tbody tr').length,1);
  assert.match(d.body.textContent,/Potential credential exposure/);assert.match(d.body.textContent,/Not established by this IDS case/);
  assert.match(d.body.textContent,/not all packets or detections/);assert.match(d.body.textContent,/0.66 on a 0–3 rubric/);
  assert.match(d.body.textContent,/Stored investigation findings/);assert.equal(d.querySelectorAll('[data-security-vlan]').length,2);
});
test('missing and cross-site case data never presents zero counts as a successful read',()=>{
  for(const raw of [undefined,{}, {events:null},{unavailable:true,events:[]},{site:'other',events:cases}]){
    const d=document(securityExplorerHTML(raw,telemetry,{site:'home'}));
    assert.equal(d.querySelector('.security-summary'),null);assert.equal(d.querySelector('[data-security-canvas]'),null);
  }
  const empty=document(securityExplorerHTML({events:[]},telemetry,{site:'home'}));
  assert.match(empty.body.textContent,/No matching cases/);assert.equal(empty.querySelectorAll('.security-metric').length,5);
  const d=document(securityExplorerHTML({...data,limit:5},telemetry,{site:'home'}));
  assert.match(d.body.textContent,/Result limit reached/);assert.match(d.body.textContent,/Mixed case outcomes/);
  assert.equal(d.querySelector('[data-security-canvas]').disabled,true);
});
test('group Canvas handoff uses only filtered same-site members, latest 20, and never changes a case',()=>{
  const events=Array.from({length:25},(_,i)=>event(`case-${i}`,'review',{timestamp:new Date(Date.UTC(2026,9,26,0,i)).toISOString()}));
  events.push(event('different-vlan','review',{capture_context:{status:'recorded',vlan_tags:[301]}}));
  let call;
  const context={site:'home',cache:{securityCases:{site:'home',events}},securityRange:'24h',readTimes:{securityCases:'now'},
    securityState:()=>({vlan:'13',group:'signature'}),options:{investigate:(...args)=>call=args}};
  const before=JSON.stringify(events);
  HomeView.prototype.investigateSecurityGroup.call(context,'case-0');
  assert.equal(call[1].case_ids.length,20);assert.equal(call[1].case_ids[0],'case-24');assert.ok(!call[1].case_ids.includes('different-vlan'));
  assert.match(call[2],/site=home&type=security&range=24h/);assert.equal(JSON.stringify(events),before);
  call=null;context.cache.securityCases.site='other';HomeView.prototype.investigateSecurityGroup.call(context,'case-0');assert.equal(call,null);
});
test('a response for a previous time window cannot populate the new selection',async()=>{
  const context={site:'home',subview:'security',securityRange:'1h',cache:{},readTimes:{},options:{embedded:true},
    renderSubview(){},discoverSites:async()=>{},syncTopbarMetrics(){},fetch:async path=>{
      if(path==='/status')return {configured:true};
      if(path.startsWith('/events?')){assert.match(path,/range=1h/);context.securityRange='6h';return {...data,range:'1h'};}
      return {};
    }};
  await HomeView.prototype.refresh.call(context);
  assert.equal(context.cache.securityCases,undefined);assert.equal(context.readTimes.securityCases,undefined);
});

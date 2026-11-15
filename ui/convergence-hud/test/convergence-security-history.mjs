import {test} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {securityHistoryHTML,securityCasesPath} from '../modules/convergence/SecurityHistory.js';
import {HomeView} from '../modules/convergence/HomeView.js';

const history={site:'home',range:'1h',start:'2026-10-26T00:00:00Z',end:'2026-10-26T01:00:00Z',total:150,bucket_seconds:150,
  buckets:[{start:'2026-10-26T00:00:00Z',end:'2026-10-26T00:02:30Z',count:140},{start:'2026-10-26T00:02:30Z',end:'2026-10-26T00:05:00Z',count:0}],
  countries:[{code:'US',count:140},{code:'SG',count:5},{code:'internal',count:3},{code:'unknown',count:2}]};
const document=html=>new JSDOM(html).window.document;
test('history shows full-window totals, accessible geography and zero-count intervals without inferring missing locations',()=>{
  const d=document(securityHistoryHTML(history,{site:'home',range:'1h',historyCountry:'US',historyWindow:history.buckets[0]}));
  assert.match(d.body.textContent,/150/);assert.match(d.body.textContent,/Internal destinations/);assert.match(d.body.textContent,/Country unavailable/);
  assert.equal(d.querySelectorAll('[data-security-history-start]').length,2);
  assert.match(d.querySelectorAll('[data-security-history-start]')[1].getAttribute('aria-label'),/0 saved cases/);
  const country=d.querySelector('path[data-security-history-country="US"]');
  assert.equal(country.getAttribute('tabindex'),'0');assert.equal(country.getAttribute('aria-pressed'),'true');
  assert.equal(d.querySelector('path[data-security-history-country="internal"]'),null);
  assert.equal(d.querySelector('[data-security-history-clear]').textContent,'Clear history selection');
  assert.match(d.body.textContent,/not risk/);assert.match(d.body.textContent,/100-case cap/);
});
test('unavailable, wrong-site or wrong-range history does not present counts or interactive geography',()=>{
  for(const raw of [undefined,{unavailable:true},{...history,site:'other'},{...history,range:'7d'},{}]){
    const d=document(securityHistoryHTML(raw,{site:'home',range:'1h'}));
    assert.equal(d.querySelector('svg'),null);assert.equal(d.querySelector('[data-security-history-start]'),null);
  }
  const d=document(securityHistoryHTML({...history,countries:[{code:'<img src=x>',count:1}]},{site:'home',range:'1h'}));
  assert.equal(d.querySelector('img'),null);
});
test('case retrieval and Canvas references preserve exact historical interval and country before the case cap',()=>{
  const path=securityCasesPath('cottage','7d',history.buckets[0],'SG');
  const params=new URL('http://test'+path).searchParams;
  assert.equal(params.get('site'),'cottage');assert.equal(params.get('start'),history.buckets[0].start);
  assert.equal(params.get('end'),history.buckets[0].end);assert.equal(params.get('country'),'SG');assert.equal(params.get('range'),null);
  const range=new URL('http://test'+securityCasesPath('home','6h',null,'unknown')).searchParams;
  assert.equal(range.get('range'),'6h');assert.equal(range.get('country'),'unknown');
});
test('changing historical selection during a read cannot publish stale cases or history',async()=>{
  let requested='';
  const context={site:'home',subview:'security',securityRange:'1h',securityHistoryCountry:'US',cache:{},readTimes:{},options:{embedded:true},
    renderSubview(){},discoverSites:async()=>{},syncTopbarMetrics(){},fetch:async path=>{
      if(path==='/status')return {configured:true};
      if(path.startsWith('/events?')){requested=path;context.securityHistoryCountry='SG';return {site:'home',events:[{id:'stale'}]};}
      if(path.startsWith('/security/history'))return history;
      return {};
    }};
  await HomeView.prototype.refresh.call(context);
  assert.match(requested,/country=US/);assert.equal(context.cache.securityCases,undefined);assert.equal(context.cache.securityHistory,undefined);
});

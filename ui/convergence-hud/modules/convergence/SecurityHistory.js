import {WORLD_REGIONS} from './world-regions.js';

const esc=value=>String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const when=value=>new Date(value).toLocaleString();
const names=new Map(WORLD_REGIONS.map(r=>[r.code,r.name]));
const regionNames=new Intl.DisplayNames(['en'],{type:'region'});
export const countryName=code=>code==='internal'?'Internal destinations':code==='unknown'?'Country unavailable':/^[A-Z]{2}$/.test(code)?regionNames.of(code)||names.get(code)||code:code;

export function securityCasesPath(site,range,window,country,embedded=true) {
  const query=new URLSearchParams({site,type:'security'});
  if(embedded){
    if(window){query.set('start',window.start);query.set('end',window.end);}
    else query.set('range',range || '24h');
    if(country)query.set('country',country);
  }
  query.set('limit','100');return `/events?${query}`;
}

/** History stays at the full time-window scope; drilldowns reload the case queue. */
export function securityHistoryHTML(data,state={}) {
  const rangeControl=`<label class="security-history-range">Time window<select data-security-range>${['1h','6h','24h','7d'].map(r=>`<option ${r===(state.range||'24h')?'selected':''}>${r}</option>`).join('')}</select></label>`;
  const valid=data && !data.unavailable && data.site===state.site && data.range===state.range &&
    Array.isArray(data.buckets) && Array.isArray(data.countries) && Number.isFinite(data.total);
  if(!valid)return `<section class="security-history security-context-card" role="status"><div class="security-history-heading"><h3>Historical activity</h3>${rangeControl}</div><p>${data?'History unavailable. Historical counts are unknown; the recent case queue remains separate.':'Loading saved-case history…'}</p></section>`;
  const max=Math.max(1,...data.buckets.map(b=>b.count)),countries=new Map(data.countries.map(c=>[c.code,c.count]));
  const maxCountry=Math.max(1,...countries.values());
  const bars=data.buckets.map(b=>{
    const selected=state.historyWindow && Date.parse(state.historyWindow.start)<Date.parse(b.end) && Date.parse(state.historyWindow.end)>Date.parse(b.start);
    const label=`${when(b.start)} – ${when(b.end)} · ${b.count} saved cases`;
    return `<button type="button" data-security-history-start="${esc(b.start)}" data-security-history-end="${esc(b.end)}" aria-pressed="${selected}" aria-label="${esc(label)}" title="${esc(label)}"><span style="height:${b.count?Math.max(3,b.count/max*100):0}%" aria-hidden="true"></span><small>${b.count}</small></button>`;
  }).join('');
  const paths=WORLD_REGIONS.map(r=>{
    const count=countries.get(r.code)||0,selected=state.historyCountry===r.code;
    return `<path d="${r.path}" class="${count?'has-cases':''} ${selected?'selected':''}" style="--country-opacity:${count ? .25+.75*Math.sqrt(count/maxCountry) : 0}" ${count?`data-security-history-country="${r.code}" role="button" tabindex="0" aria-pressed="${selected}" aria-label="${esc(countryName(r.code))} · ${count} saved cases"`:''}><title>${esc(countryName(r.code))} · ${count} saved cases</title></path>`;
  }).join('');
  const rows=data.countries.map(c=>`<button type="button" data-security-history-country="${esc(c.code)}" aria-pressed="${state.historyCountry===c.code}"><span>${esc(countryName(c.code))}</span><strong>${c.count}</strong></button>`).join('');
  return `<section class="security-history" aria-label="Saved case history">
    <div class="security-history-heading"><div><p class="eyebrow">Full window · saved observations</p><h3>Historical activity <strong>${data.total.toLocaleString()}</strong></h3></div>${rangeControl}</div>
    <p class="home-muted">All saved IDS cases in ${esc(data.range)}, beyond the queue’s 100-case cap. Case timestamps are not packet timestamps. Empty bars mean no saved cases; sensor coverage is separate.</p>
    <div class="security-timeline" role="group" aria-label="Select a time interval to load its cases">${bars}</div>
    <div class="security-timeline-axis"><span>${esc(when(data.start))}</span><span>${data.bucket_seconds/60} minute intervals · select a bar to investigate</span><span>${esc(when(data.end))}</span></div>
    <div class="security-geography"><div><h4>Destination country context</h4><svg viewBox="0 0 360 165" class="security-world-map" aria-label="Destination countries with saved IDS cases">${paths}</svg><p class="home-muted">Country shading shows saved case counts, not risk. No location is inferred for internal or unknown destinations. Small countries may appear only in the list.</p><details><summary>Map source &amp; scope</summary><p class="home-muted">Public-domain <a href="https://www.naturalearthdata.com/about/terms-of-use/" target="_blank" rel="noopener noreferrer">Natural Earth</a> country outlines. Approximate boundaries; geography does not establish the traffic’s origin, actor or intent. Enrichment is the context saved with each case, not a fresh lookup.</p></details></div>
      <div class="security-history-countries" role="group" aria-label="Load cases by destination country">${rows||'<p class="home-muted">No saved cases in this window.</p>'}</div></div>
    <p class="home-muted">History charts retain the full window. Selecting a country or interval reloads matching cases before applying the queue cap. Review-priority, VLAN, network and search filters below apply only to that loaded queue.</p>
    ${state.historyWindow||state.historyCountry?`<div class="security-history-selection" role="status"><span>Queue scope: ${state.historyWindow?`${esc(when(state.historyWindow.start))} → ${esc(when(state.historyWindow.end))}`:'Full history window'}${state.historyCountry?` · ${esc(countryName(state.historyCountry))}`:''}</span><button type="button" data-security-history-clear>Clear history selection</button></div>`:''}
  </section>`;
}

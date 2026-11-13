import {caseIPContext, ipContextHTML} from './IPContext.js';
import {caseCaptureContext, captureContextHTML, observedVlanHTML} from './CaptureContext.js';
import {assessmentHTML} from './IncidentReview.js';
import {jevReviewHTML} from './JevReview.js';
import {diaryGroup, endpoints} from './DiaryExplorer.js';
import {securityHistoryHTML} from './SecurityHistory.js';

const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const when = value => value!=null && Number.isFinite(new Date(value).getTime()) ? new Date(value).toLocaleString() : 'Time unavailable';
const stamp = event => Date.parse(event.timestamp) || 0;
export const SECURITY_GROUPS = [['signature','Similar signatures'],['network','Destination network / ASN'],['country','Destination country'],['vlan','Observed VLAN'],['none','Individual cases']];
const FILTERS = [['all','All cases'],['attention','Investigate promptly'],['review','Review context'],['confirmed','Reviewer-confirmed'],['diagnostic','Protocol / capture'],['reviewed','Reviewed exceptions']];
const labels = Object.fromEntries(FILTERS);
const tones = {attention:'urgent',confirmed:'confirmed',review:'review',diagnostic:'diagnostic',reviewed:'reviewed'};

export function caseOutcome(event) {
  if (event.security_assessment?.disposition?.kind === 'confirmed_threat') return 'confirmed';
  return Object.hasOwn(tones,event.security_assessment?.bucket) ? event.security_assessment.bucket : 'review';
}
const rank = e => ['confirmed','attention','review','diagnostic','reviewed'].indexOf(caseOutcome(e));
function title(event) {
  const signature = event.traffic_context?.signature || event.security_assessment?.signature || '';
  if (/Basic Auth|Cleartext.*Password|Password.*unencrypted/i.test(signature)) return 'Potential credential exposure';
  return event.security_assessment?.family || (caseOutcome(event)==='diagnostic' ? 'Protocol or capture anomaly' : 'Signature match to review');
}
function vlanKey(event) {
  const c=caseCaptureContext(event);
  return c?.status==='recorded' ? [...new Set(c.vlan_tags)].map(String) : [c?.status==='not_recorded'?'untagged':'unknown'];
}
function matches(event,state) {
  if(state.filter && state.filter!=='all' && caseOutcome(event)!==state.filter)return false;
  if(state.vlan && !vlanKey(event).includes(state.vlan))return false;
  if(state.network && diaryGroup(event,'network')[0]!==state.network)return false;
  if(state.country && diaryGroup(event,'country')[0]!==state.country)return false;
  const search=String(state.search || '').trim().toLowerCase();
  return !search || JSON.stringify([event.id,event.message,event.traffic_context,event.security_assessment?.signature,caseIPContext(event),caseCaptureContext(event)]).toLowerCase().includes(search);
}

/** Counts are scoped to this API result. Group membership never changes case outcomes. */
export function securityModel(data,state={}) {
  const events=Array.isArray(data?.events)?data.events:[];
  const filtered=events.filter(e=>matches(e,state));
  const mode=SECURITY_GROUPS.some(([id])=>id===state.group)?state.group:'signature', map=new Map();
  for(const e of filtered){
    const [key,label]=diaryGroup(e,mode);
    if(!map.has(key))map.set(key,{key,label,events:[]});
    map.get(key).events.push(e);
  }
  const groups=[...map.values()];
  for(const g of groups){
    g.events.sort((a,b)=>rank(a)-rank(b)||stamp(b)-stamp(a));
    const dates=g.events.map(e=>Date.parse(e.timestamp)).filter(Number.isFinite);
    g.latest=dates.length?Math.max(...dates):null;g.first=dates.length?Math.min(...dates):null;
    g.outcomes=new Set(g.events.map(caseOutcome));
  }
  groups.sort((a,b)=>rank(a.events[0])-rank(b.events[0])||b.latest-a.latest);
  const selectedGroup=groups.find(g=>g.events.some(e=>e.id===state.selectedId)) || groups[0];
  const selected=selectedGroup?.events.find(e=>e.id===state.selectedId) || selectedGroup?.events[0];
  return {events,filtered,groups,selectedGroup,selected,mode,
    counts:Object.fromEntries(FILTERS.map(([key])=>[key,key==='all'?events.length:events.filter(e=>caseOutcome(e)===key).length]))};
}

function badge(event){const outcome=caseOutcome(event);return `<span class="security-status security-status-${tones[outcome]}"><i aria-hidden="true"></i>${labels[outcome]}</span>`;}
function endpoint(event,side){
  const ip=caseIPContext(event)?.[side],address=endpoints(event)[side];
  return `<strong>${esc(address || 'Address unavailable')}</strong><small>${esc(ip?.scope==='internal'?'Internal network':[ip?.as_name,ip?.asn,ip?.country].filter(Boolean).join(' · ') || 'Network context unavailable')}</small>`;
}
function rowEndpoints(event,grouped){
  const ep=endpoints(event),dest=caseIPContext(event)?.destination;
  const context=dest?.scope==='internal'?'Internal destination':[dest?.asn,dest?.as_name].filter(Boolean).join(' · ') || 'Destination context unavailable';
  return `<div class="security-row-endpoints"><strong>${esc(ep.source || 'Unknown source')}</strong><span aria-hidden="true">→</span><strong>${esc(ep.destination || 'Unknown destination')}</strong></div><small class="security-row-network" title="${esc(context)}">${esc(context)}</small><small>VLAN · ${observedVlanHTML(caseCaptureContext(event))}${grouped?' · representative case':''}</small>`;
}
function rankings(events,mode){
  const map=new Map();
  for(const e of events){const [key,label]=diaryGroup(e,mode);if(!map.has(key))map.set(key,{key,label,count:0});map.get(key).count++;}
  return [...map.values()].sort((a,b)=>b.count-a.count||a.label.localeCompare(b.label)).slice(0,4);
}
function rankingHTML(events,mode,label,active){
  const rows=rankings(events,mode),max=rows[0]?.count || 1;
  return `<section class="security-context-card"><h3>${label}</h3><p class="home-muted">Case counts in this result</p>${rows.map(r=>`<button type="button" class="security-rank" data-security-${mode}="${esc(r.key)}" aria-pressed="${active===r.key}"><span><span>${esc(r.label)}</span><strong>${r.count}</strong></span><span class="security-rank-track" aria-hidden="true"><span style="width:${r.count/max*100}%"></span></span></button>`).join('') || '<p class="home-muted">No destination observations.</p>'}</section>`;
}
function coverageHTML(telemetry,state){
  const ids=telemetry?.intrusionDetection,sensors=ids?.sensors || [];
  return `<section class="security-context-card security-coverage"><h3>Capture coverage</h3><p class="home-muted">Sensor observations · last 15 minutes</p>
    ${sensors.length?sensors.map(s=>`<div class="security-sensor"><div><strong>${esc(s.id)}</strong><span class="home-badge">${esc(s.status || 'Unknown')}</span></div><div class="security-vlans">${(s.coverage || []).map(v=>`<button type="button" data-security-vlan="${esc(v.vlan)}" aria-pressed="${state.vlan===String(v.vlan)}" class="${v.status==='observed'&&s.status==='active'?'observed':'unverified'}" title="Filter cases carrying this VLAN tag; sensor state: ${esc(s.status)}"><span>VLAN ${esc(v.vlan)}</span><small>${esc(v.status)}</small></button>`).join('') || '<span class="home-muted">VLAN coverage unavailable</span>'}</div></div>`).join(''):'<p class="home-muted">Sensor coverage unavailable.</p>'}
    <p class="home-muted">Observed tags show capture visibility. Same-VLAN paths may still be unobserved.</p></section>`;
}

function detailHTML(model,state){
  const e=model.selected,g=model.selectedGroup;
  if(!e)return `<aside class="security-detail security-detail-empty" aria-label="Case evidence"><span aria-hidden="true">◎</span><h3>No matching cases</h3><p>Change the filters to inspect another observation.</p></aside>`;
  const key=`${state.site}:${e.id}`,traffic=e.traffic_context || {},capture=caseCaptureContext(e);
  const draft=state.drafts?.get(key) || {kind:e.security_assessment?.disposition?.kind,reason:e.security_assessment?.disposition?.reason};
  return `<aside class="security-detail" aria-labelledby="security-detail-title" data-security-detail="${esc(e.id)}">
    <div class="security-detail-heading"><p class="eyebrow">Selected case · evidence</p>${badge(e)}<h3 id="security-detail-title" tabindex="-1">${esc(title(e))}</h3><p class="home-muted">${esc(when(e.timestamp))} · ${esc(e.status || 'Unknown investigation state')}</p></div>
    <div class="home-security-case-actions"><button type="button" class="primary" data-security-canvas="${esc(e.id)}" ${state.canInvestigate?'':'disabled'}>Investigate in Canvas ↗</button>${g.events.length>1?`<button type="button" data-security-group-canvas="${esc(e.id)}" ${state.canInvestigate?'':'disabled'}>Deep dive ${g.events.length>20?'latest 20':'group'} (${Math.min(20,g.events.length)}) ↗</button>`:''}</div>
    <p class="home-muted">Prepare the evidence in Canvas, review the draft, then send.</p>
    ${g.events.length>1?`<label class="security-case-picker">Case in this group · ${g.events.length} observations<select data-security-case-picker>${g.events.map(item=>`<option value="${esc(item.id)}" ${item.id===e.id?'selected':''}>${esc(when(item.timestamp))} · ${esc(endpoints(item).source || 'Unknown')} → ${esc(endpoints(item).destination || 'Unknown')} · ${esc(labels[caseOutcome(item)])}</option>`).join('')}</select></label><p class="home-muted">Group first seen ${esc(when(g.first))} · last seen ${esc(when(g.latest))}. Each case keeps its own review.</p>`:''}
    <div class="security-endpoint-path"><div><span>Source</span>${endpoint(e,'source')}</div><span class="security-direction" aria-label="to">→</span><div><span>Destination</span>${endpoint(e,'destination')}</div></div>
    <dl class="security-facts"><div><dt>Transport / ports</dt><dd>${esc(traffic.protocol || 'Unknown')} · ${esc(traffic.source_port ?? '?')} → ${esc(traffic.destination_port ?? '?')}</dd></div><div><dt>Observed VLAN</dt><dd>${observedVlanHTML(capture)}</dd></div><div><dt>Rule priority</dt><dd>${esc(e.security_assessment?.rule_priority ?? 'Not recorded')}</dd></div><div><dt>Firewall verdict</dt><dd>Not established by this IDS case</dd></div></dl>
    <section class="security-evidence"><h4>Matched signature</h4><p>${esc(traffic.signature || e.security_assessment?.signature || 'Not recorded')}</p><small class="home-muted">Signature ID: ${esc(traffic.signature_id ?? 'Not recorded')}</small></section>
    ${assessmentHTML(e,{form:true,draft,busy:state.incidentPending?.has(key),error:state.incidentErrors?.get(key)})}
    ${e.root_cause || e.investigation_notes?`<details class="security-investigation-notes"><summary>Stored investigation findings</summary>${e.root_cause?`<p>${esc(e.root_cause)}</p>`:''}${e.investigation_notes?`<pre>${esc(e.investigation_notes)}</pre>`:''}<p class="home-muted">Stored findings require review against their cited evidence.</p></details>`:''}
    ${jevReviewHTML(e,state.jevPending?.has(key),state.jevErrors?.get(key))}
    ${captureContextHTML(capture)}${ipContextHTML(caseIPContext(e))}
    <details><summary>Case reference and observation</summary><code>${esc(e.id)}</code><p>${esc(e.message)}</p></details>
  </aside>`;
}

export function securityExplorerHTML(data,telemetry,state={}){
  const loading=!data,unavailable=data && (data.unavailable || !Array.isArray(data.events) || (data.site && data.site!==state.site));
  const model=securityModel(loading || unavailable?null:data,state),ids=telemetry?.intrusionDetection;
  const header=`<div class="home-panel-header"><div><p class="eyebrow">Security operations</p><h2>Network activity &amp; investigations</h2></div><span class="home-badge">Passive IDS</span></div>
    <div class="security-telemetry"><span><i class="${ids?.status==='active'?'observed':'unverified'}" aria-hidden="true"></i>IDS feed <strong>${esc(ids?.status || 'Unknown')}</strong></span><span>Firewall telemetry <strong>${esc(telemetry?.status || 'Unknown')}</strong></span><span>Last case read ${esc(state.readAt?when(state.readAt):'unavailable')}</span></div>`;
  const history=securityHistoryHTML(state.history,state);
  if(loading || unavailable)return header+history+`<div class="security-unavailable" role="status"><h3>${loading?'Loading security cases…':'Security cases unavailable'}</h3><p>${loading?'Reading saved IDS observations.':'Case counts and review coverage are unknown. Refresh to retry.'}</p></div>`+coverageHTML(telemetry,state);
  const filters=FILTERS.slice(1).map(([id,label])=>`<button type="button" class="security-metric security-metric-${tones[id]}" data-security-filter="${id}" aria-pressed="${state.filter===id}"><span>${label}</span><strong>${model.counts[id]}</strong><small>${id==='confirmed'?'Explicit case review':id==='reviewed'?'Expected activity / false positive':id==='diagnostic'?'Capture or protocol leads':'Cases to assess'}</small></button>`).join('');
  const groups=model.groups.map(g=>{
    const e=g.events[0],active=g===model.selectedGroup;
    return `<tr class="${active?'selected':''}"><td><button type="button" class="security-case-select" data-security-select="${esc(e.id)}" aria-pressed="${active}" aria-controls="security-detail-title">${badge(e)}<strong>${esc(model.mode==='signature'||model.mode==='none'?title(e):g.label)}</strong><small>${esc(model.mode==='signature'||model.mode==='none'?(e.traffic_context?.signature || e.security_assessment?.signature || e.message || 'Signature unavailable'):`${new Set(g.events.map(item=>item.traffic_context?.signature || item.security_assessment?.signature || item.id)).size} signatures in this group`)}</small></button></td><td>${rowEndpoints(e,g.events.length>1)}</td><td><strong>${g.events.length}</strong><small>${g.outcomes.size>1?'Mixed case outcomes':labels[caseOutcome(e)]}</small><small>Last ${esc(when(g.latest))}</small></td></tr>`;
  }).join('');
  const vlanOptions=[...new Set(model.events.flatMap(vlanKey))].sort((a,b)=>Number(a)-Number(b));
  return header+history+`<p class="security-scope">Latest ${model.events.length} saved IDS cases within ${esc(state.range || '24h')}${state.historyWindow||state.historyCountry?' · history selection applied':''} · capped at ${esc(data.limit || 100)}. ${model.events.length>=(data.limit || 100)?'<strong>Result limit reached; narrow the time window.</strong>':''} Counts describe these cases, not all packets or detections.</p>
    <div class="security-summary" role="group" aria-label="Security review summary">${filters}</div>
    <div class="security-context-grid">${rankingHTML(model.events,'network','Destination networks',state.network)}${rankingHTML(model.events,'country','Destination countries',state.country)}${coverageHTML(telemetry,state)}</div>
    <div class="home-explorer-controls security-controls"><label>Group by<select data-security-group>${SECURITY_GROUPS.map(([id,label])=>`<option value="${id}" ${model.mode===id?'selected':''}>${label}</option>`).join('')}</select></label><label>Observed VLAN<select data-security-vlan-select><option value="">All observed tags</option>${[...new Set([...vlanOptions,...(state.vlan?[state.vlan]:[])])].map(v=>`<option value="${esc(v)}" ${state.vlan===v?'selected':''}>${esc(v==='untagged'?'Not recorded':v==='unknown'?'Unavailable':v==='0'?'Priority tag 0':`VLAN ${v}`)}</option>`).join('')}</select></label><label class="security-search">Find in these cases<input data-security-search value="${esc(state.search || '')}" maxlength="200" placeholder="IP, signature, organization or case ID"></label><button type="button" data-security-reset>Clear filters</button></div>
    <div class="security-list-heading"><h3>Security review queue</h3><span>${model.filtered.length} matching cases · ${model.groups.length} ${model.mode==='none'?'rows':'groups'}</span></div>
    <div class="security-active-filters" role="group" aria-label="Active case filters"><button type="button" data-security-filter="all" aria-pressed="${!state.filter || state.filter==='all'}">All cases · ${model.events.length}</button>${state.filter&&state.filter!=='all'?`<span>${esc(labels[state.filter])}</span>`:''}${state.network?`<button type="button" data-security-network="">Clear destination network ×</button>`:''}${state.country?`<button type="button" data-security-country="">Clear country ×</button>`:''}</div>
    <div class="security-investigation-layout"><div class="security-case-list"><div class="home-table-wrap"><table class="home-table security-case-table"><thead><tr><th>Review priority / observation</th><th>Source → destination</th><th>Cases / last seen</th></tr></thead><tbody>${groups || '<tr><td colspan="3">No matching cases. Change the filters to inspect other observations.</td></tr>'}</tbody></table></div><p class="home-muted">Grouping suggests related observations; it does not establish one incident. Priority is not a threat probability. Country and ASN are network context.</p></div>${detailHTML(model,state)}</div>`;
}

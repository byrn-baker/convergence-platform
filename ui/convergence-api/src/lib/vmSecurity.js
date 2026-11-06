'use strict';
const {isIP} = require('node:net');
const object = v => v !== null && typeof v === 'object' && !Array.isArray(v);
const text = v => typeof v === 'string' && v.length > 0 && v.length <= 512 && !/[\x00-\x1f]/.test(v);
const number = v => typeof v === 'number' && Number.isFinite(v) && v > 0 && v <= 253402300799;
const count = v => Number.isSafeInteger(v) && v >= 0;
const positive = v => count(v) && v > 0;
const uuid = v => typeof v === 'string' && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(v);
const nullable = check => v => v === null || check(v);
const bool = v => typeof v === 'boolean';
const array = (v, max) => Array.isArray(v) && v.length <= max;
const unavailable = () => ({status:'unavailable', unavailable:true, sources:[], reason:'VM security evidence is unavailable; current activity is unknown'});
function need(ok) { if (!ok) throw Error('Invalid VM security evidence'); }
function project(value, checks) {
  need(object(value));
  return Object.fromEntries(Object.entries(checks).map(([key, check]) => { need(check(value[key])); return [key,value[key]]; }));
}
function sourceProjection(value, site, generatedAt) {
  const s=project(value,{id:text,site:v=>v===site,node:text,status:v=>['reporting','stale','unknown'].includes(v),last_contact:nullable(number),observed_at:nullable(number)});
  need(s.last_contact===null || s.last_contact<=generatedAt+30);
  need(s.observed_at===null || s.observed_at<=generatedAt+30);
  s.coverage=project(value.coverage,{fresh:bool,sampled:v=>v===true,inventory_ok:bool,packet_capture_ok:bool,
    capture_drops_total:nullable(count),capture_drops_recent_delta:nullable(count),events_discarded_total:nullable(count),events_discarded_recent_delta:nullable(count),
    counter_delta_seconds:nullable(number),snapshot_count:count, snapshot_span_seconds:v=>typeof v==='number'&&Number.isFinite(v)&&v>=0, max_snapshot_gap_seconds:nullable(v=>typeof v==='number'&&Number.isFinite(v)&&v>=0),history_count:count,history_limited:bool,missing_uuid_vm_count:count,excluded_identity_events:count,
    metadata_count:count,metadata_cap_reached:bool,policy_configured:bool,allowlisted_tuple_count:count,
    limitations:v=>array(v,96)&&v.every(text)});
  s.summary=project(value.summary,{finding_count:count,affected_vm_count:count,findings_truncated:bool,assessment:v=>['review','no_patterns_observed','insufficient_evidence'].includes(v)});
  need(array(value.vms,256));let nicCount=0;
  s.vms=value.vms.map(vm=>{
    const out=project(vm,{vm_id:positive,vm_uuid:nullable(uuid),power_state:v=>['running','stopped','unknown'].includes(v),capture_state:v=>['observing','partial','unavailable','stopped'].includes(v),finding_count:count});
    need(array(vm.nics,1024)&&(nicCount+=vm.nics.length)<=1024);
    out.nics=vm.nics.map(n=>project(n,{name:v=>typeof v==='string'&&/^net\d{1,3}$/.test(v),tap:v=>v===`tap${out.vm_id}i${n.name.slice(3)}`,ifindex:nullable(positive),capture_state:v=>['observing','unsupported','absent','error'].includes(v)}));
    need(new Set(out.nics.map(n=>n.name)).size===out.nics.length);
    return out;
  });
  need(new Set(s.vms.map(vm=>vm.vm_id)).size===s.vms.length);
  need(array(value.findings,100));
  s.findings=value.findings.map(f=>{
    const out=project(f,{id:v=>typeof v==='string'&&/^[a-f0-9]{20}$/.test(v),kind:v=>['port_scan','network_sweep','protected_network_contact'].includes(v),severity:v=>v==='review',vm_id:positive,vm_uuid:uuid,first_seen:number,last_seen:number,caveat:text});
    need(out.first_seen<=out.last_seen && out.last_seen<=generatedAt+30 && out.first_seen>=generatedAt-330);
    const vm=s.vms.find(v=>v.vm_id===out.vm_id&&v.vm_uuid===out.vm_uuid);need(!!vm);
    out.evidence=project(f.evidence,{unique_tcp_five_tuples:count,unique_destination_ports:v=>count(v)&&v<=65536,unique_destination_ips:count,
      destination_ip:nullable(v=>!!isIP(v)),destination_port:nullable(v=>count(v)&&v<=65535)});
    need(array(f.evidence.examples,5));
    out.evidence.examples=f.evidence.examples.map(e=>{
      const ex=project(e,{src_ip:v=>!!isIP(v),dst_ip:v=>!!isIP(v),src_port:nullable(v=>count(v)&&v<=65535),dst_port:nullable(v=>count(v)&&v<=65535),
        protocol:v=>['tcp','udp','icmp','icmpv6'].includes(v),tap:text,ifindex:positive,observed_at:number});
      need(isIP(ex.src_ip)===isIP(ex.dst_ip));
      need(ex.observed_at>=out.first_seen && ex.observed_at<=out.last_seen);
      need(vm.nics.some(n=>n.tap===ex.tap&&n.ifindex===ex.ifindex));
      need(['tcp','udp'].includes(ex.protocol) ? ex.src_port!==null && ex.dst_port!==null : ex.src_port===null && ex.dst_port===null);
      return ex;
    });
    if(out.kind==='port_scan')need(out.evidence.unique_destination_ports>=20);
    if(out.kind==='network_sweep')need(out.evidence.unique_destination_ips>=20);
    return out;
  });
  need(new Set(s.findings.map(f=>f.id)).size===s.findings.length);
  need(s.summary.finding_count>=s.findings.length && s.summary.affected_vm_count<=s.vms.length);
  need(s.summary.findings_truncated || s.summary.finding_count===s.findings.length);
  need(s.vms.reduce((sum,vm)=>sum+vm.finding_count,0)===s.summary.finding_count);
  const fresh=s.status==='reporting'&&s.coverage.fresh&&s.last_contact!==null&&s.observed_at!==null&&Math.min(s.last_contact,s.observed_at)>=Date.now()/1000-60;
  s.coverage.fresh=!!fresh;
  if(!fresh){s.status=s.last_contact===null?'unknown':'stale';if(!s.summary.finding_count)s.summary.assessment='insufficient_evidence';}
  return s;
}
async function readJSON(response) {
  let size=0;const chunks=[];
  for await(const chunk of response.body){size+=chunk.length;need(size<=4*1024*1024);chunks.push(Buffer.from(chunk));}
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}
function assess(raw, site, synthetic) {
  const data=project(raw,{status:v=>['configured','not_configured'].includes(v),generated_at:number,window_seconds:v=>v===300});
  need(data.generated_at<=Date.now()/1000+30 && data.generated_at>=Date.now()/1000-120);
  need(array(raw.sources,256));
  data.sources=raw.sources.map(s=>({...sourceProjection(s,site,data.generated_at),kind:synthetic?'synthetic':'proxmox'}));
  need(data.status!=='not_configured'||!data.sources.length);
  need(new Set(data.sources.map(s=>s.id)).size===data.sources.length && new Set(data.sources.map(s=>s.node)).size===data.sources.length);
  return {...data,site,synthetic};
}
// VM_SECURITY_DEMO=1: a labelled synthetic assessment, so the view can be tried without a
// hypervisor. It covers every pattern, a quiet and a stopped VM, and a stale source, and
// passes the same validation as live data.
function demoResponse(site) {
  const now=Math.floor(Date.now()/1000), first=now-240, last=now-30;
  const uuid=id=>`00000000-0000-4000-8000-${String(id).padStart(12,'0')}`;
  const nic=(id,state='observing')=>({name:'net0',tap:`tap${id}i0`,ifindex:state==='observing'?id:null,capture_state:state});
  const example=(id,dst,port,i)=>({src_ip:`192.0.2.${id-90}`,dst_ip:dst,src_port:40000+i,dst_port:port,protocol:'tcp',tap:`tap${id}i0`,ifindex:id,observed_at:first+30*(i+1)});
  const caveat='Synthetic demonstration finding; no real traffic was observed.';
  const finding=(hash,kind,id,evidence,examples)=>({id:hash,kind,severity:'review',vm_id:id,vm_uuid:uuid(id),first_seen:first,last_seen:last,caveat,evidence:{...evidence,examples}});
  const findings=[
    finding('d0000000000000000001','port_scan',101,{unique_tcp_five_tuples:48,unique_destination_ports:48,unique_destination_ips:1,destination_ip:'198.51.100.20',destination_port:null},
      [22,23,80,443,3389].map((port,i)=>example(101,'198.51.100.20',port,i))),
    finding('d0000000000000000002','network_sweep',102,{unique_tcp_five_tuples:64,unique_destination_ports:1,unique_destination_ips:64,destination_ip:null,destination_port:445},
      [1,2,3,4,5].map((host,i)=>example(102,`198.51.100.${host}`,445,i))),
    finding('d0000000000000000003','protected_network_contact',103,{unique_tcp_five_tuples:3,unique_destination_ports:1,unique_destination_ips:1,destination_ip:'203.0.113.10',destination_port:5432},
      [0,1,2].map(i=>example(103,'203.0.113.10',5432,i))),
  ];
  const vm=(id,count,power='running',capture='observing',nicState='observing')=>({vm_id:id,vm_uuid:uuid(id),power_state:power,capture_state:capture,finding_count:count,nics:[nic(id,nicState)]});
  const limitation='Synthetic demonstration data: no virtual machine traffic was observed.';
  const coverage=(fresh,extra)=>({fresh,sampled:true,inventory_ok:fresh,packet_capture_ok:fresh,capture_drops_total:fresh?0:null,capture_drops_recent_delta:fresh?0:null,
    events_discarded_total:fresh?0:null,events_discarded_recent_delta:fresh?0:null,counter_delta_seconds:fresh?240:null,snapshot_count:fresh?9:0,snapshot_span_seconds:fresh?240:0,
    max_snapshot_gap_seconds:fresh?30:null,history_count:fresh?420:0,history_limited:false,missing_uuid_vm_count:0,excluded_identity_events:0,metadata_count:fresh?180:0,
    metadata_cap_reached:false,policy_configured:fresh,allowlisted_tuple_count:0,limitations:[limitation,...extra]});
  return {status:'configured',generated_at:now,window_seconds:300,sources:[
    {id:'demo-hypervisor',site,node:'demo-node-1',status:'reporting',last_contact:now-5,observed_at:now-5,
     coverage:coverage(true,['Packet metadata is sampled; counts describe the retained sample only.']),
     summary:{finding_count:3,affected_vm_count:3,findings_truncated:false,assessment:'review'},findings,
     vms:[vm(101,1),vm(102,1),vm(103,1),vm(104,0),vm(105,0,'stopped','stopped','absent')]},
    {id:'demo-stale-hypervisor',site,node:'demo-node-2',status:'stale',last_contact:now-900,observed_at:now-900,
     coverage:coverage(false,['No snapshot in the last five minutes; current activity is unknown.']),
     summary:{finding_count:0,affected_vm_count:0,findings_truncated:false,assessment:'insufficient_evidence'},findings:[],
     vms:[vm(201,0,'unknown','unavailable','error')]},
  ]};
}
async function vmSecurity(site) {
  if(!text(site))return unavailable();
  if(process.env.VM_SECURITY_DEMO==='1'){
    try{return assess(demoResponse(site),site,true);}catch{return unavailable();}
  }
  const base=process.env.IDS_STATUS_URL,token=process.env.IDS_STATUS_TOKEN;
  if(!base||!token)return {status:'not_configured',sources:[]};
  try{
    const url=new URL('/v1/vm-security',base);need(['https:','http:'].includes(url.protocol)&&!url.username&&!url.password);
    url.searchParams.set('site',site);
    const response=await fetch(url,{headers:{Authorization:`Bearer ${token}`},redirect:'error',signal:AbortSignal.timeout(5000)});
    need(response.ok);
    return assess(await readJSON(response),site,false);
  }catch{return unavailable();}
}
module.exports={vmSecurity};

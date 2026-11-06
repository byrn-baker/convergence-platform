const {test}=require('node:test');
const assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const fs=require('node:fs'),vm=require('node:vm');
const {securityHistory,caseHistoryFilters}=require('./src/lib/securityHistory');
const now=Date.parse('2026-10-26T00:00:00Z');

test('history aggregates the authorized site and complete bounded window before producing zero-filled buckets',async()=>{
  let call;
  const history=await securityHistory('cottage',{range:'1h'},async(sql,args)=>{
    call={sql,args};return {rows:[{kind:'bucket',key:String((now-3600000)/150000),count:140},{kind:'country',key:'US',count:130},{kind:'country',key:'unknown',count:10}]};
  },now);
  assert.equal(history.total,140);assert.equal(history.buckets.length,24);assert.equal(history.buckets[0].count,140);
  assert.equal(history.buckets[1].count,0);assert.equal(history.start,'2026-10-25T23:00:00.000Z');
  assert.deepEqual(call.args,['cottage',history.start,history.end,150]);
  assert.match(call.sql,/site_id = \$1/);assert.match(call.sql,/timestamp < \$3/);assert.doesNotMatch(call.sql,/LIMIT /);
  await assert.rejects(securityHistory('cottage',{range:'99d'},()=>assert.fail('invalid query ran')),/Choose/);
});

test('drilldown accepts only bounded half-open security intervals and explicit country classes',()=>{
  const input={type:'security',start:'2026-10-25T23:00:00Z',end:'2026-10-26T00:00:00Z',country:'US'};
  assert.equal(caseHistoryFilters(input,now).country,'US');
  for(const country of ['internal','unknown','SG'])assert.equal(caseHistoryFilters({type:'security',country},now).country,country);
  for(const change of [{type:'wan'},{country:"US' OR 1=1"},{country:['US']},{start:'bad'},{end:undefined},{start:input.end},{start:'2026-10-01T00:00:00Z'},{end:'2026-10-27T00:00:00Z'}])
    assert.throws(()=>caseHistoryFilters({...input,...change},now));
});

function routes(file,query){
  const handlers={};const router={use(){},get:(p,h)=>handlers[p]=h,post(){},patch(){}};
  vm.runInNewContext(fs.readFileSync(`${__dirname}/src/routes/${file}.js`,'utf8'),{
    require:n=>n==='express'?{Router:()=>router}:n==='../db'?{query,DbUnavailableError:class extends Error{},dbStatus:()=>({})}:require(`${__dirname}/src/${n.slice(3)}`),
    module:{exports:{}},Date,URLSearchParams,console,fetch,AbortSignal,
  });return handlers;
}
const response=()=>({code:200,status(code){this.code=code;return this;},json(body){this.body=body;return this;}});
test('routes use authorized site for history and apply country/time predicates before the case cap; failure never looks empty',async()=>{
  const calls=[];const query=async(sql,args)=>{calls.push({sql,args});return {rows:[]};};
  const events=routes('events',query);let res=response();
  await events['/']({site:'authorized',query:{type:'security',country:'US',start:'2026-10-25T10:00:00Z',end:'2026-10-25T11:00:00Z',limit:'100'}},res);
  assert.equal(res.code,200);assert.equal(calls[0].args[0],'authorized');assert.equal(calls[0].args.at(-2),'US');
  assert.match(calls[0].sql,/AS destination\) context/);assert.match(calls[0].sql,/ORDER BY timestamp DESC LIMIT/);
  assert.deepEqual([...calls[0].args.slice(1,3)],['2026-10-25T10:00:00.000Z','2026-10-25T11:00:00.000Z']);
  res=response();await events['/']({site:'authorized',query:{type:'security',country:'invalid'}},res);assert.equal(res.code,400);assert.equal(calls.length,1);
  const history=routes('security',async()=>{throw Error('backend private details');});res=response();
  await history['/history']({site:'authorized',query:{range:'1h'}},res);assert.equal(res.code,503);assert.equal(res.body.unavailable,true);assert.equal(res.body.total,undefined);
});

// Optional integration uses connection-local temporary tables only. No real
// cases, reviews or schemas are written, including when using the pilot DB.
test('PostgreSQL counts over 100 cases, legacy/malformed enrichment, site isolation and interval edges',
  {skip:!process.env.SECURITY_HISTORY_PG_CONTAINER},async()=>{
  let historySql,args;
  await securityHistory('home',{range:'1h'},async(sql,params)=>{historySql=sql;args=params;return {rows:[]};},now);
  let caseSql,caseArgs;
  await routes('events',async(sql,params)=>{caseSql=sql;caseArgs=params;return {rows:[]};})['/']({site:'home',query:{type:'security',country:'US',start:'2026-10-25T23:00:00Z',end:'2026-10-26T00:00:00Z',limit:'100'}},response());
  const literal=v=>typeof v==='number'?String(v):`'${String(v).replaceAll("'","''")}'`;
  const expand=(sql,params)=>sql.replace(/\$(\d+)/g,(_,i)=>literal(params[Number(i)-1]));
  const setup=`BEGIN;
    CREATE TEMP TABLE events (id int,site_id text,timestamp timestamptz,message text,severity text,category text,source text,status text,alert_name text,alert_fingerprint text,root_cause text,investigation_notes text,expert_feedback text,feedback_quality text,rag_document_id text,incident_key text,alert_cleared_at timestamptz,source_evidence jsonb,jev_review jsonb,security_disposition jsonb,isp_report jsonb);
    INSERT INTO events(id,site_id,timestamp,alert_name,source_evidence)
      SELECT n,'home','2026-10-25T23:30:00Z','SuricataIntrusion',jsonb_build_object('annotations',jsonb_build_object('ip_context',jsonb_build_object('destination',jsonb_build_object('scope','external','country_code','US')))) FROM generate_series(1,130) n;
    INSERT INTO events(id,site_id,timestamp,alert_name,source_evidence) VALUES
      (131,'home','2026-10-25T23:00:00Z','SuricataIntrusion','{"annotations":{"ip_context":"{\\"destination\\":{\\"scope\\":\\"external\\",\\"country_code\\":\\"SG\\"}}"}}'),
      (132,'home','2026-10-25T23:40:00Z','SuricataIntrusion','{"annotations":{"ip_context":"invalid JSON"}}'),
      (133,'home','2026-10-25T23:40:00Z','SuricataIntrusion','{"annotations":{"ip_context":{"destination":{"scope":"internal","country_code":"US"}}}}'),
      (134,'home','2026-10-25T23:59:59Z','SuricataIntrusion',null),
      (135,'home','2026-10-26T00:00:00Z','SuricataIntrusion',null),
      (136,'other','2026-10-25T23:30:00Z','SuricataIntrusion',null),
      (137,'home','2026-10-25T23:30:00Z','WanHardDown',null);
  `;
  const sql=setup+`SELECT jsonb_agg(r) FROM (${expand(historySql,args)}) r;\nSELECT jsonb_agg(r) FROM (${expand(caseSql,caseArgs)}) r;\nROLLBACK;`;
  const child=spawnSync('docker',['exec','-i',process.env.SECURITY_HISTORY_PG_CONTAINER,'sh','-c','psql -X -qAt -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'],{input:sql,encoding:'utf8'});
  assert.equal(child.status,0,child.stderr);
  const [rows,cases]=child.stdout.trim().split('\n').map(JSON.parse);
  const countries=rows.filter(r=>r.kind==='country');
  assert.equal(countries.reduce((n,r)=>n+r.count,0),134);assert.equal(countries.find(c=>c.key==='US').count,130);
  assert.equal(countries.find(c=>c.key==='SG').count,1);assert.equal(countries.find(c=>c.key==='internal').count,1);assert.equal(countries.find(c=>c.key==='unknown').count,2);
  assert.equal(cases.length,100);assert.ok(cases.every(c=>c.id<=130));
});

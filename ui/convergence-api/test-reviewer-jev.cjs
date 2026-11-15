'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {randomUUID}=require('node:crypto'),{readFileSync}=require('node:fs'),{execFileSync}=require('node:child_process');
const {Readable}=require('node:stream'),{Pool}=require('pg');
const {migrate}=require('./reviewer-service'),{provision}=require('./reviewer-accounts');
const {createGrantAuthority}=require('./src/reviewer/grants');
const {requestJevReview,readJevReceipt,normalizeJevInput,validJevReceipt}=require('./src/reviewer/jev');
const {requestInvestigation}=require('./src/reviewer/investigations');
const {recordDisposition}=require('./src/reviewer/cases');
const {createJevRelay}=require('./src/reviewer/relay');
const connectionString=process.env.REVIEWER_TEST_DATABASE_URL,container=process.env.REVIEWER_PSQL_CONTAINER;

test('registry migration backfills old command identities and refuses ambiguous historical audit',{
 skip:!connectionString,timeout:30000,
},async t=>{
 const admin=new Pool({connectionString});t.after(()=>admin.end());
 for(const conflict of [false,true]) {
  const schema='jev_upgrade_'+randomUUID().replaceAll('-','');
  await admin.query('CREATE SCHEMA '+schema);
  const owner=new Pool({connectionString,options:'-c search_path='+schema});
  try {
   await owner.query(readFileSync(__dirname+'/src/db/schema.sql','utf8'));
   for(const file of ['grants.sql','cases.sql','investigations.sql'])await owner.query(readFileSync(__dirname+'/src/reviewer/'+file,'utf8'));
   const {rows:[event]}=await owner.query("INSERT INTO events(site_id,message,alert_name) VALUES ('home','Historical case','SuricataIntrusion') RETURNING id");
   const operation=randomUUID();
   await owner.query("INSERT INTO reviewer_case_dispositions(installation_id,actor_id,operation_id,site_id,case_id,kind,reason) VALUES('fixture','historic',$1,'home',$2,'needs_review','Historical reason')",[operation,event.id]);
   await owner.query("INSERT INTO reviewer_investigations(installation_id,actor_id,actor_name,operation_id,site_id,case_id,feedback) VALUES('fixture','historic','Historical actor',$1,'home',$2,'Historical investigation')",[conflict?operation:randomUUID(),event.id]);
   if(conflict) {
    await assert.rejects(migrate(owner),/Historical reviewer operation command conflict/);
    assert.equal((await owner.query("SELECT to_regclass('reviewer_operation_keys') AS name")).rows[0].name,null);
   } else {
    await migrate(owner);await migrate(owner);
    assert.deepEqual((await owner.query('SELECT command FROM reviewer_operation_keys ORDER BY command')).rows.map(row=>row.command),['disposition','reinvestigate']);
   }
   assert.equal((await owner.query('SELECT count(*)::int AS n FROM reviewer_case_dispositions')).rows[0].n,1);
   assert.equal((await owner.query('SELECT count(*)::int AS n FROM reviewer_investigations')).rows[0].n,1);
  } finally {await owner.end();await admin.query('DROP SCHEMA '+schema+' CASCADE');}
 }
});

test('JEV requests exclude caller-selected budget, evidence and authority; result contracts remain distinct',()=>{
 const operationId=randomUUID();assert.deepEqual(normalizeJevInput({operationId}),{operationId});
 for(const key of ['feedback','actor','site','state','budget','reconsideration_of'])assert.throws(()=>normalizeJevInput({operationId,[key]:'override'}),e=>e.status===400);
 const lease={delivery_id:randomUUID()};const receipt={delivery_id:lease.delivery_id,assessment_id:randomUUID(),job_id:null,participation:'reused',state:'completed',result:{version:1,advisory_only:true,status:'budget_exhausted'}};
 assert.equal(validJevReceipt(receipt,lease),true);
 for(const patch of [{state:'enqueued'},{result:null},{job_id:'bad'},{assessment_id:'bad'},{participation:'paid-again'},
   {result:{version:1,advisory_only:false,status:'ok'}},{result:{version:1,advisory_only:true,status:'ok',message:'x'.repeat(40000)}}])assert.equal(validJevReceipt({...receipt,...patch},lease),false);
});

test('PostgreSQL JEV acceptance shares operation identity and relay leases without investigation side effects',{
 skip:!connectionString || !container,timeout:30000,
},async t=>{
 const suffix=randomUUID().replaceAll('-',''),schema='jev_'+suffix,runtime='runtime_'+suffix,relayRole='relay_'+suffix,provisioner='provision_'+suffix;
 const admin=new Pool({connectionString});const scoped=role=>new Pool({connectionString,options:'-c search_path='+schema+(role?' -c role='+role:'')});
 const owner=scoped(),runtimeA=scoped(runtime),runtimeB=scoped(runtime),relayDB=scoped(relayRole);
 t.after(async()=>{await Promise.all([owner.end(),runtimeA.end(),runtimeB.end(),relayDB.end()]);await admin.query('DROP SCHEMA '+schema+' CASCADE');await admin.query('DROP ROLE '+[runtime,relayRole,provisioner].join(','));await admin.end();});
 await admin.query('CREATE SCHEMA '+schema);for(const role of [runtime,relayRole,provisioner])await admin.query('CREATE ROLE '+role+' NOLOGIN');
 await owner.query(readFileSync(__dirname+'/src/db/schema.sql','utf8'));await migrate(owner);await migrate(owner);
 const url=new URL(connectionString);
 for(const file of ['permissions.sql','permissions-dispatch.sql'])execFileSync('docker',['exec','-i',container,'psql','-X','-v','ON_ERROR_STOP=1','-U',url.username,'-d',url.pathname.slice(1),'-v','reviewer_schema='+schema,'-v','reviewer_runtime='+runtime,'-v','reviewer_provisioner='+provisioner,'-v','reviewer_relay='+relayRole],{input:readFileSync(__dirname+'/src/reviewer/'+file),stdio:['pipe','pipe','pipe']});
 await owner.query("INSERT INTO sites(id,name) VALUES ('foreign','Foreign')");
 for(const [user,role]of [['alice','operator'],['bob','operator'],['reader','viewer']])await provision(owner,['create',user,role,'home'],Readable.from([Buffer.from('fixture')]));
 const a=createGrantAuthority(runtimeA),b=createGrantAuthority(runtimeB);
 const alice=await a.issue('installation',{username:'alice',password:'fixture'}),aliceB=await b.issue('installation',{username:'alice',password:'fixture'}),bob=await b.issue('installation',{username:'bob',password:'fixture'}),viewer=await b.issue('installation',{username:'reader',password:'fixture'});
 const {rows:[item,other,foreign,wan]}=await owner.query("INSERT INTO events(site_id,message,alert_name,investigation_notes) VALUES ('home','IDS1','SuricataIntrusion','Original'),('home','IDS2','SuricataIntrusion','Original'),('foreign','IDS3','SuricataIntrusion','Original'),('home','WAN','WanHardDown','Original') RETURNING id,site_id");
 const write=(auth,grant,event,input,command=requestJevReview)=>auth.withGrant('installation',grant.token,{site:event.site_id,write:true},(db,actor)=>command(db,actor,event.site_id,event.id,input));
 const read=(auth,grant,event,op)=>auth.withGrant('installation',grant.token,{site:event.site_id,write:false},(db,actor)=>readJevReceipt(db,actor,event.site_id,event.id,op));
 const input={operationId:randomUUID()};let original;
 await t.test('site/role/case eligibility and concurrent replay preserve one attributed request',async()=>{
  for(const [grant,event,status]of[[viewer,item,403],[alice,foreign,403],[alice,{...item,id:foreign.id},404],[alice,wan,400]])await assert.rejects(write(a,grant,event,input),e=>e.status===status);
  const results=await Promise.all(Array.from({length:6},(_,i)=>write(i%2?a:b,i%2?alice:aliceB,item,input)));
  original=results[0];for(const result of results)assert.deepEqual(result,original);
  assert.equal(original.requested_by,alice.id);assert.equal(original.state,'accepted');assert.equal(original.result,null);
  const next=await write(b,bob,item,input);assert.notEqual(next.delivery_id,original.delivery_id);
  await assert.rejects(read(b,bob,item,randomUUID()),e=>e.status===404);
  const event=(await owner.query('SELECT investigation_notes,status FROM events WHERE id=$1',[item.id])).rows[0];
  assert.deepEqual(event,{investigation_notes:'Original',status:'logged'});
  assert.equal((await owner.query('SELECT count(*)::int AS n FROM event_history WHERE event_id=$1',[item.id])).rows[0].n,0);
 });
 await t.test('cross-command and cross-case identity cannot be reused, including concurrency',async()=>{
  await assert.rejects(write(a,alice,item,{...input,feedback:'Different command input'},requestInvestigation),e=>e.status===409);
  await assert.rejects(write(a,alice,item,{...input,kind:'needs_review',reason:'Different command'},recordDisposition),e=>e.status===409);
  const disposition={operationId:randomUUID(),kind:'needs_review',reason:'Recorded security disposition'};
  await write(a,alice,other,disposition,recordDisposition);
  await assert.rejects(write(a,alice,other,{operationId:disposition.operationId}),e=>e.status===409);
  await migrate(owner); // Backfill and all three command triggers remain idempotent.
  await assert.rejects(write(a,alice,other,input),e=>e.status===409);
  const key={operationId:randomUUID()};
  const outcomes=await Promise.allSettled([write(a,alice,item,key),write(b,aliceB,other,{...key,feedback:'Investigate this synthetic case'},requestInvestigation)]);
  assert.equal(outcomes.filter(x=>x.status==='fulfilled').length,1);assert.equal(outcomes.find(x=>x.status==='rejected').reason.status,409);
  for(const table of ['reviewer_operation_keys','reviewer_jev_requests']) {
    await assert.rejects(owner.query('DELETE FROM '+table),{code:'55000'});
    await assert.rejects(owner.query('UPDATE '+table+' SET command=command'),{code:'55000'});
  }
  await assert.rejects(relayDB.query('SELECT * FROM reviewer_accounts'),{code:'42501'});
  await assert.rejects(runtimeA.query('UPDATE reviewer_jev_outbox SET result=result'),{code:'42501'});
 });
 await owner.query("UPDATE reviewer_jev_outbox SET state='failed'");
 const fresh=()=>write(b,bob,item,{operationId:randomUUID()});
 const due=()=>owner.query("UPDATE reviewer_jev_outbox SET next_attempt_at=clock_timestamp()-interval '1 second'");
 await t.test('lost ACK repeats delivery; shared assessment result is durable and terminal',async()=>{
  const request=await fresh(),assessment_id=randomUUID(),job_id=randomUUID();let calls=0;
  const receipts=new Map();const receiver={submitJev:async command=>{
    assert.equal(command.command,'jev_review');assert.equal(command.feedback,undefined);
    receipts.set(command.delivery_id,{delivery_id:command.delivery_id,assessment_id,job_id,participation:'joined',state:'enqueued',result:null});
    if(++calls===1)throw Object.assign(Error(),{status:503});return receipts.get(command.delivery_id);
  },readJev:async id=>({...receipts.get(id),state:'completed',result:{version:1,advisory_only:true,status:'budget_exhausted'}})};
  const relay=createJevRelay(relayDB,receiver,{retryMs:1,pollMs:1});
  await relay.tick();await due();await relay.tick();await due();await relay.tick();
  const receipt=await read(b,bob,item,request.operation_id);
  assert.equal(receipt.state,'completed');assert.equal(receipt.assessment_id,assessment_id);assert.equal(receipt.participation,'joined');assert.equal(receipt.result.status,'budget_exhausted');assert.equal(calls,2);
  assert.equal(await relay.tick(),false);
  await assert.rejects(owner.query("UPDATE reviewer_jev_outbox SET result='{}' WHERE delivery_id=$1",[request.delivery_id]),{code:'55000'});
 });
 await t.test('expired leases and missing receipt do not recreate an accepted assessment',async()=>{
  const request=await fresh();let sends=0;
  const receiver={submitJev:async body=>{sends++;return {delivery_id:body.delivery_id,assessment_id:randomUUID(),job_id:null,participation:'reused',state:'completed',result:{version:1,advisory_only:true,status:'ok'}};},readJev:async()=>{throw Object.assign(Error(),{status:404});}};
  const relay=createJevRelay(relayDB,receiver,{pollMs:1});const expired=await relay.claim();
  await owner.query("UPDATE reviewer_jev_outbox SET leased_until=clock_timestamp()-interval '1 second' WHERE delivery_id=$1",[request.delivery_id]);
  const current=await relay.claim();assert.notEqual(current.lease_id,expired.lease_id);
  assert.equal(await relay.deliver(expired),false);await relay.deliver(current);
  assert.equal((await read(b,bob,item,request.operation_id)).state,'completed');assert.equal(sends,2);
  const missing=await fresh();await owner.query("UPDATE reviewer_jev_outbox SET state='enqueued' WHERE delivery_id=$1",[missing.delivery_id]);
  await relay.tick();assert.equal((await read(b,bob,item,missing.operation_id)).state,'unknown');assert.equal(sends,2);
 });
 await t.test('real mTLS command routes expose distinct receipts and preserve reads while disabled',async()=>{
  const https=require('node:https'),{once}=require('node:events'),{X509Certificate}=require('node:crypto');
  const tls=require('./test-support/tls-fixture.cjs').certificates(t);
  const {createReviewerService}=require('./src/reviewer/transport');
  const start=async enabled=>{
   const server=createReviewerService({tls:{ca:tls.ca,...tls.server},authority:a,jevEnabled:enabled,
    enrollments:[{installation:'installation',fingerprint256:new X509Certificate(tls.client.cert).fingerprint256}]});
   server.listen(0,'127.0.0.1');await once(server,'listening');t.after(()=>new Promise(resolve=>server.close(resolve)));return server;
  };
  const server=await start(true),disabled=await start(false);
  const base='/v1/sites/home/cases/'+item.id+'/jev-review';
  const request=(target,grant,path,body)=>new Promise((resolve,reject)=>{
   const data=body===undefined?undefined:Buffer.from(JSON.stringify(body));
   const req=https.request({hostname:'127.0.0.1',port:target.address().port,servername:'localhost',ca:tls.ca,...tls.client,agent:false,
    path,method:data?'POST':'GET',headers:{authorization:'Bearer '+grant.token,...(data?{'content-type':'application/json','content-length':data.length}:{})}},res=>{
     let body='';res.on('data',x=>body+=x);res.on('end',()=>resolve({status:res.statusCode,body:JSON.parse(body)}));});
   req.on('error',reject);req.end(data);
  });
  const operation={operationId:randomUUID()},accepted=await request(server,alice,base,operation);
  assert.equal(accepted.status,200);assert.equal(accepted.body.state,'accepted');assert.equal(accepted.body.result,null);
  assert.deepEqual(await request(server,alice,base,operation),accepted);
  assert.equal((await request(server,alice,base,{...operation,feedback:'not this command'})).status,400);
  assert.equal((await request(server,viewer,base,{operationId:randomUUID()})).status,403);
  assert.equal((await request(server,bob,base+'/'+operation.operationId)).status,404);
  assert.equal((await request(disabled,alice,base,{operationId:randomUUID()})).status,503);
  assert.deepEqual(await request(disabled,alice,base+'/'+operation.operationId),accepted);
  assert.equal((await request(server,alice,'/v1/sites/home/cases/'+item.id)).body.jev_enabled,true);
  assert.equal((await request(disabled,alice,'/v1/sites/home/cases/'+item.id)).body.jev_enabled,false);
  assert.deepEqual((await request(server,alice,'/v1/grants/current')).body.capabilities,{telemetry:false});
 });
 await t.test('later authorization applies to reads without changing historical attribution',async()=>{
  await provision(owner,['access','alice','viewer','home']);await assert.rejects(write(a,alice,item,{operationId:randomUUID()}),e=>e.status===403);
  assert.equal((await read(a,alice,item,input.operationId)).requested_by_name,'alice');
  await a.revoke('installation',alice.token);await assert.rejects(read(a,alice,item,input.operationId),e=>e.status===401);
 });
});

'use strict';
const test=require('node:test'), assert=require('node:assert/strict');
const {randomUUID}=require('node:crypto'), {readFileSync}=require('node:fs'), {execFileSync}=require('node:child_process');
const path=require('node:path'), {Readable}=require('node:stream'), {Pool}=require('pg');
const {migrate}=require('./reviewer-service'), {provision}=require('./reviewer-accounts');
const {createGrantAuthority}=require('./src/reviewer/grants');
const {requestInvestigation,readInvestigationReceipt,normalizeInput}=require('./src/reviewer/investigations');
const {createRelay,createReceiverClient}=require('./src/reviewer/relay');
const connectionString=process.env.REVIEWER_TEST_DATABASE_URL, container=process.env.REVIEWER_PSQL_CONTAINER;

test('normalization rejects forged, oversized and invalid Unicode inputs',()=>{
 const input={operationId:randomUUID().toUpperCase(),feedback:'  Please inspect this synthetic case  '};
 assert.deepEqual(normalizeInput(input),{operationId:input.operationId.toLowerCase(),feedback:input.feedback.trim()});
 for(const bad of [{...input,actor_id:'spoof'},{...input,feedback:'short'},{...input,feedback:'a'.repeat(4001)},
 {...input,feedback:'invalid\ud800note'},{...input,feedback:'invalid\0note'},{...input,operationId:'bad'}])assert.throws(()=>normalizeInput(bad),e=>e.status===400);
 assert.equal(Array.from(normalizeInput({...input,feedback:'😀'.repeat(4000)}).feedback).length,4000);
 for(const origin of ['http://localhost:8099','https://receiver.test/path'])assert.throws(()=>createReceiverClient({origin,token:'x'.repeat(40)}),/HTTPS/);
});

test('PostgreSQL named acceptance, two replicas and leased delivery', {skip:!connectionString||!container,timeout:45000},async t=>{
 const suffix=randomUUID().replaceAll('-',''),schema='dispatch_'+suffix;
 const runtime='runtime_'+suffix,relayRole='relay_'+suffix,provisioner='provisioner_'+suffix;
 const admin=new Pool({connectionString});
 const scoped=role=>new Pool({connectionString,options:'-c search_path='+schema+(role?' -c role='+role:'')});
 const owner=scoped(),runtimeA=scoped(runtime),runtimeB=scoped(runtime),relayDB=scoped(relayRole);
 t.after(async()=>{await Promise.all([owner.end(),runtimeA.end(),runtimeB.end(),relayDB.end()]);try{await admin.query('DROP SCHEMA IF EXISTS '+schema+' CASCADE');await admin.query('DROP ROLE IF EXISTS '+[runtime,relayRole,provisioner].join(','));}finally{await admin.end();}});
 await admin.query('CREATE SCHEMA '+schema);for(const role of [runtime,relayRole,provisioner])await admin.query('CREATE ROLE '+role+' NOLOGIN');
 await owner.query(readFileSync(path.join(__dirname,'src/db/schema.sql'),'utf8'));await migrate(owner);await migrate(owner);
 const url=new URL(connectionString);
 for(const file of ['permissions.sql','permissions-dispatch.sql'])execFileSync('docker',['exec','-i',container,'psql','-X','-v','ON_ERROR_STOP=1','-U',decodeURIComponent(url.username),'-d',decodeURIComponent(url.pathname.slice(1)),'-v','reviewer_schema='+schema,'-v','reviewer_runtime='+runtime,'-v','reviewer_provisioner='+provisioner,'-v','reviewer_relay='+relayRole],{input:readFileSync(path.join(__dirname,'src/reviewer',file)),stdio:['pipe','pipe','pipe']});
 await owner.query("INSERT INTO sites(id,name) VALUES ('elsewhere','Elsewhere')");
 for(const [name,role]of[['alice','operator'],['bob','operator'],['reader','viewer']])await provision(owner,['create',name,role,'home'],Readable.from([Buffer.from('fixture')]));
 const a=createGrantAuthority(runtimeA),b=createGrantAuthority(runtimeB);
 const alice=await a.issue('installation',{username:'alice',password:'fixture'}),alice2=await b.issue('installation',{username:'alice',password:'fixture'});
 const bob=await b.issue('installation',{username:'bob',password:'fixture'}),reader=await b.issue('installation',{username:'reader',password:'fixture'});
 const {rows:[first,second,foreign]}=await owner.query("INSERT INTO events(site_id,message,investigation_notes) VALUES ('home','Synthetic one','Original'),('home','Synthetic two','Original'),('elsewhere','Foreign','Original') RETURNING id,site_id");
 const write=(auth,grant,item,input)=>auth.withGrant('installation',grant.token,{site:item.site_id,write:true},(db,actor)=>requestInvestigation(db,actor,item.site_id,item.id,input));
 const read=(auth,grant,item,op)=>auth.withGrant('installation',grant.token,{site:item.site_id,write:false},(db,actor)=>readInvestigationReceipt(db,actor,item.site_id,item.id,op));
 const input={operationId:randomUUID(),feedback:'Please inspect this synthetic case for evidence'};
 const count=async table=>(await owner.query('SELECT count(*)::int AS n FROM '+table)).rows[0].n;
 const reject=(p,status)=>assert.rejects(p,e=>e.status===status);let original;
 await t.test('unauthorized and forged requests leave no writes',async()=>{
  await reject(write(b,reader,first,input),403);await reject(write(a,alice,foreign,input),403);
  await reject(write(a,alice,{...first,id:foreign.id},input),404);await reject(write(a,alice,first,{...input,actor_id:bob.id}),400);
  assert.equal(await count('reviewer_investigations'),0);
 });
 await t.test('concurrent retries yield one original named receipt, note and outbox',async()=>{
  const receipts=await Promise.all(Array.from({length:8},(_,i)=>write(i%2?a:b,i%2?alice:alice2,first,input)));
  original=receipts[0];for(const r of receipts)assert.deepEqual(r,original);
  assert.equal(original.state,'accepted');assert.equal(original.requested_by,alice.id);assert.equal(original.requested_by_name,'alice');
  assert.equal(await count('reviewer_investigations'),1);assert.equal(await count('reviewer_investigation_outbox'),1);
  const {rows:[event]}=await owner.query('SELECT investigation_notes,status FROM events WHERE id=$1',[first.id]);
  assert.equal(event.investigation_notes.split('[reviewer:reinvestigate').length-1,1);assert.equal(event.status,'logged');
  assert.equal((await owner.query('SELECT count(*)::int AS n FROM event_history WHERE event_id=$1',[first.id])).rows[0].n,1);
  await owner.query("UPDATE reviewer_accounts SET name='Renamed' WHERE id=$1",[alice.id]);
  assert.deepEqual(await write(a,alice,first,input),original);
  await reject(write(a,alice,first,{...input,feedback:'Different intentional question'}),409);await reject(read(b,bob,first,input.operationId),404);
 });
 await t.test('distinct people and intentional submissions stay distinct on one case',async()=>{
  const other=await write(b,bob,first,input),next=await write(a,alice,first,{...input,operationId:randomUUID()});
  assert.notEqual(other.delivery_id,original.delivery_id);assert.notEqual(next.delivery_id,original.delivery_id);
  assert.equal(await count('reviewer_investigations'),3);
  const {rows:[event]}=await owner.query('SELECT investigation_notes FROM events WHERE id=$1',[first.id]);assert.equal(event.investigation_notes.split('[reviewer:reinvestigate').length-1,3);
 });
 await t.test('conflicting cases serialize; transaction rollback preserves no partial note',async()=>{
  const action={...input,operationId:randomUUID()},attempts=await Promise.allSettled([write(a,alice,first,action),write(b,alice2,second,action)]);
  assert.equal(attempts.filter(x=>x.status==='fulfilled').length,1);assert.equal(attempts.find(x=>x.status==='rejected').reason.status,409);
  const rollback={...input,operationId:randomUUID()},before=(await owner.query('SELECT investigation_notes FROM events WHERE id=$1',[first.id])).rows[0];
  await assert.rejects(a.withGrant('installation',alice.token,{site:'home',write:true},async(db,actor)=>{await requestInvestigation(db,actor,'home',first.id,rollback);throw Error('synthetic interruption');}),/synthetic interruption/);
  assert.deepEqual((await owner.query('SELECT investigation_notes FROM events WHERE id=$1',[first.id])).rows[0],before);await reject(read(a,alice,first,rollback.operationId),404);
 });
 await t.test('immutable audit and separate runtime/relay privileges',async()=>{
  for(const sql of ['UPDATE reviewer_investigations SET feedback=feedback','DELETE FROM reviewer_investigations'])await assert.rejects(owner.query(sql),{code:'55000'});
  for(const sql of ['UPDATE reviewer_investigation_outbox SET state=state','UPDATE reviewer_accounts SET role=role'])await assert.rejects(runtimeA.query(sql),{code:'42501'});
  for(const sql of ['SELECT * FROM reviewer_accounts','SELECT * FROM events','INSERT INTO reviewer_investigation_outbox(delivery_id) VALUES (gen_random_uuid())','UPDATE reviewer_investigations SET feedback=feedback'])await assert.rejects(relayDB.query(sql),{code:'42501'});
  await assert.rejects(runtimeA.query("INSERT INTO reviewer_investigation_outbox(delivery_id,state) VALUES (gen_random_uuid(),'completed')"),{code:'42501'});
 });
 await t.test('live downgrade and revocation prevent work on the other replica',async()=>{
  await provision(owner,['access','alice','viewer','home']);await reject(write(b,alice2,first,{...input,operationId:randomUUID()}),403);
  assert.equal((await read(b,alice2,first,input.operationId)).requested_by_name,'alice');await a.revoke('installation',alice.token);await reject(read(b,alice,first,input.operationId),401);
 });
 await owner.query("UPDATE reviewer_investigation_outbox SET state='failed'");
 const fresh=()=>write(b,bob,second,{...input,operationId:randomUUID()}),due=()=>owner.query("UPDATE reviewer_investigation_outbox SET next_attempt_at=clock_timestamp()-interval '1 second'");
 await t.test('lost admission replays same delivery then polls durable completion',async()=>{
  const request=await fresh(),job=randomUUID();let calls=0;
  const receiver={submit:async env=>{assert.equal(env.delivery_id,request.delivery_id);assert.equal(env.actor_id,bob.id);calls++;if(calls===1)throw Object.assign(Error('lost'),{status:503});return{delivery_id:env.delivery_id,job_id:job,state:'enqueued'};},read:async id=>({delivery_id:id,job_id:job,state:'completed'})};
  const relay=createRelay(relayDB,receiver,{retryMs:1,pollMs:1});await relay.tick();assert.equal((await read(b,bob,second,request.operation_id)).state,'accepted');
  await due();await relay.tick();assert.equal((await read(b,bob,second,request.operation_id)).state,'enqueued');await due();await relay.tick();assert.equal((await read(b,bob,second,request.operation_id)).state,'completed');assert.equal(calls,2);assert.equal(await relay.tick(),false);
  await assert.rejects(owner.query("UPDATE reviewer_investigation_outbox SET state='enqueued' WHERE delivery_id=$1",[request.delivery_id]),{code:'55000'});
 });
 await t.test('expired lease recovery ignores stale acknowledgements',async()=>{
  const request=await fresh(),job=randomUUID(),receiver={submit:async env=>({delivery_id:env.delivery_id,job_id:job,state:'enqueued'})};
  const one=createRelay(relayDB,receiver),two=createRelay(relayDB,receiver),old=await one.claim();assert.equal(old.delivery_id,request.delivery_id);assert.equal(await two.claim(),null);
  await owner.query("UPDATE reviewer_investigation_outbox SET leased_until=clock_timestamp()-interval '1 second' WHERE delivery_id=$1",[request.delivery_id]);
  const current=await two.claim();assert.notEqual(old.lease_id,current.lease_id);receiver.submit=async env=>({delivery_id:env.delivery_id,job_id:job,state:'completed'});assert.equal(await two.deliver(current),true);
  receiver.submit=async env=>({delivery_id:env.delivery_id,job_id:job,state:'enqueued'});assert.equal(await one.deliver(old),false);assert.equal((await read(b,bob,second,request.operation_id)).state,'completed');
 });
 await t.test('lost receiver history or mismatched reply requires review without redispatch',async()=>{
  const request=await fresh(),job=randomUUID();let posts=0;
  const receiver={submit:async env=>{posts++;return{delivery_id:env.delivery_id,job_id:job,state:'enqueued'};},read:async()=>{throw Object.assign(Error('missing'),{status:404});}};
  const relay=createRelay(relayDB,receiver,{pollMs:1});await relay.tick();await due();await relay.tick();assert.equal((await read(b,bob,second,request.operation_id)).state,'unknown');assert.equal(posts,1);assert.equal(await relay.tick(),false);
  const invalid=await fresh();receiver.submit=async()=>({delivery_id:randomUUID(),job_id:randomUUID(),state:'completed'});await relay.tick();assert.equal((await read(b,bob,second,invalid.operation_id)).state,'unknown');
 });
});

'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),https=require('node:https');
const {execFileSync}=require('node:child_process'),{once}=require('node:events'),{randomUUID}=require('node:crypto');
const {createReceiverClient}=require('./src/reviewer/relay');

test('relay uses trusted HTTPS, a dedicated credential and bounded fixed endpoints without redirects',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'reviewer-relay-tls-'));
 t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 execFileSync('openssl',['req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes','-days','1','-subj','/CN=localhost',
  '-addext','subjectAltName=DNS:localhost,IP:127.0.0.1','-keyout',path.join(dir,'tls.key'),'-out',path.join(dir,'tls.crt')],{stdio:'pipe'});
 const cert=fs.readFileSync(path.join(dir,'tls.crt')),key=fs.readFileSync(path.join(dir,'tls.key'));
 const token='synthetic-service-token-'+randomUUID(),delivery=randomUUID(),job=randomUUID();
 let behavior='normal';const requests=[];
 const server=https.createServer({cert,key},async(req,res)=>{
  let body='';for await(const chunk of req)body+=chunk;
  requests.push({path:req.url,method:req.method,authorization:req.headers.authorization,body});
  if(behavior==='redirect'){res.writeHead(302,{location:'/arbitrary-other-service'});return res.end();}
  if(behavior==='large'){res.writeHead(200);return res.end('x'.repeat(5000));}
  if(behavior==='hang')return;
  if(behavior==='denied'){res.writeHead(403);return res.end('private backend diagnostic must stay private');}
  res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify({delivery_id:delivery,job_id:job,state:'enqueued'}));
 });
 server.listen(0,'127.0.0.1');await once(server,'listening');
 t.after(()=>{server.closeAllConnections();server.close();});
 const origin='https://127.0.0.1:'+server.address().port;
 const client=createReceiverClient({origin,token,ca:cert,deadlineMs:150});t.after(()=>client.close());
 const command={delivery_id:delivery,feedback:'Synthetic evidence'};
 assert.deepEqual(await client.submit(command),{delivery_id:delivery,job_id:job,state:'enqueued'});
 assert.deepEqual(requests[0],{path:'/reviewer/commands',method:'POST',authorization:'Bearer '+token,body:JSON.stringify(command)});
 await client.read(delivery);assert.equal(requests[1].path,'/reviewer/commands/'+delivery);assert.equal(requests[1].method,'GET');assert.equal(requests[1].body,'');
 assert.throws(()=>client.read('../private'),/identity/);
 const untrusted=createReceiverClient({origin,token,deadlineMs:150});t.after(()=>untrusted.close());
 await assert.rejects(untrusted.read(delivery),e=>e.status===503);assert.equal(requests.length,2);
 behavior='redirect';await assert.rejects(client.submit(command),e=>e.status===302);assert.equal(requests.length,3);assert.ok(!requests.some(r=>r.path==='/arbitrary-other-service'));
 behavior='large';await assert.rejects(client.read(delivery),e=>e.status===503);
 behavior='denied';await assert.rejects(client.read(delivery),e=>e.status===403&&!e.message.includes('private'));
 behavior='hang';await assert.rejects(client.read(delivery),e=>e.status===503);
});

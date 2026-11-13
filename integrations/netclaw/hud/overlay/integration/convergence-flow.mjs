/** Real disposable VictoriaLogs -> API route/auth -> HUD proxy -> rendered flow evidence.
 * Requires a local Linux-container Docker daemon; never reads operator .env files.
 */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {createRequire} from 'node:module';
import {setTimeout as delay} from 'node:timers/promises';
import express from 'express';
import {JSDOM} from 'jsdom';
import {register} from '../modules/convergence/server.js';
import {flowHTML, flowHandoff} from '../modules/convergence/FlowExplorer.js';

const require = createRequire(new URL('../../convergence-api/package.json', import.meta.url));
const docker = (...args) => execFileSync('docker', args, {encoding:'utf8', timeout:60000}).trim();
async function listen(app) {
  const server = await new Promise((resolve,reject) => {
    const handle=app.listen(0,'127.0.0.1',()=>resolve(handle));
    handle.on('error',reject);
  });
  return {server, url:`http://127.0.0.1:${server.address().port}`};
}
async function waitFor(check) {
  let error;
  for(let n=0;n<60;n++) {
    try { return await check(); } catch(e) {error=e; await delay(250);}
  }
  throw error;
}

test('retained flows survive restart and remain scoped through the real API and HUD modules', {timeout:120000}, async t => {
  const name=`convergence-flow-test-${randomUUID()}`;
  const servers=[];
  const envKeys=['API_KEYS','SITES_CONFIG','FLOW_VICTORIALOGS_URL'];
  const previous=Object.fromEntries(envKeys.map(k=>[k,process.env[k]]));
  let created=false;
  t.after(async()=>{
    for(const server of servers) {
      server.closeAllConnections();
      await new Promise(resolve=>server.close(resolve));
    }
    if(created) docker('rm','-f',name);
    for(const key of envKeys) previous[key]===undefined ? delete process.env[key] : process.env[key]=previous[key];
  });
  docker('run','--detach','--name',name,'--publish','127.0.0.1::9428',
    '--label','netclaw.test=convergence-flow','victoriametrics/victoria-logs:v1.52.0',
    '-storageDataPath=/storage','-retentionPeriod=1d');
  created=true;
  let archive=`http://${docker('port',name,'9428/tcp')}`;
  await waitFor(async()=>assert.equal((await fetch(`${archive}/health`,{signal:AbortSignal.timeout(2000)})).status,200));
  const timestamp=new Date(Date.now()-5000).toISOString();
  const fixture={_time:timestamp,_msg:'synthetic flow qualification',job:'netflow',site:'branch',
    sampler_address:'192.0.2.1',src_addr:'192.0.2.10',dst_addr:'198.51.100.20',
    bytes:1200,packets:12,sampling_rate:1,private_payload:'must-not-export'};
  const rows=[fixture,{...fixture,src_addr:fixture.dst_addr,dst_addr:fixture.src_addr,bytes:300},
    {...fixture,site:'other',bytes:900000},
    {...fixture,sampler_address:'192.0.2.2',bytes:800000}];
  const inserted=await fetch(`${archive}/insert/jsonline?_time_field=_time&_stream_fields=job,site`,{
    method:'POST',headers:{'Content-Type':'application/stream+json'},
    body:rows.map(r=>JSON.stringify(r)).join('\n')+'\n'});
  assert.equal(inserted.status,200);
  // Restart the actual store before reading; container-local disk must retain the observations.
  docker('restart',name);
  archive=`http://${docker('port',name,'9428/tcp')}`;
  await waitFor(async()=>assert.equal((await fetch(`${archive}/health`,{signal:AbortSignal.timeout(2000)})).status,200));
  const token=randomUUID();
  process.env.API_KEYS=JSON.stringify([{id:'fixture',key:token,role:'viewer',sites:['branch','empty']}]);
  process.env.SITES_CONFIG=JSON.stringify({branch:{flows:{exporters:['192.0.2.1']}},empty:{flows:{exporters:['192.0.2.1']}}});
  process.env.FLOW_VICTORIALOGS_URL=archive;
  const api=express();
  api.use('/api/flows',require('./src/middleware/auth').authMiddleware,require('./src/routes/flows'));
  const backend=await listen(api);servers.push(backend.server);
  const hud=express();
  const env={CONVERGENCE_API_URL:backend.url,CONVERGENCE_API_TOKEN:token};
  register(hud,{env:key=>env[key]});
  const front=await listen(hud);servers.push(front.server);
  const read=path=>fetch(`${front.url}/api/home${path}`);
  assert.equal((await fetch(`${backend.url}/api/flows?site=branch`)).status,401);
  assert.equal((await read('/flows?site=other')).status,403);
  assert.equal((await read('/flows?site=branch&exporter=192.0.2.2')).status,400);
  const response=await read('/flows?site=branch&range=1h');
  assert.equal(response.status,200);
  const summary=await response.json();
  assert.equal(summary.site,'branch');assert.equal(summary.status,'active');
  assert.equal(summary.total.bytes,1500);assert.equal(summary.total.records,2);
  assert.doesNotMatch(JSON.stringify(summary),new RegExp(token));
  const document=new JSDOM(flowHTML(summary,{range:'1h',canInvestigate:true})).window.document;
  assert.match(document.body.textContent,/192\.0\.2\.10/);
  const selection=document.querySelector('[data-flow-canvas]').dataset.flowCanvas.split(':');
  const handoff=flowHandoff(summary,selection[0],Number(selection[1]));
  assert.equal(handoff.site,'branch');assert.deepEqual(handoff.exporters,['192.0.2.1']);
  const query=new URLSearchParams({site:handoff.site,address:handoff.observation.src_addr,
    start:handoff.start,end:handoff.end,exporter:handoff.exporters[0]});
  const evidenceResponse=await read(`/flows/records?${query}`);
  assert.equal(evidenceResponse.status,200);
  const evidence=await evidenceResponse.json();
  assert.equal(evidence.records.length,2);
  assert.equal(handoff.observation.bytes,1200);
  // Address evidence includes both directions; it is not a directional rank subtotal.
  assert.deepEqual(evidence.records.map(r=>Number(r.bytes)).sort((a,b)=>a-b),[300,1200]);
  assert.ok(evidence.records.some(r=>r.src_addr===handoff.observation.src_addr));
  assert.ok(evidence.records.some(r=>r.dst_addr===handoff.observation.src_addr));
  assert.equal(evidence.start,handoff.start);assert.equal(evidence.end,handoff.end);
  assert.doesNotMatch(JSON.stringify(evidence),/must-not-export|private_payload/);
  assert.equal((await read('/flows/records?site=branch&address=invalid')).status,400);
  const empty=await (await read('/flows?site=empty')).json();
  assert.equal(empty.status,'empty');
  // A new range avoids the API's documented 30-second aggregate cache.
  docker('stop',name);
  const unavailableResponse=await read('/flows?site=branch&range=15m');
  assert.equal(unavailableResponse.status,502);
  const unavailable=await unavailableResponse.json();
  assert.equal(unavailable.unavailable,true);
  const failureDocument=new JSDOM(flowHTML(unavailable)).window.document;
  assert.match(failureDocument.body.textContent,/totals are unknown/);
  assert.doesNotMatch(failureDocument.body.textContent,/0 B/);
});

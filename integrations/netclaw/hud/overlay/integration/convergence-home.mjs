/** Verify a real prepared home bundle through production HUD proxy and rendering. */
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import express from 'express';
import dotenv from 'dotenv';
import {JSDOM} from 'jsdom';
import {register} from '../modules/convergence/server.js';
import {flowHTML,flowHandoff} from '../modules/convergence/FlowExplorer.js';

const bundle=process.argv[2];
const {options}=JSON.parse(readFileSync(join(bundle,'deployment.json'),'utf8'));
const env=dotenv.parse(readFileSync(join(bundle,'hud.env')));
const app=express();
register(app,{env:key=>env[key]});
const server=await new Promise(resolve=>{const instance=app.listen(0,'127.0.0.1',()=>resolve(instance));});
try {
  const base=`http://127.0.0.1:${server.address().port}/api/home/flows`;
  const response=await fetch(`${base}?site=${options.site}&range=15m`);
  assert.equal(response.status,200);
  const summary=await response.json();
  assert.equal(summary.coverage.mode,options.flows);
  assert.equal(summary.status,'active');
  assert.ok(summary.total.records>0);
  const document=new JSDOM(flowHTML(summary,{range:'15m',canInvestigate:true})).window.document;
  assert.match(document.querySelector('.home-flow-coverage').textContent,
    options.flows==='demo'?/synthetic demonstration/i:/not whole-network coverage/i);
  const [kind,index]=document.querySelector('[data-flow-canvas]').dataset.flowCanvas.split(':');
  const handoff=flowHandoff(summary,kind,Number(index));
  assert.equal(handoff.coverage.mode,options.flows);
  const query=new URLSearchParams({site:options.site,address:handoff.observation.src_addr,
    start:handoff.start,end:handoff.end,exporter:handoff.exporters[0]});
  const evidence=await (await fetch(`${base}/records?${query}`)).json();
  assert.equal(evidence.coverage.mode,options.flows);
  assert.ok(evidence.records.length);
  assert.equal(evidence.start,handoff.start);
  assert.equal(evidence.end,handoff.end);
  assert.equal((await fetch(`${base}?site=unassigned`)).status,403);
  assert.equal((await fetch(`${base}?site=${options.site}&exporter=192.0.2.250`)).status,400);
  assert.ok(!JSON.stringify({summary,evidence}).includes(env.CONVERGENCE_API_TOKEN));
  console.log(`PASS ${options.flows}: production HUD proxy/rendering and scoped same-window evidence (JSDOM)`);
} finally {
  server.closeAllConnections();
  await new Promise(resolve=>server.close(resolve));
}

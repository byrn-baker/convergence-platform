// Run after installing tests/reviewer-browser's Playwright dependencies.
// Ephemeral HTTPS fixture only; never connects to production services.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdtempSync,readFileSync,rmSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import {execFileSync} from 'node:child_process';
import {once} from 'node:events';
import {randomBytes,randomUUID} from 'node:crypto';
import {createReviewerServer} from '../reviewer/server.mjs';
import {reviewerAssets} from '../reviewer/start.mjs';
const require = createRequire(new URL('../../../tests/reviewer-browser/package.json',import.meta.url));
const {chromium} = require('playwright');
const temporary = mkdtempSync(path.join(os.tmpdir(),'review-telemetry-browser-'));
const grants = new Map(), calls = [];
const caseId = randomUUID();
let browser,server;
try {
  const cert=path.join(temporary,'cert.pem'),key=path.join(temporary,'key.pem');
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-out',cert,'-keyout',key,
    '-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1'],{stdio:'ignore'});
  const reserve=net.createServer().listen(0,'127.0.0.1'); await once(reserve,'listening');
  const port=reserve.address().port; await new Promise(resolve=>reserve.close(resolve));
  const origin='https://127.0.0.1:'+port;
  const caseData={id:caseId,site_id:'red',alert_name:'Fixture WAN incident',message:'Retained incident evidence',
    status:'investigating',category:'wan',timestamp:new Date().toISOString(),isp_report_generated_at:new Date().toISOString(),review_history:[]};
  const client={
    async issue({username,password}) {
      assert.equal(password,'fixture-only');
      const token=randomBytes(32).toString('base64url');
      const grant={id:username,name:username,role:'viewer',capabilities:{telemetry:true},sites:[username==='alice'?'red':'blue'],
        expiresAt:new Date(Date.now()+120000).toISOString(),idleExpiresAt:new Date(Date.now()+120000).toISOString()};
      grants.set(token,grant);return {...grant,token};
    },
    async current(token) {if(!grants.has(token))throw Object.assign(Error('revoked'),{status:401});return grants.get(token);},
    async revoke(token){grants.delete(token);},
    async readCase(token,site,id) {assert.ok(grants.get(token).sites.includes(site));return {...caseData,id,site_id:site};},
    async readTelemetry(token,site,resource) {
      assert.ok(grants.get(token).sites.includes(site)); calls.push({site,resource});
      if(resource==='health')return {site,healthScore:{value:97,status:'healthy'},wanLatency:{value:2},wanLoss:{value:0},alertCount:{firing:0},sources:{metrics:'available'}};
      if(resource==='wifi')return {site,clients:{wireless:4},txRetries:[]};
      if(resource==='devices')return {site,wanProbes:[],edge:[],accessPoints:[],switches:[],unavailable:true,reason:'Fixture inventory unavailable'};
      if(resource==='security/vms')return {site,status:'configured',sources:[],hostTelemetry:{status:'configured',agents:[{id:'fixture-vm',status:'stale',reason:'Fixture heartbeat stale',lastSeen:1}]}};
      if(resource==='security')return {site,status:'unavailable',unavailable:true,reason:'Fixture firewall unavailable',intrusionDetection:{status:'unknown',sensors:[]}};
      if(resource==='security/history')return {site,unavailable:true};
      if(resource==='events'||resource==='events/escalated')return {site,events:[{...caseData,site_id:site}]};
      if(resource==='flows')return {site,status:'unavailable',unavailable:true,reason:'Fixture flow unavailable'};
      if(resource==='isp/providers')return {providers:[]};
      if(resource==='events/'+caseId)return {...caseData,site_id:site,isp_report:{provider:{name:'Fixture ISP',support_url:'https://example.test/support',channel:'Support',guidance:'Fixture guidance'},draft:{subject:'Fixture saved subject',body:'Fixture saved evidence draft'},evidence:{samples:[]},evidence_sha256:'fixture'}};
      throw Error('Unexpected resource '+resource);
    }
  };
  server=createReviewerServer({tls:{cert:readFileSync(cert),key:readFileSync(key)},origin,client,assets:reviewerAssets()});
  server.listen(port,'127.0.0.1');await once(server,'listening');
  browser=await chromium.launch({headless:true});
  const contexts=[],pages=[];
  for(const [username,width] of [['alice',1440],['bob',390]]) {
    const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width,height:950}});contexts.push(context);
    const page=await context.newPage(),errors=[]; pages.push(page);
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(origin);await page.locator('#login:not([hidden])').waitFor();
    await page.locator('[name=username]').fill(username);await page.locator('[name=password]').fill('fixture-only');
    await page.locator('#login button').click();await page.locator('#account:not([hidden])').waitFor();
    await page.getByRole('button',{name:'Site overview',exact:true}).click();
    await page.getByText('Health score',{exact:true}).waitFor();
    if(process.env.REVIEWER_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.REVIEWER_SCREENSHOT_DIR,username+'-health.png'),fullPage:true});
    assert.equal(await page.locator('[data-home-sub=models]').isVisible(),false);
    await page.locator('[data-home-sub="vm-security"]').click();
    await page.getByRole('heading',{name:'VM Security',exact:true}).waitFor();
    await page.locator('.vm-diagnostics > summary').click();
    await page.getByText('Fixture heartbeat stale',{exact:false}).waitFor();
    if(process.env.REVIEWER_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.REVIEWER_SCREENSHOT_DIR,username+'-vm.png'),fullPage:true});
    await page.locator('[data-home-sub=diary]').click();
    await page.getByText('Fixture WAN incident',{exact:false}).first().waitFor();
    await page.locator('[data-home-sub=flows]').click();
    await page.getByText(/unavailable/i).first().waitFor();
    await page.locator('[data-home-sub=isp]').click();
    await page.getByRole('heading',{name:'Internet incidents & report evidence'}).waitFor();
    await page.getByRole('button',{name:'View saved draft and evidence'}).click();
    await page.locator('[data-isp-field=subject]').waitFor();
    assert.equal(await page.locator('[data-isp-field=subject]').inputValue(),'Fixture saved subject');
    assert.equal(await page.locator('[data-isp-field=subject]').getAttribute('readonly'),'');
    await page.getByRole('link',{name:/Review this case/}).click();
    await page.locator('#detail:not([hidden])').waitFor();
    assert.match(await page.locator('#facts').textContent(),/Retained incident evidence/);
    assert.equal(await page.evaluate(()=>localStorage.length),0);
    await page.getByRole('button',{name:'Site overview',exact:true}).click();
    await page.getByText('Health score',{exact:true}).waitFor();
    assert.deepEqual(errors,[]);
  }
  await pages[0].locator('#logout').click();
  await pages[0].locator('#login:not([hidden])').waitFor();
  assert.equal(await pages[0].locator('#site-workspace').textContent(),'');
  await pages[1].locator('#home-refresh').click();
  await pages[1].getByText('Health score',{exact:true}).waitFor();
  assert.equal(await pages[1].locator('#account').isVisible(),true);
  assert.match(await pages[1].locator('#identity').textContent(),/bob/);
  await pages[1].locator('#logout').click();
  await pages[1].locator('#login:not([hidden])').waitFor();
  assert.equal(await pages[1].locator('#site-workspace').textContent(),'');
  assert.ok(calls.some(call=>call.site==='red'&&call.resource==='security/vms'));
  assert.ok(calls.some(call=>call.site==='blue'&&call.resource==='security/vms'));
  console.log('PASS: real HTTPS assets, desktop/mobile independent sessions, shared health/VM/heartbeat/diary/flow/WAN evidence, case navigation and logout clearing');
} finally {
  await browser?.close();
  if(server)await new Promise(resolve=>{server.close(resolve);server.closeAllConnections();});
  rmSync(temporary,{recursive:true,force:true});
}

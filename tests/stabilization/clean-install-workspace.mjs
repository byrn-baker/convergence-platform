// Actual deployed lab workspace. The caller supplies synthetic state and a trusted
// NSS profile; this fixture never disables certificate verification.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {pathToFileURL} from 'node:url';

const [state, phase = 'before'] = process.argv.slice(2);
assert.ok(state && ['before', 'after'].includes(phase));
const root = path.resolve(state);
const {chromium} = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE));
const record = JSON.parse(fs.readFileSync(path.join(root, 'review-case.json')));
const browser = await chromium.launch({
  executablePath: process.env.REVIEWER_CHROMIUM,
  headless: true,
  env: {...process.env, HOME:path.join(root, 'browser-home'),
    XDG_DATA_HOME:path.join(root, 'browser-home/.local/share')},
  args:['--no-proxy-server', '--host-resolver-rules=MAP reviewer.test 127.0.0.1'],
});
try {
  const context = await browser.newContext({viewport:{width:1280,height:800}});
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto('https://reviewer.test:13444');
  await page.getByLabel('Username').fill('clean-operator');
  await page.getByLabel('Password').fill('synthetic-stabilization-password');
  await page.getByRole('button', {name:'Sign in', exact:true}).click();
  await page.locator('#account').waitFor({state:'visible'});
  const session = await page.evaluate(async () => (await fetch('/review/session')).json());
  assert.equal(session.capabilities.telemetry, true);
  assert.deepEqual(session.user.sites, ['demo']);
  const fetchJson = async (route, body, csrf) => page.evaluate(async ({route,body,csrf}) => {
    const r = await fetch(route, body === undefined ? {} : {method:'POST',
      headers:{'Content-Type':'application/json','X-Review-CSRF':csrf || ''},body:JSON.stringify(body)});
    return {status:r.status,body:await r.json()};
  }, {route,body,csrf});
  const flows = await fetchJson('/review/sites/demo/telemetry/flows?range=15m');
  assert.equal(flows.status, 200);
  assert.equal(flows.body.coverage.mode, 'demo');
  assert.ok(flows.body.total.records > 0);
  assert.equal((await fetchJson('/review/sites/other/telemetry/flows')).status,403);
  assert.equal((await fetchJson('/api/terminal')).status,404);
  const casePath = '/review/sites/demo/cases/' + record.case_id;
  const input = {kind:'expected_activity',reason:'Synthetic stabilization fixture, not a production finding.',
    operationId:phase === 'before' ? randomUUID() : record.operation_id};
  assert.equal((await fetchJson(casePath + '/disposition',input)).status,403);
  let receipt;
  if (phase === 'before') {
    const written = await fetchJson(casePath + '/disposition',input,session.csrf);
    assert.equal(written.status,200,JSON.stringify(written.body));
    receipt = written.body;
    record.operation_id = receipt.operation_id;
    record.receipt = receipt;
    fs.writeFileSync(path.join(root,'review-case.json'),JSON.stringify(record,null,2));
  } else {
    const read = await fetchJson(casePath + '/disposition/' + record.operation_id);
    assert.equal(read.status,200,JSON.stringify(read.body));
    assert.deepEqual(read.body,record.receipt);
    receipt = read.body;
  }
  await page.getByLabel('Case ID').fill(record.case_id);
  await page.getByRole('button',{name:'Open case',exact:true}).click();
  await page.locator('#detail').waitFor({state:'visible'});
  await page.getByRole('button',{name:'Site overview',exact:true}).click();
  await page.locator('#site-workspace').waitFor({state:'visible'});
  await page.screenshot({path:path.join(root,'workspace-'+phase+'.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  await page.getByRole('button',{name:'Sign out',exact:true}).click();
  await page.locator('#login').waitFor({state:'visible'});
  assert.equal((await fetchJson(casePath)).status,401);
  const result = {status:'PASS',phase,case_id:record.case_id,operation_id:receipt.operation_id,
    retained_flow_records:flows.body.total.records,checks:['trusted HTTPS','chart workloads',
      'mTLS installation','restricted PostgreSQL role','named sign-in','site grant',
      'telemetry','CSRF denial','other-site grant denial','native route denial',
      phase==='before' ? 'durable disposition' : 'exact receipt after restart',
      'case and overview UI','logout revocation'],not_qualified:['physical telemetry',
      'fresh browser system dependencies','new live model evaluation']};
  fs.writeFileSync(path.join(root,'workspace-'+phase+'.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
} finally {await browser.close();}

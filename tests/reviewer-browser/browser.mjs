import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import https from 'node:https';
import { randomUUID, X509Certificate } from 'node:crypto';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { createRequire } from 'node:module';
import { setTimeout as pause } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const serviceDir = process.env.REVIEWER_API_DIR;
const viewDir = fileURLToPath(new URL('../../ui/convergence-hud/', import.meta.url));
const { Pool } = createRequire(path.join(serviceDir, 'package.json'))('pg');
const {createRelay, createReceiverClient} = createRequire(path.join(serviceDir,'package.json'))('./src/reviewer/relay');
const fixture = process.env.REVIEWER_FIXTURE_DIR;
const file = (key) => path.join(fixture, key);
const url = new URL(process.env.REVIEWER_TEST_DATABASE_URL);
const pg = {
  PGHOST: url.hostname,
  PGPORT: url.port || '5432',
  PGUSER: decodeURIComponent(url.username),
  PGPASSWORD: decodeURIComponent(url.password),
  PGDATABASE: decodeURIComponent(url.pathname.slice(1)),
};
const schema = 'browser_' + randomUUID().replaceAll('-', '');
const baseEnv = { ...process.env, ...pg, PGOPTIONS: '-c search_path=' + schema };
async function availablePort() {
  const s = net.createServer().listen(0, '127.0.0.1');
  await once(s, 'listening');
  const port = s.address().port;
  await new Promise((ok) => s.close(ok));
  return port;
}
function command(cwd, args, input) {
  const r = spawnSync(process.execPath, args, { cwd, env: baseEnv, input, encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
}
async function launch(t, cwd, args, env, runtime = process.execPath) {
  const worker = spawn(runtime, args, {
    cwd,
    env: { ...baseEnv, ...env },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let stderr = '';
  worker.stderr.on('data', (v) => (stderr += v));
  const ended = once(worker, 'exit');
  t.after(async () => {
    if (worker.exitCode === null && worker.signalCode === null) worker.kill();
    const timer = setTimeout(() => worker.kill('SIGKILL'), 1500);
    try {
      await ended;
    } finally {
      clearTimeout(timer);
    }
  });
  await Promise.race([
    once(worker.stdout, 'data'),
    ended.then(() => {
      throw Error('start failed ' + stderr);
    }),
    pause(10000, null, { ref: false }).then(() => {
      throw Error('start timed out');
    }),
  ]);
}
test(
  'separate Chromium reviewers, production server entry points and real durable audit',
  { timeout: 65000 },
  async (t) => {
    const admin = new Pool({
      host: pg.PGHOST,
      port: +pg.PGPORT,
      user: pg.PGUSER,
      password: pg.PGPASSWORD,
      database: pg.PGDATABASE,
    });
    await admin.query('CREATE SCHEMA ' + schema);
    const db = new Pool({
      host: pg.PGHOST,
      port: +pg.PGPORT,
      user: pg.PGUSER,
      password: pg.PGPASSWORD,
      database: pg.PGDATABASE,
      options: baseEnv.PGOPTIONS,
    });
    t.after(async () => {
      await db.end();
      try {
        await admin.query('DROP SCHEMA ' + schema + ' CASCADE');
      } finally {
        await admin.end();
      }
    });
    await db.query(fs.readFileSync(path.join(serviceDir, 'src/db/schema.sql'), 'utf8'));
    command(serviceDir, ['reviewer-service.js', 'migrate']);
    await db.query("INSERT INTO sites(id,name) VALUES ('red','Red'),('blue','Blue')");
    for (const [name, role] of [
      ['reader', 'viewer'],
      ['responder', 'operator'],
    ])
      command(serviceDir, ['reviewer-accounts.js', 'create', name, role, 'red'], 'fixture-only-password');
    const result = await db.query(
      "INSERT INTO events(site_id,message,alert_name) VALUES ('red','Synthetic review case','SuricataIntrusion') RETURNING id",
    );
    const caseId = result.rows[0].id;
    const pin = new X509Certificate(fs.readFileSync(file('installation.pem'))).fingerprint256;
    fs.writeFileSync(
      file('enrolled.json'),
      JSON.stringify([{ installation: 'browser-fixture', fingerprint256: pin }]),
    );
    const apiPort = await availablePort(),
      uiPort = await availablePort();
    const origin = 'https://reviewer.test:' + uiPort;
    await launch(t, serviceDir, ['reviewer-service.js'], {
      REVIEWER_SERVICE_PORT: '' + apiPort,
      REVIEWER_INVESTIGATION_ENABLED: '1',
      REVIEWER_CLIENT_CA: file('root.pem'),
      REVIEWER_TLS_CERT: file('localhost.pem'),
      REVIEWER_TLS_KEY: file('localhost.key'),
      REVIEWER_INSTALLATIONS_FILE: file('enrolled.json'),
    });
    await launch(t, viewDir, ['reviewer/start.mjs'], {
      REVIEWER_HUD_PORT: '' + uiPort,
      REVIEWER_HUD_ORIGIN: origin,
      REVIEWER_HUD_CERT: file('reviewer.test.pem'),
      REVIEWER_HUD_KEY: file('reviewer.test.key'),
      REVIEWER_SERVICE_ORIGIN: 'https://localhost:' + apiPort,
      REVIEWER_SERVICE_CA: file('root.pem'),
      REVIEWER_INSTALLATION_KEY: file('installation.key'),
      REVIEWER_INSTALLATION_CERT: file('installation.pem'),
    });
    const browser = await chromium.launch({
      executablePath: process.env.REVIEWER_CHROMIUM || undefined,
      headless: true,
      env: { ...process.env, HOME: file('browser-home'), XDG_DATA_HOME: file('browser-home/.local/share') },
      args: [
        '--no-proxy-server',
        '--host-resolver-rules=MAP reviewer.test 127.0.0.1,MAP hostile.test 127.0.0.1',
      ],
    });
    t.after(() => browser.close());
    async function openCase(page) {
      await page.getByLabel('Case ID').fill(caseId);
      await page.getByRole('button', { name: 'Open case' }).click();
      await page.getByText('Synthetic review case', { exact: true }).first().waitFor();
    }
    async function login(name, mobile = false) {
      const ctx = await browser.newContext({
        viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 800 },
      });
      const page = await ctx.newPage();
      page.setDefaultTimeout(6000);
      const errs = [];
      page.on('pageerror', (e) => errs.push(String(e)));
      await page.goto(origin);
      await page.getByLabel('Username').fill(name);
      await page.getByLabel('Password').fill('fixture-only-password');
      await page.getByRole('button', { name: 'Sign in' }).click();
      await openCase(page);
      assert.ok(
        (await ctx.cookies()).some(
          (c) => c.secure && c.httpOnly && c.sameSite === 'Strict' && c.name.startsWith('__Host-'),
        ),
      );
      assert.deepEqual(errs, []);
      return { ctx, page };
    }
    const reader = await login('reader', true);
    const operator = await login('responder');
    assert.equal(await reader.page.getByRole('button', { name: 'Save disposition' }).isVisible(), false);
    await operator.page
      .getByLabel('Evidence and reason')
      .fill('Synthetic packet trace confirmed expected activity');
    await operator.page.getByLabel('Outcome').selectOption('expected_activity');
    // Commit through the real API, then discard only the browser's response.
    const wire = await operator.ctx.newCDPSession(operator.page);
    await wire.send('Fetch.enable', {
      patterns: [{ urlPattern: '*/disposition', requestStage: 'Response' }],
    });
    const interrupted = once(wire, 'Fetch.requestPaused').then(async ([event]) => {
      await wire.send('Fetch.failRequest', { requestId: event.requestId, errorReason: 'Failed' });
      return { status: event.responseStatusCode, id: JSON.parse(event.request.postData).operationId };
    });
    await operator.page.getByRole('button', { name: 'Save disposition' }).click();
    const committed = await interrupted;
    await wire.detach();
    await operator.page.getByText(/The outcome is unknown/).waitFor();
    assert.equal(committed.status, 200);
    assert.equal(await operator.page.locator('#operation-id').innerText(), committed.id);
    await operator.page.reload();
    const recovery = operator.page.waitForResponse(
      (r) => r.request().method() === 'GET' && r.url().endsWith('/disposition/' + committed.id),
    );
    await openCase(operator.page);
    assert.equal((await recovery).status(), 200);
    await operator.page.locator('#operation-note').getByText(/recorded by responder/).waitFor();
    assert.equal(await operator.page.locator('#operation-id').innerText(), committed.id);
    const audit = await db.query(
      'SELECT actor_id, actor_name AS reviewed_by_name, operation_id FROM reviewer_case_dispositions',
    );
    assert.equal(audit.rowCount, 1);
    assert.equal(audit.rows[0].operation_id, committed.id);
    assert.equal(audit.rows[0].reviewed_by_name, 'responder');
    await reader.page.getByRole('button', { name: 'Open case' }).click();
    await reader.page
      .locator('#history')
      .getByText(/responder/)
      .first()
      .waitFor();
    assert.equal(await reader.page.getByRole('button', {name:'Request investigation',exact:true}).isVisible(), false);
    await operator.page.getByLabel('Investigation question').fill('Inspect the synthetic counters and preserve the original observation');
    const requestWire = await operator.ctx.newCDPSession(operator.page);
    await requestWire.send('Fetch.enable', {patterns:[{urlPattern:'*/investigation',requestStage:'Response'}]});
    const lostRequest = once(requestWire,'Fetch.requestPaused').then(async ([event]) => {
      await requestWire.send('Fetch.failRequest',{requestId:event.requestId,errorReason:'Failed'});
      return {status:event.responseStatusCode,id:JSON.parse(event.request.postData).operationId};
    });
    await operator.page.getByRole('button',{name:'Request investigation',exact:true}).click();
    const acceptedRequest = await lostRequest; await requestWire.detach();
    assert.equal(acceptedRequest.status,200);
    await operator.page.locator('#investigation-status').getByText(/outcome is unknown/).waitFor();
    await operator.page.reload(); await openCase(operator.page);
    await operator.page.locator('#investigation-status').getByText(/Requested by responder/).waitFor();
    assert.equal(await operator.page.locator('#investigation-operation').innerText(),acceptedRequest.id);
    assert.equal((await db.query('SELECT 1 FROM reviewer_investigations')).rowCount,1);
    assert.equal((await db.query('SELECT 1 FROM reviewer_investigation_outbox')).rowCount,1);
    const notes=(await db.query('SELECT investigation_notes FROM events WHERE id=$1',[caseId])).rows[0].investigation_notes;
    assert.equal(notes.split(acceptedRequest.id).length-1,1);
    assert.match(notes,/responder/);
    const stored=await operator.page.evaluate(()=>Object.entries(sessionStorage));
    assert.ok(!JSON.stringify(stored).includes('synthetic counters'));

    const receiverPort = await availablePort();
    await launch(t, fileURLToPath(new URL('.',import.meta.url)), ['receiver_fixture.py'], {
      REVIEWER_FIXTURE_DIR:fixture, REVIEWER_RECEIVER_TEST_PORT:String(receiverPort),
    },process.env.REVIEWER_RECEIVER_PYTHON || 'python3');
    const receiver = createReceiverClient({origin:'https://localhost:'+receiverPort,token:'x'.repeat(40),ca:fs.readFileSync(file('root.pem'))});
    t.after(()=>receiver.close());
    let admissions=0;
    const relay=createRelay(db,{
      async submit(envelope) {
        const reply=await receiver.submit(envelope);
        if(++admissions===1)throw Error('Synthetic lost receiver acknowledgement');
        return reply;
      },
      read:id=>receiver.read(id),
    },{retryMs:1,pollMs:1});
    let delivery;
    for(let attempt=0;attempt<200;attempt++) {
      await relay.tick();
      delivery=(await db.query('SELECT * FROM reviewer_investigation_outbox')).rows[0];
      if(delivery.state==='completed')break;
      await pause(25);
    }
    assert.equal(delivery.state,'completed');assert.equal(admissions,2);
    const hooks=JSON.parse(fs.readFileSync(file('hook-calls.json')));
    assert.equal(hooks.length,1);assert.equal(hooks[0].delivery_id,delivery.delivery_id);
    assert.equal(hooks[0].job_id,delivery.job_id);assert.equal(hooks[0].actor_name,'responder');
    assert.equal(hooks[0].site_id,'red');
    assert.equal((await db.query('SELECT 1 FROM reviewer_investigations')).rowCount,1);
    assert.equal((await db.query('SELECT investigation_notes FROM events WHERE id=$1',[caseId])).rows[0].investigation_notes,notes);
    await operator.page.getByRole('button',{name:'Check investigation request',exact:true}).click();
    await operator.page.locator('#investigation-status').getByText(/Investigation completed/).waitFor();

    const cross = await operator.page.evaluate(
      async ({ caseId }) => (await fetch('/review/sites/blue/cases/' + caseId)).status,
      { caseId },
    );
    assert.equal(cross, 403);
    for (const native of [
      '/api/auth/login',
      '/api/hud/owner/session',
      '/api/ssh/capabilities',
      '/api/models',
    ]) {
      assert.equal(await operator.page.evaluate(async (url) => (await fetch(url)).status, native), 404);
    }
    const evil = https.createServer(
      { cert: fs.readFileSync(file('hostile.test.pem')), key: fs.readFileSync(file('hostile.test.key')) },
      (_req, res) => res.end('<!doctype html><title>Other</title>'),
    );
    evil.listen(0, '127.0.0.1');
    await once(evil, 'listening');
    t.after(() => new Promise((ok) => evil.close(ok)));
    const attack = await operator.ctx.newPage();
    await attack.goto('https://hostile.test:' + evil.address().port);
    const caught = attack.waitForResponse((r) => r.url() === origin + '/review/sign-out');
    await attack.evaluate(async (url) => {
      await fetch(url, { method: 'POST', mode: 'no-cors', credentials: 'include', body: '{}' });
    }, origin + '/review/sign-out');
    assert.equal((await caught).status(), 403);
    await attack.close();
    await operator.page.getByRole('button', { name: 'Sign out' }).click();
    await operator.page.getByLabel('Username').waitFor();
    assert.equal(await operator.page.locator('#detail').isVisible(), false);
    assert.equal(
      await reader.page.evaluate(async () => (await (await fetch('/review/session')).json()).user.name),
      'reader',
    );
    assert.equal((await db.query('SELECT 1 FROM reviewer_case_dispositions')).rowCount, 1);
    t.diagnostic(
      'Real CLI startup, named accounts, trusted HTTPS, mTLS and PostgreSQL. Independent mobile viewer and desktop operator. Committed request with a lost response recovered the same receipt after reload, with one audit row and a named actor. Cross-site, native routes and hostile origin denied; separate logout. Lost investigation browser response and receiver acknowledgement recovered one PostgreSQL note, one SQLite job and one counted correlated synthetic hook through the real HTTPS relay.',
    );
  },
);

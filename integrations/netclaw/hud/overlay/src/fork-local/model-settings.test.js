import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import express from 'express';
import { randomUUID } from 'node:crypto';
import { register } from '../../server.local.js';
import { Bindings } from '../hud-server/bindings.js';
import { mountAssessmentRoutes } from '../hud-server/assessment-routes.js';
import { createLocalAccess } from '../security/local-access.js';
import { readModelConfig, runtimeEnvironment } from './model-config.js';
import { publicChatModels, resolveChatModel } from '../hud-server/chat-models.js';
import { gatewayEnvironment } from '../hud-server/chat-runtime.js';

test('HUD resolves selected-state model and gateway references and offers concrete wildcard choices', t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'netclaw-model-env-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const config = path.join(directory, 'openclaw.json');
  fs.writeFileSync(path.join(directory, '.env'), 'NETCLAW_BRAIN_MODEL=p/chat\nOPENCLAW_GATEWAY_TOKEN=private-local-token\n');
  fs.writeFileSync(config, JSON.stringify({ agents: { defaults: { model: { primary: '${NETCLAW_BRAIN_MODEL}' }, models: { 'p/*': {} } } }, models: { providers: { p: { models: [{ id: 'chat' }, { id: 'small' }] } } }, gateway: { auth: { token: '${OPENCLAW_GATEWAY_TOKEN}' } } }));
  const loaded = readModelConfig(config, {}), catalog = publicChatModels(loaded, null);
  assert.equal(catalog.defaultModel, 'p/chat');
  assert.deepEqual(catalog.models.map(row => row.label), ['p/chat', 'p/small']);
  assert.equal(resolveChatModel(loaded, catalog.models[1].id), 'p/small');
  assert.doesNotMatch(JSON.stringify(catalog), /\$\{|\*|private-local-token/);
  assert.equal(gatewayEnvironment(loaded, 18789, config, runtimeEnvironment(config, {})).OPENCLAW_GATEWAY_TOKEN, 'private-local-token');
});

test('model aliases cannot project gateway credentials through the native catalog', t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hud-model-alias-'));
  t.after(() => fs.rmSync(dir, {recursive:true, force:true}));
  const configPath = path.join(dir, 'openclaw.json');
  fs.writeFileSync(configPath, JSON.stringify({
    agents: {defaults: {model: '${OPENCLAW_GATEWAY_TOKEN}', models: {'${OPENCLAW_GATEWAY_TOKEN}': {}}}},
    gateway: {auth: {token: '${OPENCLAW_GATEWAY_TOKEN}'}},
  }));
  const secret = 'sensitive-not-a-model';
  const config = readModelConfig(configPath, {OPENCLAW_GATEWAY_TOKEN:secret});
  assert.equal(config.gateway.auth.token, secret);
  assert.ok(!JSON.stringify(publicChatModels(config, null)).includes(secret));
  fs.symlinkSync(configPath, path.join(dir, '.env'));
  assert.throws(() => readModelConfig(configPath, {}), /regular file/);
});

test('the bound owner can inspect model identifiers but cannot apply global model settings', async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hud-model-readonly-'));
  t.after(() => fs.rmSync(dir, {recursive:true, force:true}));
  const configPath = path.join(dir, 'openclaw.json');
  const config = JSON.stringify({agents: {
    defaults: {model: {primary:'provider/chat'}},
    entries: {alert: {model: {primary:'provider/alert', private:'fixture-private'}}},
  }, hooks: {mappings: [{match:{path:'alert'}, model:'provider/hook', authorization:'fixture-private'}]}});
  fs.writeFileSync(configPath, config);
  const envPath = path.join(dir, '.env');
  const original = 'NETCLAW_ALERT_FALLBACK_MODEL=provider/fallback\nOLLAMA_API_KEY=fixture-private\n';
  fs.writeFileSync(envPath, original);
  const installation = {kind:'openclaw', installationId:randomUUID(), configPath};
  const bindings = new Bindings(path.join(dir, 'bindings'), Date.now, installation);
  const ports = {api:0, ui:0};
  const owner = {installation, hudBindings:bindings, ports, hudHost:'127.0.0.1', protectedProfile:false,
    openLabWorkspace:null, env:key => key === 'CONVERGENCE_HUD_LOCAL_OWNER' ? '1' : ''};
  const app = express();
  app.use(express.json());
  mountAssessmentRoutes(app, {bindings});
  register(app, {ROOT:dir, RAG_DATA_DIR:dir, RAG_INTAKE_DIR:dir,
    broadcastWS() {}, callRagTool() {}, ragStartProgressPolling() {}, readText() {}, ownerContext:owner});
  const server = await new Promise(resolve => { const listening = app.listen(0, '127.0.0.1', () => resolve(listening)); });
  t.after(() => new Promise(resolve => server.close(resolve)));
  ports.api = server.address().port;
  owner.allowedLocalRequest = createLocalAccess(ports, {host:'127.0.0.1'});
  const origin = 'http://127.0.0.1:' + ports.api;
  assert.equal((await fetch(origin + '/api/models')).status, 403);
  const session = await fetch(origin + '/api/hud/session', {method:'POST', headers:{Origin:origin}});
  assert.equal(session.status, 200);
  const headers = {Cookie:session.headers.get('set-cookie').split(';')[0],
    'X-NetClaw-Installation':installation.installationId, Origin:origin, 'Content-Type':'application/json'};
  const status = await fetch(origin + '/api/models', {headers});
  assert.equal(status.status, 200);
  const body = await status.json();
  assert.equal(body.selectionSupported, false);
  assert.equal(body.sot.fallback, 'provider/fallback');
  assert.deepEqual(body.live, {defaultsPrimary:'provider/chat', alertAgent:'provider/alert', hookAlert:{model:'provider/hook'}});
  assert.ok(!JSON.stringify(body).includes('fixture-private'));
  assert.ok(!JSON.stringify(body).includes(dir));
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
    const result = await fetch(origin + '/api/models', {method, headers, body:JSON.stringify({
      preset:'openai', brain:'a/b\nATTACKER=value', restart:true,
    })});
    assert.equal(result.status, 405);
    assert.equal(result.headers.get('allow'), 'GET, HEAD');
  }
  assert.equal(fs.readFileSync(configPath, 'utf8'), config);
  assert.equal(fs.readFileSync(envPath, 'utf8'), original);
});

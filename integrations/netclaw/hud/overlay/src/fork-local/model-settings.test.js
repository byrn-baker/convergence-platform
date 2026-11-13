import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
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

test('model apply updates current agent entries, respects selected state, and clears removed fallbacks', { skip: process.platform === 'win32' }, t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'netclaw-model-settings-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const state = path.join(directory, 'state'), bin = path.join(directory, 'bin');
  fs.mkdirSync(state); fs.mkdirSync(bin);
  fs.writeFileSync(path.join(bin, 'openclaw'), '#!/bin/sh\nexit 0\n', { mode: 0o700 });
  const config = path.join(state, 'openclaw.json'), envFile = path.join(directory, 'repo.env');
  fs.writeFileSync(config, JSON.stringify({ agents: { defaults: { model: { primary: 'p/old' } }, entries: { main: {}, alert: { tools: { profile: 'minimal' }, model: 'p/old-alert' } } }, hooks: { mappings: [{ match: { path: 'alert' }, agentId: 'alert', model: 'p/old-alert' }] } }));
  fs.writeFileSync(envFile, 'NETCLAW_BRAIN_MODEL=p/chat\nNETCLAW_ALERT_TRIAGE_MODEL=p/investigate\nNETCLAW_ALERT_FALLBACK_MODEL=\n');
  fs.writeFileSync(path.join(state, '.env'), 'NETCLAW_ALERT_FALLBACK_MODEL=p/obsolete\n');
  const result = spawnSync('bash', [new URL('../../../../scripts/netclaw-apply-models.sh', import.meta.url).pathname, 'apply', '--no-restart'], {
    env: { ...process.env, PATH: `${bin}:${process.env.PATH}`, NETCLAW_ENV_FILE: envFile, OPENCLAW_STATE_DIR: state, OPENCLAW_HOME: path.join(directory, 'launch-home'), OPENCLAW_CONFIG_PATH: config }, encoding: 'utf8', timeout: 15000,
  });
  assert.equal(result.status, 0, result.stderr);
  const updated = JSON.parse(fs.readFileSync(config));
  assert.deepEqual(updated.agents.entries.alert.model, { primary: 'p/investigate', fallbacks: [] });
  assert.deepEqual(updated.agents.entries.alert.tools, { profile: 'minimal' });
  assert.equal(updated.agents.list, undefined, 'never adds obsolete agents.list beside agents.entries');
  assert.equal(updated.hooks.mappings[0].model, 'p/investigate');
  assert.match(fs.readFileSync(path.join(state, 'gateway.systemd.env'), 'utf8'), /NETCLAW_ALERT_FALLBACK_MODEL=\n/);
  assert.equal(fs.existsSync(path.join(directory, 'launch-home', 'gateway.systemd.env')), false);
  const preset = spawnSync('bash', [new URL('../../../../scripts/netclaw-apply-models.sh', import.meta.url).pathname, 'preset', 'openai', '--no-restart'], {
    env: { ...process.env, PATH: `${bin}:${process.env.PATH}`, NETCLAW_ENV_FILE: envFile, OPENCLAW_STATE_DIR: state, OPENCLAW_CONFIG_PATH: config }, encoding: 'utf8', timeout: 15000,
  });
  assert.equal(preset.status, 0, preset.stderr);
  const openai = JSON.parse(fs.readFileSync(config));
  assert.deepEqual(openai.agents.defaults.model, { primary: 'openai/gpt-6.1-sol', fallbacks: ['openai/gpt-6-luna'] });
  assert.deepEqual(openai.agents.entries.alert.model, { primary: 'openai/gpt-6-luna', fallbacks: [] });
  assert.equal(openai.hooks.mappings[0].model, 'openai/gpt-6-luna');
  assert.equal(openai.agents.defaults.models['openai/gpt-6.1-sol'].agentRuntime.id, 'openclaw');
  assert.equal(openai.agents.defaults.models['openai/gpt-6-luna'].agentRuntime.id, 'openclaw');
});

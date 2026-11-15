// FORK-LOCAL: read the same selected-state environment as the native gateway.
import fs from 'node:fs';
import path from 'node:path';
import { parseEnvData, regularTarget } from '../security/private-files.js';

export function runtimeEnvironment(configPath, env = process.env) {
  const result = {};
  for (const name of ['.env', 'gateway.systemd.env']) {
    try {
      const file = path.join(path.dirname(configPath), name);
      regularTarget(file);
      Object.assign(result, parseEnvData(fs.readFileSync(file, 'utf8')));
    }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  return { ...result, ...env };
}

export function readModelConfig(configPath, env = process.env) {
  regularTarget(configPath);
  const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  const values = runtimeEnvironment(configPath, env);
  const resolve = value => typeof value === 'string' ? value.replace(
    /\$\{(NETCLAW_BRAIN_MODEL|NETCLAW_ALERT_TRIAGE_MODEL|NETCLAW_ALERT_FALLBACK_MODEL)\}/g,
    (match, key) => values[key] ?? match) : value;
  const credential = value => typeof value === 'string' ? value.replace(
    /\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (match, key) => values[key] ?? match) : value;
  const model = value => typeof value === 'string' ? resolve(value) : value && { ...value, primary: resolve(value.primary), ...(Array.isArray(value.fallbacks) ? { fallbacks: value.fallbacks.map(resolve) } : {}) };
  const agent = value => {
    if (!value) return;
    if (value.model) value.model = model(value.model);
    if (value.models) value.models = Object.fromEntries(Object.entries(value.models).map(([key, metadata]) => [resolve(key), metadata]));
  };
  agent(config.agents?.defaults);
  Object.values(config.agents?.entries || {}).forEach(agent);
  (config.agents?.list || []).forEach(agent);
  for (const mapping of config.hooks?.mappings || []) if (mapping.model) mapping.model = resolve(mapping.model);
  // Authentication stays server-side and is needed by CLI catalog discovery.
  if (config.gateway?.auth?.token) config.gateway.auth.token = credential(config.gateway.auth.token);
  if (config.gateway?.auth?.password) config.gateway.auth.password = credential(config.gateway.auth.password);
  return config;
}

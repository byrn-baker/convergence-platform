import { createHash } from 'node:crypto';
import { gatewayAgentId } from './gateway-agent.js';

export function chatModelCatalog(config, discovered = []) {
  const defaults = config?.agents?.defaults || {};
  const agentId = gatewayAgentId(config);
  const agent = config?.agents?.entries?.[agentId]
    || config?.agents?.list?.find(a => a.id === agentId) || {};
  const model = agent.model ?? defaults.model;
  const primary = typeof model === 'string' ? model : model?.primary;
  const refs = [...new Set([primary, ...Object.keys(defaults.models || {}), ...Object.keys(agent.models || {}),
    ...(discovered || []).filter(m => m.available === true && typeof m.provider === 'string' && typeof m.id === 'string' && /^[a-zA-Z0-9_.-]+$/.test(m.provider) && /^[a-zA-Z0-9_.:/-]+$/.test(m.id)).map(m => `${m.provider}/${m.id}`),
    ...(Array.isArray(model?.fallbacks) ? model.fallbacks : [])])]
    .filter(ref => typeof ref === 'string' && ref.length <= 512 && !/[\r\n\0]/.test(ref));
  const entries = refs.map(ref => ({
    id: createHash('sha256').update(ref).digest('hex').slice(0, 24),
    label: ref.split('@')[0], ref,
  })).filter((entry, index, all) => all.findIndex(other => other.label === entry.label) === index);
  for (const entry of entries) {
    const metadata = (discovered || []).find(m => `${m.provider}/${m.id}` === entry.label);
    entry.efforts = (metadata?.thinkingLevels || []).map(l => l.id).filter(l => ['off','minimal','low','medium','high','xhigh','max','ultra'].includes(l));
    entry.defaultEffort = entry.efforts.includes(metadata?.thinkingDefault) ? metadata.thinkingDefault : null;
  }
  return { primary, entries };
}
export function publicChatModels(config, discovered = []) {
  const { primary, entries } = chatModelCatalog(config, discovered);
  return { defaultModel: typeof primary === 'string' ? primary.split('@')[0] : null,
    discoveryAvailable: discovered !== null,
    models: entries.map(({ id, label, efforts, defaultEffort }) => ({ id, label, efforts, defaultEffort })) };
}
export function resolveChatModel(config, selection, discovered = []) {
  if (selection === undefined) return undefined; // Existing clients retain agent routing.
  const { primary, entries } = chatModelCatalog(config, discovered);
  if (selection === '') return primary;
  const match = typeof selection === 'string' && entries.find(entry => entry.id === selection);
  if (!match) throw Error('That model is no longer configured. Refresh the model list and choose again.');
  return match.ref;
}

export function resolveChatEffort(config, selection, effort, discovered = []) {
  if (effort === undefined) return undefined;
  if (effort === '') return '';
  const { primary, entries } = chatModelCatalog(config, discovered);
  const model = entries.find(e => selection ? e.id === selection : e.ref === primary);
  if (typeof effort !== 'string' || !model?.efforts.includes(effort)) throw Error('Choose an effort supported by this model.');
  return effort;
}

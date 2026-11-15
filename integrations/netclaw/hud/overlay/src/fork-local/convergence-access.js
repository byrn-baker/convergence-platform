import { bootstrapRuntime, runtimeInfo } from '../shared/runtime-client.js';

let binding;

function localFetch(path, options = {}, installationId) {
  const headers = new Headers(options.headers);
  if (installationId) headers.set('X-NetClaw-Installation', installationId);
  return fetch(path, { ...options, headers, credentials: 'same-origin', redirect: 'error', cache: 'no-store' });
}

async function ownerId() {
  const runtime = runtimeInfo() || await bootstrapRuntime();
  if (runtime.kind !== 'openclaw' || runtime.access?.mode === 'open-lab' || runtime.capabilities?.protectedProfile) return null;
  if (binding?.id !== runtime.installationId) {
    const id = runtime.installationId;
    binding = { id, ready: localFetch('/api/hud/session', { method: 'POST' }, id).then(r => r.ok) };
  }
  return (await binding.ready) ? binding.id : null;
}

export const convergenceAccess = {
  models(options) {
    return ownerFetch('/api/models', options);
  },
  async available() {
    const runtime = runtimeInfo() || await bootstrapRuntime();
    return runtime.capabilities?.siteOperations === true;
  },
  async request(path, options = {}) {
    if (!path.startsWith('/api/home/') || (options.method || 'GET') !== 'GET') throw Error('Open the signed-in reviewer to change a case.');
    if (path === '/api/home/status') {
      const initial = await localFetch(path, options);
      if (!initial.ok || !(await initial.clone().json()).localOwnerMode) return initial;
      const id = await ownerId();
      return id ? localFetch(path, options, id) : initial;
    }
    if (!binding || binding.id !== runtimeInfo()?.installationId || !await binding.ready) throw Error('HUD owner session unavailable.');
    return localFetch(path, options, binding.id);
  }
};

// Fork-only administrative views share the same server-verified local owner.
export async function ownerFetch(path, options = {}) {
  const status = await convergenceAccess.request('/api/home/status');
  if (!status.ok || !(await status.json()).localOwnerMode || !binding?.id) throw Error('Local HUD owner session required.');
  return localFetch(path, options, binding.id);
}

// Only an ordinary, explicitly loopback-only installation can bind a browser
// to the NetClaw owner. A network HUD can still advertise the reviewer origin.
const loopbacks = new Set(['127.0.0.1', '::1', '::ffff:127.0.0.1']);
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;

export function localOwnerMode(ctx) {
  const { installation, hudBindings, ports } = ctx;
  return ctx.env('CONVERGENCE_HUD_LOCAL_OWNER') === '1' &&
    loopbacks.has(ctx.hudHost) && ctx.openLabWorkspace === null &&
    ctx.protectedProfile === false && typeof ctx.allowedLocalRequest === 'function' &&
    Number.isInteger(ports?.api) && Number.isInteger(ports?.ui) &&
    installation?.kind === 'openclaw' && uuid.test(installation.installationId) &&
    hudBindings?.installation?.installationId === installation.installationId &&
    hudBindings.installation.kind === installation.kind &&
    hudBindings.cookieName === 'nc_hud_' + installation.installationId.replaceAll('-', '') &&
    typeof hudBindings.read === 'function';
}

function rawHeaderValues(req, name) {
  const values = [];
  for (let i = 0; i < req.rawHeaders.length; i += 2) {
    if (req.rawHeaders[i].toLowerCase() === name) values.push(req.rawHeaders[i + 1]);
  }
  return values;
}

function loopbackRequest(req, ctx) {
  if (!localOwnerMode(ctx) || !loopbacks.has(req.socket.remoteAddress) || !ctx.allowedLocalRequest(req)) return false;
  const [host, ...otherHosts] = rawHeaderValues(req, 'host');
  const authorities = [ctx.ports.ui, ctx.ports.api].flatMap(port =>
    ['localhost', '127.0.0.1', '[::1]'].map(localhost => localhost + ':' + port));
  if (otherHosts.length || !authorities.includes(host)) return false;
  const origins = rawHeaderValues(req, 'origin');
  if (origins.length > 1 || (origins.length && origins[0] !== 'http://' + host && origins[0] !== 'https://' + host)) return false;
  const fetchSites = rawHeaderValues(req, 'sec-fetch-site');
  if (fetchSites.length > 1 || (fetchSites.length && !['same-origin', 'none'].includes(fetchSites[0]))) return false;
  return !req.rawHeaders.some((name, i) => i % 2 === 0 &&
    /^(?:forwarded$|x-forwarded-|x-original-|x-rewrite-|x-real-ip$|proxy-authorization$|authorization$)/i.test(name));
}

export function mayBootstrap(req, ctx) {
  return loopbackRequest(req, ctx);
}

export function hasOwner(req, ctx) {
  if (!loopbackRequest(req, ctx)) return false;
  const { installation, hudBindings } = ctx;
  const ids = rawHeaderValues(req, 'x-netclaw-installation');
  if (ids.length !== 1 || ids[0] !== installation.installationId) return false;
  // The host normalizes req.headers.cookie for legacy callers. Inspect the
  // wire header to reject legacy, foreign or duplicate ownership claims.
  const cookies = rawHeaderValues(req, 'cookie');
  if (cookies.length !== 1) return false;
  const claims = cookies[0].split(';').map(s => s.trim()).filter(s => /^nc_hud(?:_|=)/.test(s));
  const prefix = hudBindings.cookieName + '=';
  if (claims.length !== 1 || !claims[0].startsWith(prefix)) return false;
  const cookie = claims[0].slice(prefix.length);
  if (!/^[a-f0-9]{48}$/.test(cookie)) return false;
  try {
    const owner = hudBindings.read(cookie);
    return owner.version === 2 && owner.runtime === installation.kind &&
      owner.installationId === installation.installationId &&
      Number.isFinite(owner.expires) && owner.expires > Date.now();
  } catch {
    return false;
  }
}

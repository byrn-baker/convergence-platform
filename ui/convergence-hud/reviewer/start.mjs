import fs from 'node:fs';
import net from 'node:net';
import {pathToFileURL} from 'node:url';
import {createOidcClient} from './oidc.mjs';
import {createReviewerClient} from './service-client.mjs';
import {createReviewerServer} from './server.mjs';
import {identity, watchTls} from './tls-reload.mjs';

// Deliberately static dependency graph: no directory traversal or host modules.
function siteAssets() {
  const names = ['HomeView.js','ReviewerLink.js','IPContext.js','CaptureContext.js','JevReview.js',
    'IncidentReview.js','DiaryExplorer.js','FlowExplorer.js','SecurityExplorer.js','SecurityHistory.js',
    'VMSecurity.js','HostHeartbeat.js','world-regions.js','home.css','dashboard.css','vm-security.css'];
  return names.map(file => ['/site-assets/' + file, {
    content:fs.readFileSync(new URL('../modules/convergence/' + file, import.meta.url)),
    type:file.endsWith('.css') ? 'text/css; charset=utf-8' : 'text/javascript; charset=utf-8',
  }]);
}

export function reviewerAssets() {
  const asset = (file, type) => ({content: fs.readFileSync(new URL('public/' + file, import.meta.url)), type});
  return new Map([
    ...siteAssets(),
    ['/', asset('index.html', 'text/html; charset=utf-8')],
    ['/reviewer.css', asset('reviewer.css', 'text/css; charset=utf-8')],
    ...['reviewer.js', 'pending-operations.js', 'investigation.js', 'telemetry.js', 'jev.js', 'queue.js', 'bulk.js'].map(file => ['/' + file, asset(file, 'text/javascript; charset=utf-8')]),
  ]);
}

function start() {
  const env = process.env;
  const file = name => fs.readFileSync(env[name]);
  const host = env.REVIEWER_HUD_HOST || '127.0.0.1';
  const port = Number(env.REVIEWER_HUD_PORT || 3444);
  if (!net.isIP(host) || !Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid listener address');
  const client = createReviewerClient({origin: env.REVIEWER_SERVICE_ORIGIN, tls: {
    ca: file('REVIEWER_SERVICE_CA'), cert: file('REVIEWER_INSTALLATION_CERT'), key: file('REVIEWER_INSTALLATION_KEY'),
  }});
  // The issuer, client and secret file are required together.
  const provider = [env.REVIEWER_OIDC_ISSUER, env.REVIEWER_OIDC_CLIENT_ID, env.REVIEWER_OIDC_CLIENT_SECRET_FILE];
  if (provider.some(Boolean) && !provider.every(Boolean)) throw new Error('Incomplete identity provider settings');
  const oidc = provider[0] ? createOidcClient({issuer: provider[0], clientId: provider[1],
    clientSecret: fs.readFileSync(provider[2], 'utf8').trim(), origin: env.REVIEWER_HUD_ORIGIN}) : undefined;
  const addresses = (env.REVIEWER_PASSWORD_SIGN_IN_ADDRESSES || '').split(',').map(item => item.trim()).filter(Boolean);
  if (!addresses.every(item => net.isIP(item))) throw new Error('Invalid password sign-in address');
  const passwordSignIn = {enabled: env.REVIEWER_PASSWORD_SIGN_IN !== '0',
    addresses: addresses.length ? new Set(addresses) : null};
  const server = createReviewerServer({
    origin: env.REVIEWER_HUD_ORIGIN, assets: reviewerAssets(), client, oidc, passwordSignIn,
    tls: {cert: file('REVIEWER_HUD_CERT'), key: file('REVIEWER_HUD_KEY')},
  });
  server.listen(port, host, () => console.log('Convergence HTTPS reviewer HUD listening'));
  // Renewed certificates apply in place: restarting would sign every reviewer out.
  const watchers = [
    watchTls({files: {cert: env.REVIEWER_HUD_CERT, key: env.REVIEWER_HUD_KEY},
      build: identity, apply: ({cert, key}) => server.setSecureContext({cert, key, minVersion: 'TLSv1.2'})}),
    watchTls({files: {ca: env.REVIEWER_SERVICE_CA, cert: env.REVIEWER_INSTALLATION_CERT, key: env.REVIEWER_INSTALLATION_KEY},
      build: identity, apply: next => client.reload(next)}),
  ];
  const shutdown = () => server.close(async () => {
    for (const watcher of watchers) watcher.close();
    try { await server.grantsDrained; } finally { client.close(); }
  });
  process.once('SIGINT', shutdown);
  process.once('SIGTERM', shutdown);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { start(); }
  catch { console.error('Reviewer HUD could not start. Check the HTTPS origins, bind address, TLS files and sign-in settings.'); process.exitCode = 1; }
}

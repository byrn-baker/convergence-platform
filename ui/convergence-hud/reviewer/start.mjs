import fs from 'node:fs';
import net from 'node:net';
import {pathToFileURL} from 'node:url';
import {createReviewerClient} from './service-client.mjs';
import {createReviewerServer} from './server.mjs';
import {identity, watchTls} from './tls-reload.mjs';

export function reviewerAssets() {
  const asset = (file, type) => ({content: fs.readFileSync(new URL('public/' + file, import.meta.url)), type});
  return new Map([
    ['/', asset('index.html', 'text/html; charset=utf-8')],
    ['/reviewer.css', asset('reviewer.css', 'text/css; charset=utf-8')],
    ...['reviewer.js', 'pending-operations.js', 'investigation.js'].map(file => ['/' + file, asset(file, 'text/javascript; charset=utf-8')]),
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
  const server = createReviewerServer({
    origin: env.REVIEWER_HUD_ORIGIN, assets: reviewerAssets(), client,
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
  catch { console.error('Reviewer HUD could not start. Check the HTTPS origins, bind address and TLS files.'); process.exitCode = 1; }
}

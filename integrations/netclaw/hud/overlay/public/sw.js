// Remove this HUD's former offline caches when a previously registered worker updates.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil((async () => {
  for (const key of await caches.keys()) {
    if (key.startsWith('netclaw-hud-shell-') || key.startsWith('netclaw-hud-graph-')) await caches.delete(key);
  }
  await self.registration.unregister();
})()));

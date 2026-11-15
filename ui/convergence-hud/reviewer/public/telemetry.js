// Adapt the shared site view to a single authenticated HTTPS browser session.
// No native HUD URL or capability is reachable through this transport.
export function telemetryWorkspace({document, browser, session, scopeLost, openCase, updateExpiry,
  loadView = () => import('/site-assets/HomeView.js')}) {
  const root = document.getElementById('site-workspace');
  let view = null, epoch = 0, visible = false, loading = false;
  const json = value => new browser.Response(JSON.stringify(value), {headers:{'content-type':'application/json'}});
  function requireSession(captured) {
    const current = session();
    if (!current?.user || current.capabilities?.telemetry !== true || (captured && current.csrf !== captured.csrf)) {
      throw Object.assign(Error('Sign in to view site telemetry.'), {status:401});
    }
    return current;
  }
  async function request(path, options = {}) {
    const captured = requireSession();
    const generation = epoch;
    if ((options.method || 'GET') !== 'GET' || !path.startsWith('/api/home/')) throw Error('Telemetry is read only.');
    const url = new URL(path, browser.location.origin);
    const resource = url.pathname.slice('/api/home/'.length);
    if (resource === 'status') return json({configured:true, reviewerOrigin:browser.location.origin});
    if (resource === 'sites') return json({sites:captured.user.sites.map(id => ({id,name:id,healthy:null}))});
    const site = url.searchParams.get('site') || view?.site;
    if (!captured.user.sites.includes(site)) throw Object.assign(Error('Site unavailable.'), {status:403});
    url.searchParams.delete('site');
    const target = '/review/sites/' + encodeURIComponent(site) + '/telemetry/' + resource +
      (url.search ? '?' + url.searchParams.toString() : '');
    const response = await browser.fetch(target, {...options, method:'GET', credentials:'same-origin',
      mode:'same-origin', redirect:'error', cache:'no-store', headers:{Accept:'application/json'}});
    // Consume before releasing: logout must suppress a body that arrives late.
    const text = await response.text();
    requireSession(captured);
    if (generation !== epoch) throw Object.assign(Error('Session changed.'), {status:401});
    if ([401,403].includes(response.status)) {
      scopeLost();
      throw Object.assign(Error('Site access changed. Sign in again.'), {status:response.status});
    }
    const expires = response.headers.get('X-Review-Expires-At');
    if (expires) updateExpiry(expires);
    return new browser.Response(text, {status:response.status, headers:{'content-type':'application/json'}});
  }
  async function show() {
    if (!root) return;
    visible = true;
    root.hidden = false;
    if (view) { view.refresh(); return; }
    if (loading) return;
    loading = true;
    const captured = requireSession(), generation = epoch;
    root.textContent = 'Loading site telemetry…';
    try {
      const {HomeView} = await loadView();
      if (!visible || generation !== epoch || session()?.csrf !== captured.csrf) return;
      view = new HomeView(root, {embedded:true, hostAccess:{request, capabilities:{models:false,embeddedReviewer:true},
        sessionMessage:'Sign in to view site telemetry.'}});
      view.mount();
    } catch {
      if (generation === epoch) root.textContent = 'Site workspace unavailable. Try opening it again.';
    } finally { if (generation === epoch) loading = false; }
  }
  function clear() {
    epoch++; loading = false;
    view?.destroy(); view = null;
    if (root) root.replaceChildren();
  }
  function hide() { visible = false; clear(); if (root) root.hidden = true; }
  root?.addEventListener('click', event => {
    const link = event.target.closest('a');
    if (!link) return;
    const target = new URL(link.href, browser.location.origin);
    if (target.origin !== browser.location.origin || !target.hash) return;
    const ref = new URLSearchParams(target.hash.slice(1));
    if (ref.size !== 2 || !ref.get('site') || !ref.get('case')) return;
    event.preventDefault();
    openCase(ref.get('site'), ref.get('case'));
  });
  return {show, hide, clear, request};
}

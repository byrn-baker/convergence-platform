import {telemetryWorkspace} from './telemetry.js';
import {tokenDigest, receiptPointers} from './pending-operations.js';
import {investigationPanel} from './investigation.js';
import {jevPanel} from './jev.js';
import {bulkPanel} from './bulk.js';
import {queuePanel, parseRoute, routeFragment, endpoint, addressKind} from './queue.js';

const UUID = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

function actorLabel(entry) {
  return entry.reviewed_by_name ? `${entry.reviewed_by_name} (${entry.reviewed_by})` : entry.reviewed_by;
}

function dateLabel(value) {
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleString() : '';
}

export function startReviewer(document, browser = document.defaultView) {
  const byId = id => document.getElementById(id);
  const login = byId('login'), lookup = byId('lookup'), form = byId('disposition');
  const site = lookup.elements.site, caseId = lookup.elements.caseId;
  const text = (id, value) => { byId(id).textContent = value ?? ''; };
  const visible = (id, show) => { byId(id).hidden = !show; };
  const node = (tag, value) => { const element = document.createElement(tag); element.textContent = value; return element; };
  let session = null, generation = 0, sessionRequest = 0, authBusy = false;
  let current = null, currentKey = null, operation = null, expiryTimer = null, expiresAt = 0;
  let logoutNonce = null, preparing = false, suspendedDraft = null, recoveryUnavailable = false;
  // Consume the case reference once. This only fills in the lookup after sign-in.
  const reference = new URLSearchParams(browser.location.hash.slice(1));
  let linkedCase = null;
  if (reference.has('case')) {
    browser.history.replaceState(null, '', browser.location.pathname + browser.location.search);
    if (reference.size === 2 && UUID.test(reference.get('case')) && reference.get('site')) {
      linkedCase = {id: reference.get('case').toLowerCase(), site: reference.get('site')};
    }
  }
  // Single sign-on leaves this page and returns to it, which would drop the
  // reference. It waits in this tab's storage and is consumed once on return.
  const heldCase = 'review-linked-case';
  try {
    const held = browser.sessionStorage.getItem(heldCase);
    browser.sessionStorage.removeItem(heldCase);
    const parsed = held && !linkedCase ? JSON.parse(held) : null;
    if (parsed && UUID.test(parsed.id) && typeof parsed.site === 'string' && parsed.site) {
      linkedCase = {id: parsed.id.toLowerCase(), site: parsed.site};
    }
  } catch { /* storage is optional */ }
  let workspaceMode = 'cases';
  const telemetry = telemetryWorkspace({document, browser, session:() => session, scopeLost, updateExpiry:setExpiry,
    openCase(selectedSite, id) {
      if (!session?.user?.sites.includes(selectedSite) || !UUID.test(id)) return;
      showWorkspace('cases'); site.value = selectedSite;
      openCase(id.toLowerCase());
    }});
  function showWorkspace(mode) {
    if (mode === 'site' && session?.capabilities?.telemetry !== true) mode = 'cases';
    workspaceMode = mode;
    visible('workspace', Boolean(session?.user) && mode === 'cases');
    byId('site-tab')?.setAttribute('aria-pressed', String(mode === 'site'));
    byId('cases-tab')?.setAttribute('aria-pressed', String(mode === 'cases'));
    if (mode === 'site' && session?.user) telemetry.show(); else telemetry.hide();
    if (mode === 'cases' && view !== 'case' && !queue.active()) route(true);
  }
  byId('site-tab')?.addEventListener('click', () => showWorkspace('site'));
  byId('cases-tab')?.addEventListener('click', () => showWorkspace('cases'));
  const pointers = receiptPointers(browser);
  const investigations = investigationPanel({document, browser, request, scopeLost});
  const jev = jevPanel({document, browser, request, scopeLost});
  // Three views share the workspace: the queue, one signature's cases, and a case.
  // `applied` is the fragment already shown, so one navigation is routed once.
  let view = 'queue', applied = null, listRoute = null;
  // The list a case was opened from: its order, and where its next page starts.
  let trail = null;
  const fragment = route => routeFragment(route, session?.user?.sites[0]);
  const queue = queuePanel({document, request, scopeLost, navigate, fragment});
  const bulk = bulkPanel({document, browser, request, scopeLost, session: () => session, recorded: () => queue.refresh()});

  function navigate(target) {
    if (browser.location.hash.slice(1) !== target) browser.history.pushState(null, '', '#' + target);
    route();
  }

  function openCase(id) {
    const target = routeFragment({view: 'case', id, site: site.value});
    if (browser.location.hash.slice(1) !== target) browser.history.pushState(null, '', '#' + target);
    applied = browser.location.hash;
    const listed = queue.snapshot();
    if (listed?.ids.includes(id)) trail = listed;
    else if (!trail?.ids.includes(id) || trail.route.site !== site.value) trail = null;
    view = 'case';
    queue.clear(); bulk.suspend();
    purgeCase();
    caseId.value = id;
    text('status', 'Loading case…');
    loadCase(id, generation);
  }

  function route(force = false) {
    if (!session?.user?.sites.length || workspaceMode !== 'cases') return;
    if (!force && applied === browser.location.hash) return;
    applied = browser.location.hash;
    let next = parseRoute(applied);
    const denied = next.site !== null && !session.user.sites.includes(next.site);
    if (denied) next = parseRoute('');
    if (next.view === 'case') { site.value = next.site; return openCase(next.id); }
    purgeCase();
    caseId.value = '';
    site.value = next.site || session.user.sites[0];
    view = next.view;
    listRoute = {...next, site: site.value};
    queue.show(listRoute);
    bulk.show(listRoute);
    if (denied) text('status', 'Your account does not have access to the linked site.');
  }

  function trailAt() { return trail && current ? trail.ids.indexOf(current.id) : -1; }
  function hasNext() { const at = trailAt(); return at >= 0 && (at < trail.ids.length - 1 || Boolean(trail.next)); }

  // Open the next or previous case of the list, reading its next page when needed.
  async function step(delta) {
    const at = trailAt(), held = trail, seq = generation;
    if (at < 0) return;
    if (delta > 0 && at === held.ids.length - 1 && held.next) {
      try {
        const more = await queue.page(held.route, held.next);
        if (seq !== generation || trail !== held) return;
        held.ids.push(...more.cases.map(row => row.id).filter(id => !held.ids.includes(id)));
        held.next = more.next;
      } catch (error) {
        if (seq !== generation) return;
        if (error.status === 401 || error.status === 403) return scopeLost();
        return text('status', 'The next case could not be loaded. Try again.');
      }
    }
    const target = held.ids[at + delta];
    if (!target) return text('status', delta > 0 ? 'That was the last case in this list.' : 'This is the first case in this list.');
    openCase(target);
  }

  function up() {
    if (view === 'queue') return;
    const base = listRoute || {...parseRoute(''), site: site.value};
    navigate(fragment(view === 'case' ? base : {...base, view: 'queue', signature: null}));
  }

  const changes = typeof browser.BroadcastChannel === 'function' ? new browser.BroadcastChannel('review-session') : null;
  if (changes) changes.onmessage = () => { if (!logoutNonce) { ++sessionRequest; purgeSession(); refreshSession(); } };

  function purgeCase() {
    investigations.clear();
    jev.clear();
    generation++;
    current = currentKey = operation = null;
    preparing = recoveryUnavailable = false;
    form.reset();
    for (const id of ['case-title', 'case-status', 'operation-note', 'operation-id', 'status', 'case-when', 'trail-position',
      'flow-source', 'flow-source-kind', 'flow-protocol', 'flow-destination', 'flow-destination-kind']) text(id, '');
    byId('fired-signature').replaceChildren();
    for (const id of ['trail', 'flow', 'fired-signature', 'save-next']) visible(id, false);
    for (const id of ['facts', 'history']) byId(id).replaceChildren();
    for (const id of ['assessment', 'current']) byId(id).querySelector('div').replaceChildren();
    for (const id of ['detail', 'operation', 'check-receipt', 'retry', 'disposition']) visible(id, false);
  }

  function purgeSession() {
    telemetry.hide();
    queue.clear(); bulk.clear();
    visible('keys', false);
    // A case reference does not outlive the session that opened it.
    if (parseRoute(browser.location.hash).view === 'case') {
      browser.history.replaceState(null, '', browser.location.pathname + browser.location.search);
    }
    view = 'queue'; applied = listRoute = trail = null;
    visible('workspace-nav', false);
    purgeCase();
    browser.clearTimeout(expiryTimer);
    session = null; expiresAt = 0; pointers.clearPrivate(); suspendedDraft = null;
    caseId.value = '';
    site.replaceChildren();
    form.reset();
    login.reset();
    text('identity', '');
    for (const id of ['workspace', 'account', 'login', 'sso', 'reconnect']) visible(id, false);
  }

  function setExpiry(value) {
    const limit = new Date(value).getTime();
    if (!Number.isFinite(limit) || limit <= Date.now()) {
      purgeSession();
      throw Object.assign(Error('Review session expired'), {status: 401});
    }
    expiresAt = limit;
    browser.clearTimeout(expiryTimer);
    expiryTimer = browser.setTimeout(() => { ++sessionRequest; purgeSession(); refreshSession(); }, limit - Date.now());
  }

  async function request(path, body, csrf = session?.csrf) {
    const requestSession = session?.csrf;
    const options = {method: 'GET', credentials: 'same-origin', mode: 'same-origin',
      redirect: 'error', cache: 'no-store', headers: {Accept: 'application/json'}};
    if (body !== undefined) {
      options.method = 'POST';
      options.headers['Content-Type'] = 'application/json';
      options.headers['X-Review-CSRF'] = csrf || '';
      options.body = JSON.stringify(body);
    }
    const response = await browser.fetch(path, options);
    if (!response.ok) throw Object.assign(new Error('Review request failed'), {status: response.status});
    const value = response.status === 204 ? null : await response.json();
    if (session?.user && requestSession === session.csrf && path.startsWith('/review/sites/')) {
      const effective = response.headers?.get('X-Review-Expires-At');
      if (effective) setExpiry(effective);
      else if (expiresAt && Date.now() >= expiresAt) setExpiry(expiresAt);
    }
    return value;
  }

  function applySession(next) {
    const user = next?.user;
    if (typeof next?.csrf !== 'string' || !next.csrf || (user !== null &&
        (!user?.id || !['viewer', 'operator', 'admin'].includes(user.role) || !Array.isArray(user.sites)))) {
      throw Error('Invalid review session');
    }
    const changed = !session || next.csrf !== session.csrf ||
      JSON.stringify(user) !== JSON.stringify(session.user) ||
      next.capabilities?.telemetry !== session.capabilities?.telemetry;
    const resume = !changed && !document.hidden ? suspendedDraft : null;
    if (changed) purgeSession();
    session = next;
    visible('reconnect', false);
    // An older server reports no methods; it offers password sign-in only.
    visible('login', !user && next.signIn?.password !== false);
    visible('sso', !user && next.signIn?.oidc === true);
    visible('account', Boolean(user));
    if (next.capabilities?.telemetry !== true) workspaceMode = 'cases';
    visible('workspace-nav', Boolean(user) && next.capabilities?.telemetry === true);
    visible('workspace', Boolean(user) && workspaceMode === 'cases');
    if (user) text('identity', `${user.name} · ${user.role}`);
    if (changed && user) {
      site.replaceChildren(...user.sites.map(name => new browser.Option(name, name)));
      if (!user.sites.length) text('status', 'You have no sites available for review.');
      if (linkedCase) {
        if (user.sites.includes(linkedCase.site)) {
          site.value = linkedCase.site;
          caseId.value = linkedCase.id;
          text('status', 'Open the selected case to review it.');
          view = 'case';
        } else {
          text('status', 'Your account does not have access to the linked site.');
        }
        linkedCase = null;
      }
    }
    if (user && next.expiresAt) setExpiry(next.expiresAt);
    if (user && workspaceMode === 'site' && !document.hidden) telemetry.show();
    if (resume && user) {
      suspendedDraft = null;
      if (resume.id) return reloadDraft(resume);
    }
    // Lists are private too: they are reloaded when the session is confirmed.
    if (user && !document.hidden && view !== 'case' && !queue.active()) {
      const notice = changed ? byId('status').textContent : '';
      route(true);
      if (notice) text('status', notice);
    }
  }

  async function reloadDraft(draft) {
    if (draft.site !== site.value || !session.user.sites.includes(draft.site) || !draft.id) return;
    caseId.value = draft.id;
    const seq = generation;
    await loadCase(draft.id, seq);
    if (seq === generation && current?.id === draft.id && !operation && draft.reason && session?.user?.role !== 'viewer') {
      form.elements.kind.value = draft.kind;
      form.elements.reason.value = draft.reason;
    }
  }

  function disconnected() {
    purgeSession();
    visible('reconnect', true);
    text('status', 'Review session unavailable. Check your connection and try again.');
  }

  async function refreshSession() {
    if (authBusy || logoutNonce) return;
    const seq = ++sessionRequest;
    try {
      const next = await request('/review/session');
      if (seq === sessionRequest) applySession(next);
    } catch {
      if (seq === sessionRequest) disconnected();
    }
  }

  function scopeLost() {
    ++sessionRequest;
    purgeSession();
    refreshSession();
  }

  // An action and its private response must belong to the same site, case and
  // reviewer. Late results from a closed workspace never repopulate its DOM.
  function pathFor(id) {
    return '/review/sites/' + encodeURIComponent(site.value) + '/cases/' + id;
  }

  function render(entry) {
    current = entry;
    const assessment = entry.security_assessment, traffic = entry.traffic_context;
    // What fired, when and between which hosts comes first; the form comes last.
    text('case-title', assessment?.signature || traffic?.signature || entry.alert_name || 'Investigation');
    text('case-status', entry.status);
    text('case-when', [dateLabel(entry.timestamp), entry.severity ? entry.severity + ' severity' : ''].filter(Boolean).join(' · '));
    const hosts = Boolean(traffic?.source_ip || traffic?.destination_ip);
    visible('flow', hosts);
    if (hosts) {
      const kind = address => { const value = addressKind(address); return value ? value + ' address' : ''; };
      text('flow-source', endpoint(traffic.source_ip, traffic.source_port) || 'unknown');
      text('flow-source-kind', kind(traffic.source_ip));
      text('flow-protocol', traffic.protocol || '');
      text('flow-destination', endpoint(traffic.destination_ip, traffic.destination_port) || 'unknown');
      text('flow-destination-kind', kind(traffic.destination_ip));
    }
    const fired = byId('fired-signature');
    fired.replaceChildren();
    visible('fired-signature', Number.isSafeInteger(traffic?.signature_id));
    if (Number.isSafeInteger(traffic?.signature_id)) {
      const link = node('a', 'All cases of signature ' + traffic.signature_id);
      link.href = '#' + fragment({...(listRoute || parseRoute('')), view: 'signature', signature: String(traffic.signature_id), site: entry.site_id});
      fired.append(link);
      if (traffic.action) fired.append(node('span', ' · sensor action: ' + traffic.action));
    }
    const at = trailAt();
    visible('trail', at >= 0);
    if (at >= 0) {
      text('trail-position', `Case ${at + 1} of ${trail.ids.length}${trail.next ? '+' : ''} in this list`);
      byId('case-previous').disabled = at === 0;
      byId('case-next').disabled = !hasNext();
    }
    const facts = [
      ['Observation', entry.message], ['Case ID', entry.id], ['Site', entry.site_id], ['Alert', entry.alert_name],
      ['Investigation notes', entry.investigation_notes], ['Root cause', entry.root_cause],
    ];
    byId('facts').replaceChildren(...facts.filter(([, value]) => value)
      .flatMap(([label, value]) => [node('dt', label), node('dd', value)]));
    visible('assessment', Boolean(assessment));
    if (assessment) byId('assessment').querySelector('div').replaceChildren(node('strong', assessment.label),
      node('p', assessment.reason), node('p', assessment.next_check));
    const disposition = entry.security_disposition;
    visible('current', Boolean(disposition));
    if (disposition) byId('current').querySelector('div').replaceChildren(
      outcomeNode(disposition.kind), node('p', disposition.reason),
      node('small', `${actorLabel(disposition)} · ${dateLabel(disposition.reviewed_at)}` +
        (disposition.bulk_operation_id ? ' · recorded by a bulk decision' : '')));
    const history = byId('history');
    history.replaceChildren();
    if (!entry.review_history.length) history.append(node('p', 'No reviewer actions recorded.'));
    else {
      const list = document.createElement('ol');
      for (const row of entry.review_history) {
        const item = document.createElement('li');
        // A bulk decision is one entry shared by every case it covered.
        const bulk = Number.isInteger(row.bulk_case_count)
          ? ` · bulk decision covering ${row.bulk_case_count.toLocaleString('en-US')} ${row.bulk_case_count === 1 ? 'case' : 'cases'}` : '';
        item.append(outcomeNode(row.kind, bulk),
          node('p', row.reason_preview + (row.reason_truncated ? '…' : '')),
          node('small', `${actorLabel(row)} · ${dateLabel(row.reviewed_at)}`));
        list.append(item);
      }
      history.append(list);
      if (entry.review_history_truncated) history.append(node('small', 'Showing the 20 most recent reviews.'));
    }
    visible('detail', true);
    visible('disposition', Boolean(assessment) && session.user.role !== 'viewer');
    updateOperation();
    investigations.show(entry, session.user);
    jev.show(entry, session.user);
  }

  // The warning colour marks a confirmed threat and nothing else.
  function outcomeNode(kind, suffix = '') {
    const element = node('strong', outcomeLabel(kind) + suffix);
    if (kind === 'confirmed_threat') element.className = 'outcome-confirmed_threat';
    return element;
  }
  function outcomeLabel(kind) {
    return Array.from(form.elements.kind.options).find(option => option.value === kind)?.textContent || kind;
  }

  async function loadCase(id, seq) {
    try {
      const entry = await request(pathFor(id));
      if (seq !== generation) return;
      if (!entry || entry.id !== id || entry.site_id !== site.value || !Array.isArray(entry.review_history)) {
        throw Error('Invalid case response');
      }
      let key, pending, unavailable = false;
      try {
        key = await pointers.scope(session.user.id, entry.site_id, entry.id);
        pending = pointers.get(key);
      } catch { unavailable = true; }
      if (seq !== generation) return;
      recoveryUnavailable = unavailable;
      currentKey = key;
      const recover = pending && !operation;
      if (recover) operation = {...pending, state: 'unknown'};
      render(entry);
      text('status', unavailable && session.user.role !== 'viewer' ?
        'Recording is disabled because this tab cannot access pending receipts.' : '');
      if (recover) await submitOperation(true);
    } catch (error) {
      if (seq !== generation) return;
      if (error.status === 401 || error.status === 403) return scopeLost();
      if (error.status === 404) purgeCase();
      text('status', error.status === 404 ? 'Case not found at this site.' :
        'Case details could not be loaded. Open the case again to refresh.');
    }
  }

  function updateOperation() {
    const reenter = operation?.state === 'unknown' && operation.retry && !operation.input;
    for (const field of form.elements) field.disabled = recoveryUnavailable || preparing || Boolean(operation && operation.state !== 'saved' && !reenter);
    form.querySelector('button').textContent = reenter ? 'Retry original disposition' : 'Save disposition';
    visible('save-next', !reenter && hasNext() && !byId('disposition').hidden);
    visible('operation', Boolean(operation));
    visible('check-receipt', operation?.state === 'unknown');
    visible('retry', operation?.state === 'unknown' && operation.retry && Boolean(operation.input));
    if (operation) {
      text('operation-id', operation.id);
      text('operation-note', operation.note);
    }
  }

  async function submitOperation(check = false, advance = false) {
    const active = operation;
    const seq = generation;
    active.retry = false;
    active.state = 'pending';
    active.note = check ? 'Checking for a recorded disposition…' : 'Saving disposition…';
    updateOperation();
    try {
      const url = pathFor(current.id) + '/disposition';
      const receipt = check ? await request(url + '/' + active.id) : await request(url, active.input);
      if (seq !== generation) return;
      if (!receipt || receipt.operation_id !== active.id || receipt.case_id !== current.id ||
          receipt.site_id !== site.value || receipt.reviewed_by !== session.user.id ||
          typeof receipt.kind !== 'string' || typeof receipt.reason !== 'string') throw Error('Invalid receipt');
      const digest = await tokenDigest(browser, [active.id, receipt.kind, receipt.reason]);
      if (seq !== generation) return;
      if (digest !== active.digest) throw Error('Receipt does not match the submitted operation');
      pointers.forget(active.key);
      active.state = 'saved';
      active.note = `${outcomeLabel(receipt.kind)} recorded by ${actorLabel(receipt)} · ${dateLabel(receipt.reviewed_at)}`;
      form.reset();
      updateOperation();
      await loadCase(current.id, seq);
      // Only a confirmed receipt moves on; anything else stays on this case.
      if (advance && seq === generation && operation === active) step(1);
    } catch (error) {
      if (seq !== generation) return;
      if (error.status === 401 || error.status === 403) return scopeLost();
      if (!check && [400, 404].includes(error.status)) {
        pointers.forget(active.key);
        operation = null;
        updateOperation();
        text('status', 'Disposition rejected. Check the case and evidence before submitting.');
        return;
      }
      active.state = 'unknown';
      active.retry = check && error.status === 404;
      if (active.retry) {
        active.note = active.input ? 'No receipt is available yet. You can retry the same operation.' :
          'No receipt is available yet. Re-enter the original outcome and evidence to retry with the same ID.';
      } else active.note = 'The outcome is unknown. Check the receipt before submitting again.';
      updateOperation();
    }
  }

  login.addEventListener('submit', async event => {
    event.preventDefault();
    if (authBusy || !session || session.user) return;
    authBusy = true;
    const seq = ++sessionRequest;
    const fields = {username: login.elements.username.value, password: login.elements.password.value};
    login.elements.password.value = '';
    login.querySelector('button').disabled = true;
    try {
      const next = await request('/review/sign-in', fields);
      if (seq === sessionRequest) { applySession(next); text('status', ''); changes?.postMessage('changed'); }
    } catch (error) {
      if (seq === sessionRequest) {
        if (error.status === 401) text('status', 'The username or password was not accepted.');
        else if (error.status === 429) text('status', 'Too many sign-in attempts. Try again later.');
        else disconnected();
      }
    } finally {
      authBusy = false;
      login.querySelector('button').disabled = false;
    }
  });

  byId('sso-start').addEventListener('click', () => {
    if (authBusy || !session || session.user) return;
    try { if (linkedCase) browser.sessionStorage.setItem(heldCase, JSON.stringify(linkedCase)); } catch { /* optional */ }
    browser.location.assign('/oidc/login');
  });

  async function logout() {
    if (authBusy || !session?.csrf) return;
    authBusy = true;
    logoutNonce = session.csrf;
    ++sessionRequest;
    const ending = request('/review/sign-out', {});
    purgeSession();
    try {
      const ended = await ending;
      changes?.postMessage('changed');
      logoutNonce = null;
      // The identity provider session ends too, or the next visit signs back in.
      if (typeof ended?.location === 'string' && ended.location.startsWith('https://')) {
        return browser.location.assign(ended.location);
      }
    } catch {
      text('status', 'Sign-out could not be confirmed. Retry sign-out before leaving this browser.');
      visible('reconnect', true);
      byId('reconnect').textContent = 'Retry sign-out';
    } finally { authBusy = false; }
    if (!logoutNonce) await refreshSession();
  }

  byId('logout').addEventListener('click', logout);
  byId('reconnect').addEventListener('click', async () => {
    if (!logoutNonce) return refreshSession();
    if (authBusy) return;
    authBusy = true;
    try {
      // A replacement session belongs to another sign-in. Use the original
      // proof even if a cookie changes again between the check and retry.
      const next = await request('/review/session');
      if (next.csrf === logoutNonce && next.user) {
        await request('/review/sign-out', {}, logoutNonce);
        changes?.postMessage('changed');
      }
      logoutNonce = null;
      session = null;
      byId('reconnect').textContent = 'Check session';
    } catch { session = null; }
    finally { authBusy = false; }
    if (!logoutNonce) refreshSession();
  });
  site.addEventListener('change', () => {
    purgeCase(); caseId.value = '';
    if (session?.user) navigate(fragment({...(listRoute || parseRoute('')), view: 'queue', signature: null, site: site.value}));
  });
  caseId.addEventListener('input', () => { if (current || operation) purgeCase(); else generation++; });
  lookup.addEventListener('submit', event => {
    event.preventDefault();
    if (!session?.user || !site.value) return;
    const id = caseId.value.trim().toLowerCase();
    if (!UUID.test(id)) { purgeCase(); return text('status', 'Enter a case ID in UUID format.'); }
    openCase(id);
  });
  browser.addEventListener('hashchange', () => route());
  browser.addEventListener('popstate', () => route());
  byId('keys-close').addEventListener('click', () => visible('keys', false));
  document.addEventListener('keydown', event => {
    if (!session?.user || workspaceMode !== 'cases' || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === 'Escape' && !byId('keys').hidden) return visible('keys', false);
    const target = event.target, tag = target?.tagName;
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) || target?.isContentEditable) return;
    const act = action => { event.preventDefault(); action(); };
    if (event.key === '?') return act(() => visible('keys', byId('keys').hidden));
    if (event.key === 'u') return act(up);
    if (view === 'case') {
      if (event.key === 'j') act(() => step(1));
      else if (event.key === 'k') act(() => step(-1));
      return;
    }
    if (!queue.active()) return;
    if (event.key === 'j') act(() => queue.move(1));
    else if (event.key === 'k') act(() => queue.move(-1));
    else if (event.key === '/') act(() => queue.focusFilter());
    else if (event.key === 'b') act(() => bulk.start());
    // A focused link or button keeps its own Enter.
    else if (event.key === 'Enter' && !['A', 'BUTTON'].includes(tag)) act(() => queue.open());
  });
  form.addEventListener('submit', event => { event.preventDefault(); save(false); });
  byId('save-next').addEventListener('click', () => { if (form.reportValidity()) save(true); });
  byId('case-next').addEventListener('click', () => step(1));
  byId('case-previous').addEventListener('click', () => step(-1));
  async function save(advance) {
    const reenter = operation?.state === 'unknown' && operation.retry && !operation.input;
    if (!current || !currentKey || recoveryUnavailable || preparing || (operation && operation.state !== 'saved' && !reenter) || session?.user?.role === 'viewer') return;
    const reason = form.elements.reason.value.trim(), kind = form.elements.kind.value;
    if (reason.length < 10 || reason.length > 4000) return text('status', 'Enter 10 to 4000 characters of evidence.');
    const seq = generation;
    preparing = true;
    updateOperation();
    try {
      const saved = await pointers.prepare(currentKey, {kind, reason}, reenter ? operation : null, () => seq === generation);
      if (seq !== generation) return;
      if (!saved) return text('status', 'The outcome and evidence must match the pending disposition.');
      operation = saved;
      submitOperation(false, advance && !reenter);
    } catch {
      if (seq === generation) text('status', 'This tab could not retain a receipt pointer. No disposition was submitted.');
    } finally {
      if (seq === generation) { preparing = false; updateOperation(); }
    }
  }
  byId('check-receipt').addEventListener('click', () => { if (operation?.state === 'unknown') submitOperation(true); });
  byId('retry').addEventListener('click', () => { if (operation?.retry && operation.input) submitOperation(); });
  browser.addEventListener('pagehide', () => { ++sessionRequest; purgeSession(); });
  browser.addEventListener('pageshow', event => { if (event.persisted && !logoutNonce) { purgeSession(); refreshSession(); } });
  document.addEventListener('visibilitychange', () => {
    if (logoutNonce || authBusy) return;
    ++sessionRequest;
    if (session?.user && !suspendedDraft) {
      suspendedDraft = {id: current?.id, site: site.value, kind: form.elements.kind.value,
        reason: current && (!operation || operation.state === 'saved') ? form.elements.reason.value : ''};
    }
    if (session?.user) {
      telemetry.hide();
      queue.clear(); bulk.suspend();
      purgeCase();
      text('identity', '');
      visible('account', false);
      visible('workspace', false);
    }
    if (!document.hidden) refreshSession();
  });
  browser.addEventListener('focus', refreshSession);
  const poll = browser.setInterval(() => { if (!document.hidden && session?.user) refreshSession(); }, 15000);
  refreshSession();
  return () => { telemetry.hide(); browser.clearInterval(poll); browser.clearTimeout(expiryTimer); changes?.close(); };
}

if (typeof document !== 'undefined') startReviewer(document);

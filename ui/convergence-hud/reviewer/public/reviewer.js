const UUID = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

async function tokenDigest(browser, fields) {
  const bytes = new TextEncoder().encode(JSON.stringify(fields));
  const digest = await browser.crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}

// The tab keeps one opaque receipt pointer per principal, site and case. Notes,
// names, grant tokens and case IDs are never serialized into browser storage.
function receiptPointers(browser) {
  const inputs = new Map();
  return {
    async scope(user, site, id) {
      return 'review:' + await tokenDigest(browser, [user, site, id]);
    },
    get(key) {
      const value = browser.sessionStorage.getItem(key);
      if (!value) return null;
      const [id, digest] = value.split('.');
      if (!UUID.test(id) || !/^[0-9a-f]{64}$/.test(digest)) throw Error('Invalid receipt pointer');
      return {key, id, digest, input: inputs.get(key)};
    },
    async prepare(key, kind, reason, previous, stillCurrent) {
      const id = previous?.id || browser.crypto.randomUUID();
      const digest = await tokenDigest(browser, [id, kind, reason]);
      if (!stillCurrent() || (previous && digest !== previous.digest)) return null;
      const input = {operationId: id, kind, reason};
      browser.sessionStorage.setItem(key, id + '.' + digest);
      inputs.set(key, input);
      return {key, id, digest, input};
    },
    clearPrivate() { inputs.clear(); },
    forget(key) { inputs.delete(key); browser.sessionStorage.removeItem(key); },
  };
}

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
  if (reference.has('case') || reference.has('site')) {
    browser.history.replaceState(null, '', browser.location.pathname + browser.location.search);
    if (reference.size === 2 && UUID.test(reference.get('case')) && reference.get('site')) {
      linkedCase = {id: reference.get('case').toLowerCase(), site: reference.get('site')};
    }
  }
  const pointers = receiptPointers(browser);
  const changes = typeof browser.BroadcastChannel === 'function' ? new browser.BroadcastChannel('review-session') : null;
  if (changes) changes.onmessage = () => { if (!logoutNonce) { ++sessionRequest; purgeSession(); refreshSession(); } };

  function purgeCase() {
    generation++;
    current = currentKey = operation = null;
    preparing = recoveryUnavailable = false;
    form.reset();
    for (const id of ['case-title', 'case-status', 'operation-note', 'operation-id', 'status']) text(id, '');
    for (const id of ['facts', 'history']) byId(id).replaceChildren();
    for (const id of ['assessment', 'current']) byId(id).querySelector('div').replaceChildren();
    for (const id of ['detail', 'operation', 'check-receipt', 'retry', 'disposition']) visible(id, false);
  }

  function purgeSession() {
    purgeCase();
    browser.clearTimeout(expiryTimer);
    session = null; expiresAt = 0; pointers.clearPrivate(); suspendedDraft = null;
    caseId.value = '';
    site.replaceChildren();
    form.reset();
    login.reset();
    text('identity', '');
    for (const id of ['workspace', 'account', 'login', 'reconnect']) visible(id, false);
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
      JSON.stringify(user) !== JSON.stringify(session.user);
    const resume = !changed && !document.hidden ? suspendedDraft : null;
    if (changed) purgeSession();
    session = next;
    visible('reconnect', false);
    visible('login', !user);
    visible('account', Boolean(user));
    visible('workspace', Boolean(user));
    if (user) text('identity', `${user.name} · ${user.role}`);
    if (changed && user) {
      site.replaceChildren(...user.sites.map(name => new browser.Option(name, name)));
      if (!user.sites.length) text('status', 'You have no sites available for review.');
      if (linkedCase) {
        if (user.sites.includes(linkedCase.site)) {
          site.value = linkedCase.site;
          caseId.value = linkedCase.id;
          text('status', 'Open the selected case to review it.');
        } else {
          text('status', 'Your account does not have access to the linked site.');
        }
        linkedCase = null;
      }
    }
    if (user && next.expiresAt) setExpiry(next.expiresAt);
    if (resume && user) {
      suspendedDraft = null;
      reloadDraft(resume);
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
    text('case-title', entry.alert_name || 'Investigation');
    text('case-status', entry.status);
    const facts = [
      ['Case ID', entry.id], ['Site', entry.site_id], ['Observed', dateLabel(entry.timestamp)],
      ['Severity', entry.severity], ['Observation', entry.message],
      ['Investigation notes', entry.investigation_notes], ['Root cause', entry.root_cause],
    ];
    byId('facts').replaceChildren(...facts.filter(([, value]) => value)
      .flatMap(([label, value]) => [node('dt', label), node('dd', value)]));
    const assessment = entry.security_assessment;
    visible('assessment', Boolean(assessment));
    if (assessment) byId('assessment').querySelector('div').replaceChildren(node('strong', assessment.label),
      node('p', assessment.reason), node('p', assessment.next_check));
    const disposition = entry.security_disposition;
    visible('current', Boolean(disposition));
    if (disposition) byId('current').querySelector('div').replaceChildren(
      node('strong', outcomeLabel(disposition.kind)), node('p', disposition.reason),
      node('small', `${actorLabel(disposition)} · ${dateLabel(disposition.reviewed_at)}`));
    const history = byId('history');
    history.replaceChildren();
    if (!entry.review_history.length) history.append(node('p', 'No reviewer actions recorded.'));
    else {
      const list = document.createElement('ol');
      for (const row of entry.review_history) {
        const item = document.createElement('li');
        item.append(node('strong', outcomeLabel(row.kind)),
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
    visible('operation', Boolean(operation));
    visible('check-receipt', operation?.state === 'unknown');
    visible('retry', operation?.state === 'unknown' && operation.retry && Boolean(operation.input));
    if (operation) {
      text('operation-id', operation.id);
      text('operation-note', operation.note);
    }
  }

  async function submitOperation(check = false) {
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

  async function logout() {
    if (authBusy || !session?.csrf) return;
    authBusy = true;
    logoutNonce = session.csrf;
    ++sessionRequest;
    const ending = request('/review/sign-out', {});
    purgeSession();
    try {
      await ending;
      changes?.postMessage('changed');
      logoutNonce = null;
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
  site.addEventListener('change', () => { purgeCase(); caseId.value = ''; });
  caseId.addEventListener('input', () => { if (current || operation) purgeCase(); else generation++; });
  lookup.addEventListener('submit', event => {
    event.preventDefault();
    if (!session?.user || !site.value) return;
    const id = caseId.value.trim().toLowerCase();
    purgeCase();
    if (!UUID.test(id)) return text('status', 'Enter a case ID in UUID format.');
    text('status', 'Loading case…');
    loadCase(id, generation);
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const reenter = operation?.state === 'unknown' && operation.retry && !operation.input;
    if (!current || !currentKey || recoveryUnavailable || preparing || (operation && operation.state !== 'saved' && !reenter) || session?.user?.role === 'viewer') return;
    const reason = form.elements.reason.value.trim(), kind = form.elements.kind.value;
    if (reason.length < 10 || reason.length > 4000) return text('status', 'Enter 10 to 4000 characters of evidence.');
    const seq = generation;
    preparing = true;
    updateOperation();
    try {
      const saved = await pointers.prepare(currentKey, kind, reason, reenter ? operation : null, () => seq === generation);
      if (seq !== generation) return;
      if (!saved) return text('status', 'The outcome and evidence must match the pending disposition.');
      operation = saved;
      submitOperation();
    } catch {
      if (seq === generation) text('status', 'This tab could not retain a receipt pointer. No disposition was submitted.');
    } finally {
      if (seq === generation) { preparing = false; updateOperation(); }
    }
  });
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
  return () => { browser.clearInterval(poll); browser.clearTimeout(expiryTimer); changes?.close(); };
}

if (typeof document !== 'undefined') startReviewer(document);

// Bulk review: one decision for every case of a signature that still needs review.
// The reviewer sees the count first and confirms it; the service refuses the write
// if more cases match than were confirmed.
const UUID = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const LABELS = {expected_activity: 'Expected activity', false_positive: 'False positive', confirmed_threat: 'Confirmed threat'};
const WINDOW_LABELS = {'24h': 'last 24 hours', '7d': 'last 7 days', '30d': 'last 30 days'};

export function bulkPanel({document, browser, request, scopeLost, session, recorded}) {
  const byId = id => document.getElementById(id);
  const root = byId('bulk'), form = byId('bulk-form'), scope = form.elements.scope;
  const text = (id, value) => { byId(id).textContent = value ?? ''; };
  const visible = (id, show) => { byId(id).hidden = !show; };
  const count = value => Number(value).toLocaleString('en-US');
  const cases = value => `${count(value)} ${value === 1 ? 'case' : 'cases'}`;
  const when = value => { const date = new Date(value); return Number.isFinite(date.getTime()) ? date.toLocaleString() : value; };
  // `epoch` discards answers for a view that has gone. `pending` is a decision whose
  // outcome is not known yet; it survives a hidden tab so the same one can be retried.
  let route = null, preview = null, pending = null, epoch = 0;

  const keyOf = target => JSON.stringify([session()?.user?.id, target.site, target.signature]);
  const base = () => '/review/sites/' + encodeURIComponent(route.site) + '/signatures/' + route.signature + '/dispositions';
  function allowed(target) {
    return target?.view === 'signature' && Boolean(target.signature) && ['operator', 'admin'].includes(session()?.user?.role);
  }

  function lock(locked) {
    for (const field of form.elements) field.disabled = locked;
    if (!locked) byId('bulk-review').disabled = !preview || preview.cases < 1 || preview.cases > preview.ceiling;
  }

  function reset() {
    preview = null;
    form.reset();
    scope.replaceChildren();
    for (const id of ['bulk-status', 'bulk-confirm-text', 'bulk-receipt-note', 'bulk-operation', 'bulk-review']) text(id, '');
    for (const id of ['bulk-form', 'bulk-confirm', 'bulk-receipt', 'bulk-retry', 'bulk-again']) visible(id, false);
    visible('bulk-start', true);
    lock(false);
  }

  function showPreview() {
    const chosen = preview.source_host || '';
    const all = new browser.Option(`All that need review in the ${WINDOW_LABELS[preview.window]}`, '');
    const hosts = preview.sources.map(row => new browser.Option(`Only source ${row.source_host} (${count(row.cases)})`, row.source_host));
    if (chosen && !preview.sources.some(row => row.source_host === chosen)) hosts.push(new browser.Option(`Only source ${chosen}`, chosen));
    scope.replaceChildren(all, ...hosts);
    scope.value = chosen;
    text('bulk-review', `Review ${cases(preview.cases)} as one decision`);
    visible('bulk-start', false);
    visible('bulk-form', true);
    visible('bulk-confirm', false);
    lock(false);
    text('bulk-status', preview.cases < 1 ? 'No case in this scope needs review.'
      : preview.cases > preview.ceiling ? `More than ${count(preview.ceiling)} cases match. Narrow the scope to one source or a shorter window.`
        : preview.sources_truncated ? 'Only the ten busiest sources are listed.' : '');
  }

  async function load(source, note = '') {
    const seq = ++epoch;
    visible('bulk-confirm', false);
    lock(true);
    text('bulk-status', 'Counting cases…');
    try {
      const value = await request(base() + '/preview?window=' + route.window + (source ? '&source=' + source : ''));
      if (seq !== epoch) return;
      if (!value || value.site !== route.site || value.signature_id !== route.signature || value.window !== route.window ||
          (value.source_host ?? null) !== (source || null) || !Number.isInteger(value.cases) || !Number.isInteger(value.ceiling) ||
          typeof value.window_start !== 'string' || typeof value.window_end !== 'string' || !Array.isArray(value.sources) ||
          value.sources.some(row => typeof row?.source_host !== 'string' || !Number.isInteger(row.cases))) {
        throw Error('Invalid preview');
      }
      preview = value;
      showPreview();
      if (note) text('bulk-status', note);
    } catch (error) {
      if (seq !== epoch) return;
      if (error.status === 401 || error.status === 403) return scopeLost();
      lock(false);
      byId('bulk-review').disabled = true;
      text('bulk-status', 'The count could not be loaded. Try again.');
      visible('bulk-start', true);
    }
  }

  function showPending() {
    visible('bulk-start', false);
    visible('bulk-form', false);
    visible('bulk-confirm', false);
    visible('bulk-receipt', true);
    text('bulk-operation', pending.body.operationId);
    visible('bulk-retry', pending.state === 'unknown');
    visible('bulk-again', false);
    text('bulk-receipt-note', pending.state === 'unknown'
      ? `The outcome of this decision (${LABELS[pending.body.kind]}, up to ${cases(pending.body.confirmedCount)}) is unknown. ` +
        'Retry sends the same decision again; it cannot be recorded twice.' : 'Saving the decision…');
  }

  async function send() {
    const seq = ++epoch, active = pending;
    active.state = 'pending';
    text('bulk-status', '');
    showPending();
    try {
      const receipt = await request(base(), active.body);
      if (pending !== active) return;
      if (!receipt || receipt.operation_id !== active.body.operationId || receipt.site_id !== route?.site ||
          receipt.signature_id !== route.signature || receipt.reviewed_by !== session()?.user?.id ||
          receipt.kind !== active.body.kind || receipt.reason !== active.body.reason || !Number.isInteger(receipt.case_count)) {
        throw Error('Invalid receipt');
      }
      pending = null;
      if (seq !== epoch) return;
      visible('bulk-again', true);
      text('bulk-receipt-note', `${LABELS[receipt.kind]} recorded for ${cases(receipt.case_count)} by ` +
        `${receipt.reviewed_by_name || receipt.reviewed_by} · ${when(receipt.reviewed_at)}` +
        (receipt.case_count < active.body.confirmedCount ? '. Fewer than previewed: the others were reviewed in the meantime.' : ''));
      recorded();
    } catch (error) {
      if (pending !== active) return;
      if (error.status === 401 || error.status === 403) { pending = null; return scopeLost(); }
      if (error.status === 409 || error.status === 400) {
        pending = null;
        if (seq !== epoch) return;
        visible('bulk-receipt', false);
        const kind = active.body.kind, reason = active.body.reason;
        await load(active.body.source, error.status === 409
          ? 'The cases changed since the preview. Nothing was recorded. Check the new count and confirm again.'
          : 'The decision was rejected. Nothing was recorded.');
        if (preview) { form.elements.kind.value = kind; form.elements.reason.value = reason; }
        return;
      }
      active.state = 'unknown';
      if (seq === epoch) showPending();
    }
  }

  byId('bulk-start').addEventListener('click', () => { if (route && !pending) load(null); });
  scope.addEventListener('change', () => {
    if (!route || pending) return;
    const kind = form.elements.kind.value, reason = form.elements.reason.value, source = scope.value;
    load(source || null).then(() => { if (preview) { form.elements.kind.value = kind; form.elements.reason.value = reason; } });
  });
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (!route || !preview || pending || preview.cases < 1 || preview.cases > preview.ceiling) return;
    const reason = form.elements.reason.value.trim(), kind = form.elements.kind.value;
    if (!LABELS[kind]) return;
    if (reason.length < 10 || reason.length > 4000) return text('bulk-status', 'Enter 10 to 4000 characters of evidence.');
    text('bulk-status', '');
    lock(true);
    text('bulk-confirm-text', `Record "${LABELS[kind]}" for ${cases(preview.cases)} of signature ${route.signature}` +
      (preview.source_host ? ` from source ${preview.source_host}` : '') +
      ` seen between ${when(preview.window_start)} and ${when(preview.window_end)}? ` +
      'This is one decision under your name. Cases that arrive later are not included.');
    visible('bulk-confirm', true);
    byId('bulk-confirm-yes').textContent = `Confirm ${cases(preview.cases)}`;
    byId('bulk-confirm-yes').focus();
  });
  byId('bulk-cancel').addEventListener('click', () => { visible('bulk-confirm', false); lock(false); });
  byId('bulk-confirm-yes').addEventListener('click', () => {
    if (!route || !preview || pending || byId('bulk-confirm').hidden) return;
    const id = browser.crypto.randomUUID();
    if (!UUID.test(id)) return;
    pending = {key: keyOf(route), state: 'pending', body: {operationId: id, kind: form.elements.kind.value,
      reason: form.elements.reason.value.trim(), windowStart: preview.window_start, windowEnd: preview.window_end,
      source: preview.source_host ?? null, confirmedCount: preview.cases}};
    send();
  });
  byId('bulk-retry').addEventListener('click', () => { if (route && pending?.state === 'unknown') send(); });
  byId('bulk-again').addEventListener('click', () => { if (route && !pending) { reset(); load(null); } });

  function hide() { ++epoch; route = null; reset(); root.hidden = true; }
  return {
    show(next) {
      ++epoch;
      if (!allowed(next)) return hide();
      if (pending && pending.key !== keyOf(next)) pending = null;
      route = {...next};
      reset();
      root.hidden = false;
      if (pending) { pending.state = 'unknown'; showPending(); }
    },
    // Hide the panel but keep an undecided operation so it can be retried.
    suspend: hide,
    clear() { pending = null; hide(); },
    start() { if (route && !pending && !byId('bulk-start').hidden) { byId('bulk-start').focus(); load(null); } },
  };
}

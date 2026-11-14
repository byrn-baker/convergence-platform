import {receiptPointers, tokenDigest} from './pending-operations.js';

const stateLabels = {
  accepted: 'Accepted; waiting for delivery.', enqueued: 'Queued for investigation.',
  dispatched: 'Investigation dispatched; waiting for its result.', completed: 'Investigation completed.',
  failed: 'Investigation failed. Review the evidence before requesting more work.',
  unknown: 'Investigation outcome is unknown. Review it before requesting more work.',
};

// One controller owns the request form and its receipt. The session/case owner
// explicitly clears it on navigation, sign-out and loss of authorization.
export function investigationPanel({document, browser, request, scopeLost}) {
  const el = id => document.getElementById('investigation-' + id);
  const form = el('form');
  const pointers = receiptPointers(browser, 'investigation', ['feedback']);
  let context = null, operation = null, generation = 0, busy = false, unavailable = false;
  const visible = (id, show) => { el(id).hidden = !show; };
  const status = text => { el('status').textContent = text; };
  const allowed = () => context?.enabled && ['operator', 'admin'].includes(context?.user.role);
  const path = () => '/review/sites/' + encodeURIComponent(context.site) + '/cases/' + context.id + '/investigation';

  function render() {
    visible('panel', Boolean(context && (allowed() || operation)));
    const reenter = operation?.retry && !operation.input;
    form.hidden = !allowed() || Boolean(operation?.receipt);
    for (const field of form.elements) field.disabled = busy || unavailable || Boolean(operation && !reenter);
    form.querySelector('button').textContent = reenter ? 'Retry original investigation request' : 'Request investigation';
    el('operation').textContent = operation?.id || '';
    visible('check', Boolean(operation) && !busy);
    visible('retry', allowed() && Boolean(operation?.retry && operation.input) && !busy);
    visible('new', allowed() && Boolean(operation?.receipt) && !busy);
  }

  function clear() {
    generation++;
    context = operation = null; busy = unavailable = false;
    pointers.clearPrivate(); form.reset(); status(''); render();
  }

  async function reconcile(check = true) {
    if (!context || !operation || busy || (!check && !allowed())) return;
    const seq = generation, active = operation, target = path(), identity = context;
    busy = true; active.retry = false;
    status(check ? 'Checking request…' : 'Submitting investigation request…'); render();
    try {
      const receipt = check ? await request(target + '/' + active.id) : await request(target, active.input);
      if (seq !== generation) return;
      if (receipt?.operation_id !== active.id || receipt.case_id !== identity.id || receipt.site_id !== identity.site ||
          receipt.requested_by !== identity.user.id || typeof receipt.requested_by_name !== 'string' ||
          typeof receipt.feedback !== 'string' || !Object.hasOwn(stateLabels, receipt.state)) throw Error('Invalid receipt');
      const digest = await tokenDigest(browser, [active.id, receipt.feedback]);
      if (seq !== generation) return;
      if (digest !== active.digest) throw Error('Mismatched request receipt');
      active.receipt = true;
      active.input = null;
      pointers.clearPrivate();
      form.reset();
      status(`${stateLabels[receipt.state]} Requested by ${receipt.requested_by_name} (${receipt.requested_by}).`);
    } catch (error) {
      if (seq !== generation) return;
      if ([401,403].includes(error.status)) return scopeLost();
      // A receipt already observed cannot turn into permission to resubmit after
      // a later404 (for example, an inconsistent restored receiver/database).
      active.retry = check && error.status === 404 && !active.receipt;
      if (!check && [400,404].includes(error.status)) {
        pointers.forget(active.key); operation = null;
        status('Request rejected. Check the case and question before submitting.');
      } else status(active.retry ? 'No request receipt found. Retry with the original question and request ID.' :
        'The request outcome is unknown. Check its receipt before submitting again.');
    } finally { if (seq === generation) { busy = false; render(); } }
  }

  async function show(entry, user) {
    if (context?.id === entry.id && context.site === entry.site_id && context.user.id === user.id) {
      context.user = user; context.enabled = entry.investigation_enabled === true; render(); return;
    }
    clear();
    const seq = generation;
    context = {id: entry.id, site: entry.site_id, user, enabled: entry.investigation_enabled === true};
    try {
      const key = await pointers.scope(user.id, entry.site_id, entry.id);
      if (seq !== generation) return;
      context.key = key; operation = pointers.get(key);
      render();
      if (operation) await reconcile();
    } catch {
      if (seq === generation) { unavailable = true; status('Requests are disabled because this tab cannot retain receipt pointers.'); render(); }
    }
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!allowed() || !context.key || busy || unavailable || (operation && !(operation.retry && !operation.input))) return;
    const feedback = form.elements.feedback.value.trim();
    if (Array.from(feedback).length < 10 || Array.from(feedback).length > 4000 || /[\u0000\uD800-\uDFFF]/u.test(feedback)) {
      status('Enter 10 to 4000 characters describing the evidence you need.'); return;
    }
    const seq = generation;
    busy = true; render();
    try {
      const saved = await pointers.prepare(context.key, {feedback}, operation, () => seq === generation);
      if (seq !== generation) return;
      if (!saved) { status('Use the exact question from the pending request.'); return; }
      operation = saved;
    } catch { if (seq === generation) { status('No request submitted: this tab could not retain its receipt pointer.'); return; } }
    finally { if (seq === generation) { busy = false; render(); } }
    if (seq === generation) await reconcile(false);
  });
  el('check').addEventListener('click', () => reconcile());
  el('retry').addEventListener('click', () => { if (operation?.retry && operation.input) reconcile(false); });
  el('new').addEventListener('click', () => {
    if (!allowed() || busy || !operation?.receipt) return;
    pointers.forget(operation.key); operation = null; form.reset(); status(''); render(); form.elements.feedback.focus();
  });
  return {show, clear};
}

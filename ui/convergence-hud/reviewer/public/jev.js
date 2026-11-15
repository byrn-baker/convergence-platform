import {receiptPointers, tokenDigest} from './pending-operations.js';

const states = {
  accepted:'Request accepted; awaiting delivery.',
  enqueued:'Request delivered; assessment is queued.',
  dispatched:'The shared assessment is in progress.',
  completed:'The shared assessment request has finished. Review its recorded outcome and limitations below.',
  failed:'The assessment request failed. Check its recorded result before taking action.',
  unknown:'The assessment outcome is unknown. Check the receipt before requesting again.',
};
const participation = {started:'Started the shared assessment',joined:'Joined an existing assessment',reused:'Reused the retained assessment'};

// JEV owns a shared site/case assessment; this controller owns only the named
// request and its receipt. It never creates a new assessment budget or retries
// evaluation, and never conflates assessment with an investigation/disposition.
export function jevPanel({document,browser,request,scopeLost}) {
  const el = id => document.getElementById('jev-' + id);
  const pointers = receiptPointers(browser,'jev',[]);
  let context = null, operation = null, generation = 0, busy = false, unavailable = false;
  const allowed = () => context?.enabled && ['operator','admin'].includes(context?.user.role);
  const path = () => '/review/sites/' + encodeURIComponent(context.site) + '/cases/' + context.id + '/jev-review';
  const status = value => {el('status').textContent = value;};
  function render() {
    el('panel').hidden = !context || (!allowed() && !operation);
    el('request').hidden = !allowed() || Boolean(operation);
    el('request').disabled = busy || unavailable;
    el('check').hidden = !operation;
    el('check').disabled = busy;
    el('retry').hidden = !allowed() || !operation?.retry;
    el('retry').disabled = busy || unavailable;
    el('operation').textContent = operation?.id || '';
  }
  function clear() {
    generation++; context = operation = null; busy = unavailable = false;
    pointers.clearPrivate(); status(''); el('result').textContent = ''; el('result').hidden = true;
    el('summary').textContent='';el('summary').hidden=true;el('evidence').hidden=true;render();
  }
  async function reconcile(check = true) {
    if (!context || !operation || busy || (!check && !allowed())) return;
    const seq = generation, active = operation, identity = context, target = path();
    busy = true; active.retry = false;
    status(check ? 'Checking JEV request…' : 'Requesting the shared JEV assessment…'); render();
    try {
      const receipt = check ? await request(target+'/'+active.id) : await request(target,{operationId:active.id});
      if (seq !== generation) return;
      if (receipt?.operation_id !== active.id || receipt.case_id !== identity.id || receipt.site_id !== identity.site ||
          receipt.requested_by !== identity.user.id || typeof receipt.requested_by_name !== 'string' ||
          !Object.hasOwn(states,receipt.state) ||
          (receipt.participation != null && !Object.hasOwn(participation,receipt.participation))) throw Error('Invalid JEV receipt');
      if (await tokenDigest(browser,[active.id]) !== active.digest) throw Error('Mismatched JEV receipt');
      if (seq !== generation) return;
      active.receipt = true;
      const involvement = receipt.participation ? participation[receipt.participation]+'. ' : '';
      status(`${involvement}${states[receipt.state]} Requested by ${receipt.requested_by_name} (${receipt.requested_by}).`);
      el('summary').hidden = el('evidence').hidden = receipt.result == null;
      el('summary').textContent = receipt.result == null ? '' : 'Recorded outcome: ' + String(receipt.result.status || 'unknown').replaceAll('_',' ') + (receipt.result.message ? '. ' + receipt.result.message : '');
      el('result').hidden = receipt.result == null;
      el('result').textContent = receipt.result == null ? '' : JSON.stringify(receipt.result,null,2);
    } catch(error) {
      if (seq !== generation) return;
      if ([401,403].includes(error.status)) return scopeLost();
      active.retry = check && error.status === 404 && !active.receipt;
      if (!check && [400,404].includes(error.status)) {
        pointers.forget(active.key); operation = null;
        status('JEV request rejected. Check whether this case supports an assessment.');
      } else status(active.retry ? 'No request receipt found. Retry the same request ID; this does not create another assessment budget.' :
        'JEV request outcome is unknown. Check its receipt before submitting again.');
    } finally {if(seq === generation){busy=false;render();}}
  }
  async function show(entry,user) {
    if(context?.id === entry.id && context.site === entry.site_id && context.user.id === user.id) {
      context.user=user;context.enabled=entry.jev_enabled === true;render();return;
    }
    clear(); const seq=generation;
    context={id:entry.id,site:entry.site_id,user,enabled:entry.jev_enabled === true};
    try {
      const key=await pointers.scope(user.id,entry.site_id,entry.id);
      if(seq !== generation)return;
      context.key=key;operation=pointers.get(key);render();
      if(operation)await reconcile();
    } catch {
      if(seq === generation){unavailable=true;status('JEV requests are disabled because this tab cannot retain receipt pointers.');render();}
    }
  }
  el('request').addEventListener('click',async()=>{
    if(!allowed() || !context.key || busy || unavailable || operation)return;
    const seq=generation;busy=true;render();
    try {
      const saved=await pointers.prepare(context.key,{},null,()=>seq===generation);
      if(seq!==generation)return;
      if(!saved)throw Error('Session changed');
      operation=saved;
    } catch {
      if(seq===generation)status('No request submitted: this tab could not retain its receipt pointer.');
      return;
    } finally {if(seq===generation){busy=false;render();}}
    if(seq===generation)await reconcile(false);
  });
  el('check').addEventListener('click',()=>reconcile());
  el('retry').addEventListener('click',()=>{if(operation?.retry)reconcile(false);});
  return {show,clear};
}

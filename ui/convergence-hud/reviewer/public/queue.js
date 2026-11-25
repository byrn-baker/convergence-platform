// The case queue: signatures for a site, and one signature's cases. The page's
// fragment names the view and its filters, so links and the back button work.
export const WINDOWS = ['24h', '7d', '30d'];
export const STATES = ['needs_review', 'reviewed', 'all', 'expected_activity', 'false_positive', 'confirmed_threat'];
export const SEVERITIES = ['alert', 'watch', 'info', 'ok'];
const STATE_LABELS = {needs_review: 'Needs review', reviewed: 'Reviewed', all: 'All',
  expected_activity: 'Expected activity', false_positive: 'False positive', confirmed_threat: 'Confirmed threat'};
const WINDOW_LABELS = {'24h': 'last 24 hours', '7d': 'last 7 days', '30d': 'last 30 days'};
const UUID = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const SIGNATURE = /^[A-Za-z0-9_.:-]{0,64}$/;
const CURSOR = /^[A-Za-z0-9_-]{1,128}$/;

// Anything the fragment does not say, or says badly, takes its default. A case
// reference needs both of its parts; a bad one opens the queue.
export function parseRoute(hash) {
  const params = new URLSearchParams(String(hash || '').replace(/^#/, ''));
  const site = params.get('site') || null;
  if (params.has('case')) {
    if (UUID.test(params.get('case')) && site) return {view: 'case', id: params.get('case').toLowerCase(), site};
    return {view: 'queue', signature: null, site: null, window: '24h', state: 'needs_review', severity: null};
  }
  const signature = params.get('signature');
  const pick = (name, allowed, fallback) => allowed.includes(params.get(name)) ? params.get(name) : fallback;
  return {
    view: signature !== null && SIGNATURE.test(signature) ? 'signature' : 'queue',
    signature: signature !== null && SIGNATURE.test(signature) ? signature : null, site,
    window: pick('window', WINDOWS, '24h'), state: pick('state', STATES, 'needs_review'),
    severity: pick('severity', SEVERITIES, null),
  };
}

// Defaults are left out, so the plain queue is `#queue`.
export function routeFragment(route, defaultSite) {
  const site = encodeURIComponent(route.site ?? '');
  if (route.view === 'case') return `case=${route.id}&site=${site}`;
  let fragment = route.view === 'signature' ? 'signature=' + route.signature : 'queue';
  if (route.window && route.window !== '24h') fragment += '&window=' + route.window;
  if (route.state && route.state !== 'needs_review') fragment += '&state=' + route.state;
  if (route.severity) fragment += '&severity=' + route.severity;
  if (route.site && route.site !== defaultSite) fragment += '&site=' + site;
  return fragment;
}

export function ago(value, now = Date.now()) {
  const seconds = Math.round((now - new Date(value).getTime()) / 1000);
  if (!Number.isFinite(seconds)) return '';
  if (seconds < 60) return `${Math.max(seconds, 0)} s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
}

export function endpoint(address, port) {
  if (!address) return '';
  if (port === null || port === undefined) return address;
  return (address.includes(':') ? `[${address}]` : address) + ':' + port;
}

// Whether an address is on a private or local network. This is the address's own
// range, not a statement about the host behind it.
export function addressKind(address) {
  if (typeof address !== 'string' || !address) return '';
  const value = address.toLowerCase();
  if (value.includes(':')) return /^(f[cd]|fe[89ab])/.test(value) || value === '::1' ? 'internal' : 'external';
  const [a, b] = value.split('.').map(Number);
  return a === 10 || a === 127 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) ||
    (a === 169 && b === 254) || (a === 100 && b >= 64 && b <= 127) ? 'internal' : 'external';
}

const HEADS = {
  queue: ['Severity', 'Signature', 'SID', 'Cases', 'Needs review', 'Last seen'],
  signature: ['Time', 'Source', 'Destination', 'Protocol', 'State'],
};

export function queuePanel({document, request, scopeLost, navigate, fragment}) {
  const byId = id => document.getElementById(id);
  const root = byId('list-view'), body = byId('list-rows'), filter = byId('filter-text');
  const node = (tag, value, className) => {
    const element = document.createElement(tag);
    if (value !== undefined) element.textContent = value;
    if (className) element.className = className;
    return element;
  };
  const count = value => Number(value).toLocaleString('en-US');
  // `epoch` discards answers that arrive after the view changed or was cleared.
  let route = null, epoch = 0, data = null, selected = 0, busy = false, summary = null;

  function severityCell(value) {
    const known = SEVERITIES.includes(value);
    return node('td', known ? value : '', known ? 'severity severity-' + value : 'severity');
  }
  function timeCell(value, label) {
    const cell = node('td'), time = node('time', label);
    time.dateTime = value;
    const date = new Date(value);
    if (Number.isFinite(date.getTime())) time.title = date.toLocaleString();
    cell.append(time);
    return cell;
  }
  function linkCell(label, target) {
    const cell = node('td', undefined, 'primary'), link = node('a', label);
    link.href = '#' + target;
    cell.append(link);
    return cell;
  }

  // The phone layout turns each row into a card: a cell shows its column name, and
  // the explicit roles keep the table readable to assistive technology there.
  function tableRow(view, ...cells) {
    const line = node('tr');
    line.setAttribute('role', 'row');
    cells.forEach((cell, at) => { cell.setAttribute('role', 'cell'); cell.dataset.label = HEADS[view][at]; });
    line.append(...cells);
    return line;
  }

  function links() { return Array.from(body.querySelectorAll('a')); }
  function select(index, focus = false) {
    const all = links();
    if (!all.length) { selected = 0; return; }
    selected = Math.min(Math.max(index, 0), all.length - 1);
    all.forEach((link, at) => {
      link.closest('tr').classList.toggle('selected', at === selected);
      if (at === selected) link.setAttribute('aria-current', 'true'); else link.removeAttribute('aria-current');
    });
    if (focus) all[selected].focus();
  }

  function chrome() {
    const signature = route.view === 'signature';
    for (const button of byId('filter-window').querySelectorAll('button')) {
      button.setAttribute('aria-pressed', String(button.dataset.window === route.window));
    }
    byId('filter-state').value = route.state;
    byId('filter-severity').value = route.severity || '';
    byId('filter-text-label').hidden = signature;
    byId('crumb-queue').href = '#' + fragment({...route, view: 'queue', signature: null});
    byId('crumb-signature').hidden = !signature;
    byId('crumb-signature').textContent = signature ? (route.signature ? 'sid ' + route.signature : 'no signature') : '';
    byId('list-title').textContent = signature ? (route.signature ? 'Signature ' + route.signature : 'Cases with no signature') : 'Case queue';
    byId('list-head').replaceChildren(...HEADS[signature ? 'signature' : 'queue'].map(label => {
      const cell = node('th', label);
      cell.scope = 'col';
      cell.setAttribute('role', 'columnheader');
      return cell;
    }));
  }

  function renderQueue() {
    const needle = filter.value.trim().toLowerCase();
    const rows = data.signatures.filter(row => !needle ||
      String(row.name || '').toLowerCase().includes(needle) || String(row.signature_id || '').toLowerCase().includes(needle));
    body.replaceChildren(...rows.map(entry => tableRow('queue', severityCell(entry.severity),
      linkCell(entry.name || 'Unnamed signature', fragment({...route, view: 'signature', signature: entry.signature_id || ''})),
      node('td', entry.signature_id || 'none'), node('td', count(entry.cases), 'number'),
      node('td', count(entry.needs_review), 'number'), timeCell(entry.last_seen, ago(entry.last_seen)))));
    const needs = data.signatures.reduce((sum, row) => sum + (Number(row.needs_review) || 0), 0);
    byId('list-summary').textContent = `${count(needs)} need review across ${count(data.signatures.length)} ` +
      `${data.signatures.length === 1 ? 'signature' : 'signatures'} · ${STATE_LABELS[route.state].toLowerCase()} · ` +
      `${WINDOW_LABELS[route.window]}${route.severity ? ' · ' + route.severity + ' severity' : ''}`;
    byId('list-status').textContent = !data.signatures.length ? 'No cases match these filters.'
      : !rows.length ? 'No signature matches the filter text.'
        : data.truncated ? 'Showing the first 500 signatures. Narrow the filters to see the rest.' : '';
    select(selected);
  }

  function caseRow(entry) {
    const date = new Date(entry.occurred_at);
    return tableRow('signature', linkCell(Number.isFinite(date.getTime()) ? date.toLocaleString() : entry.id,
      fragment({view: 'case', id: entry.id, site: route.site})),
      node('td', endpoint(entry.source_ip, entry.source_port)), node('td', endpoint(entry.destination_ip, entry.destination_port)),
      node('td', entry.protocol || ''), node('td', STATE_LABELS[entry.state] || 'Reviewed', 'state state-' + (STATE_LABELS[entry.state] ? entry.state : 'reviewed')));
  }

  function renderCases(added) {
    if (added) body.append(...added.map(caseRow)); else body.replaceChildren(...data.cases.map(caseRow));
    const first = data.cases[0];
    if (first?.name && route.signature) byId('list-title').textContent = first.name;
    const known = summary?.signature_id === (route.signature || null) ? summary : null;
    byId('list-summary').textContent = [
      route.signature ? 'sid ' + route.signature : 'no signature ID',
      known ? `${count(known.cases)} cases, ${count(known.needs_review)} need review` : null,
      `${STATE_LABELS[route.state].toLowerCase()} · ${WINDOW_LABELS[route.window]}${route.severity ? ' · ' + route.severity + ' severity' : ''}`,
      `showing ${count(data.cases.length)}${data.next ? ' so far' : ''}, newest first`,
    ].filter(Boolean).join(' · ');
    byId('list-status').textContent = data.cases.length ? '' : 'No cases match these filters.';
    byId('list-more').hidden = !data.next;
    select(selected);
  }

  function failed(error, seq) {
    if (seq !== epoch) return;
    busy = false;
    if (error.status === 401 || error.status === 403) return scopeLost();
    byId('list-status').textContent = 'The list could not be loaded. Use Refresh to try again.';
  }

  // One page of a signature's cases, checked against what was asked for.
  async function page(from, before) {
    const value = await request('/review/sites/' + encodeURIComponent(from.site) + `/cases?window=${from.window}&state=${from.state}` +
      (from.severity ? '&severity=' + from.severity : '') + '&signature=' + from.signature + (before ? '&before=' + before : ''));
    if (!value || value.site !== from.site || value.signature !== from.signature || !Array.isArray(value.cases) ||
        value.cases.some(row => !row || !UUID.test(row.id)) || (value.next !== null && (typeof value.next !== 'string' || !CURSOR.test(value.next)))) {
      throw Error('Invalid case list response');
    }
    return value;
  }

  async function load(more = false) {
    if (!route || (more && (busy || !data?.next))) return;
    const seq = more ? epoch : ++epoch;
    busy = true;
    const base = '/review/sites/' + encodeURIComponent(route.site);
    const query = `?window=${route.window}&state=${route.state}` + (route.severity ? '&severity=' + route.severity : '');
    if (!more) {
      data = null; selected = 0;
      body.replaceChildren();
      byId('list-more').hidden = true;
      byId('list-summary').textContent = '';
    }
    byId('list-status').textContent = 'Loading…';
    try {
      if (route.view === 'queue') {
        const value = await request(base + '/signatures' + query);
        if (seq !== epoch) return;
        if (!value || value.site !== route.site || !Array.isArray(value.signatures)) throw Error('Invalid queue response');
        data = value;
        renderQueue();
      } else {
        const value = await page(route, more ? data.next : null);
        if (seq !== epoch) return;
        data = more ? {...value, cases: [...data.cases, ...value.cases]} : value;
        renderCases(more ? value.cases : null);
      }
      busy = false;
    } catch (error) { failed(error, seq); }
  }

  byId('filter-window').addEventListener('click', event => {
    const value = event.target?.dataset?.window;
    if (route && WINDOWS.includes(value) && value !== route.window) navigate(fragment({...route, window: value}));
  });
  byId('filter-state').addEventListener('change', event => {
    if (route && STATES.includes(event.target.value)) navigate(fragment({...route, state: event.target.value}));
  });
  byId('filter-severity').addEventListener('change', event => {
    const value = event.target.value;
    if (route && (value === '' || SEVERITIES.includes(value))) navigate(fragment({...route, severity: value || null}));
  });
  filter.addEventListener('input', () => { if (route?.view === 'queue' && data) { selected = 0; renderQueue(); } });
  byId('list-refresh').addEventListener('click', () => load());
  byId('list-more').addEventListener('click', () => load(true));
  body.addEventListener('focusin', event => {
    const at = links().indexOf(event.target);
    if (at >= 0) select(at);
  });

  return {
    show(next) {
      // The queue's counts for a signature head that signature's case list.
      if (next.view === 'signature' && route?.view === 'queue' && route.site === next.site && data?.signatures) {
        summary = data.signatures.find(row => (row.signature_id || '') === next.signature) || null;
        if (summary && (route.window !== next.window || route.state !== next.state || route.severity !== next.severity)) summary = null;
      } else if (next.view !== 'signature' || summary?.signature_id !== (next.signature || null) ||
          route?.window !== next.window || route?.state !== next.state || route?.severity !== next.severity || route?.site !== next.site) {
        summary = null;
      }
      route = next;
      if (next.view !== 'queue') filter.value = '';
      root.hidden = false;
      chrome();
      return load();
    },
    clear() {
      ++epoch;
      route = data = summary = null; busy = false; selected = 0;
      filter.value = '';
      body.replaceChildren();
      for (const id of ['list-title', 'list-summary', 'list-status', 'crumb-signature']) byId(id).textContent = '';
      byId('list-more').hidden = true;
      root.hidden = true;
    },
    active: () => route !== null,
    refresh: () => load(),
    page,
    // The order of the listed cases, for moving to the next one from a case page.
    snapshot: () => route?.view === 'signature' && data?.cases
      ? {route: {...route}, ids: data.cases.map(row => row.id), next: data.next} : null,
    route: () => route,
    move(delta) { select(selected + delta, true); },
    open() { const link = links()[selected]; if (link) navigate(link.getAttribute('href').slice(1)); },
    focusFilter() { (route?.view === 'queue' ? filter : byId('filter-state')).focus(); },
  };
}

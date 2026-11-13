/**
 * 080-convergence — Home product view (HUD-styled).
 * Live data via HUD proxy /api/home/* → convergence-api / Network Guardian (dual-run).
 */
import {reviewerLink} from './ReviewerLink.js';
import {caseIPContext, ipContextHTML} from './IPContext.js';
import {caseCaptureContext, observedVlanHTML, captureContextHTML} from './CaptureContext.js';
import {jevReviewHTML} from './JevReview.js';
import {assessmentHTML, securityQueueHTML, ispReportsHTML, reportDownload} from './IncidentReview.js';

import {diaryHTML, groupDiary, caseHandoff, MAX_SELECTION} from './DiaryExplorer.js';
import {flowHTML, flowHandoff} from './FlowExplorer.js';
import {securityExplorerHTML, securityModel} from './SecurityExplorer.js';
import {securityCasesPath} from './SecurityHistory.js';
import {vmSecurityHTML} from './VMSecurity.js';

const SUBVIEWS = [
  { id: 'overview', label: 'Overview' },
  { id: 'wifi', label: 'Wi‑Fi' },
  { id: 'devices', label: 'Devices' },
  { id: 'security', label: 'Security' },
  { id: 'vm-security', label: 'VM Security' },
  { id: 'diary', label: 'Diary' },
  { id: 'flows', label: 'Traffic' },
  { id: 'isp', label: 'Internet reports' },
  { id: 'triage', label: 'Triage' },
  { id: 'models', label: 'Models' },
];

// The site is discovered from the API, not hardcoded. convergence-api has always
// been multi-site (SITES_CONFIG / getSiteConfig, and GET /api/sites filtered by
// JWT scope); only this client assumed a single site called "home", which meant a
// deployment using any other site id got empty panels.
//
// DEFAULT_SITE is the fallback used only when /sites cannot be reached or returns
// nothing, so a broken discovery degrades to the previous behaviour instead of a
// blank view.
const DEFAULT_SITE = 'home';
const SITE_STORAGE_KEY = 'netclaw.convergence.site';

/** Last site the operator selected. localStorage can throw in private mode. */
function readStoredSite() {
  try {
    const v = localStorage.getItem(SITE_STORAGE_KEY);
    return v && v.trim() ? v.trim() : null;
  } catch {
    return null;
  }
}

function writeStoredSite(siteId) {
  try {
    if (siteId) localStorage.setItem(SITE_STORAGE_KEY, siteId);
  } catch {
    /* non-fatal — the selection just won't persist */
  }
}

async function homeFetch(path, options = {}, request = fetch) {
  const method = (options.method || 'GET').toUpperCase();
  const headers = { Accept: 'application/json', ...(options.headers || {}) };
  let body;
  if (options.body != null && method !== 'GET' && method !== 'HEAD') {
    headers['Content-Type'] = 'application/json';
    body = typeof options.body === 'string' ? options.body : JSON.stringify(options.body);
  }
  const res = await request(`/api/home${path}`, {
    method,
    headers,
    body,
    signal: options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(options.timeoutMs || 25000)]) : AbortSignal.timeout(options.timeoutMs || 25000),
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  if (!res.ok) {
    const err = new Error(data?.error || data?.detail || `HTTP ${res.status}`);
    err.status = res.status;
    err.payload = data;
    throw err;
  }
  return data;
}

function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fmtMbps(bits) {
  if (bits == null || Number.isNaN(Number(bits))) return '—';
  const n = Number(bits);
  if (n > 1e6) return `${(n / 1e6).toFixed(0)}`;
  if (n > 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(n);
}

function statusClass(status) {
  const s = String(status || '').toLowerCase();
  if (s === 'healthy' || s === 'ok' || s === 'resolved') return 'ok';
  if (s === 'degraded' || s === 'warning' || s === 'watch' || s === 'investigating') return 'warn';
  if (s === 'unhealthy' || s === 'critical' || s === 'alert' || s === 'escalated') return 'crit';
  return '';
}

/** Device name → external management GUI (pfSense / UniFi) when mgmtUrl present. */
function nameLink(label, url, title) {
  const text = esc(label || '—');
  if (!url || !/^https?:\/\//i.test(String(url))) return text;
  const href = esc(url);
  const tip = esc(title || `Open management GUI: ${url}`);
  return `<a class="home-mgmt-link" href="${href}" target="_blank" rel="noopener noreferrer" title="${tip}">${text}</a>`;
}

// ── HUD SSH terminal integration ────────────────────────────────────────────
// The API and the SSH testbed discover devices independently. Offer a console
// only when the API device name matches an SSH-enabled testbed entry.

/** @returns {Promise<{enabled:boolean,dedicatedAccount:boolean,names:Set<string>}>} */
async function loadTerminalTargets() {
  const [graph, caps] = await Promise.all([
    fetch('/api/graph').then((r) => r.json()),
    fetch('/api/ssh/capabilities').then((r) => r.json()),
  ]);
  const names = new Set(
    (graph.devices || [])
      .filter((d) => String(d.protocol || '').toLowerCase() === 'ssh')
      .map((d) => String(d.name)),
  );
  return {
    enabled: Boolean(caps?.enabled),
    dedicatedAccount: Boolean(caps?.dedicatedAccount),
    names,
  };
}

/**
 * Console action for a device row. Renders nothing unless the device is in the
 * testbed and the terminal is enabled, so the table never offers an action that
 * cannot work.
 */
function consoleAction(name, terminal) {
  if (!terminal?.enabled || !name) return '';
  if (!terminal.names.has(String(name))) return '';
  const n = esc(name);
  const warn = terminal.dedicatedAccount
    ? 'Open an SSH terminal'
    : 'Open an SSH terminal (privilege-15 credentials — app filter is the only guard)';
  return `<button type="button" class="home-console-btn" data-console-device="${n}" title="${esc(warn)}">Console</button>`;
}

export class HomeView {
  constructor(rootEl, options = {}) {
    this.options = options;
    this.readOnly = Boolean(options.hostAccess);
    this.authorityGeneration = 0;
    this.disposed = false;
    this.requests = new Set();
    this.notesDrafts = new Map();
    this.lastSuccess = null;
    this.readTimes = {};
    this.root = rootEl;
    this.subview = 'overview';
    /** Active site id. Seeded from the operator's last choice, then confirmed
     *  against what the API actually offers (see discoverSites). */
    this.site = readStoredSite() || DEFAULT_SITE;
    /** @type {Array<{id:string,name:string,healthy:boolean|null}>} */
    this.sites = [];
    this.element = null;
    /**
     * Toolbar: section selector, site picker, refresh. Mounted into the HUD
     * topbar, so it is *not* inside this.element — every lookup that might land
     * on it goes through qs()/qsa().
     */
    this.chrome = null;
    this.cache = {};
    this.lastError = null;
    this.sourceErrors = {};
    this.loading = false;
    /** @type {string|null} selected escalated event id */
    this.selectedEventId = null;
    this.triageBusy = false;
    this.triageFlash = null; // { kind, message }
    this.jevPending = new Set();
    this.jevErrors = new Map();
    this.vmSecuritySearch = ''; this.vmSecurityFilter = 'all'; this.vmSecurityPage = 0; this.vmSecuritySelected = null;
    this.securityFilter = 'all';
    this.securityRange = '24h'; this.securityGroup = 'signature'; this.securitySearch = '';
    this.securityVlan = ''; this.securityNetwork = ''; this.securityCountry = ''; this.securitySelectedId = null;
    this.securityHistoryWindow = null; this.securityHistoryCountry = '';
    this.diaryMode = 'signature'; this.diaryRange = '24h'; this.diarySearch = ''; this.diarySelected = new Set();
    this.flowRange = '1h'; this.flowExporter = ''; this.diaryFlash = null;
    this.securityDrafts = new Map();
    this.incidentPending = new Set();
    this.incidentErrors = new Map();
    this.ispReports = new Map();
    this.ispProviders = new Map();
    this.modelsBusy = false;
    this.modelsFlash = null; // { kind, message }
    /** @type {{ brain?: string, alert?: string, fallback?: string }|null} */
    this.modelsDraft = null;
  }

  async fetch(path, options = {}) {
    if (this.disposed) throw new Error('Convergence workspace closed');
    const generation = this.authorityGeneration;
    const controller = new AbortController();
    this.requests.add(controller);
    try {
      const data = await homeFetch(path, { ...options, signal: controller.signal }, this.options?.hostAccess?.request);
      if (this.readOnly && generation !== this.authorityGeneration) throw Error('Owner session changed.');
      // VM observations set their read time only after the requesting site is checked.
      if ((!options.method || options.method === 'GET') && !data?.unavailable && !path.startsWith('/security/vms?')) {
        const key = path.startsWith('/events/escalated') ? 'escalated' : path.slice(1).split(/[/?]/)[0];
        this.readTimes[key] = new Date().toISOString();
      }
      return data;
    }
    catch (error) {
      if (this.readOnly && [401, 403].includes(error.status) && generation === this.authorityGeneration) {
        this.clearOwnerData({...this.cache.status, configured: false});
        this.renderSubview();
      }
      throw error;
    }
    finally { this.requests.delete(controller); }
  }

  destroy() {
    this.disposed = true;
    if (this.onOwnerVisibility) {
      document.removeEventListener('visibilitychange', this.onOwnerVisibility);
      window.removeEventListener('pagehide', this.onOwnerHidden);
      window.removeEventListener('pageshow', this.onOwnerReturn);
    }
    this.requests.forEach(controller => controller.abort());
    this.requests.clear();
    this.chrome?.remove();
    this.element?.remove();
  }

  mount() {
    if (!this.root) return;
    this.element = document.createElement('div');
    this.element.className = 'home-shell';
    this.element.innerHTML = this.templateShell();
    this.root.innerHTML = '';
    this.root.appendChild(this.element);
    if (this.readOnly) {
      this.onOwnerHidden = () => {
        this.clearOwnerData({...this.cache.status, configured:false});
        this.renderSubview();
      };
      this.onOwnerReturn = () => this.refresh();
      this.onOwnerVisibility = () => document.hidden ? this.onOwnerHidden() : this.onOwnerReturn();
      document.addEventListener('visibilitychange', this.onOwnerVisibility);
      window.addEventListener('pagehide', this.onOwnerHidden);
      window.addEventListener('pageshow', this.onOwnerReturn);
    }
    this.mountChrome();
    this.bind();
    this.renderSubview();
    this.refresh();
    return this.element;
  }

  /**
   * Mount the section selector, site picker and refresh button.
   *
   * These used to be the first row *inside* the scrolling panel, which meant
   * they scrolled out of the clip region of `.home-root` (position: fixed,
   * overflow: auto) as soon as the content was taller than the viewport — so on
   * any long table the controls simply vanished, with no scrollbar hint that
   * they had gone anywhere.
   *
   * They now live in the HUD topbar, which is fixed and had a wide empty middle
   * column between the brand block and the metrics. `#home-topbar-slot` is
   * created by the module's registerUI. If it is missing — a HUD without a
   * topbar, or markup that has moved on — this falls back to the old in-panel
   * position rather than dropping the controls entirely.
   */
  mountChrome() {
    const host = this.options.embedded ? null : document.getElementById('home-topbar-slot');
    this.chrome = document.createElement('div');
    this.chrome.className = host ? 'home-toolbar home-toolbar--topbar' : 'home-toolbar';
    this.chrome.innerHTML = this.templateToolbar();

    if (host) {
      host.innerHTML = '';
      host.appendChild(this.chrome);
    } else {
      this.element.querySelector('.home-frame')?.prepend(this.chrome);
    }
    return this.chrome;
  }

  /**
   * Query the view *or* the chrome, since the chrome is mounted outside
   * `this.element`. Every toolbar lookup goes through these so that moving the
   * chrome cannot silently break the active-state sync the way a direct
   * `this.element.querySelector` would.
   */
  qs(sel) {
    return this.element?.querySelector(sel) || this.chrome?.querySelector(sel) || null;
  }

  qsa(sel) {
    return [...new Set([
      ...(this.element?.querySelectorAll(sel) || []),
      ...(this.chrome?.querySelectorAll(sel) || []),
    ])];
  }

  templateShell() {
    return `
      <div class="home-frame">
        <section class="home-panel" id="home-panel-body"></section>
      </div>
    `;
  }

  templateToolbar() {
    const buttons = SUBVIEWS.map(
      (s) =>
        `<button type="button" ${this.readOnly && s.id === 'models' ? 'hidden' : ''} aria-pressed="${s.id === this.subview}" class="home-sub-btn${s.id === this.subview ? ' active' : ''}" data-home-sub="${s.id}">${s.label}</button>`,
    ).join('');
    return `
      <div class="home-toolbar-left">
        <p class="eyebrow home-toolbar-title">NetClaw Convergence</p>
        <div class="home-segmented" role="group" aria-label="Convergence sections">
          ${buttons}
        </div>
      </div>
      <div class="home-toolbar-right">
        <span id="home-site-slot"></span>
        <button type="button" class="home-refresh-btn" id="home-refresh" title="Refresh data">
          <span class="home-refresh-icon" aria-hidden="true">↻</span>
          <span>Refresh</span>
        </button>
      </div>
    `;
  }

  bind() {
    this.qsa('.home-sub-btn[data-home-sub]').forEach((btn) => {
      btn.addEventListener('click', () => {
        this.subview = btn.dataset.homeSub;
        this.qsa('.home-sub-btn[data-home-sub]').forEach((b) => {
          b.classList.toggle('active', b.dataset.homeSub === this.subview);
          b.setAttribute('aria-pressed', String(b.dataset.homeSub === this.subview));
        });
        this.renderSubview();
        this.refresh();
      });
    });
    this.qs('#home-refresh')?.addEventListener('click', () => this.refresh(true));

    // Site selector — delegated, because the <select> is re-rendered whenever
    // the site list changes. Bound on the chrome as well as the panel, since the
    // two are no longer in the same subtree.
    const onChange = (ev) => {
      if (ev.target?.id !== 'home-site-select') return;
      this.selectSite(ev.target.value);
    };
    this.element.addEventListener('change', onChange);
    this.element.addEventListener('change', ev => {
      if (ev.target.matches('[data-vm-filter]')) { this.vmSecurityFilter = ev.target.value; this.vmSecurityPage = 0; this.renderSubview(); }
      if (ev.target.matches('[data-security-range]')) {
        this.securityRange = ev.target.value; this.securitySelectedId = null;
        this.securityHistoryWindow = null; this.securityHistoryCountry = '';
        delete this.cache.securityHistory; this.reloadSecurityCases();
      }
      if (ev.target.matches('[data-security-group]')) { this.securityGroup = ev.target.value; this.renderSubview(); }
      if (ev.target.matches('[data-security-vlan-select]')) { this.securityVlan = ev.target.value; this.renderSubview(); }
      if (ev.target.matches('[data-security-case-picker]')) { this.securitySelectedId = ev.target.value; this.renderSubview(); this.focusSecurityDetail(); }
      if (ev.target.matches('[data-isp-provider]')) { this.ispProviders.set(this.site, ev.target.value); this.renderSubview(); }
      if (ev.target.matches('[data-diary-group]')) { this.diaryMode = ev.target.value; this.renderSubview(); }
      if (ev.target.matches('[data-diary-search]')) { this.diarySearch = ev.target.value; this.renderSubview(); }
      if (ev.target.matches('[data-diary-range]')) { this.diaryRange = ev.target.value; this.diarySelected.clear(); this.refresh(); }
      if (ev.target.matches('[data-diary-select]')) {
        const id = ev.target.dataset.diarySelect;
        if (ev.target.checked && this.diarySelected.size < MAX_SELECTION) this.diarySelected.add(id); else this.diarySelected.delete(id);
        this.renderSubview();
      }
      if (ev.target.matches('[data-flow-range]')) { this.flowRange = ev.target.value; this.refresh(); }
      if (ev.target.matches('[data-flow-exporter]')) { this.flowExporter = ev.target.value; this.refresh(); }
      this.saveIncidentDraft(ev.target);
    });
    this.element.addEventListener('input', ev => {
      if (ev.target.matches('[data-vm-search]')) {
        const cursor = ev.target.selectionStart; this.vmSecuritySearch = ev.target.value; this.vmSecurityPage = 0; this.renderSubview();
        const input = this.element.querySelector('[data-vm-search]'); input?.focus(); input?.setSelectionRange(cursor,cursor);
      } else if (ev.target.matches('[data-security-search]')) {
        const cursor = ev.target.selectionStart;
        this.securitySearch = ev.target.value; this.renderSubview();
        const input = this.element.querySelector('[data-security-search]');
        input?.focus(); input?.setSelectionRange(cursor, cursor);
      } else if (ev.target.matches('[data-diary-search]')) {
        const cursor = ev.target.selectionStart;
        this.diarySearch = ev.target.value; this.renderSubview();
        const input = this.element.querySelector('[data-diary-search]');
        input?.focus(); input?.setSelectionRange(cursor, cursor);
      } else this.saveIncidentDraft(ev.target);
    });
    this.element.addEventListener('submit', ev => {
      const form = ev.target.closest('[data-security-case]');
      if (!form) return;
      ev.preventDefault();
      this.handleIncidentAction('security', form.dataset.securityCase, {kind: form.elements.kind.value, reason: form.elements.reason.value});
    });
    this.element.addEventListener('click', ev => {
      const finding = ev.target.closest('[data-vm-finding]');
      if (finding) { this.vmSecuritySelected = finding.dataset.vmFinding; this.renderSubview(); this.element.querySelector('#vm-finding-title')?.focus(); return; }
      const page = ev.target.closest('[data-vm-page]');
      if (page && !page.disabled) { this.vmSecurityPage = Number(page.dataset.vmPage); this.renderSubview(); return; }
      const interval=ev.target.closest('[data-security-history-start]');
      if(interval){
        const start=interval.dataset.securityHistoryStart,end=interval.dataset.securityHistoryEnd;
        this.securityHistoryWindow=this.securityHistoryWindow?.start===start?null:{start,end};
        this.reloadSecurityCases();return;
      }
      const country=ev.target.closest('[data-security-history-country]');
      if(country){
        const code=country.dataset.securityHistoryCountry;
        this.securityHistoryCountry=this.securityHistoryCountry===code?'':code;
        this.reloadSecurityCases();return;
      }
      if(ev.target.closest('[data-security-history-clear]')){
        this.securityHistoryWindow=null;this.securityHistoryCountry='';this.reloadSecurityCases();return;
      }
      const selected = ev.target.closest('[data-security-select]');
      if (selected) { this.securitySelectedId = selected.dataset.securitySelect; this.renderSubview(); this.focusSecurityDetail(); return; }
      const securityGroup = ev.target.closest('[data-security-group-canvas]');
      if (securityGroup && !securityGroup.disabled) { this.investigateSecurityGroup(securityGroup.dataset.securityGroupCanvas); return; }
      for (const [attribute,field] of [['network','securityNetwork'],['country','securityCountry'],['vlan','securityVlan']]) {
        const facet=ev.target.closest(`[data-security-${attribute}]`);
        if(facet){const value=facet.getAttribute(`data-security-${attribute}`);this[field]=this[field]===value?'':value;this.renderSubview();return;}
      }
      if(ev.target.closest('[data-security-reset]')) {
        this.securityFilter='all';this.securitySearch='';this.securityVlan='';this.securityNetwork='';this.securityCountry='';this.securitySelectedId=null;
        this.renderSubview();return;
      }
      const securityCase = ev.target.closest('[data-security-canvas]');
      if (securityCase && !securityCase.disabled) { this.investigateSecurity(securityCase.dataset.securityCanvas); return; }
      const one = ev.target.closest('[data-diary-canvas]'), group = ev.target.closest('[data-diary-group-canvas]');
      if (one) this.investigateDiary([one.dataset.diaryCanvas], 'Selected diary case');
      if (group) {
        const groups = groupDiary(this.cache.events?.events || [], this.diaryMode, this.diarySearch);
        const chosen = groups[Number(group.dataset.diaryGroupCanvas)];
        if (chosen) this.investigateDiary(chosen.events.slice(0, MAX_SELECTION).map(e => e.id), chosen.label);
      }
      if (ev.target.closest('[data-diary-selected]')) this.investigateDiary([...this.diarySelected], 'Operator-selected diary cases');
      if (ev.target.closest('[data-diary-clear]')) { this.diarySelected.clear(); this.renderSubview(); }
      const flow = ev.target.closest('[data-flow-canvas]');
      if (flow) {
        const [kind, index] = flow.dataset.flowCanvas.split(':');
        const context = flowHandoff(this.cache.flows, kind, Number(index));
        if (context) this.options.investigate?.('Convergence traffic deep dive', context, `/api/home/flows?site=${encodeURIComponent(this.site)}&range=${this.flowRange}&exporter=${encodeURIComponent(this.flowExporter)}`, this.cache.flows?.read_at);
      }
      const filter = ev.target.closest('[data-security-filter]');
      if (filter) { this.securityFilter = filter.dataset.securityFilter; this.renderSubview(); return; }
      const generate = ev.target.closest('[data-isp-generate]');
      if (generate && !generate.disabled) this.handleIncidentAction('isp', generate.dataset.ispGenerate);
      const load = ev.target.closest('[data-isp-load]');
      if (load) this.handleIncidentAction('load', load.dataset.ispLoad);
      const download = ev.target.closest('[data-isp-download]');
      if (download) this.downloadIspReport(download.dataset.eventId, download.dataset.ispDownload);
    });
    this.element.addEventListener('keydown',ev=>{
      if(ev.target.matches('path[data-security-history-country]') && ['Enter',' '].includes(ev.key)){
        ev.preventDefault();ev.target.dispatchEvent(new MouseEvent('click',{bubbles:true}));
      }
    });
    if (!this.element.contains(this.chrome)) this.chrome?.addEventListener('change', onChange);


    // Models panel actions
    this.element.addEventListener('click', (ev) => {
      const btn = ev.target.closest('[data-models-action]');
      if (!btn || !this.element.contains(btn)) return;
      const action = btn.getAttribute('data-models-action');
      if (action === 'apply') this.handleModelsApply(false);
      else if (action === 'preset') this.handleModelsApply(true, btn.getAttribute('data-preset'));
      else if (action === 'reload') this.refreshModels();
    });
    this.element.addEventListener('input', (ev) => {
      const t = ev.target;
      if (t?.classList.contains('home-triage-textarea')) {
        this.notesDrafts.set(t.dataset.eventId, t.value);
        return;
      }
      if (!t || !t.getAttribute || !t.getAttribute('data-models-field')) return;
      if (!this.modelsDraft) this.modelsDraft = {};
      this.modelsDraft[t.getAttribute('data-models-field')] = t.value;
    });

    // Triage actions (event delegation — panel body is re-rendered often)
    this.element.addEventListener('click', (ev) => {
      const reviewBtn = ev.target.closest('[data-jev-review]');
      if (reviewBtn && !reviewBtn.disabled) {
        this.handleJevReview(reviewBtn.getAttribute('data-jev-review'));
        return;
      }
      const sel = ev.target.closest('[data-triage-select]');
      if (sel) {
        this.selectedEventId = sel.getAttribute('data-triage-select');
        this.renderSubview();
        return;
      }
      const actionBtn = ev.target.closest('[data-triage-action]');
      if (actionBtn && !this.triageBusy) {
        const action = actionBtn.getAttribute('data-triage-action');
        const id = actionBtn.getAttribute('data-event-id') || this.selectedEventId;
        if (id && action) this.handleTriageAction(action, id);
      }
    });

    // Console actions in the Devices table (delegated — table is re-rendered).
    // TerminalPanel is imported lazily so xterm stays out of the initial bundle.
    this.element.addEventListener('click', async (ev) => {
      const btn = ev.target.closest('[data-console-device]');
      if (!btn) return;
      const name = btn.getAttribute('data-console-device');
      if (!name || btn.disabled) return;
      btn.disabled = true;
      const original = btn.textContent;
      btn.textContent = 'Opening…';
      // Ask whoever owns the terminal to open it, rather than importing it.
      // Modules must not reach into each other's internals or into src/ — this
      // event is the whole contract, so the terminal can move (or be absent)
      // without Convergence changing. The button only renders when
      // /api/ssh/capabilities reports enabled, so a listener exists whenever it
      // is clickable; the timeout covers the case where it does not.
      const opened = await new Promise((resolve) => {
        const done = (ok) => { clearTimeout(t); resolve(ok); };
        const t = setTimeout(() => done(false), 8000);
        window.dispatchEvent(new CustomEvent('netclaw:open-terminal', {
          detail: { device: name, ack: (ok) => done(ok !== false) },
        }));
      });
      btn.textContent = opened ? original : 'Unavailable';
      if (!opened) btn.title = 'No terminal provider is listening';
      btn.disabled = false;
    });
  }

  getEscalatedEvents() {
    const raw = this.cache.escalated;
    return raw?.events || raw?.items || (Array.isArray(raw) ? raw : []);
  }

  saveIncidentDraft(target) {
    const form = target.closest('[data-security-case]');
    if (form) this.securityDrafts.set(`${this.site}:${form.dataset.securityCase}`, {kind: form.elements.kind.value, reason: form.elements.reason.value});
    const field = target.dataset.ispField, pane = target.closest('[data-isp-case]');
    if (field && pane) {
      const report = this.ispReports.get(`${this.site}:${pane.dataset.ispCase}`);
      if (report && ['subject', 'body'].includes(field)) report.draft[field] = target.value;
    }
  }

  async handleIncidentAction(action, eventId, body) {
    const site = this.site, key = `${site}:${eventId}`;
    if (this.incidentPending.has(key)) return;
    this.incidentPending.add(key); this.incidentErrors.delete(key); this.renderSubview();
    try {
      const path = `/events/${encodeURIComponent(eventId)}`;
      let result;
      if (action === 'load') result = await this.fetch(`${path}?site=${encodeURIComponent(site)}`);
      else result = await this.fetch(`${path}/${action === 'security' ? 'security-disposition' : 'isp-report'}?site=${encodeURIComponent(site)}`, {method: 'POST',
        body: action === 'security' ? body : {provider_id: this.ispProviders.get(site), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone}});
      if (this.site !== site || this.disposed) return;
      if (action === 'security') {
        for (const group of ['events', 'escalated', 'securityCases']) {
          const event = this.cache[group]?.events?.find(e => e.id === eventId);
          if (event) Object.assign(event, result);
        }
        this.securityDrafts.delete(key);
      } else {
        const report = action === 'load' ? result.isp_report : result;
        if (!report) throw new Error('No saved report is available');
        this.ispReports.set(key, report);
      }
    } catch (error) { if (this.site === site) this.incidentErrors.set(key, error.message || 'Incident action unavailable'); }
    finally { this.incidentPending.delete(key); if (!this.disposed && this.site === site) this.renderSubview(); }
  }

  downloadIspReport(eventId, kind) {
    const report = this.ispReports.get(`${this.site}:${eventId}`);
    if (!report) return;
    const file = reportDownload(report, kind), blob = new Blob([file.text], {type: file.type});
    const url = URL.createObjectURL(blob), anchor = document.createElement('a');
    anchor.href = url; anchor.download = `internet-incident-${String(eventId).replace(/[^a-zA-Z0-9-]/g, '')}.${file.extension}`;
    document.body.append(anchor); anchor.click(); anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async handleJevReview(eventId) {
    const site = this.site;
    const key = `${site}:${eventId}`;
    if (this.jevPending.has(key)) return;
    this.jevPending.add(key);
    this.jevErrors.delete(key);
    this.renderSubview();
    const update = review => {
      for (const group of ['events', 'escalated', 'securityCases']) {
        const raw = this.cache[group];
        const rows = raw?.events || raw?.items || (Array.isArray(raw) ? raw : []);
        const row = rows.find(e => e.id === eventId);
        if (row) row.jev_review = review;
      }
      this.renderSubview();
    };
    try {
      const result = await this.fetch(`/events/${encodeURIComponent(eventId)}/jev-review?site=${encodeURIComponent(site)}`, {method: 'POST'});
      if (this.site !== site || this.disposed) return;
      update(result.review || {status: 'queued'});
      if (result.review && result.review.status !== 'queued') return;
      // Poll this case even while the operator has focus inside the workspace.
      // A delayed job remains queued and can be checked again without a new call.
      for (let attempt = 0; attempt < 15; attempt++) {
        await this.waitJevPoll();
        if (this.site !== site || this.disposed) return;
        const event = await this.fetch(`/events/${encodeURIComponent(eventId)}?site=${encodeURIComponent(site)}`);
        if (this.site !== site || this.disposed) return;
        if (event.jev_review) update(event.jev_review);
        if (event.jev_review && event.jev_review.status !== 'queued') return;
      }
      this.jevErrors.set(key, 'Review is still queued. Use Check review to retrieve it later.');
    } catch (err) {
      if (this.site === site) this.jevErrors.set(key, err.message || 'JEV review unavailable');
    } finally {
      this.jevPending.delete(key);
      if (!this.disposed && this.site === site) this.renderSubview();
    }
  }

  waitJevPoll() { return new Promise(resolve => setTimeout(resolve, 2000)); }

  getSelectedEvent() {
    const events = this.getEscalatedEvents();
    if (!events.length) return null;
    if (this.selectedEventId) {
      const found = events.find((e) => String(e.id) === String(this.selectedEventId));
      if (found) return found;
    }
    return events[0];
  }

  notesFromForm(eventId) {
    const ta = this.element?.querySelector('.home-triage-textarea');
    if (ta?.dataset.eventId !== String(eventId)) return (this.notesDrafts.get(String(eventId)) || '').trim();
    return (ta?.value || '').trim();
  }

  async handleTriageAction(action, eventId) {
    if (this.triageBusy) return;
    const notes = this.notesFromForm(eventId);
    this.triageBusy = true;
    this.triageFlash = null;
    this.renderSubview();
    try {
      if (action === 'need_more') {
        const res = await this.fetch(`/events/${encodeURIComponent(eventId)}/reinvestigate?site=${this.site}`, {
          method: 'POST',
          body: { expert_feedback: notes || undefined, site: this.site },
        });
        const st = res?.reinvestigate?.status || 'accepted';
        this.triageFlash = {
          kind: st === 'error' ? 'error' : 'ok',
          message:
            st === 'skipped'
              ? 'Case reopened as investigating. Alert-receiver not configured on convergence-api (set ALERT_RECEIVER_URL to re-hook guardian-claw).'
              : st === 'error'
                ? `Reinvestigate partial: case reopened, but receiver error: ${res?.reinvestigate?.reason || res?.reinvestigate?.detail?.message || 'unknown'}`
                : 'Need More sent — case is investigating; investigator will re-run.',
        };
        this.selectedEventId = null;
      } else {
        // Feedback buttons: correct | partial | incorrect | resolve
        const qualityMap = {
          correct: 'correct',
          partial: 'partially_correct',
          incorrect: 'incorrect',
          resolve: null,
        };
        const feedback_quality = qualityMap[action];
        const body = {
          expert_feedback: notes || undefined,
        };
        if (feedback_quality) body.feedback_quality = feedback_quality;
        if (action === 'correct' || action === 'resolve') {
          body.status = 'resolved';
          body.severity = action === 'correct' ? 'ok' : undefined;
        }
        // incorrect / partial stay escalated unless notes say otherwise
        await this.fetch(`/events/${encodeURIComponent(eventId)}?site=${this.site}`, {
          method: 'PATCH',
          body,
        });
        this.triageFlash = {
          kind: 'ok',
          message:
            action === 'correct'
              ? 'Marked correct and resolved.'
              : action === 'partial'
                ? 'Marked partially correct — still escalated for follow-up.'
                : action === 'incorrect'
                  ? 'Marked incorrect — still escalated; add notes and Need More if re-run needed.'
                  : 'Case resolved.',
        };
        if (action === 'correct' || action === 'resolve') this.selectedEventId = null;
      }
      // Refresh queue
      this.cache.escalated = await this.fetch(`/events/escalated?site=${this.site}`).catch(async () =>
        this.fetch(`/events?site=${this.site}&status=escalated&limit=20`),
      );
      // Keep diary warm
      this.cache.events = await this.fetch(`/events?site=${this.site}&limit=100&range=${this.diaryRange}`).catch(() => this.cache.events);
    } catch (err) {
      this.triageFlash = {
        kind: 'error',
        message: err.message || String(err),
      };
    } finally {
      this.triageBusy = false;
      this.renderSubview();
    }
  }

  /**
   * Ask the API which sites exist and settle on one.
   *
   * GET /sites is already scope-filtered server-side, so this only ever offers
   * sites the caller may actually read. Runs once per session; the result backs
   * both the active site and the selector.
   *
   * Falls back to the current value on any failure — a discovery problem should
   * degrade to the old single-site behaviour, not blank the view.
   */
  async discoverSites() {
    if (this.sites.length && !this.readOnly) return;
    let sites = [];
    try {
      const res = await this.fetch('/sites');
      sites = Array.isArray(res?.sites) ? res.sites : [];
    } catch (error) {
      if (this.readOnly) throw error;
      return;
    }
    if (!sites.length) {
      if (this.readOnly) throw Error('No authorized site available.');
      return;
    }
    if (this.readOnly && this.sites.length && this.site !== sites[0].id) this.clearOwnerData(this.cache.status);

    this.sites = sites;
    const ids = sites.map((s) => s.id);
    if (!ids.includes(this.site)) {
      // Stored or default id is not on offer (renamed site, revoked access, or a
      // deployment that never used "home") — take the first authorised one.
      this.site = ids[0];
      if (!this.readOnly) writeStoredSite(this.site);
    }
  }

  /**
   * Render the site selector. Deliberately absent with a single site — a picker
   * with one option is noise, and it keeps the single-site view identical to
   * before this change.
   */
  renderSiteSelector() {
    const slot = this.qs('#home-site-slot');
    if (!slot) return;
    if (this.sites.length < 2) {
      slot.innerHTML = '';
      return;
    }
    const opts = this.sites.map((s) => {
      const sel = s.id === this.site ? ' selected' : '';
      const dot = s.healthy === false ? ' ⚠' : '';
      return `<option value="${esc(s.id)}"${sel}>${esc(s.name || s.id)}${dot}</option>`;
    }).join('');
    slot.innerHTML = `
      <label class="home-site-label" for="home-site-select">Site</label>
      <select class="home-site-select" id="home-site-select" aria-label="Active site" ${this.loading || this.triageBusy || this.modelsBusy ? 'disabled' : ''}>${opts}</select>`;
  }

  /** Switch site, persist the choice, and reload everything scoped to it. */
  async selectSite(siteId) {
    if (!siteId || siteId === this.site) return;
    if (this.loading || this.triageBusy || this.modelsBusy) return;
    if (this.sites.length && !this.sites.some((s) => s.id === siteId)) return;
    this.site = siteId;
    writeStoredSite(siteId);
    // Site-scoped caches are now stale; keep only the site list.
    this.cache = {};
    this.vmSecuritySearch = ''; this.vmSecurityFilter = 'all'; this.vmSecurityPage = 0; this.vmSecuritySelected = null;
    this.securitySelectedId = null; this.securityFilter = 'all'; this.securitySearch = '';
    this.securityVlan = ''; this.securityNetwork = ''; this.securityCountry = '';
    this.securityHistoryWindow = null; this.securityHistoryCountry = '';
    this.notesDrafts.clear();
    this.diarySelected.clear(); this.diaryFlash = null; this.flowExporter = '';
    this.lastSuccess = null;
    this.readTimes = {};
    this.selectedEventId = null;
    await this.refresh(true);
  }

  async refresh(force = false) {
    if (this.disposed) return;
    // Coalesce concurrent refreshes; still run one more pass if requested while busy
    if (this.loading) {
      this._refreshAgain = true;
      return;
    }
    this.loading = true;
    this.lastError = null;
    this.sourceErrors = {};
    this.renderSubview(); // show loading
    try {
      const status = await this.fetch('/status').catch(() => ({ configured: false }));
      this.cache.status = status;

      if (this.readOnly && (document.hidden || this.disposed)) return;
      if (!status.configured) {
        if (this.readOnly) this.clearOwnerData(status);
        if (!this.readOnly) this.cache.security = {hostTelemetry: {status: 'unavailable', agents: []}};
        this.lastError = {
          kind: 'config',
          message: this.readOnly ? 'Site telemetry requires a local owner session. Sign in to the reviewer to work on cases.' : 'Convergence API is unavailable.',
        };
        this.syncTopbarMetrics(null);
        return;
      }

      // Resolve the active site before any site-scoped query, so a stored or
      // default id that the API does not serve cannot produce empty panels.
      await this.discoverSites();

      // Health + wifi always — top-right Command Deck metrics (Health / WAN / Wi‑Fi / Alerts)
      this.cache.health = await this.fetch(`/health?site=${this.site}`).catch(err => {
        if (!['security','vm-security'].includes(this.subview)) throw err;
        return {unavailable: true, reason: err.message};
      });
      if (this.cache.health?.unavailable) this.sourceErrors.overview = this.cache.health.reason || 'Monitoring data incomplete';
      this.cache.wifi = await this.fetch(`/wifi?site=${this.site}`).catch(err => { this.sourceErrors.wifi = err.message; return this.cache.wifi || null; });

      if (this.subview === 'vm-security' || force) {
        const vmSite = this.site;
        try {
          const observations = await this.fetch(`/security/vms?site=${encodeURIComponent(vmSite)}`);
          if (vmSite !== this.site) return;
          this.cache.vmSecurity = observations;
          if (observations?.unavailable || observations?.status === 'unavailable') {
            this.sourceErrors['vm-security'] = observations.reason || 'VM security observations unavailable';
          } else {
            this.readTimes.vmSecurity = new Date().toISOString();
          }
        } catch (error) {
          if (vmSite !== this.site) return;
          this.sourceErrors['vm-security'] = error.message || 'VM security observations unavailable';
        }
      }
      if (this.subview === 'security' || force) {
        const caseSite=this.site, caseRange=this.securityRange || '24h';
        const security = await this.fetch(`/security?site=${encodeURIComponent(caseSite)}`).catch(() => ({hostTelemetry: {status: 'unavailable', agents: []}}));
        // A delayed read belongs only to the site that requested it.
        if (caseSite !== this.site) return;
        this.cache.security = security;
        const scope=JSON.stringify([this.securityHistoryWindow,this.securityHistoryCountry]);
        const [cases,history]=await Promise.all([
          this.fetch(securityCasesPath(caseSite,caseRange,this.securityHistoryWindow,this.securityHistoryCountry,Boolean(this.options?.embedded))).catch(()=>({unavailable:true})),
          this.options?.embedded?this.fetch(`/security/history?site=${encodeURIComponent(caseSite)}&range=${caseRange}`).catch(()=>({unavailable:true})):null,
        ]);
        if(caseSite===this.site && caseRange===(this.securityRange || '24h') && scope===JSON.stringify([this.securityHistoryWindow,this.securityHistoryCountry])) {
          this.cache.securityCases = cases;
          this.cache.securityHistory = history;
          if (!cases?.unavailable) this.readTimes.securityCases = this.readTimes.events;
        }
      }
      if (this.subview === 'devices' || force) {
        this.cache.devices = await this.fetch(`/devices?site=${this.site}`);
        // Which of these are reachable by the HUD SSH terminal. Two local
        // endpoints (not convergence-api): the pyATS testbed inventory, and
        // whether the terminal is enabled at all. Failures are non-fatal — the
        // Console action just doesn't render.
        this.cache.terminal = this.readOnly ? null : await loadTerminalTargets().catch(() => null);
      }
      if (this.subview === 'isp' || force) {
        this.cache.wanCases = await this.fetch(`/events?site=${this.site}&type=wan&limit=50`);
        this.readTimes.wanCases = this.readTimes.events;
        this.cache.ispCatalog = await this.fetch('/isp/providers');
      }
      if (this.subview === 'flows' || force) {
        this.cache.flows = await this.fetch(`/flows?site=${encodeURIComponent(this.site)}&range=${this.flowRange}&exporter=${encodeURIComponent(this.flowExporter)}`).catch(err => { this.sourceErrors.flows = err.message; return {unavailable:true}; });
      }
      if (this.subview === 'diary' || force) {
        this.cache.events = await this.fetch(`/events?site=${this.site}&limit=100&range=${this.diaryRange}`);
        const availableIds = new Set((this.cache.events?.events || []).map(e => e.id));
        this.diarySelected = new Set([...this.diarySelected].filter(id => availableIds.has(id)));
        // Firing alerts help when diary DB is still empty (fresh Docker)
        this.cache.alerts = await this.fetch(`/alerts?site=${this.site}`).catch(() => null);
      }
      if (this.subview === 'triage' || force) {
        this.cache.escalated = await this.fetch(`/events/escalated?site=${this.site}`).catch(async () =>
          this.fetch(`/events?site=${this.site}&status=escalated&limit=20`),
        );
      }
      if (this.subview === 'models' || (!this.readOnly && force)) {
        await this.refreshModels(false);
      }

      if (this.readOnly && !this.cache.status?.configured) return;
      this.lastSuccess = new Date().toISOString();
      this.syncTopbarMetrics(this.cache.health);
    } catch (err) {
      this.lastError = {
        kind: err.status === 502 || err.status === 503 ? 'down' : 'error',
        message: err.message || String(err),
        detail: err.payload,
      };
      // Keep last good topbar values if we have them
      this.syncTopbarMetrics(this.cache.health || null);
    } finally {
      this.loading = false;
      this.renderSubview();
      if (this._refreshAgain) {
        this._refreshAgain = false;
        this.refresh(true);
      }
    }
  }

  syncTopbarMetrics(health) {
    if (this.options.embedded || this.disposed) return;
    const set = (id, v) => {
      const el = document.getElementById(id);
      if (el) el.textContent = v;
    };
    // Allow no-arg call to re-use cache (tab switch)
    const h = health === undefined ? this.cache?.health : health;
    if (!h) {
      set('home-metric-health', '—');
      set('home-metric-latency', '—');
      set('home-metric-wifi', '—');
      set('home-metric-alerts', '—');
      return;
    }
    const hs = h.healthScore?.value ?? h.health_score ?? '—';
    const lat = h.wanLatency?.value ?? h.latency ?? '—';
    const alerts = h.alertCount?.firing ?? h.alerts?.firing ?? '—';
    set('home-metric-health', String(hs));
    set('home-metric-latency', lat === '—' ? '—' : String(Math.round(Number(lat) * 10) / 10));
    set('home-metric-wifi', this.cache.wifi?.clients?.wireless != null
      ? `${this.cache.wifi.clients.wireless} cl.`
      : '—');
    set('home-metric-alerts', String(alerts));
  }

  renderSubview() {
    if (this.disposed) return;
    if (this.readOnly) {
      const models = this.chrome?.querySelector('[data-home-sub="models"]');
      if (models) models.hidden = !this.cache.status?.configured;
    }
    this.options.onUpdate?.(this);
    const body = this.element?.querySelector('#home-panel-body');
    if (!body) return;

    // Cheap and idempotent; keeps the selector in step with discovery.
    this.renderSiteSelector();

    if (this.loading && !this.cache.health) {
      body.innerHTML = this.htmlBanner('loading', 'Loading home telemetry…');
      return;
    }
    if (this.lastError) {
      body.innerHTML =
        this.htmlBanner(this.lastError.kind, this.lastError.message) +
        this.htmlBodyForSubview();
      return;
    }
    body.innerHTML = (this.sourceErrors[this.subview] ? this.htmlBanner('down', this.sourceErrors[this.subview]) : '') + this.htmlBodyForSubview();
  }

  htmlBanner(kind, message) {
    const label =
      kind === 'config' ? 'Not configured' :
      kind === 'down' ? 'API unreachable' :
      kind === 'loading' ? 'Loading' : 'Error';
    return `
      <div class="home-banner home-banner-${esc(kind)}" role="status">
        <span class="home-badge ${kind === 'loading' ? '' : 'mock'}">${esc(label)}</span>
        <p class="home-muted" style="margin:8px 0 16px">${esc(message)}</p>
      </div>`;
  }

  clearOwnerData(status) {
    this.authorityGeneration++;
    this.requests.forEach(controller => controller.abort());
    this.cache = {status};
    this.sites = [];
    this.site = '';
    this.selectedEventId = this.securitySelectedId = this.vmSecuritySelected = null;
    this.lastSuccess = this.triageFlash = this.securityHistoryWindow = null;
    this.modelsDraft = this.modelsFlash = null;
    this.readTimes = {};
    this.sourceErrors = {};
    for (const collection of [this.notesDrafts, this.securityDrafts, this.incidentErrors, this.jevErrors,
      this.ispReports, this.ispProviders, this.diarySelected]) collection.clear();
    this.syncTopbarMetrics(null);
  }

  htmlBodyForSubview() {
    const origin = this.cache.status?.reviewerOrigin;
    if (this.readOnly && !this.cache.status?.configured) return reviewerLink(origin);
    const map = {
      overview: () => this.htmlOverview(),
      wifi: () => this.htmlWifi(),
      devices: () => this.htmlDevices(),
      security: () => this.htmlSecurity(),
      'vm-security': () => vmSecurityHTML(this.cache.vmSecurity, {site:this.site, search:this.vmSecuritySearch, filter:this.vmSecurityFilter, page:this.vmSecurityPage, selected:this.vmSecuritySelected, loading:this.loading, error:this.sourceErrors['vm-security'] || this.lastError?.message}),
      isp: () => this.readOnly ? this.htmlWanCases() : ispReportsHTML(this.cache.wanCases, this.cache.ispCatalog, this.ispProviders.get(this.site), this.ispReports, this.incidentPending, this.incidentErrors, this.site),
      diary: () => this.htmlDiary(),
      flows: () => flowHTML(this.cache.flows, {range:this.flowRange, exporter:this.flowExporter, canInvestigate:Boolean(this.options?.investigate)}),
      triage: () => this.htmlTriage(),
      models: () => this.htmlModels(),
    };
    return (this.readOnly ? reviewerLink(origin) : '') + (map[this.subview] || map.overview)();
  }

  htmlSecurity() {
    const data = this.cache.security;
    if (!data) return '<p class="home-muted">Loading security observations…</p>';
    const ids = data.intrusionDetection || {};
    const embedded=Boolean(this.options?.embedded);
    return `${embedded?securityExplorerHTML(this.cache.securityCases,data,this.securityState())+'<details class="security-sensor-details"><summary>Sensor health, firewall counts &amp; coverage details</summary>':''}<div class="home-section">
      <h3>Security visibility</h3>
      <p>Firewall telemetry: <strong>${esc(data.status)}</strong> · observation window ${esc(data.observedWindow || 'unknown')}</p>
      ${data.reason ? `<p class="home-muted">${esc(data.reason)}</p>` : ''}
      <p>IDS feed: <strong>${esc(ids.status || 'unknown')}</strong> · ${esc(ids.reason || 'Coverage needs verification')}</p>
      ${embedded?'':securityQueueHTML(this.cache.securityCases, this.securityFilter, this.securityDrafts, this.incidentPending, this.incidentErrors, this.site, {readOnly:this.readOnly, reviewerOrigin:this.cache.status?.reviewerOrigin, pending: this.jevPending, errors: this.jevErrors, canInvestigate: Boolean(this.options?.investigate)})}
      <div class="home-kpi-grid">
        <div class="home-kpi"><div class="home-kpi-label">Blocked connections · 1 hour</div><div class="home-kpi-value">${esc(data.blockedConnections?.lastHour ?? 'Unknown')}</div></div>
        <div class="home-kpi"><div class="home-kpi-label">Blocked connections · 24 hours</div><div class="home-kpi-value">${esc(data.blockedConnections?.last24h ?? 'Unknown')}</div></div>
      </div>
      <h3>Intrusion detection · ${esc(ids.status || 'unknown')}</h3>
      <p>${esc(ids.reason || 'Sensor coverage unavailable')}</p>
      ${(ids.sensors || []).map(sensor => `<div class="home-section">
        <h4>${esc(sensor.id)} · ${esc(sensor.status)}</h4><p>${esc(sensor.reason)}</p>
        <div class="home-kpi-grid">
          <div class="home-kpi"><div class="home-kpi-label">Captured packets</div><div class="home-kpi-value">${esc(sensor.packets ?? 'Unknown')}</div></div>
          <div class="home-kpi"><div class="home-kpi-label">Capture drops · since engine start</div><div class="home-kpi-value">${esc(sensor.captureDrops ?? 'Unknown')}</div></div>
          <div class="home-kpi"><div class="home-kpi-label">Decoder errors · since engine start</div><div class="home-kpi-value">${esc(sensor.decoderErrors ?? 'Unknown')}</div></div>
          <div class="home-kpi"><div class="home-kpi-label">Rules available</div><div class="home-kpi-value">${esc(sensor.rulesCount ?? 'Unknown')}</div></div>
          <div class="home-kpi"><div class="home-kpi-label">Events awaiting delivery</div><div class="home-kpi-value">${esc(sensor.pendingEvents ?? 'Unknown')}</div></div>
        </div>
        <p class="home-muted">${esc(sensor.scope)}. Automatic investigations: ${sensor.autoInvestigate ? 'subject to Guardian policy' : 'disabled during tuning; cases remain available for review'}.</p>
        ${(sensor.captureSources || []).length ? `<h4>Capture points</h4>
          <p class="home-muted">Source liveness comes from mirrored headers; engine statistics above report Suricata capture health.</p>
          <div class="home-table-wrap"><table class="home-table"><thead><tr><th>Source</th><th>State</th><th>Scope</th></tr></thead><tbody>
            ${sensor.captureSources.map(source => `<tr><td>${esc(source.name)}</td><td>${esc(source.status)}</td><td>${esc(source.scope)}</td></tr>`).join('')}
          </tbody></table></div>` : ''}
        ${(sensor.coverageGaps || []).length ? `<details><summary>Coverage gaps</summary><ul>${sensor.coverageGaps.map(gap => `<li>${esc(gap)}</li>`).join('')}</ul></details>` : ''}
        <div class="home-table-wrap"><table class="home-table"><thead><tr><th>VLAN</th><th>Packet evidence · last 15 minutes</th></tr></thead><tbody>
          ${(sensor.coverage || []).map(v => `<tr><td>${esc(v.vlan)}</td><td>${esc(v.status)}</td></tr>`).join('')}
        </tbody></table></div>
      </div>`).join('')}
      ${ids.encryptedTraffic ? `<h3>Encrypted traffic visibility</h3>
        <p>Metadata only · last ${esc(ids.encryptedTraffic.window)}: ${esc(ids.encryptedTraffic.tlsObservations)} TLS observations,
        ${esc(ids.encryptedTraffic.fingerprinted)} with client fingerprints, ${esc(ids.encryptedTraffic.serverNamesObserved)} with visible server names.</p>
        <p class="home-muted">Payload decryption is not enabled. Encryption and incomplete handshakes can hide server names or other metadata; fingerprints alone do not establish malicious activity.</p>` : ''}
      ${ids.ipEnrichment ? `<h3>External IP context · ${esc(ids.ipEnrichment.status)}</h3>
        <p>Local country and ASN lookups for IDS sources and destinations. IP address data powered by
        <a href="https://ipinfo.io" target="_blank" rel="noopener noreferrer">IPinfo</a>.</p>
        <p class="home-muted">New IDS cases show this context in the Diary and Triage views. Country and network organization do not establish malicious activity. Reputation assessment remains a separate investigation.</p>` : ''}
      <h3>Most frequently blocked sources · 1 hour</h3>
      <p class="home-muted">A firewall block is an observation. A source needs investigation before it can be classified as malicious.</p>
      <div class="home-table-wrap"><table class="home-table"><thead><tr><th>Source</th><th>Blocks</th><th>Assessment</th></tr></thead><tbody>
      ${(data.topSources || []).map(source => `<tr><td>${esc(source.ip)}</td><td>${esc(source.count)}</td><td>${esc(source.classification)}</td></tr>`).join('') || '<tr><td colspan="3">No source observations in this window</td></tr>'}
      </tbody></table></div></div>${embedded?'</details>':''}`;
  }

  htmlWanCases() {
    const cases = this.cache.wanCases;
    if (!Array.isArray(cases?.events)) return '<p>WAN case history unavailable.</p>';
    return '<h2>Internet incidents</h2>' + (cases.events.map(e =>
      `<article><h3>${esc(e.alert_name)}</h3><p>${esc(e.message)}</p>${reviewerLink(this.cache.status?.reviewerOrigin, this.site, e.id)}</article>`
    ).join('') || '<p>No recorded WAN incidents in this view.</p>');
  }

  securityState() {
    return {readOnly:this.readOnly, reviewerOrigin:this.cache.status?.reviewerOrigin, site:this.site,range:this.securityRange,group:this.securityGroup,filter:this.securityFilter,search:this.securitySearch,
      history:this.cache.securityHistory,historyWindow:this.securityHistoryWindow,historyCountry:this.securityHistoryCountry,
      vlan:this.securityVlan,network:this.securityNetwork,country:this.securityCountry,selectedId:this.securitySelectedId,
      drafts:this.securityDrafts,incidentPending:this.incidentPending,incidentErrors:this.incidentErrors,
      jevPending:this.jevPending,jevErrors:this.jevErrors,readAt:this.readTimes.securityCases,canInvestigate:Boolean(this.options?.investigate)};
  }

  reloadSecurityCases() {
    this.securitySelectedId=null;
    delete this.cache.securityCases;delete this.readTimes.securityCases;
    this.renderSubview();this.refresh();
  }

  focusSecurityDetail() {
    const heading=this.element?.querySelector('#security-detail-title');
    heading?.focus({preventScroll:true});
    if(window.matchMedia('(max-width: 1100px)').matches)heading?.scrollIntoView({block:'start',behavior:'auto'});
  }

  investigateSecurityGroup(eventId) {
    const cases=this.cache.securityCases;
    if(!cases || cases.unavailable || (cases.site && cases.site!==this.site) || !this.options?.investigate)return;
    const group=securityModel(cases,this.securityState()).groups.find(g=>g.events.some(e=>e.id===eventId));
    if(!group)return;
    const events=[...group.events].sort((a,b)=>Date.parse(b.timestamp)-Date.parse(a.timestamp)).slice(0,MAX_SELECTION);
    this.options.investigate('Convergence grouped security deep dive',caseHandoff(events,this.site,this.readTimes.securityCases,`Selected Security group: ${group.label}`),
      `/api/home${securityCasesPath(this.site,this.securityRange,this.securityHistoryWindow,this.securityHistoryCountry)}`,this.readTimes.securityCases);
  }

  fetchModels(options) {
    return this.readOnly ? this.options.hostAccess.models(options) : fetch('/api/models', options);
  }

  async refreshModels(render = true) {
    if (this.readOnly && !this.cache.status?.configured) return;
    try {
      const res = await this.fetchModels({
        headers: { Accept: 'application/json' },
        signal: AbortSignal.timeout(10000),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
      this.cache.models = data;
      if (!this.modelsDraft) {
        this.modelsDraft = {
          brain: data.sot?.brain || '',
          alert: data.sot?.alert || '',
          fallback: data.sot?.fallback || '',
        };
      }
    } catch (err) {
      this.cache.models = { error: err.message || String(err) };
    }
    if (render && this.subview === 'models') this.renderSubview();
  }

  async handleModelsApply(isPreset, presetName) {
    if (this.readOnly || this.modelsBusy) return;
    this.modelsBusy = true;
    this.modelsFlash = { kind: 'ok', message: isPreset ? `Applying preset ${presetName}…` : 'Applying models…' };
    this.renderSubview();
    try {
      // Capture current form values (re-render may wipe inputs)
      const body = isPreset
        ? { preset: presetName, restart: true }
        : {
            brain: this.modelsDraft?.brain || this.element?.querySelector('[data-models-field="brain"]')?.value,
            alert: this.modelsDraft?.alert || this.element?.querySelector('[data-models-field="alert"]')?.value,
            fallback:
              this.modelsDraft?.fallback ??
              this.element?.querySelector('[data-models-field="fallback"]')?.value ??
              '',
            restart: true,
          };
      const res = await this.fetchModels({
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(120000),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
      }
      this.modelsDraft = {
        brain: data.sot?.brain ?? data.sot?.NETCLAW_BRAIN_MODEL ?? body.brain ?? this.modelsDraft?.brain,
        alert: data.sot?.alert ?? data.sot?.NETCLAW_ALERT_TRIAGE_MODEL ?? body.alert ?? this.modelsDraft?.alert,
        fallback: data.sot?.fallback ?? data.sot?.NETCLAW_ALERT_FALLBACK_MODEL ?? body.fallback ?? this.modelsDraft?.fallback,
      };
      this.modelsFlash = {
        kind: 'ok',
        message: 'Applied to .env + openclaw.json; gateway restarted. Investigations use alert model; chat uses brain.',
      };
      await this.refreshModels(false);
      window.dispatchEvent(new CustomEvent('netclaw:models-updated'));
    } catch (err) {
      this.modelsFlash = { kind: 'error', message: err.message || String(err) };
    } finally {
      this.modelsBusy = false;
      this.renderSubview();
    }
  }

  htmlModels() {
    const m = this.cache.models;
    const err = m?.error;
    const sot = m?.sot || {};
    const live = m?.live || {};
    if (this.readOnly) {
      if (err) return this.htmlBanner('down', err);
      return `<h2>Models</h2><dl>
        <dt>Default model</dt><dd>${esc(live.defaultsPrimary || sot.brain || 'Unavailable')}</dd>
        <dt>Alert agent</dt><dd>${esc(live.alertAgent || sot.alert || 'Unavailable')}</dd>
        <dt>Alert fallback</dt><dd>${esc(sot.fallback || 'None configured')}</dd>
      </dl><p>Select a model for your conversation in NetClaw Chat.</p>`;
    }
    const draft = this.modelsDraft || {
      brain: sot.brain || '',
      alert: sot.alert || '',
      fallback: sot.fallback || '',
    };
    const flash = this.modelsFlash
      ? `<p class="home-flash home-flash-${esc(this.modelsFlash.kind)}">${esc(this.modelsFlash.message)}</p>`
      : '';
    if (err && !sot.file) {
      return `
        <div class="home-panel-header">
          <div><p class="eyebrow">OpenClaw</p><h2>Models</h2></div>
        </div>
        ${flash}
        <p class="home-muted">Could not load model status: ${esc(err)}</p>`;
    }
    const liveBrain = live.defaultsPrimary || '—';
    const liveHook = live.hookAlert?.model || '—';
    const liveAgent = live.alertAgent?.primary || live.alertAgent || '—';
    return `
      <div class="home-panel-header">
        <div>
          <p class="eyebrow">SoT · netclaw/.env</p>
          <h2>Models</h2>
        </div>
        <button type="button" class="home-refresh-btn" data-models-action="reload" ${this.modelsBusy ? 'disabled' : ''}>Reload</button>
      </div>
      ${flash}
      <p class="home-muted">
        ${this.options.embedded ? 'Choose the models used for interactive chat and alert investigations. Applying a change restarts the gateway; investigations already running keep their original model.' : `One place to set brains. Edit fields (or pick a preset), then <strong>Apply &amp; restart gateway</strong>.
        Writes <code>NETCLAW_BRAIN_MODEL</code> / <code>NETCLAW_ALERT_TRIAGE_MODEL</code> in
        <code>${esc(sot.file || '…/netclaw/.env')}</code>, projects into
        <code>~/.openclaw/openclaw.json</code> + <code>gateway.systemd.env</code>.
        CLI: <code>./scripts/netclaw-apply-models.sh show|apply|preset cloud-flash</code>`}
      </p>
      <div class="home-models-grid">
        <div class="home-models-card">
          <p class="home-section-title">Source of truth</p>
          <label class="home-models-label">Brain (interactive main / HUD chat)
            <input type="text" class="home-models-input" data-models-field="brain"
              value="${esc(draft.brain)}" placeholder="ollama/deepseek-v4-flash:cloud"
              ${this.modelsBusy ? 'disabled' : ''} />
          </label>
          <label class="home-models-label">Alert triage (T2 hooks / thin alert agent)
            <input type="text" class="home-models-input" data-models-field="alert"
              value="${esc(draft.alert)}" placeholder="ollama/deepseek-v4-flash:cloud"
              ${this.modelsBusy ? 'disabled' : ''} />
          </label>
          <label class="home-models-label">Alert fallback (optional)
            <input type="text" class="home-models-input" data-models-field="fallback"
              value="${esc(draft.fallback)}" placeholder="ollama/glm-5.2:cloud"
              ${this.modelsBusy ? 'disabled' : ''} />
          </label>
          <div class="home-models-actions">
            <button type="button" class="home-action-btn primary" data-models-action="apply" ${this.modelsBusy ? 'disabled' : ''}>
              ${this.modelsBusy ? 'Applying…' : 'Apply & restart gateway'}
            </button>
          </div>
          <p class="home-section-title" style="margin-top:1rem">Presets</p>
          <p class="home-muted" style="margin-bottom:12px">OpenAI uses your configured OpenClaw sign-in. Subscription access is connected through OpenClaw; an API key is not required for a Codex subscription.</p>
          <div class="home-models-presets">
            <button type="button" class="home-action-btn" data-models-action="preset" data-preset="openai" ${this.modelsBusy ? 'disabled' : ''} title="OpenAI chat and investigation models; requires an OpenClaw OpenAI sign-in">OpenAI</button>
            <button type="button" class="home-action-btn" data-models-action="preset" data-preset="split" ${this.modelsBusy ? 'disabled' : ''} title="Local chat + cloud investigations">split</button>
            <button type="button" class="home-action-btn" data-models-action="preset" data-preset="cloud-flash" ${this.modelsBusy ? 'disabled' : ''}>cloud-flash</button>
            <button type="button" class="home-action-btn" data-models-action="preset" data-preset="anthropic" ${this.modelsBusy ? 'disabled' : ''} title="Sonnet brain + Haiku alert">anthropic</button>
            <button type="button" class="home-action-btn" data-models-action="preset" data-preset="local" ${this.modelsBusy ? 'disabled' : ''}>local</button>
          </div>
        </div>
        <div class="home-models-card">
          <p class="home-section-title">Live OpenClaw</p>
          <div class="home-detail-rows">
            <div class="home-detail-row"><span>defaults.primary</span><strong>${esc(liveBrain)}</strong></div>
            <div class="home-detail-row"><span>hooks.alert.model</span><strong>${esc(liveHook)}</strong></div>
            <div class="home-detail-row"><span>agent alert.primary</span><strong>${esc(typeof liveAgent === 'object' ? liveAgent.primary || JSON.stringify(liveAgent) : liveAgent)}</strong></div>
            <div class="home-detail-row"><span>hook agentId</span><strong>${esc(live.hookAlert?.agentId || '—')}</strong></div>
            <div class="home-detail-row"><span>OLLAMA_BASE_URL</span><strong>${esc(sot.ollamaBaseUrl || '—')}</strong></div>
            <div class="home-detail-row"><span>OLLAMA_API_KEY</span><strong>${sot.ollamaKeySet ? 'set' : '—'}</strong></div>
          </div>
          <p class="home-muted" style="margin-top:0.75rem">
            After Apply, new T2 investigations use the alert model. In-flight sessions keep their original model.
          </p>
        </div>
      </div>
    `;
  }

  htmlOverview() {
    const h = this.cache.health;
    if (!h) {
      return `
        <div class="home-panel-header">
          <div><p class="eyebrow">Site · ${esc(this.site)}</p><h2>Overview</h2></div>
        </div>
        <p class="home-muted">No health data yet. Click Refresh after configuring Home API.</p>`;
    }
    const score = h.healthScore?.value;
    const scoreStatus = h.healthScore?.status || statusClass(score >= 90 ? 'healthy' : score >= 70 ? 'degraded' : 'unhealthy');
    const lat = h.wanLatency?.value;
    const loss = h.wanLoss?.value;
    const dl = h.speedtest?.download;
    const ul = h.speedtest?.upload;
    const firing = h.alertCount?.firing;
    const dual = this.cache.status?.dualRun;

    return `
      <div class="home-panel-header">
        <div>
          <p class="eyebrow">Site · ${esc(h.site || this.site)}</p>
          <h2>Overview</h2>
        </div>
        <span class="home-badge${dual ? '' : ''}">${dual ? 'Dual-run · pilot Guardian' : 'Live'}</span>
      </div>
      <div class="home-kpi-grid" role="group" aria-label="Home health metrics">
        <div class="home-kpi">
          <div class="home-kpi-label">Health score</div>
          <div class="home-kpi-value ${statusClass(scoreStatus)}">${score != null ? esc(score) : '—'}</div>
        </div>
        <div class="home-kpi">
          <div class="home-kpi-label">WAN latency</div>
          <div class="home-kpi-value ${statusClass(h.wanLatency?.status)}">${lat != null ? esc(Number(lat).toFixed(1)) : '—'}<span class="unit"> ms</span></div>
        </div>
        <div class="home-kpi">
          <div class="home-kpi-label">Packet loss</div>
          <div class="home-kpi-value ${statusClass(h.wanLoss?.status)}">${loss != null ? esc(loss) : '—'}<span class="unit">%</span></div>
        </div>
        <div class="home-kpi">
          <div class="home-kpi-label">Speedtest</div>
          <div class="home-kpi-value ${statusClass(h.speedtest?.status)}" title="${
            dl == null
              ? 'No speedtest metrics — enable full stack: docker compose -f docker-compose.yml -f docker-compose.full.yml --profile full up -d'
              : ''
          }">${
            dl != null
              ? `${esc(dl)}<span class="unit"> / ${ul != null ? esc(ul) : '—'} Mbps</span>`
              : 'n/a<span class="unit"> (no runner)</span>'
          }</div>
        </div>
        <div class="home-kpi">
          <div class="home-kpi-label">Firing alerts</div>
          <div class="home-kpi-value ${firing == null ? '' : firing > 0 ? 'warn' : 'ok'}">${esc(firing ?? '—')}</div>
        </div>
        ${h.devices?.snmpSwitches != null ? `
        <div class="home-kpi" title="Greenfield device_snmp — campus switches">
          <div class="home-kpi-label">Switches (SNMP)</div>
          <div class="home-kpi-value ${statusClass(h.devices.status)}">${esc(h.devices.snmpSwitches)}<span class="unit">${
            h.devices.portsUp != null
              ? ` · ${esc(h.devices.portsUp)}/${esc(h.devices.portsTotal ?? '—')} if`
              : ''
          }</span></div>
        </div>` : ''}
      </div>
      <p class="home-section-title">Network Guardian</p>
      <p class="home-muted">
        ${this.options.embedded ? 'Review site telemetry here, follow investigations in Diary, and handle escalated cases in Triage. Send observations to Canvas to continue an investigation with NetClaw.' : `Metrics → Alertmanager → alert-receiver → risk investigator (guardian-claw) → diary / triage.
        Convergence tab proxies <code>/api/home/*</code> to convergence-api (or pilot Network Guardian during dual-run).`}
      </p>
    `;
  }

  htmlWifi() {
    const w = this.cache.wifi;
    if (!w) {
      return `<div class="home-panel-header"><div><p class="eyebrow">Wireless</p><h2>Wi‑Fi</h2></div></div>
        <p class="home-muted">No Wi‑Fi data loaded. Click Refresh.</p>`;
    }
    const clients = w.clients || {};
    const retries = Array.isArray(w.txRetries) ? w.txRetries : [];
    const hasAnyClient =
      [clients.wireless, clients.wired, clients.guest, clients.total].some(
        (v) => v != null && Number(v) > 0,
      );
    const hasSeries = hasAnyClient || retries.length > 0;
    // Group by device
    const byDev = new Map();
    for (const r of retries) {
      const name = r.device || r.name || 'AP';
      if (!byDev.has(name)) byDev.set(name, { name, bands: [] });
      byDev.get(name).bands.push(r);
    }
    const rows = [...byDev.values()]
      .map((d) => {
        const b24 = d.bands.find((b) => /2\.4/i.test(b.band || ''));
        const b5 = d.bands.find((b) => /5/i.test(b.band || ''));
        // Prefer per-row mgmtUrl; fall back to wifi.mgmt.unifi
        const link = nameLink(d.name, d.bands[0]?.mgmtUrl || w.mgmt?.unifi, 'Open UniFi Network console');
        return `<tr>
          <td>${link}</td>
          <td class="${statusClass(b24?.status)}">${b24 ? `${esc(b24.value)}%` : '—'}</td>
          <td class="${statusClass(b5?.status)}">${b5 ? `${esc(b5.value)}%` : '—'}</td>
        </tr>`;
      })
      .join('');

    const emptyHint = !hasSeries
      ? `<div class="home-banner home-banner-config" style="margin:12px 0">
          <span class="home-badge mock">No unifi_* metrics</span>
          <p class="home-muted" style="margin:8px 0 0">
            Wi‑Fi KPIs need the UniFi exporter. On Docker Convergence:
            set <code>UNIFI_HOST</code> + <code>UNIFI_API_KEY</code> in <code>deploy/convergence/.env</code>, then
            <code>docker compose -f deploy/convergence/docker-compose.yml --env-file deploy/convergence/.env --profile unifi up -d</code>.
            Until then Overview WAN probes still work; this tab will stay empty.
          </p>
        </div>`
      : '';

    return `
      <div class="home-panel-header">
        <div>
          <p class="eyebrow">Wireless · ${esc(clients.wireless ?? '—')} clients</p>
          <h2>Wi‑Fi</h2>
        </div>
        <span class="home-badge${hasSeries ? '' : ' mock'}">${hasSeries ? 'Live · unifi_*' : 'Waiting · unifi'}</span>
      </div>
      ${emptyHint}
      <div class="home-kpi-grid home-kpi-grid-4">
        <div class="home-kpi"><div class="home-kpi-label">Wireless</div><div class="home-kpi-value">${esc(clients.wireless ?? '—')}</div></div>
        <div class="home-kpi"><div class="home-kpi-label">Wired</div><div class="home-kpi-value">${esc(clients.wired ?? '—')}</div></div>
        <div class="home-kpi"><div class="home-kpi-label">Guest</div><div class="home-kpi-value">${esc(clients.guest ?? '—')}</div></div>
        <div class="home-kpi"><div class="home-kpi-label">Total</div><div class="home-kpi-value">${esc(clients.total ?? '—')}</div></div>
      </div>
      <p class="home-section-title">TX retries by AP / band</p>
      <div class="home-table-wrap">
        <table class="home-table">
          <thead><tr><th>AP</th><th>2.4 GHz retries</th><th>5 GHz retries</th></tr></thead>
          <tbody>${rows || '<tr><td colspan="3">No retry series yet</td></tr>'}</tbody>
        </table>
      </div>
    `;
  }

  htmlDevices() {
    const d = this.cache.devices;
    if (d?.unavailable) return this.htmlBanner('down', d.reason || 'Site-scoped inventory is unavailable.');
    if (!d) {
      return `<div class="home-panel-header"><div><p class="eyebrow">Inventory</p><h2>Devices</h2></div></div>
        <p class="home-muted">No device data loaded. Click Refresh.</p>`;
    }

    // convergence-api shape: { wanProbes, edge/firewall, accessPoints, switches }
    const terminal = this.cache.terminal || null;
    const probes = Array.isArray(d.wanProbes) ? d.wanProbes : [];
    const edge = Array.isArray(d.edge) ? d.edge : (Array.isArray(d.firewall) ? d.firewall : []);
    const aps = Array.isArray(d.accessPoints) ? d.accessPoints : [];
    const switches = Array.isArray(d.switches) ? d.switches : [];
    const flat = Array.isArray(d.devices) ? d.devices : Array.isArray(d) ? d : d.items || [];

    const probeRows = probes
      .map((p) => {
        const st = p.status || '';
        return `<tr>
          <td>${esc(p.target || p.endpoint || '—')}</td>
          <td>${esc(p.type || 'probe')}</td>
          <td class="${statusClass(st === 'online' || st === 'up' ? 'healthy' : st === 'offline' || st === 'down' ? 'unhealthy' : st)}">${esc(st || '—')}</td>
          <td>${p.latencyMs != null ? `${esc(p.latencyMs)} ms` : '—'}</td>
        </tr>`;
      })
      .join('');

    const edgeRows = edge
      .map((e) => {
        const st = e.status || '';
        const detail = [
          e.model,
          e.latencyMs != null ? `${e.latencyMs} ms` : null,
          e.cpu != null && e.cpu > 0 ? `CPU ${e.cpu}%` : null,
          e.endpoint,
        ].filter(Boolean).join(' · ');
        return `<tr>
          <td>${nameLink(e.name || '—', e.mgmtUrl, 'Open pfSense / edge management')}</td>
          <td>${esc(e.role || 'firewall')}</td>
          <td class="${statusClass(st === 'online' || st === 'up' ? 'healthy' : 'unhealthy')}">${esc(st || '—')}</td>
          <td>${esc(detail)}</td>
        </tr>`;
      })
      .join('');

    const apRows = aps
      .map((ap) => {
        const st = ap.status || (ap.up === 1 || ap.up === true ? 'online' : ap.up === 0 ? 'offline' : '');
        const detail = [
          ap.clients != null ? `${ap.clients} clients` : null,
          ap.cpu != null ? `CPU ${ap.cpu}%` : null,
          ap.memory != null ? `Mem ${ap.memory}%` : null,
          ap.uptime || null,
        ].filter(Boolean).join(' · ');
        return `<tr>
          <td>${nameLink(ap.name || ap.device || '—', ap.mgmtUrl || d.mgmt?.unifi, 'Open UniFi Network console')}</td>
          <td>${esc(ap.model || 'AP')}</td>
          <td class="${statusClass(st === 'online' || st === 'up' ? 'healthy' : 'unhealthy')}">${esc(st || '—')}</td>
          <td>${esc(detail || ap.mac || '')}</td>
        </tr>`;
      })
      .join('');

    const swRows = switches
      .map((sw) => {
        const st = sw.status || '';
        const portsUp = sw.portsUp ?? sw.interfacesUp;
        const portsTotal = sw.portsTotal ?? sw.interfacesTotal;
        const detail = [
          portsUp != null ? `${portsUp}/${portsTotal ?? '—'} ports up` : null,
          sw.model,
          sw.cpu != null && sw.cpu > 0 ? `CPU ${sw.cpu}%` : null,
          sw.source ? `src:${sw.source}` : null,
        ].filter(Boolean).join(' · ');
        const swName = sw.name || sw.device_name || '';
        return `<tr>
          <td>${nameLink(swName || '—', sw.mgmtUrl, 'Open switch management')}</td>
          <td>Switch</td>
          <td class="${statusClass(st === 'online' || st === 'up' ? 'healthy' : st)}">${esc(st || '—')}</td>
          <td>${esc(detail)}</td>
          <td>${consoleAction(swName, terminal)}</td>
        </tr>`;
      })
      .join('');

    const flatRows = flat
      .map((dev) => {
        const name = dev.name || dev.device || dev.id || '—';
        const role = dev.role || dev.type || '';
        const status = dev.status || dev.state || (dev.up === 1 || dev.up === true ? 'up' : dev.up === 0 ? 'down' : '');
        const ip = dev.ip || dev.instance || '';
        return `<tr>
          <td>${esc(name)}</td>
          <td>${esc(role)}</td>
          <td class="${statusClass(status === 'up' || status === 'online' ? 'healthy' : status === 'down' ? 'unhealthy' : status)}">${esc(status || '—')}</td>
          <td>${esc(ip)}</td>
        </tr>`;
      })
      .join('');

    const hasStructured = probes.length || edge.length || aps.length || switches.length;
    const body = hasStructured
      ? `
      ${edge.length ? `
        <p class="home-section-title">Firewall / edge</p>
        <div class="home-table-wrap">
          <table class="home-table">
            <thead><tr><th>Name</th><th>Role</th><th>Status</th><th>Detail</th></tr></thead>
            <tbody>${edgeRows}</tbody>
          </table>
        </div>` : `
        <p class="home-section-title">Firewall / edge</p>
        <p class="home-muted">No edge probe yet. Docker stack probes pfSense HTTPS when <code>blackbox_edge</code> is configured.</p>`}
      ${probes.length ? `
        <p class="home-section-title">WAN / blackbox probes</p>
        <div class="home-table-wrap">
          <table class="home-table">
            <thead><tr><th>Target</th><th>Type</th><th>Status</th><th>Latency</th></tr></thead>
            <tbody>${probeRows}</tbody>
          </table>
        </div>` : ''}
      ${aps.length ? `
        <p class="home-section-title">Access points (UniFi)</p>
        <div class="home-table-wrap">
          <table class="home-table">
            <thead><tr><th>Name</th><th>Model</th><th>Status</th><th>Detail</th></tr></thead>
            <tbody>${apRows}</tbody>
          </table>
        </div>` : `
        <p class="home-section-title">Access points</p>
        <p class="home-muted">No APs — enable UniFi exporter (<code>--profile unifi</code>).</p>`}
      ${switches.length ? `
        <p class="home-section-title">Switches</p>
        ${terminal?.enabled && !terminal?.dedicatedAccount
    ? '<p class="home-muted">Console sessions use privilege-15 credentials — the app-layer filter is the only guard. Set <code>HUD_SSH_USERNAME</code> to a privilege-1 account.</p>'
    : ''}
        <div class="home-table-wrap">
          <table class="home-table">
            <thead><tr><th>Name</th><th>Role</th><th>Status</th><th>Detail</th><th></th></tr></thead>
            <tbody>${swRows}</tbody>
          </table>
        </div>` : `
        <p class="home-section-title">Switches</p>
        <p class="home-muted">
          No switch series yet. UniFi switches appear when adopted in the controller.
          Campus Cisco IF-MIB: <code>docker compose --profile device-snmp up -d</code>
          (or K3s <code>components/device-snmp</code>). Greenfield metrics use
          <code>ifOperStatus{job="device_snmp"}</code>.
        </p>`}
      `
      : `
      <div class="home-table-wrap">
        <table class="home-table">
          <thead><tr><th>Name</th><th>Role</th><th>Status</th><th>IP / instance</th></tr></thead>
          <tbody>${flatRows || '<tr><td colspan="4">No devices in response</td></tr>'}</tbody>
        </table>
      </div>`;

    return `
      <div class="home-panel-header">
        <div><p class="eyebrow">Edge · APs · probes</p><h2>Devices</h2></div>
        <span class="home-badge">Live</span>
      </div>
      ${body}
    `;
  }

  investigateSecurity(eventId) {
    const cases = this.cache.securityCases;
    if (cases?.unavailable || (cases?.site && cases.site !== this.site) || !this.options?.investigate) return;
    const event = cases?.events?.find(e => e.id === eventId);
    if (!event) return;
    const retrievedAt = this.readTimes.securityCases;
    this.options.investigate('Convergence security case deep dive',
      caseHandoff([event], this.site, retrievedAt, 'Selected Security review case'),
      `/api/home/events/${encodeURIComponent(event.id)}?site=${encodeURIComponent(this.site)}`, retrievedAt);
  }

  investigateDiary(ids, scope) {
    const events = (this.cache.events?.events || []).filter(e => ids.includes(e.id));
    if (!events.length || !this.options.investigate) return;
    this.options.investigate('Convergence formal case deep dive', caseHandoff(events, this.site, this.readTimes.events, scope),
      `/api/home/events?site=${encodeURIComponent(this.site)}&range=${this.diaryRange}&limit=100`, this.readTimes.events);
  }

  htmlDiary() {
    return diaryHTML(this.cache.events, {mode:this.diaryMode, range:this.diaryRange, search:this.diarySearch,
      selected:this.diarySelected, site:this.site, readOnly:this.readOnly, reviewerOrigin:this.cache.status?.reviewerOrigin, canInvestigate:Boolean(this.options?.investigate),
      jevPending:this.jevPending, jevErrors:this.jevErrors, flash:this.diaryFlash});
  }

  htmlTriage() {
    if (this.cache.escalated?.unavailable) return this.htmlBanner('down', this.cache.escalated.reason || 'Operator queue is unavailable.');
    const events = this.getEscalatedEvents();
    const selected = this.getSelectedEvent();
    const flash = this.triageFlash
      ? `<div class="home-banner home-banner-${esc(this.triageFlash.kind === 'error' ? 'error' : 'ok')}" style="margin-bottom:12px">
           <span class="home-badge">${this.triageFlash.kind === 'error' ? 'Error' : 'Done'}</span>
           <p class="home-muted" style="margin:8px 0 0">${esc(this.triageFlash.message)}</p>
         </div>`
      : '';
    const busy = this.triageBusy ? ' is-busy' : '';

    const list = events
      .map((e) => {
        const active = selected && String(e.id) === String(selected.id) ? ' active' : '';
        const when = (e.escalated_at || e.timestamp || '').toString().slice(0, 19);
        return `
          <button type="button" class="home-triage-item${active}" data-triage-select="${esc(e.id)}">
            <span class="home-triage-item-when">${esc(when)}</span>
            <span class="home-triage-item-alert">${esc(e.alert_name || 'case')}</span>
            <span class="home-triage-item-msg">${esc((e.message || '').slice(0, 90))}</span>
            ${e.rag_document_id ? '<span class="home-rag-chip" title="RAG document">RAG</span>' : ''}
          </button>`;
      })
      .join('');

    let detail = `
      <div class="home-triage-empty">
        <p class="home-muted">No escalated cases. Cases appear when the investigator sets
        <code>status=escalated</code> (needs human action). Use Diary for the full timeline.</p>
      </div>`;

    if (selected) {
      const id = selected.id;
      const ragBlock = selected.rag_document_id
        ? `<div class="home-triage-field">
             <span class="home-triage-label">RAG document</span>
             <code class="home-rag-id">${esc(selected.rag_document_id)}</code>
             ${selected.rag_snapshotted_at ? `<span class="home-muted"> · ${esc(String(selected.rag_snapshotted_at).slice(0, 19))}</span>` : ''}
           </div>`
        : `<div class="home-triage-field">
             <span class="home-triage-label">RAG document</span>
             <span class="home-muted">Not snapshotted yet</span>
           </div>`;

      detail = `
        <div class="home-triage-detail${busy}">
          <div class="home-triage-detail-head">
            <div>
              <p class="eyebrow">${esc(selected.alert_name || 'Investigation')}</p>
              <h3 class="home-triage-title">${esc(selected.message || 'Escalated case')}</h3>
            </div>
            <span class="home-badge crit">escalated</span>
          </div>
          <div class="home-triage-meta">
            <span>id <code>${esc(String(id).slice(0, 8))}…</code></span>
            <span class="${statusClass(selected.severity)}">sev ${esc(selected.severity || '—')}</span>
            <span>${esc((selected.escalated_at || selected.timestamp || '').toString().slice(0, 19))}</span>
            <span>Observed VLAN: ${observedVlanHTML(caseCaptureContext(selected))}</span>
          </div>
          <div class="home-triage-field">
            <span class="home-triage-label">Root cause</span>
            <p>${esc(selected.root_cause || '—')}</p>
          </div>
          <div class="home-triage-field">
            <span class="home-triage-label">Investigation notes</span>
            <pre class="home-triage-notes-pre">${esc(selected.investigation_notes || '—')}</pre>
            ${selected.source_evidence ? `<details><summary>Original sensor / alert evidence</summary><pre class="home-triage-notes-pre">${esc(JSON.stringify(selected.source_evidence, null, 2))}</pre></details>` : ''}
          </div>
          ${captureContextHTML(caseCaptureContext(selected))}
          ${assessmentHTML(selected)}
          ${ipContextHTML(caseIPContext(selected))}
          ${jevReviewHTML(selected, this.jevPending?.has(`${this.site}:${selected.id}`), this.jevErrors?.get(`${this.site}:${selected.id}`), !this.readOnly)}
          ${ragBlock}
          ${selected.expert_feedback ? `
            <div class="home-triage-field">
              <span class="home-triage-label">Prior operator feedback</span>
              <p>${esc(selected.expert_feedback)}
                ${selected.feedback_quality ? ` <span class="home-muted">(${esc(selected.feedback_quality)})</span>` : ''}
              </p>
            </div>` : ''}
          ${this.readOnly ? reviewerLink(this.cache.status?.reviewerOrigin, this.site, selected.id) : `<label class="home-triage-field" for="triage-notes-${esc(id)}">
            <span class="home-triage-label">Your notes (optional)</span>
            <textarea id="triage-notes-${esc(id)}" class="home-triage-textarea" data-event-id="${esc(id)}" rows="3"
              placeholder="Context for the investigator or diary…">${esc(this.notesDrafts.get(String(id)) || '')}</textarea>
          </label>
          <div class="home-triage-actions" role="group" aria-label="Feedback">
            <button type="button" class="home-triage-btn ok" data-triage-action="correct" data-event-id="${esc(id)}"
              ${this.triageBusy ? 'disabled' : ''}>Correct</button>
            <button type="button" class="home-triage-btn warn" data-triage-action="partial" data-event-id="${esc(id)}"
              ${this.triageBusy ? 'disabled' : ''}>Partial</button>
            <button type="button" class="home-triage-btn crit" data-triage-action="incorrect" data-event-id="${esc(id)}"
              ${this.triageBusy ? 'disabled' : ''}>Incorrect</button>
            <button type="button" class="home-triage-btn accent" data-triage-action="need_more" data-event-id="${esc(id)}"
              ${this.triageBusy ? 'disabled' : ''}>Need More</button>
            <button type="button" class="home-triage-btn ghost" data-triage-action="resolve" data-event-id="${esc(id)}"
              ${this.triageBusy ? 'disabled' : ''}>Resolve</button>
          </div>
          <p class="home-muted home-triage-hint">
            <strong>Need More</strong> reopens the case as <code>investigating</code> and re-triggers the host
            investigation pipe when alert-receiver is reachable. Other buttons write operator feedback via PATCH.
          </p>`}
        </div>`;
    }

    return `
      <div class="home-panel-header">
        <div><p class="eyebrow">Operator queue</p><h2>Triage</h2></div>
        <span class="home-badge">Live · ${esc(String(events.length))} escalated</span>
      </div>
      ${flash}
      <p class="home-muted" style="margin-bottom:12px">
        Review escalated investigations, leave feedback, or request a deeper re-run without leaving the HUD.
      </p>
      <div class="home-triage-layout">
        <div class="home-triage-list" role="listbox" aria-label="Escalated cases">
          ${list || '<p class="home-muted">Queue empty</p>'}
        </div>
        <div class="home-triage-detail-wrap">
          ${detail}
        </div>
      </div>
    `;
  }
}

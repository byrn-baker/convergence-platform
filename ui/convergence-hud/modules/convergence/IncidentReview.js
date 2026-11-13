import {caseIPContext, ipContextHTML} from './IPContext.js';
import {caseCaptureContext, captureContextHTML} from './CaptureContext.js';
import {jevReviewHTML} from './JevReview.js';

const esc = value => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const tones = {attention: 'crit', review: 'warn', diagnostic: '', reviewed: 'ok'};
export function assessmentHTML(event, {form = false, draft = {}, busy = false, error = ''} = {}) {
  const assessment = event.security_assessment;
  if (!assessment) return '';
  const review = assessment.disposition;
  return `<div class="home-security-assessment">
    <strong class="home-badge ${tones[assessment.bucket] || ''}">${esc(assessment.label)}</strong>
    <p>${esc(assessment.reason)}</p><p><strong>Next check:</strong> ${esc(assessment.next_check)}</p>
    ${review ? `<p><strong>Case review:</strong> ${esc(review.reason)}<br><small>${esc(review.reviewed_at)} · ${esc(review.reviewed_by)}</small></p>` : ''}
    ${form ? `<details><summary>Record a review of this case</summary>
      <form data-security-case="${esc(event.id)}">
        <label>Outcome <select name="kind"><option value="needs_review" ${draft.kind === 'needs_review' ? 'selected' : ''}>Needs review</option>
        <option value="expected_activity" ${draft.kind === 'expected_activity' ? 'selected' : ''}>Expected activity</option>
        <option value="false_positive" ${draft.kind === 'false_positive' ? 'selected' : ''}>False positive</option>
        <option value="confirmed_threat" ${draft.kind === 'confirmed_threat' ? 'selected' : ''}>Confirmed threat</option></select></label>
        <label>Evidence and reason <textarea name="reason" rows="3" minlength="10" maxlength="4000" required placeholder="What did you verify?">${esc(draft.reason || '')}</textarea></label>
        <button type="submit" ${busy ? 'disabled' : ''}>${busy ? 'Saving…' : 'Save case review'}</button>
      </form><p class="home-muted">Applies to this case only. Future detections, investigation state and original evidence remain available.</p>
      </details>` : ''}
    ${error ? `<p role="alert">${esc(error)}</p>` : ''}
  </div>`;
}

export function securityQueueHTML(data, filter = 'all', drafts = new Map(), pending = new Set(), errors = new Map(), site = '', options = {}) {
  if (!data) return '<p class="home-muted">Loading security cases…</p>';
  if (data.unavailable) return '<p role="alert">Security cases unavailable. Detection counts and review coverage are unknown.</p>';
  const events = data.events || [];
  const groups = [['all','All cases'], ['attention','Investigate promptly'], ['review','Review context'], ['diagnostic','Protocol / capture'], ['reviewed','Reviewed exceptions']];
  const selected = groups.some(([id]) => id === filter) ? filter : 'all';
  const rows = events.filter(e => selected === 'all' || e.security_assessment?.bucket === selected)
    .sort((a, b) => ['attention','review','diagnostic','reviewed'].indexOf(a.security_assessment?.bucket) - ['attention','review','diagnostic','reviewed'].indexOf(b.security_assessment?.bucket));
  return `<section class="home-security-queue"><h3>Security review queue</h3>
    <p class="home-muted">Latest ${events.length} IDS cases, capped at 100. Review priority is a suggestion, not a threat probability. Diagnostic alerts still need context; reviewed exceptions apply only to the individual case.</p>
    <div class="home-incident-filters" role="group" aria-label="Security case filters">${groups.map(([id,label]) => `<button type="button" data-security-filter="${id}" aria-pressed="${id === selected}">${label} · ${id === 'all' ? events.length : events.filter(e => e.security_assessment?.bucket === id).length}</button>`).join('')}</div>
    ${rows.map(e => { const key = `${site}:${e.id}`; return `<article class="home-incident-card">
      <div class="home-security-case-actions"><button type="button" class="primary" data-security-canvas="${esc(e.id)}" ${options.canInvestigate ? '' : 'disabled title="Open the new HUD to investigate in Canvas"'}>Investigate in Canvas ↗</button>
      <span class="home-muted">Prepare a deep dive with this case and its JEV advice. Review the Canvas draft, then send.</span></div><details>
      <summary><span class="home-badge ${tones[e.security_assessment?.bucket] || ''}">${esc(e.security_assessment?.label || 'Review context')}</span> ${esc(e.security_assessment?.signature || e.message || e.alert_name)}</summary>
      <p class="home-muted">${esc(e.timestamp)} · Investigation: ${esc(e.status)} · Observed VLAN: ${esc(e.capture_context?.vlan_tags?.join(', ') || 'not recorded')}</p>
      ${assessmentHTML(e, {form: true, draft: drafts.get(key), busy: pending.has(key), error: errors.get(key)})}
      ${captureContextHTML(caseCaptureContext(e))}${ipContextHTML(caseIPContext(e))}
      ${jevReviewHTML(e, options.pending?.has(key), options.errors?.get(key))}
    </details></article>`; }).join('') || '<p>No cases in this view. This does not establish full network coverage.</p>'}
  </section>`;
}

export function ispReportsHTML(events, catalog, providerId, reports, pending, errors, site) {
  if (events?.unavailable) return '<p role="alert">WAN case history unavailable.</p>';
  const providers = catalog?.providers || [];
  return `<section class="home-isp-reports"><h2>Internet incidents &amp; provider reports</h2>
    <p>Review a measured incident, prepare an evidence package, then submit it through your provider’s official support channel. A service credit depends on your plan, the cause and the provider’s terms.</p>
    <label>Provider for the affected circuit <select data-isp-provider><option value="">Choose your provider…</option>${providers.map(p => `<option value="${esc(p.id)}" ${providerId === p.id ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select></label>
    <p class="home-muted">Major U.S. residential providers · source links checked ${esc(catalog?.checked_at || 'unavailable')}. Recheck the official page before submitting. Business service contacts and terms may differ.</p>
    ${(events?.events || []).map(event => {const key = `${site}:${event.id}`, report = reports.get(key);return `<article class="home-incident-card">
      <h3>${esc(event.alert_name || 'WAN incident')}</h3><p>${esc(event.timestamp)} · ${esc(event.message)}</p>
      <button type="button" data-isp-generate="${esc(event.id)}" ${!providerId || pending.has(key) ? 'disabled' : ''}>${pending.has(key) ? 'Preparing…' : report ? 'Regenerate from evidence' : 'Prepare provider report & email draft'}</button>
      ${event.isp_report_generated_at && !report ? `<button type="button" data-isp-load="${esc(event.id)}">View saved draft</button>` : ''}
      ${errors.get(key) ? `<p role="alert">${esc(errors.get(key))}</p>` : ''}
      ${report ? reportHTML(report, event.id) : ''}
    </article>`;}).join('') || '<p>No recorded WAN incidents in this view. Reports are offered for stored WAN cases; a quiet diary is not proof of complete monitoring.</p>'}
    <p class="home-muted">Showing up to 50 recent WAN cases. Related alerts can describe one disruption; review them together and use an existing provider ticket to avoid duplicate credit requests.</p>
    </section>`;
}

function reportHTML(report, id) {
  return `<div class="home-isp-draft" data-isp-case="${esc(id)}">
    <h4>Draft for ${esc(report.provider.name)}</h4>
    <p><a href="${esc(report.provider.support_url)}" target="_blank" rel="noopener noreferrer">Official support channel ↗</a> · ${esc(report.provider.channel)}</p>
    ${report.provider.credit_url ? `<p><a href="${esc(report.provider.credit_url)}" target="_blank" rel="noopener noreferrer">Provider credit guidance ↗</a></p>` : ''}
    <p>${esc(report.provider.guidance)}</p><p class="home-muted">${esc(report.recipient_note)}</p>
    <label>Subject <input data-isp-field="subject" value="${esc(report.draft.subject)}" maxlength="300"></label>
    <label>Draft email / support message <textarea data-isp-field="body" rows="18">${esc(report.draft.body)}</textarea></label>
    <div class="home-incident-filters"><button type="button" data-isp-download="email" data-event-id="${esc(id)}">Download email draft</button>
    <button type="button" data-isp-download="evidence" data-event-id="${esc(id)}">Download evidence JSON</button>
    <button type="button" data-isp-download="csv" data-event-id="${esc(id)}">Download probe CSV</button></div>
    <p class="home-muted">Draft only; nothing has been sent. Verify the recipient, add account details through the provider’s secure channel, and review attachments before sharing. Edits above remain in this browser view; the saved case retains the generated draft.</p>
    <details><summary>Evidence and limitations</summary><pre>${esc(JSON.stringify(report.evidence, null, 2))}</pre></details>
  </div>`;
}

export function reportDownload(report, kind) {
  const subjectWords = Array.from(report.draft.subject.replace(/[\r\n]/g, ' '));
  const encodedSubject = [];
  for (let i = 0; i < subjectWords.length; i += 11) encodedSubject.push('=?UTF-8?B?' + btoa(unescape(encodeURIComponent(subjectWords.slice(i, i + 11).join('')))) + '?=');
  if (kind === 'email') return {extension: 'eml', type: 'message/rfc822', text: [
    'X-Unsent: 1', 'To:', 'Subject: ' + encodedSubject.join('\r\n '),
    'MIME-Version: 1.0', 'Content-Type: text/plain; charset=UTF-8', 'Content-Transfer-Encoding: 8bit', '', report.draft.body.replace(/\r?\n/g, '\r\n'),
  ].join('\r\n')};
  if (kind === 'csv') return {extension: 'csv', type: 'text/csv', text: 'probe,timestamp_utc,metric,value\n' + report.evidence.samples.flatMap(t =>
    ['success','duration_ms'].flatMap(metric => t[metric].map(([time,value]) => `${t.target},${new Date(time * 1000).toISOString()},${metric},${value}`))).join('\n')};
  return {extension: 'json', type: 'application/json', text: JSON.stringify({sha256: report.evidence_sha256, evidence: report.evidence}, null, 2)};
}

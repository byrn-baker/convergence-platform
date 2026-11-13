const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));

export function jevReviewHTML(event, busy = false, error = '') {
  if (event?.alert_name !== 'SuricataIntrusion') return '';
  const review = event.jev_review;
  const pending = busy || review?.status === 'queued';
  const button = !review || pending
    ? `<button type="button" class="home-triage-btn ghost" data-jev-review="${esc(event.id)}" ${busy ? 'disabled' : ''}>${busy ? 'JEV review queued…' : pending ? 'Check review' : 'Review with JEV'}</button>` : '';
  let content = '';
  if (review?.status === 'ok') {
    const next = review.answers?.next_check;
    const nextLabel = typeof next?.answer === 'string' ? next.answer : next?.choice;
    const criteria = review.questions?.next_check?.criteria || {};
    const suggestion = criteria[nextLabel] || nextLabel || 'Unavailable';
    const score = review.answers?.evidence_strength?.score;
    const levels = review.questions?.evidence_strength?.criteria;
    content = `<p><strong>Suggested next check:</strong> ${esc(suggestion)}</p>
      <p class="home-muted">Evidence strength: ${typeof score === 'number' && Number.isFinite(score) ? esc(score.toFixed(2)) : 'Unavailable'}${Array.isArray(levels) ? ` on a 0–${levels.length - 1} rubric` : ''}. This is a model judgment, not an incident probability.</p>
      ${Array.isArray(levels) ? `<ol start="0" class="home-muted">${levels.map(level => `<li>${esc(level)}</li>`).join('')}</ol>` : ''}`;
  } else if (review && !pending) {
    content = `<p class="home-muted">${esc(review.status)}${review.message ? ` · ${esc(review.message)}` : ''}. No conclusion about network safety.</p>`;
  }
  return `<details class="home-jev-review" ${busy || error ? 'open' : ''}><summary>JEV · ${review?.status === 'ok' ? 'review available' : pending ? 'review queued' : review ? 'review unavailable' : 'advisory review'}</summary>
    <p class="home-muted">Reviews abstract IDS metadata using your JEV budget. Addresses, VLAN IDs, names, raw signatures and case notes stay local.</p>
    ${button}${error ? `<p role="status" class="home-muted">${esc(error)}</p>` : ''}${content}
    ${review?.model ? `<p class="home-muted">Model ${esc(review.model)} · assessment <code>${esc(review.assessment_id || 'Unavailable')}</code>${typeof review.cost_usd === 'number' ? ` · $${esc(review.cost_usd.toFixed(6))}` : ''}</p>` : ''}
    ${review?.input_summary ? `<details><summary>Abstract evidence sent to JEV</summary><pre class="home-triage-notes-pre">${esc(JSON.stringify(review.input_summary, null, 2))}</pre></details>` : ''}
  </details>`;
}

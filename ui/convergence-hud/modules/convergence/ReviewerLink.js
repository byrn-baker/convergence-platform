// Use only the configured reviewer origin. No evidence or credentials cross origins.
export function reviewerLink(origin, site, id) {
  let url;
  try { url = new URL(origin); } catch { return ''; }
  if (url.protocol !== 'https:' || url.origin !== origin) return '';
  if (site && /^[a-zA-Z0-9_-]{1,128}$/.test(site) && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(id)) {
    url.hash = new URLSearchParams({site, case: id}).toString();
  }
  const href = url.href.replaceAll('&', '&amp;');
  return `<a href="${href}" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">${url.hash ? 'Review this case' : 'Open reviewer'} ↗</a>`;
}

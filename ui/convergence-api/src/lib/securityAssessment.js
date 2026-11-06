'use strict';

const PREFIX = 'Passive IDS observation; not proof of compromise. ';
const DISPOSITIONS = ['needs_review', 'expected_activity', 'false_positive', 'confirmed_threat'];

function securityAssessment(event) {
  if (event.alert_name !== 'SuricataIntrusion') return null;
  let raw;
  const description = event.source_evidence?.annotations?.description;
  try { if (typeof description === 'string' && description.startsWith(PREFIX)) raw = JSON.parse(description.slice(PREFIX.length)); } catch { /* incomplete evidence remains unknown */ }
  const alert = raw?.alert;
  const signature = typeof alert?.signature === 'string' ? alert.signature : '';
  const priority = [1, 2, 3].includes(alert?.severity) ? alert.severity : null;
  let bucket = 'review', label = 'Review context', family = 'Unclassified signature';
  let reason = 'An IDS signature matched; corroborating traffic and endpoint evidence are needed.';
  let next = 'Correlate this observation with the endpoint, related flows and expected activity.';
  if (!signature) {
    family = 'Incomplete evidence'; reason = 'The complete original signature is unavailable.';
    next = 'Retrieve the original sensor event before deciding whether it is a threat.';
  } else if (/^(ET (MALWARE|TROJAN|CNC|EXPLOIT|WEB_SERVER|WEB_CLIENT)\b)/i.test(signature) || priority === 1) {
    bucket = 'attention'; label = 'Investigate promptly'; family = 'High-priority indicator';
    reason = 'The rule indicates potentially serious activity or has sensor priority 1. Compromise is not established.';
    next = 'Check the affected endpoint and corroborate process, flow, DNS and TLS evidence.';
  } else if (/^SURICATA (STREAM|TCP|UDP|ICMP|TLS|APPLAYER|DECODER)\b/i.test(signature)) {
    bucket = 'diagnostic'; label = 'Check protocol / capture'; family = 'Protocol or decoder anomaly';
    reason = 'Protocol behavior or capture artifacts can cause this alert. This is a diagnostic lead, not a benign verdict.';
    next = 'Check mirror duplication, drops, offload artifacts and stream behavior; investigate unexplained or correlated activity.';
  } else if (/^ET (SCAN|DROP)\b/i.test(signature)) {
    family = 'Scan or reputation indicator'; next = 'Check whether the connection was expected and whether it reached a protected service.';
  } else if (/^ET POLICY\b/i.test(signature)) {
    family = 'Policy observation'; next = 'Compare the application and destination with the intended policy for this device and VLAN.';
  }
  const disposition = event.security_disposition;
  if (disposition?.version === 1 && DISPOSITIONS.includes(disposition.kind)) {
    if (disposition.kind === 'confirmed_threat') { bucket = 'attention'; label = 'Reviewer-confirmed threat'; }
    if (['expected_activity', 'false_positive'].includes(disposition.kind)) {
      bucket = 'reviewed'; label = disposition.kind === 'expected_activity' ? 'Reviewed · expected activity' : 'Reviewed · false positive';
    }
    if (disposition.kind === 'needs_review') { bucket = 'review'; label = 'Reopened for review'; }
  }
  return {version: 1, bucket, label, family, reason, next_check: next, rule_priority: priority,
    signature: signature || null, disposition: disposition || null,
    basis: 'Rule metadata and explicit case review; no automatic suppression or traffic changes.'};
}

module.exports = {securityAssessment, DISPOSITIONS};

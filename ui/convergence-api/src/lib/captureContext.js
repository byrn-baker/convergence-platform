'use strict';

const LEGACY_PREFIX = 'Passive IDS observation; not proof of compromise. ';

function object(value) {
  if (typeof value === 'string') {
    try { value = JSON.parse(value); } catch { return null; }
  }
  return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
}

/** Read capture metadata without changing the original case evidence. */
function captureContext(evidence, alertName) {
  const labels = evidence?.labels;
  if (labels?.source !== 'suricata'
      && !['SuricataIntrusion', 'SuricataCanary'].includes(labels?.alertname || alertName)) return null;

  const context = {
    sensor_id: typeof labels?.sensor_id === 'string' ? labels.sensor_id : null,
    status: 'unavailable',
    vlan_tags: [],
  };
  const annotations = evidence?.annotations;
  let tags;
  if (annotations?.capture_context != null) {
    const capture = object(annotations.capture_context);
    if (capture?.version !== 1) return context;
    tags = capture.vlan_tags;
  } else {
    // Older cases kept EVE fields only in the description. Parse the complete
    // known envelope; never extract apparent tags from arbitrary/truncated text.
    const description = annotations?.description;
    if (typeof description !== 'string' || !description.startsWith(LEGACY_PREFIX)) return context;
    const event = object(description.slice(LEGACY_PREFIX.length));
    if (!event) return context;
    tags = Object.hasOwn(event, 'vlan') ? event.vlan : [];
  }
  if (!Array.isArray(tags) || tags.length > 3
      || tags.some(tag => !Number.isInteger(tag) || tag < 0 || tag > 4094)) return context;

  // Keep tag order and duplicates. EVE tags are capture evidence, not a path
  // through VLANs, and missing tags cannot be inferred from endpoint addresses.
  return {...context, status: tags.length ? 'recorded' : 'not_recorded', vlan_tags: [...tags]};
}

module.exports = {captureContext};

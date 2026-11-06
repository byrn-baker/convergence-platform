'use strict';
const {isIP} = require('node:net');
const PREFIX = 'Passive IDS observation; not proof of compromise. ';

// Project only bounded traffic metadata, never packet payloads, into the diary.
function trafficContext(evidence) {
  const description = evidence?.annotations?.description;
  if (typeof description !== 'string' || !description.startsWith(PREFIX)) return null;
  let raw;
  try { raw = JSON.parse(description.slice(PREFIX.length)); } catch { return null; }
  const port = n => Number.isInteger(n) && n >= 0 && n <= 65535 ? n : null;
  return {
    source_ip: isIP(String(raw?.src_ip || '')) ? raw.src_ip : null,
    destination_ip: isIP(String(raw?.dest_ip || '')) ? raw.dest_ip : null,
    source_port: port(raw?.src_port), destination_port: port(raw?.dest_port),
    protocol: typeof raw?.proto === 'string' ? raw.proto.slice(0, 24) : null,
    signature_id: Number.isSafeInteger(raw?.alert?.signature_id) ? raw.alert.signature_id : null,
    signature: typeof raw?.alert?.signature === 'string' ? raw.alert.signature.slice(0, 500) : null,
    action: typeof raw?.alert?.action === 'string' ? raw.alert.action.slice(0, 32) : null,
  };
}
module.exports = {trafficContext};

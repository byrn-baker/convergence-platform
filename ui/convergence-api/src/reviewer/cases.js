'use strict';

const {securityAssessment, securityDisposition, validDisposition} = require('../lib/securityAssessment');

const UUID = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const HISTORY_LIMIT = 20;

function failure(status, message) {
  return Object.assign(new Error(message), {status});
}

function uuid(value) {
  if (typeof value !== 'string' || !UUID.test(value)) throw failure(400, 'A UUID is required');
  return value.toLowerCase();
}

function requireScope(actor, site, write = false) {
  if (!actor?.id || !actor.installation) throw failure(401, 'A reviewer session is required');
  if (!Array.isArray(actor.sites) || !actor.sites.includes(site)) throw failure(403, 'Site access denied');
  if (write && (typeof actor.name !== 'string' || !actor.name || Array.from(actor.name).length > 256)) {
    throw failure(401, 'A named reviewer session is required');
  }
  if (write && !['operator', 'admin'].includes(actor.role)) throw failure(403, 'Operator role required');
}

// Return only the v1 fields, including for records created by older writers.
function savedDisposition(value) {
  if (value?.version !== 1 || !validDisposition(value.kind, value.reason)) return null;
  if (typeof value.reviewed_by !== 'string' || typeof value.reviewed_at !== 'string') return null;
  const disposition = securityDisposition(value.kind, value.reason, value.reviewed_by.slice(0, 256), value.reviewed_at.slice(0, 64));
  if (typeof value.reviewed_by_name === 'string') {
    disposition.reviewed_by_name = Array.from(value.reviewed_by_name).slice(0, 256).join('');
  }
  return disposition;
}

async function readCase(client, actor, site, caseId) {
  requireScope(actor, site);
  const id = uuid(caseId);
  // Share-lock the case through the history query. A concurrent disposition is
  // seen either in both the current case and its history or in neither.
  const result = await client.query(`
    SELECT id, site_id, timestamp, left(message, 2048) AS message,
           left(severity, 64) AS severity, left(category, 64) AS category,
           left(status, 64) AS status, left(alert_name, 256) AS alert_name,
           left(investigation_notes, 4096) AS investigation_notes, left(root_cause, 2048) AS root_cause,
           left(source_evidence #>> '{annotations,description}', 16384) AS description,
           CASE WHEN jsonb_typeof(security_disposition) = 'object'
                  AND octet_length(security_disposition::text) <= 32768 THEN security_disposition END AS disposition
      FROM events WHERE id = $1 AND site_id = $2 FOR SHARE`, [id, site]);
  if (!result.rowCount) throw failure(404, 'Case not found');

  const {description, disposition, ...record} = result.rows[0];
  const history = await client.query(`
    SELECT operation_id, kind, left(reason, 256) AS reason_preview, length(reason) > 256 AS reason_truncated,
           actor_id AS reviewed_by, actor_name AS reviewed_by_name, reviewed_at FROM reviewer_case_dispositions
     WHERE case_id = $1 AND site_id = $2 ORDER BY reviewed_at DESC, operation_id DESC LIMIT $3`,
  [id, site, HISTORY_LIMIT + 1]);

  record.security_disposition = savedDisposition(disposition);
  const assessment = securityAssessment({...record, source_evidence: {annotations: {description}}});
  if (assessment?.signature) assessment.signature = assessment.signature.slice(0, 512);
  return {...record, security_assessment: assessment, review_history: history.rows.slice(0, HISTORY_LIMIT),
    review_history_truncated: history.rowCount > HISTORY_LIMIT};
}

const RECEIPT = 'operation_id, case_id, site_id, actor_id AS reviewed_by, actor_name AS reviewed_by_name, reviewed_at, kind, reason';

async function readDispositionReceipt(client, actor, site, caseId, operationId) {
  requireScope(actor, site);
  const result = await client.query(`
    SELECT ${RECEIPT} FROM reviewer_case_dispositions
     WHERE installation_id = $1 AND actor_id = $2 AND operation_id = $3 AND site_id = $4 AND case_id = $5
       AND EXISTS (SELECT 1 FROM events WHERE id = $5 AND site_id = $4)`,
  [actor.installation, actor.id, uuid(operationId), site, uuid(caseId)]);
  if (!result.rowCount) throw failure(404, 'Receipt not found');
  return result.rows[0];
}

async function recordDisposition(client, actor, site, caseId, input) {
  requireScope(actor, site, true);
  const id = uuid(caseId);
  if (!input || Object.keys(input).length !== 3 || !['kind', 'reason', 'operationId'].every(k => Object.hasOwn(input, k))
      || !validDisposition(input.kind, input.reason) || /[\u0000\uD800-\uDFFF]/u.test(input.reason)) {
    throw failure(400, 'Invalid security disposition');
  }
  const operationId = uuid(input.operationId);
  const reason = input.reason.trim();
  const locked = await client.query('SELECT alert_name FROM events WHERE id = $1 AND site_id = $2 FOR UPDATE', [id, site]);
  if (!locked.rowCount) throw failure(404, 'Case not found');

  const identity = [actor.installation, actor.id, operationId];
  // Store the normalized input and the original database timestamp. The unique
  // constraint serializes this operation ID even if different cases are locked.
  const inserted = await client.query(`
    INSERT INTO reviewer_case_dispositions (installation_id, actor_id, operation_id, site_id, case_id, kind, reason, actor_name)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    ON CONFLICT (installation_id, actor_id, operation_id) DO NOTHING RETURNING ${RECEIPT}`,
  [...identity, site, id, input.kind, reason, actor.name]);
  if (!inserted.rowCount) {
    const result = await client.query(`SELECT ${RECEIPT} FROM reviewer_case_dispositions
      WHERE installation_id = $1 AND actor_id = $2 AND operation_id = $3`, identity);
    const prior = result.rows[0];
    if (prior?.case_id !== id || prior.site_id !== site || prior.kind !== input.kind || prior.reason !== reason) {
      throw failure(409, 'Operation ID already used with different inputs');
    }
    return prior;
  }

  // Exceptions leave the transaction for the grant authority to roll back,
  // including this operation's provisional receipt.
  if (!securityAssessment(locked.rows[0])) throw failure(400, 'This review applies to IDS cases');
  const receipt = inserted.rows[0];
  const disposition = securityDisposition(receipt.kind, receipt.reason, actor.id, receipt.reviewed_at.toISOString());
  disposition.reviewed_by_name = receipt.reviewed_by_name;
  await client.query('UPDATE events SET security_disposition = $1 WHERE id = $2 AND site_id = $3',
    [disposition, id, site]);
  return receipt;
}

module.exports = {readCase, recordDisposition, readDispositionReceipt};

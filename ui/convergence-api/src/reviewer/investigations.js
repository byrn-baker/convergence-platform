'use strict';

const {requireScope, uuid} = require('./cases');
const failure = (status, message) => Object.assign(new Error(message), {status});
const RECEIPT = `i.operation_id, i.case_id, i.site_id, i.actor_id AS requested_by,
  i.actor_name AS requested_by_name, i.requested_at, i.feedback, i.delivery_id, o.state, o.job_id`;

function normalizeInput(input) {
  if (!input || typeof input !== 'object' || Object.keys(input).length !== 2 ||
      !['operationId','feedback'].every(key => Object.hasOwn(input,key)) || typeof input.feedback !== 'string' ||
      /[\u0000\uD800-\uDFFF]/u.test(input.feedback)) throw failure(400,'Invalid investigation request');
  const feedback = input.feedback.trim();
  if (Array.from(feedback).length < 10 || Array.from(feedback).length > 4000) throw failure(400,'Invalid investigation request');
  return {operationId: uuid(input.operationId), feedback};
}

async function readInvestigationReceipt(client, actor, site, caseId, operationId) {
  requireScope(actor, site);
  const {rows:[receipt]} = await client.query(`SELECT ${RECEIPT}
    FROM reviewer_investigations i JOIN reviewer_investigation_outbox o USING (delivery_id)
    WHERE i.installation_id=$1 AND i.actor_id=$2 AND i.operation_id=$3 AND i.site_id=$4 AND i.case_id=$5
      AND EXISTS (SELECT 1 FROM events WHERE id=$5 AND site_id=$4)`,
  [actor.installation,actor.id,uuid(operationId),site,uuid(caseId)]);
  if (!receipt) throw failure(404,'Receipt not found');
  return receipt;
}

async function requestInvestigation(client, actor, site, caseId, input) {
  requireScope(actor, site, true);
  const id = uuid(caseId);
  const {operationId,feedback} = normalizeInput(input);
  // The grant authority owns this transaction. A failed note/outbox insertion
  // rolls back the acceptance too; concurrent case requests cannot lose notes.
  if (!(await client.query('SELECT id FROM events WHERE id=$1 AND site_id=$2 FOR UPDATE',[id,site])).rowCount) {
    throw failure(404,'Case not found');
  }
  const identity = [actor.installation,actor.id,operationId];
  const inserted = await client.query(`INSERT INTO reviewer_investigations
    (installation_id,actor_id,operation_id,site_id,case_id,actor_name,feedback)
    VALUES ($1,$2,$3,$4,$5,$6,$7)
    ON CONFLICT (installation_id,actor_id,operation_id) DO NOTHING RETURNING delivery_id`,
  [...identity,site,id,actor.name,feedback]);
  if (!inserted.rowCount) {
    const {rows:[prior]} = await client.query(`SELECT site_id,case_id,feedback FROM reviewer_investigations
      WHERE installation_id=$1 AND actor_id=$2 AND operation_id=$3`,identity);
    if (prior?.site_id!==site || prior.case_id!==id || prior.feedback!==feedback) throw failure(409,'Conflicting operation');
  } else {
    await client.query('INSERT INTO reviewer_investigation_outbox(delivery_id) VALUES ($1)',[inserted.rows[0].delivery_id]);
    const note = `\n\n[reviewer:reinvestigate ${operationId}; actor ${actor.id}] ${JSON.stringify(actor.name)}\n${feedback}`;
    await client.query(`UPDATE events SET investigation_notes=COALESCE(investigation_notes,'') || $1
      WHERE id=$2 AND site_id=$3`,[note,id,site]);
  }
  return readInvestigationReceipt(client,actor,site,id,operationId);
}

function envelope(row) {
  return {version:1,delivery_id:row.delivery_id,installation_id:row.installation_id,
    actor_id:row.actor_id,actor_name:row.actor_name,operation_id:row.operation_id,
    site_id:row.site_id,case_id:row.case_id,command:row.command,feedback:row.feedback};
}

module.exports = {requestInvestigation,readInvestigationReceipt,normalizeInput,envelope};

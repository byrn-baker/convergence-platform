'use strict';
const {requireScope,uuid}=require('./cases');
const failure=(status,message)=>Object.assign(new Error(message),{status});
const receiptFields=`i.operation_id,i.case_id,i.site_id,i.actor_id AS requested_by,
 i.actor_name AS requested_by_name,i.requested_at,i.delivery_id,o.state,o.job_id,o.assessment_id,o.participation,o.result`;
function normalizeJevInput(input) {
  if(!input || typeof input!=='object' || Array.isArray(input) || Object.keys(input).length!==1 || !Object.hasOwn(input,'operationId')) throw failure(400,'Invalid JEV request');
  return {operationId:uuid(input.operationId)};
}
async function readJevReceipt(client,actor,site,caseId,operationId) {
  requireScope(actor,site);
  const {rows:[receipt]}=await client.query(`SELECT ${receiptFields} FROM reviewer_jev_requests i
    JOIN reviewer_jev_outbox o USING(delivery_id)
    WHERE i.installation_id=$1 AND i.actor_id=$2 AND i.operation_id=$3 AND i.site_id=$4 AND i.case_id=$5
      AND EXISTS(SELECT 1 FROM events WHERE id=$5 AND site_id=$4)`,
    [actor.installation,actor.id,uuid(operationId),site,uuid(caseId)]);
  if(!receipt)throw failure(404,'Receipt not found');
  return receipt;
}
async function requestJevReview(client,actor,site,caseId,input) {
  requireScope(actor,site,true);const id=uuid(caseId),{operationId}=normalizeJevInput(input);
  const {rows:[item]}=await client.query('SELECT id,alert_name FROM events WHERE id=$1 AND site_id=$2 FOR SHARE',[id,site]);
  if(!item)throw failure(404,'Case not found');
  if(item.alert_name!=='SuricataIntrusion')throw failure(400,'JEV assessment requires an IDS case');
  const identity=[actor.installation,actor.id,operationId];
  let inserted;
  try {
    inserted=await client.query(`INSERT INTO reviewer_jev_requests
      (installation_id,actor_id,operation_id,site_id,case_id,actor_name) VALUES($1,$2,$3,$4,$5,$6)
      ON CONFLICT(installation_id,actor_id,operation_id) DO NOTHING RETURNING delivery_id`,[...identity,site,id,actor.name]);
  } catch(error) {
    if(error.constraint==='reviewer_command_identity')throw failure(409,'Conflicting operation');
    throw error;
  }
  if(inserted.rowCount) await client.query('INSERT INTO reviewer_jev_outbox(delivery_id) VALUES($1)',[inserted.rows[0].delivery_id]);
  else {
    const {rows:[prior]}=await client.query('SELECT site_id,case_id FROM reviewer_jev_requests WHERE installation_id=$1 AND actor_id=$2 AND operation_id=$3',identity);
    if(prior?.site_id!==site || prior.case_id!==id)throw failure(409,'Conflicting operation');
  }
  return readJevReceipt(client,actor,site,id,operationId);
}
function jevEnvelope(row) {
  return {version:1,delivery_id:row.delivery_id,installation_id:row.installation_id,actor_id:row.actor_id,
    actor_name:row.actor_name,operation_id:row.operation_id,site_id:row.site_id,case_id:row.case_id,command:'jev_review'};
}
const UUID=/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
function validJevReceipt(receipt,lease) {
  if(!receipt || Object.keys(receipt).length!==6 || receipt.delivery_id!==lease.delivery_id ||
      !UUID.test(receipt.assessment_id) || !(receipt.job_id===null || UUID.test(receipt.job_id)) ||
      !['started','joined','reused'].includes(receipt.participation) ||
      !['enqueued','dispatched','completed','failed','unknown'].includes(receipt.state) ||
      (lease.assessment_id && lease.assessment_id!==receipt.assessment_id) ||
      (lease.participation && lease.participation!==receipt.participation) ||
      (lease.job_id && lease.job_id!==receipt.job_id))return false;
  const result=receipt.result;
  if(result!==null && (!result || typeof result!=='object' || Array.isArray(result) ||
      result.version!==1 || result.advisory_only!==true || typeof result.status!=='string' || result.status.length>64 ||
      Buffer.byteLength(JSON.stringify(result))>32768))return false;
  return (receipt.state==='completed')===(result!==null);
}
module.exports={normalizeJevInput,requestJevReview,readJevReceipt,jevEnvelope,validJevReceipt};

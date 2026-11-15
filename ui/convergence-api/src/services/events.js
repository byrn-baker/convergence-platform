'use strict';
const {query: databaseQuery, DbUnavailableError} = require('../db');
const {captureContext} = require('../lib/captureContext');
const {securityAssessment} = require('../lib/securityAssessment');
const {trafficContext} = require('../lib/trafficContext');
const {WAN_ALERTS} = require('../lib/ispReport');
const {caseHistoryFilters,DESTINATION_SQL,COUNTRY_SQL} = require('../lib/securityHistory');
const {ReadError,fail} = require('./read-error');
const isDbDown = err => err instanceof DbUnavailableError || err?.code === 'DB_UNAVAILABLE';
const emptyWithReason = site => ({site,events:[],unavailable:true,reason:'Event database unavailable'});
async function readEvents(site, filters = {}, routeParams = {}, query = databaseQuery) {
  const limit = Math.max(1, Math.min(parseInt(filters.limit) || 20, 100));
  const windows = {'1h':3600,'6h':21600,'24h':86400,'7d':604800};
  if (filters.range && !Object.hasOwn(windows, filters.range)) return fail(400, {error:'Invalid diary time window'});
  const status = filters.status; // optional: filter by status
  const fingerprint = filters.fingerprint; // optional: alertmanager fingerprint
  let historical;
  try { historical=caseHistoryFilters(filters); }
  catch (err) { return fail(400, {error:err.message}); }

  try {
    let sql = `SELECT id, timestamp, message, severity, category, source, status,
               alert_name, alert_fingerprint, root_cause, investigation_notes,
               expert_feedback, feedback_quality, rag_document_id, incident_key, alert_cleared_at,
               source_evidence->'annotations'->'ip_context' AS ip_context, source_evidence, jev_review,
               security_disposition, isp_report->>'generated_at' AS isp_report_generated_at
               FROM events WHERE site_id = $1`;
    const params = [site];
    if (filters.range) {
      params.push(new Date(Date.now() - windows[filters.range] * 1000).toISOString());
      sql += ` AND timestamp >= $${params.length}::timestamptz`;
    }

    if (filters.type === 'security') sql += " AND alert_name = 'SuricataIntrusion'";
    if (historical.start) {
      params.push(historical.start,historical.end);
      sql += ` AND timestamp >= $${params.length-1}::timestamptz AND timestamp < $${params.length}::timestamptz`;
    }
    if (historical.country) {
      params.push(historical.country);
      sql += ` AND (SELECT ${COUNTRY_SQL} FROM (SELECT ${DESTINATION_SQL} AS destination) context) = $${params.length}`;
    }
    if (filters.type === 'wan') {
      params.push(WAN_ALERTS);
      sql += ` AND (category = 'wan' OR alert_name = ANY($${params.length}::text[]))`;
    }

    if (status) {
      sql += ` AND status = $${params.length + 1}`;
      params.push(status);
    }
    if (fingerprint) {
      sql += ` AND alert_fingerprint = $${params.length + 1}`;
      params.push(fingerprint);
    }
    if (filters.incident_key) {
      sql += ` AND incident_key = $${params.length + 1}`;
      params.push(filters.incident_key);
    }

    sql += ` ORDER BY timestamp DESC LIMIT $${params.length + 1}`;
    params.push(limit);

    const result = await query(sql, params);
    return ({ site, limit, range: filters.range || null, historical, events: result.rows.map(({source_evidence, ...event}) => ({
      ...event, capture_context: captureContext(source_evidence, event.alert_name),
      traffic_context: trafficContext(source_evidence),
      security_assessment: securityAssessment({...event, source_evidence}),
    })) });
  } catch (err) {
    if (!isDbDown(err)) console.error('Events GET error:', err.message);
    if (isDbDown(err)) return emptyWithReason(site);
    fail(500, {error: 'Case query failed', unavailable: true});
  }
}

async function readEscalatedEvents(site, filters = {}, routeParams = {}, query = databaseQuery) {

  try {
    const result = await query(
      `SELECT id, site_id, timestamp, message, severity, category, source, status,
              alert_name, alert_fingerprint, root_cause, investigation_notes,
              expert_feedback, feedback_quality, rag_document_id, rag_snapshotted_at,
              escalated_at, investigation_started_at, investigation_completed_at,
              feedback_provided_at, created_at, updated_at, source_evidence, jev_review, security_disposition
       FROM events
       WHERE site_id = $1 AND status = 'escalated'
       ORDER BY escalated_at DESC NULLS LAST, timestamp DESC${filters.limit === 100 ? ' LIMIT 100' : ''}`,
      [site]
    );
    return ({ site, events: result.rows.map(event => ({
      ...event, traffic_context: trafficContext(event.source_evidence), capture_context: captureContext(event.source_evidence, event.alert_name),
      security_assessment: securityAssessment(event),
    })) });
  } catch (err) {
    if (!isDbDown(err)) console.error('Escalated events error:', err.message);
    if (isDbDown(err)) return emptyWithReason(site);
    fail(500, {error: 'Case query failed', unavailable: true});
  }
}

async function readEvent(site, filters = {}, routeParams = {}, query = databaseQuery) {
  const id = routeParams.id;
  try {
    const result = await query(`SELECT * FROM events WHERE id = $1 AND site_id = $2`, [id, site]);
    if (result.rows.length === 0) {
      return fail(404, { error: 'Event not found' });
    }
    const event = result.rows[0];
    return ({...event, traffic_context: trafficContext(event.source_evidence), capture_context: captureContext(event.source_evidence, event.alert_name), security_assessment: securityAssessment(event)});
  } catch (err) {
    if (err instanceof ReadError) throw err;
    if (isDbDown(err)) return fail(503, {error:'Event database unavailable', unavailable:true});
    console.error('Event GET error:', err.message);
    fail(500, { error: 'Failed to fetch event' });
  }
}

module.exports = {readEvents,readEscalatedEvents,readEvent};

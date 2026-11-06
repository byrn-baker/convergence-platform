'use strict';
const router = require('express').Router();
const {query, DbUnavailableError} = require('../db');
const {securityAssessment, DISPOSITIONS} = require('../lib/securityAssessment');
const {buildIspReport} = require('../lib/ispReport');

router.use((req, res, next) => {
  if (!['admin', 'operator'].includes(req.user?.role)) return res.status(403).json({error: 'Operator role required'});
  next();
});

async function readCase(req) {
  const result = await query('SELECT * FROM events WHERE id = $1 AND site_id = $2', [req.params.id, req.site]);
  return result.rows[0];
}
function failure(res, error) {
  return res.status(error instanceof DbUnavailableError ? 503 : 500).json({error: 'Incident action unavailable; no message was sent'});
}

router.post('/:id/security-disposition', async (req, res) => {
  const {kind, reason} = req.body || {};
  if (!DISPOSITIONS.includes(kind) || typeof reason !== 'string' || reason.trim().length < 10 || reason.length > 4000) {
    return res.status(400).json({error: 'Choose a review outcome and provide a reason between 10 and 4000 characters'});
  }
  try {
    const event = await readCase(req);
    if (!event) return res.status(404).json({error: 'Event not found'});
    if (!securityAssessment(event)) return res.status(400).json({error: 'This review applies to IDS cases'});
    const disposition = {version: 1, kind, reason: reason.trim(), reviewed_at: new Date().toISOString(), reviewed_by: req.user.sub};
    await query('UPDATE events SET security_disposition = $1 WHERE id = $2 AND site_id = $3', [disposition, event.id, req.site]);
    res.json({id: event.id, security_disposition: disposition, security_assessment: securityAssessment({...event, security_disposition: disposition})});
  } catch (error) { failure(res, error); }
});

router.post('/:id/isp-report', async (req, res) => {
  try {
    const event = await readCase(req);
    if (!event) return res.status(404).json({error: 'Event not found'});
    let report;
    try { report = await buildIspReport(event, req.site, req.body || {}); }
    catch (error) { return res.status(400).json({error: error.message}); }
    await query('UPDATE events SET isp_report = $1 WHERE id = $2 AND site_id = $3', [report, event.id, req.site]);
    res.json(report);
  } catch (error) { failure(res, error); }
});
module.exports = router;

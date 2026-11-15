-- Run only against the isolated restored database. Output contains private IDs.
BEGIN READ ONLY;
SELECT jsonb_build_object(
  'database_bytes', pg_database_size(current_database()),
  'counts', jsonb_build_object(
    'events',(SELECT count(*) FROM events),
    'history',(SELECT count(*) FROM event_history),
    'accounts',(SELECT count(*) FROM reviewer_accounts),
    'dispositions',(SELECT count(*) FROM reviewer_case_dispositions),
    'investigations',(SELECT count(*) FROM reviewer_investigations),
    'jev_requests',(SELECT count(*) FROM reviewer_jev_requests)),
  'orphan_history',(SELECT count(*) FROM event_history h LEFT JOIN events e ON e.id=h.event_id WHERE e.id IS NULL),
  'investigations',COALESCE((SELECT jsonb_agg(to_jsonb(r)) FROM (
    SELECT i.delivery_id,i.operation_id,i.case_id,i.site_id,i.actor_id,o.job_id,o.state
    FROM reviewer_investigations i JOIN reviewer_investigation_outbox o USING(delivery_id) ORDER BY i.delivery_id
  ) r),'[]'::jsonb),
  'jev',COALESCE((SELECT jsonb_agg(to_jsonb(r)) FROM (
    SELECT i.delivery_id,i.operation_id,i.case_id,i.site_id,i.actor_id,
      o.job_id,o.assessment_id,o.state,o.participation,o.result
    FROM reviewer_jev_requests i JOIN reviewer_jev_outbox o USING(delivery_id) ORDER BY i.delivery_id
  ) r),'[]'::jsonb),
  'rag_references',COALESCE((SELECT jsonb_agg(to_jsonb(r)) FROM (
    SELECT id,rag_document_id FROM events WHERE rag_document_id IS NOT NULL ORDER BY id
  ) r),'[]'::jsonb)
);
COMMIT;

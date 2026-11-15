-- Opt-in extension. Run as owner after migration; roles must already exist.
-- Existing reviewer/provisioner permissions.sql remains unchanged.
GRANT USAGE ON SCHEMA :"reviewer_schema" TO :"reviewer_relay";
SET search_path TO :"reviewer_schema", pg_catalog;
GRANT SELECT, INSERT ON reviewer_investigations TO :"reviewer_runtime";
GRANT SELECT, INSERT (delivery_id) ON reviewer_investigation_outbox TO :"reviewer_runtime";
GRANT UPDATE (investigation_notes) ON events TO :"reviewer_runtime";
-- Relay can deliver the already-authorized envelope and advance delivery state;
-- it cannot read accounts, authenticate reviewers, create commands or edit cases.
GRANT SELECT ON reviewer_investigations, reviewer_investigation_outbox TO :"reviewer_relay";
GRANT UPDATE (state,job_id,lease_id,leased_until,next_attempt_at,attempts,last_error,updated_at)
    ON reviewer_investigation_outbox TO :"reviewer_relay";

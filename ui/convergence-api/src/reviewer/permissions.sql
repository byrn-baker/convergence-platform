-- Run with psql as the schema owner. Supply the names of existing rollout
-- roles. These grants assume a dedicated Convergence database and the same
-- search_path used by the schema migration. No credentials are created here.
GRANT USAGE ON SCHEMA :"reviewer_schema" TO :"reviewer_runtime", :"reviewer_provisioner";
SET search_path TO :"reviewer_schema", pg_catalog;
GRANT SELECT ON reviewer_accounts TO :"reviewer_runtime";
-- PostgreSQL requires an UPDATE privilege for FOR SHARE row locks.
-- The revision trigger preserves the old revision on a direct revision-only update.
GRANT UPDATE (revision) ON reviewer_accounts TO :"reviewer_runtime";
GRANT SELECT, INSERT, UPDATE, DELETE ON reviewer_grants, reviewer_login_buckets TO :"reviewer_runtime";
GRANT SELECT, INSERT ON reviewer_case_dispositions TO :"reviewer_runtime";
GRANT SELECT, UPDATE (security_disposition) ON events TO :"reviewer_runtime";
GRANT INSERT ON event_history TO :"reviewer_runtime";
GRANT USAGE ON SEQUENCE event_history_sequence_seq TO :"reviewer_runtime";
GRANT SELECT (id) ON sites TO :"reviewer_provisioner";
GRANT SELECT (id, username), INSERT (id, username, name, role, sites, password_hash),
      UPDATE (name, role, sites, password_hash, enabled) ON reviewer_accounts TO :"reviewer_provisioner";

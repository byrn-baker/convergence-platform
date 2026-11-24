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
-- Sign-in through an identity provider: the runtime reads links and records each
-- spent ID token. Only the provisioner creates or removes a link.
GRANT SELECT ON reviewer_identity_links TO :"reviewer_runtime";
GRANT SELECT, INSERT, UPDATE, DELETE ON reviewer_oidc_exchanges TO :"reviewer_runtime";
GRANT SELECT, INSERT ON reviewer_case_dispositions TO :"reviewer_runtime";
-- A bulk decision is written once and never changed, like a single disposition.
GRANT SELECT, INSERT ON reviewer_bulk_dispositions TO :"reviewer_runtime";
GRANT SELECT, INSERT ON reviewer_operation_keys TO :"reviewer_runtime";
GRANT SELECT, UPDATE (security_disposition) ON events TO :"reviewer_runtime";
-- The case queue is maintained by triggers that run as the schema owner; the runtime only reads it.
GRANT SELECT ON reviewer_case_queue TO :"reviewer_runtime";
GRANT INSERT ON event_history TO :"reviewer_runtime";
GRANT USAGE ON SEQUENCE event_history_sequence_seq TO :"reviewer_runtime";
GRANT SELECT (id) ON sites TO :"reviewer_provisioner";
GRANT SELECT (id, username), INSERT (id, username, name, role, sites, password_hash),
      UPDATE (name, role, sites, password_hash, enabled) ON reviewer_accounts TO :"reviewer_provisioner";
GRANT SELECT, INSERT, DELETE ON reviewer_identity_links TO :"reviewer_provisioner";
-- Removing a link ends that account's sessions.
GRANT SELECT (account_id), DELETE ON reviewer_grants TO :"reviewer_provisioner";

-- Each row is both the original receipt and the idempotency record.
CREATE TABLE IF NOT EXISTS reviewer_case_dispositions (
    installation_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    operation_id UUID NOT NULL,
    site_id TEXT NOT NULL,
    case_id UUID NOT NULL REFERENCES events(id),
    kind TEXT NOT NULL CHECK (kind IN ('needs_review', 'expected_activity', 'false_positive', 'confirmed_threat')),
    reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 4000),
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (installation_id, actor_id, operation_id)
);
-- Old rows have no reliable historical display name. Leave their snapshot NULL.
ALTER TABLE reviewer_case_dispositions ADD COLUMN IF NOT EXISTS
    actor_name TEXT CHECK (length(actor_name) BETWEEN 1 AND 256);

CREATE INDEX IF NOT EXISTS reviewer_case_dispositions_by_case
    ON reviewer_case_dispositions (site_id, case_id, reviewed_at DESC, operation_id DESC);

CREATE OR REPLACE FUNCTION reviewer_deny_audit_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'reviewer case audit is append-only' USING ERRCODE = '55000';
END;
$$;

CREATE OR REPLACE TRIGGER reviewer_immutable_dispositions
    BEFORE UPDATE OR DELETE OR TRUNCATE ON reviewer_case_dispositions
    FOR EACH STATEMENT EXECUTE FUNCTION reviewer_deny_audit_change();

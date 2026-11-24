-- One row per bulk decision: a reviewer's single outcome and reason for every case of a
-- signature that still needed review in a window, optionally from one source host.
-- Like a single disposition, the row is both the receipt and the idempotency record.
-- It names the scope the reviewer chose and how many cases it covered; each covered
-- case carries the operation ID in its own disposition.
CREATE TABLE IF NOT EXISTS reviewer_bulk_dispositions (
    installation_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    operation_id UUID NOT NULL,
    actor_name TEXT NOT NULL CHECK (length(actor_name) BETWEEN 1 AND 256),
    site_id TEXT NOT NULL,
    -- Empty for cases that carry no signature ID, as in reviewer_case_queue.
    signature_id TEXT NOT NULL CHECK (length(signature_id) <= 64),
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    -- One address, never a network. NULL covers every source.
    source_host INET CHECK (masklen(source_host) = CASE family(source_host) WHEN 4 THEN 32 ELSE 128 END),
    -- A bulk decision is an outcome; "needs review" is recorded case by case.
    kind TEXT NOT NULL CHECK (kind IN ('expected_activity', 'false_positive', 'confirmed_threat')),
    reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 4000),
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    case_count INTEGER NOT NULL CHECK (case_count >= 1),
    PRIMARY KEY (installation_id, actor_id, operation_id),
    CHECK (window_start < window_end)
);
-- A case page looks up the decision that covered it.
CREATE INDEX IF NOT EXISTS reviewer_bulk_dispositions_by_operation
    ON reviewer_bulk_dispositions (site_id, operation_id);

CREATE OR REPLACE TRIGGER reviewer_immutable_bulk_dispositions
    BEFORE UPDATE OR DELETE OR TRUNCATE ON reviewer_bulk_dispositions
    FOR EACH STATEMENT EXECUTE FUNCTION reviewer_deny_audit_change();
-- An operation ID already used for another command cannot become a bulk decision.
CREATE OR REPLACE TRIGGER reviewer_register_bulk_disposition
    BEFORE INSERT ON reviewer_bulk_dispositions
    FOR EACH ROW EXECUTE FUNCTION reviewer_register_operation('bulk_disposition');

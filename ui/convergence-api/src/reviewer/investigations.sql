-- Immutable acceptance and mutable delivery state have separate owners/grants.
CREATE TABLE IF NOT EXISTS reviewer_investigations (
    delivery_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    installation_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    actor_name TEXT NOT NULL CHECK (length(actor_name) BETWEEN 1 AND 256),
    operation_id UUID NOT NULL,
    site_id TEXT NOT NULL,
    case_id UUID NOT NULL REFERENCES events(id),
    command TEXT NOT NULL DEFAULT 'reinvestigate' CHECK (command = 'reinvestigate'),
    feedback TEXT NOT NULL CHECK (length(feedback) BETWEEN 10 AND 4000),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (installation_id, actor_id, operation_id)
);
CREATE INDEX IF NOT EXISTS reviewer_investigations_by_case
    ON reviewer_investigations(site_id, case_id, requested_at DESC);
CREATE OR REPLACE TRIGGER reviewer_immutable_investigations
    BEFORE UPDATE OR DELETE OR TRUNCATE ON reviewer_investigations
    FOR EACH STATEMENT EXECUTE FUNCTION reviewer_deny_audit_change();

CREATE TABLE IF NOT EXISTS reviewer_investigation_outbox (
    delivery_id UUID PRIMARY KEY REFERENCES reviewer_investigations(delivery_id),
    state TEXT NOT NULL DEFAULT 'accepted'
        CHECK (state IN ('accepted','enqueued','dispatched','completed','failed','unknown')),
    job_id UUID,
    lease_id UUID,
    leased_until TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT CHECK (last_error IN ('unavailable','unauthorized','conflict','invalid_receipt','missing_receipt')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS reviewer_investigation_outbox_pending
    ON reviewer_investigation_outbox(next_attempt_at)
    WHERE state IN ('accepted','enqueued','dispatched');

CREATE OR REPLACE FUNCTION reviewer_delivery_monotonic() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.delivery_id <> OLD.delivery_id OR
       (OLD.job_id IS NOT NULL AND NEW.job_id IS DISTINCT FROM OLD.job_id) OR
       (OLD.state IN ('completed','failed','unknown') AND NEW.state <> OLD.state) OR
       (OLD.state = 'dispatched' AND NEW.state IN ('accepted','enqueued')) OR
       (OLD.state = 'enqueued' AND NEW.state = 'accepted') THEN
        RAISE EXCEPTION 'reviewer delivery state cannot move backwards' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;
CREATE OR REPLACE TRIGGER reviewer_delivery_monotonic
    BEFORE UPDATE ON reviewer_investigation_outbox
    FOR EACH ROW EXECUTE FUNCTION reviewer_delivery_monotonic();

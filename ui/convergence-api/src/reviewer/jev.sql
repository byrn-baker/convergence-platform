-- A user operation cannot silently change command across API replicas or versions.
CREATE TABLE IF NOT EXISTS reviewer_operation_keys (
    installation_id TEXT NOT NULL, actor_id TEXT NOT NULL, operation_id UUID NOT NULL,
    command TEXT NOT NULL CHECK (command IN ('disposition','reinvestigate','jev_review','bulk_disposition')),
    PRIMARY KEY (installation_id,actor_id,operation_id)
);
ALTER TABLE reviewer_operation_keys DROP CONSTRAINT IF EXISTS reviewer_operation_keys_command_check;
ALTER TABLE reviewer_operation_keys ADD CONSTRAINT reviewer_operation_keys_command_check
    CHECK(command IN ('disposition','reinvestigate','jev_review','bulk_disposition'));
-- An older deployment could have reused a UUID across the formerly independent
-- disposition/investigation interfaces. Refuse to invent a winning command.
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM (
        SELECT installation_id,actor_id,operation_id,'disposition' AS command FROM reviewer_case_dispositions
        UNION ALL SELECT installation_id,actor_id,operation_id,command FROM reviewer_investigations
        UNION ALL SELECT installation_id,actor_id,operation_id,command FROM reviewer_operation_keys
    ) historical GROUP BY installation_id,actor_id,operation_id HAVING count(DISTINCT command)>1) THEN
        RAISE EXCEPTION 'Historical reviewer operation command conflict requires reconciliation';
    END IF;
END $$;
INSERT INTO reviewer_operation_keys SELECT installation_id,actor_id,operation_id,command
    FROM reviewer_investigations ON CONFLICT DO NOTHING;
INSERT INTO reviewer_operation_keys SELECT installation_id,actor_id,operation_id,'disposition'
    FROM reviewer_case_dispositions ON CONFLICT DO NOTHING;
CREATE OR REPLACE TRIGGER reviewer_immutable_operation_keys
    BEFORE UPDATE OR DELETE OR TRUNCATE ON reviewer_operation_keys
    FOR EACH STATEMENT EXECUTE FUNCTION reviewer_deny_audit_change();
CREATE OR REPLACE FUNCTION reviewer_register_operation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE chosen TEXT;
BEGIN
    INSERT INTO reviewer_operation_keys(installation_id,actor_id,operation_id,command)
        VALUES (NEW.installation_id,NEW.actor_id,NEW.operation_id,TG_ARGV[0]) ON CONFLICT DO NOTHING;
    SELECT command INTO chosen FROM reviewer_operation_keys WHERE installation_id=NEW.installation_id
        AND actor_id=NEW.actor_id AND operation_id=NEW.operation_id;
    IF chosen IS DISTINCT FROM TG_ARGV[0] THEN
        RAISE EXCEPTION 'Conflicting reviewer operation' USING ERRCODE='23505',CONSTRAINT='reviewer_command_identity';
    END IF;
    RETURN NEW;
END;
$$;
CREATE OR REPLACE TRIGGER reviewer_register_investigation
    BEFORE INSERT ON reviewer_investigations FOR EACH ROW EXECUTE FUNCTION reviewer_register_operation('reinvestigate');
CREATE OR REPLACE TRIGGER reviewer_register_disposition
    BEFORE INSERT ON reviewer_case_dispositions FOR EACH ROW EXECUTE FUNCTION reviewer_register_operation('disposition');

CREATE TABLE IF NOT EXISTS reviewer_jev_requests (
    delivery_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    installation_id TEXT NOT NULL, actor_id TEXT NOT NULL,
    actor_name TEXT NOT NULL CHECK(length(actor_name) BETWEEN 1 AND 256),
    operation_id UUID NOT NULL, site_id TEXT NOT NULL, case_id UUID NOT NULL REFERENCES events(id),
    command TEXT NOT NULL DEFAULT 'jev_review' CHECK(command='jev_review'),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(installation_id,actor_id,operation_id)
);
CREATE OR REPLACE TRIGGER reviewer_immutable_jev_requests
    BEFORE UPDATE OR DELETE OR TRUNCATE ON reviewer_jev_requests
    FOR EACH STATEMENT EXECUTE FUNCTION reviewer_deny_audit_change();
CREATE OR REPLACE TRIGGER reviewer_register_jev_request
    BEFORE INSERT ON reviewer_jev_requests FOR EACH ROW EXECUTE FUNCTION reviewer_register_operation('jev_review');
CREATE TABLE IF NOT EXISTS reviewer_jev_outbox (
    delivery_id UUID PRIMARY KEY REFERENCES reviewer_jev_requests(delivery_id),
    state TEXT NOT NULL DEFAULT 'accepted' CHECK(state IN ('accepted','enqueued','dispatched','completed','failed','unknown')),
    job_id UUID, assessment_id UUID,
    participation TEXT CHECK(participation IN ('started','joined','reused')),
    result JSONB CHECK(result IS NULL OR (jsonb_typeof(result)='object' AND octet_length(result::text)<=65536)),
    lease_id UUID, leased_until TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT CHECK(last_error IN ('unavailable','unauthorized','conflict','invalid_receipt','missing_receipt')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS reviewer_jev_outbox_pending ON reviewer_jev_outbox(next_attempt_at)
    WHERE state IN ('accepted','enqueued','dispatched');
CREATE OR REPLACE TRIGGER reviewer_jev_delivery_monotonic BEFORE UPDATE ON reviewer_jev_outbox
    FOR EACH ROW EXECUTE FUNCTION reviewer_delivery_monotonic();
CREATE OR REPLACE FUNCTION reviewer_jev_result_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.assessment_id IS NOT NULL AND NEW.assessment_id IS DISTINCT FROM OLD.assessment_id)
        OR (OLD.participation IS NOT NULL AND NEW.participation IS DISTINCT FROM OLD.participation)
        OR (OLD.result IS NOT NULL AND NEW.result IS DISTINCT FROM OLD.result) THEN
        RAISE EXCEPTION 'JEV assessment association and result are immutable' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END;
$$;
CREATE OR REPLACE TRIGGER reviewer_jev_result_immutable BEFORE UPDATE ON reviewer_jev_outbox
    FOR EACH ROW EXECUTE FUNCTION reviewer_jev_result_immutable();

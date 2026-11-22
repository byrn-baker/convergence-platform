-- The case queue: one narrow row per intrusion case, kept beside `events` so that the
-- queue never reads case evidence to group or count. `events` rows are about 2 kB and
-- hold the signature inside JSON; grouping a day of them took seconds in production.
--
-- Triggers keep it exact. Existing cases are copied by `reviewer-service.js
-- backfill-queue`, outside this transaction, because creating the triggers locks
-- `events` against writes until commit.
SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS reviewer_case_queue (
  case_id uuid PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
  site_id text NOT NULL,
  occurred_at timestamptz NOT NULL,
  signature_id text NOT NULL,
  severity text NOT NULL,
  needs_review boolean NOT NULL
);
CREATE INDEX IF NOT EXISTS reviewer_case_queue_window ON reviewer_case_queue (site_id, occurred_at DESC)
  INCLUDE (signature_id, severity, needs_review);
CREATE INDEX IF NOT EXISTS reviewer_case_queue_signature ON reviewer_case_queue (site_id, signature_id, occurred_at DESC, case_id DESC);

-- A case needs review until it carries a disposition other than "needs_review".
CREATE OR REPLACE FUNCTION reviewer_case_needs_review(disposition jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT disposition IS NULL OR jsonb_typeof(disposition) <> 'object'
      OR disposition->>'kind' IS NULL OR disposition->>'kind' = 'needs_review'
$$;

-- Runs as the schema owner, so writers of `events` need no privilege on the queue.
CREATE OR REPLACE FUNCTION reviewer_case_queue_sync() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
BEGIN
  IF NEW.alert_name IS DISTINCT FROM 'SuricataIntrusion' THEN
    IF TG_OP = 'UPDATE' THEN DELETE FROM reviewer_case_queue WHERE case_id = NEW.id; END IF;
    RETURN NULL;
  END IF;
  INSERT INTO reviewer_case_queue (case_id, site_id, occurred_at, signature_id, severity, needs_review)
  VALUES (NEW.id, NEW.site_id, NEW.timestamp,
          coalesce(left(NEW.source_evidence #>> '{labels,signature_id}', 64), ''),
          NEW.severity, reviewer_case_needs_review(NEW.security_disposition))
  ON CONFLICT (case_id) DO UPDATE SET site_id = EXCLUDED.site_id, occurred_at = EXCLUDED.occurred_at,
    signature_id = EXCLUDED.signature_id, severity = EXCLUDED.severity, needs_review = EXCLUDED.needs_review;
  RETURN NULL;
END $$;

CREATE OR REPLACE TRIGGER reviewer_case_queue_insert AFTER INSERT ON events
  FOR EACH ROW EXECUTE FUNCTION reviewer_case_queue_sync();
CREATE OR REPLACE TRIGGER reviewer_case_queue_update
  AFTER UPDATE OF site_id, timestamp, alert_name, severity, source_evidence, security_disposition ON events
  FOR EACH ROW EXECUTE FUNCTION reviewer_case_queue_sync();

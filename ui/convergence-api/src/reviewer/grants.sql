-- The reviewer service uses this directory as its shared source of current
-- entitlements. Provision it explicitly; starting a replica never imports users.
CREATE TABLE IF NOT EXISTS reviewer_accounts (
  id text PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 128),
  username text NOT NULL UNIQUE CHECK (length(username) BETWEEN 1 AND 128),
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 256),
  password_hash text NOT NULL,
  role text NOT NULL CHECK (role IN ('viewer', 'operator', 'admin')),
  sites text[] NOT NULL CHECK (
    cardinality(sites) <= 256 AND coalesce(array_ndims(sites), 1) = 1
    AND array_position(sites, NULL) IS NULL AND array_position(sites, '') IS NULL
  ),
  enabled boolean NOT NULL DEFAULT true,
  revision uuid NOT NULL DEFAULT gen_random_uuid(),
  credential_revision uuid NOT NULL DEFAULT gen_random_uuid()
);

CREATE OR REPLACE FUNCTION update_reviewer_account_revision() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- Fresh values also prevent a deleted and re-created account with the same
  -- public ID from inheriting prior sign-in attempts or authorization.
  IF TG_OP = 'INSERT' THEN
    NEW.revision := gen_random_uuid();
    NEW.credential_revision := gen_random_uuid();
    RETURN NEW;
  END IF;
  NEW.revision := OLD.revision;
  NEW.credential_revision := OLD.credential_revision;
  IF (NEW.username, NEW.password_hash, NEW.enabled) IS DISTINCT FROM
     (OLD.username, OLD.password_hash, OLD.enabled) THEN
    NEW.credential_revision := gen_random_uuid();
  END IF;
  IF (NEW.credential_revision, NEW.role, NEW.sites) IS DISTINCT FROM
     (OLD.credential_revision, OLD.role, OLD.sites) THEN
    NEW.revision := gen_random_uuid();
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER reviewer_account_revision
BEFORE INSERT OR UPDATE ON reviewer_accounts
FOR EACH ROW EXECUTE FUNCTION update_reviewer_account_revision();

-- This table and the dedicated installation-authenticated service define the
-- reviewer audience. Only the digest of a freshly generated bearer is stored.
CREATE TABLE IF NOT EXISTS reviewer_grants (
  token_digest bytea PRIMARY KEY CHECK (octet_length(token_digest) = 32),
  account_id text NOT NULL REFERENCES reviewer_accounts(id) ON DELETE CASCADE,
  installation text NOT NULL CHECK (length(installation) BETWEEN 1 AND 128),
  issued_role text NOT NULL,
  issued_sites text[] NOT NULL,
  issued_revision uuid NOT NULL,
  credential_revision uuid NOT NULL,
  expires_at timestamptz NOT NULL,
  idle_expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS reviewer_grants_expiry
  ON reviewer_grants (LEAST(expires_at, idle_expires_at));
CREATE INDEX IF NOT EXISTS reviewer_grants_account ON reviewer_grants (account_id);

-- Anonymous attempts are bounded first by the global/installation admission
-- counts. Store only a digest of each username until the one-minute slot ends.
CREATE TABLE IF NOT EXISTS reviewer_login_buckets (
  kind text NOT NULL CHECK (kind IN ('global', 'installation', 'username')),
  key text NOT NULL,
  started_at timestamptz NOT NULL,
  attempts integer NOT NULL,
  PRIMARY KEY (kind, key)
);
CREATE INDEX IF NOT EXISTS reviewer_login_bucket_age ON reviewer_login_buckets (started_at);

# Embedded reviewer service

The reviewer service is the private API for the dedicated HTTPS review HUD. Each HUD installation authenticates to it with an enrolled TLS client certificate. Use an isolated test environment before enabling a deployment. Convergence authenticates the person, checks that person's current site and role on each operation, and records the actor on each security disposition. The regular API and the reviewer listener are separate processes with different route tables. Starting the regular server does not expose any reviewer endpoints.

## Prepare the database

Use PostgreSQL 16 and the existing Convergence event schema. Run the following from the convergence-api directory with PGHOST, PGPORT, PGDATABASE, PGUSER and PGPASSWORD set for the intended database. Re-running the reviewer migration preserves issued grants and audit records.

```sh
node reviewer-service.js migrate
```

After creating the two restricted database roles through your normal PG administration process, apply the checked-in grants from a schema-owner connection:

```sh
psql -X -v ON_ERROR_STOP=1 -v reviewer_schema=public \
  -v reviewer_runtime=convergence_reviewer -v reviewer_provisioner=convergence_account_admin \
  -f src/reviewer/permissions.sql
```

Keep the schema owner and account administrator credentials separate from the runtime role. The migrator needs DDL privileges. The reviewer runtime needs SELECT on reviewer_accounts; SELECT, INSERT, UPDATE and DELETE on reviewer_grants and reviewer_login_buckets; SELECT and INSERT on reviewer_case_dispositions; and SELECT plus UPDATE(security_disposition) on events. PostgreSQL row locking with SELECT FOR SHARE also requires an UPDATE privilege on the locked table. The supplied role grants UPDATE on revision for account locks. The revision trigger preserves the existing value on a direct revision-only update. Existing event-history triggers require access to event_history and its sequence for the event update. The runtime must have no DDL or direct account-administration privileges beyond the necessary lock privilege. Grant the account CLI its own restricted provisioning role. Audit immutability is enforced for ordinary DML; database owners and infrastructure administrators remain trusted.

## Provision reviewers

The PostgreSQL directory is empty after migration. Use a separate account per person. The following Bash example reads a new password without echo and pipes it to the command. Use the exact site ID stored in the sites table. New passwords accept up to 72 UTF-8 bytes, bcrypt's input limit.

```bash
read -rsp 'New reviewer password: ' reviewer_password
printf '\n'
printf '%s' "$reviewer_password" | node reviewer-accounts.js create alice operator red
unset reviewer_password
node reviewer-accounts.js access alice viewer red
node reviewer-accounts.js disable alice
```

Use the password subcommand instead of create to rotate an existing account's password through stdin. A password change or disablement invalidates existing grants. Every new action checks the issued grant ceiling and current account permissions. A viewer can only read authorized sites. Admin accounts on this interface also require explicit sites. Dispositions store the acting user's stable ID and display name as recorded at the time.

To use the same accounts for the standalone web dashboard, set CONVERGENCE_ACCOUNT_SOURCE=postgres on the standalone API and configure its JWT_SECRET with at least 32 bytes from a securely generated secret. That mode rejects pre-existing environment-directory JWTs and rechecks the database account revision on each authenticated user request. Rotate a user's credentials or change their authorization to invalidate those web sessions. Legacy deployments default to the USERS environment directory. Static machine API keys retain their existing contracts and cannot sign in to the reviewer service.

## Run the private service

Set REVIEWER_TLS_CERT and REVIEWER_TLS_KEY to the service's certificate chain and private key, REVIEWER_CLIENT_CA to the trusted installation client CA and REVIEWER_INSTALLATIONS_FILE to a JSON file. The file contains an array of objects with installation and fingerprint256 fields. The fingerprint must be the complete colon-separated SHA-256 fingerprint of the DER leaf certificate. Obtain the fingerprint with openssl x509 -in installation.pem -noout -fingerprint -sha256. An installation can have two enrolled certificate fingerprints during rotation. Restart the service after changing its certificates or enrollment file. All replicas for the same environment must use the same enrollment policy and database.

```sh
node reviewer-service.js
```

The default listener is 127.0.0.1:3443. Set REVIEWER_SERVICE_HOST and REVIEWER_SERVICE_PORT explicitly for another private interface. Connect directly with verified mutual TLS. Keep the listener off browser ingress. It rejects browser credentials and uses the verified client certificate for installation identity. Configure separate trusted HTTPS for the operator-facing HUD. Disabling the reviewer service and its ingress closes reviewer access; retain audit and case data.

## Service operations

| Method | Path | Result |
| --- | --- | --- |
| POST | /v1/grants | Accepts username and password JSON from the enrolled HUD and returns its opaque server-side grant and reviewer metadata. |
| GET / DELETE | /v1/grants/current | Shows current permissions or revokes this installation's grant. |
| GET | /v1/sites/{site}/cases/{caseId} | Returns bounded case data and recent review history. |
| POST | /v1/sites/{site}/cases/{caseId}/disposition | Accepts only operationId, kind and reason. Commits the case and audit receipt together. |
| GET | /v1/sites/{site}/cases/{caseId}/disposition/{operationId} | Retrieves this actor and installation's receipt from the authorized case. |

Use Authorization: Bearer with the opaque grant after sign-in. Send a new UUID for each intended disposition and retain that ID for reconciliation after a timeout. Replaying exactly the same normalized command returns its original receipt. Reusing it with different inputs returns 409. The DB stores a digest of the grant, enforces a 30-minute absolute lifetime and 10-minute idle lifetime, and serializes grant revocation with case writes. Only a successful write renews idle time. Reading alone can therefore require a fresh login after 10 minutes. Sign-in admission is limited across API replicas. In a service outage operations fail closed.

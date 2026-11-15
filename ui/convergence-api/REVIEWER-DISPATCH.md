# Attributed reviewer investigation delivery

These extensions are disabled by default. The `reinvestigate` and `jev_review`
commands have distinct requests, receipts and receiver endpoints. Deployment needs both the qualified
receiver implementation and a trusted HTTPS service path before enabling writes.
The ordinary machine API and legacy receiver callers keep their current behavior.

## Database and processes

1. Back up PostgreSQL and rehearse restoration. Run the exact release's
   `node reviewer-service.js migrate` as the existing schema owner. This adds
   immutable request tables, mutable delivery outboxes and the shared immutable
   `reviewer_operation_keys` registry. An actor's operation UUID cannot change command.
   Migration is transactional and may be repeated.
2. Create a distinct restricted relay login: NOSUPERUSER, NOCREATEDB, NOCREATEROLE,
   NOREPLICATION, NOBYPASSRLS, NOINHERIT. Grant database CONNECT and set its
   search_path to the reviewer schema and pg_catalog. Keep all passwords outside
   command-line arguments and Git.
3. Apply this release's `permissions.sql`; additionally run `permissions-dispatch.sql`
   as the schema owner with psql variables `reviewer_schema`, `reviewer_runtime`
   and `reviewer_relay`. Runtime may append the case note and create acceptance;
   relay may read already-authorized envelopes and advance delivery state. The
   relay cannot read accounts or cases, create commands, or mutate audit records.
   Apply the migration and these grants together before accepting traffic: the
   registry trigger also protects disposition and investigation inserts from older API versions,
   whose runtime role now needs SELECT/INSERT on the registry.
   Historical operation IDs already reused across different commands require
   reconciliation: migration refuses such ambiguity without changing audit rows.
4. Run `node reviewer-relay.js` using only the restricted relay PG settings plus:

   - `REVIEWER_DELIVERY_ENABLED=1`
   - `REVIEWER_RECEIVER_ORIGIN=https://<trusted-receiver-origin>` (exact origin)
   - `REVIEWER_DELIVERY_TOKEN_FILE=/protected/path/service-token`
   - Optional `REVIEWER_RECEIVER_CA=/protected/path/receiver-ca.pem` for private CA
   - `REVIEWER_JEV_DELIVERY_ENABLED=1` to process the separate JEV outbox

   The receiver must use this same dedicated service credential, distinct from
   its ordinary receiver token. Receiver GET receipts are authenticated too.
   Production relay configuration refuses plaintext HTTP and redirects.
5. After delivery qualification, restart the reviewer API with
   `REVIEWER_INVESTIGATION_ENABLED=1`. Case reads advertise
   `investigation_enabled`; the HUD can then show the operator action. Neither
   the migration nor relay startup enables browser submission automatically.
   Enable JEV acceptance separately with `REVIEWER_JEV_ENABLED=1`; case reads
   advertise `jev_enabled`. The receiver also needs its own JEV acceptance flag.

## JEV advisory assessments

`POST /v1/sites/:site/cases/:case/jev-review` accepts exactly `{operationId}`.
`GET .../jev-review/:operationId` returns only that installation and actor's
receipt after checking current access. Requests require an operator and an IDS
case; they cannot select budget, model, evidence, feedback or evaluator options.
JEV acceptance does not append investigation notes or change disposition.

Each attributed request retains a distinct delivery identity. The receiver binds
it to one canonical assessment per case, reporting `started`, `joined` or
`reused`. Different reviewers and retries use the same per-case assessment budget.
The relay uses `/reviewer/commands/jev` and polls its immutable delivery receipt.
`completed` here means a persisted advisory result, including explicit budget
exhaustion or unavailable results; it does not claim Gateway completion or safety.
Assessment identity, participation and the bounded result are immutable once set.
Disabling API acceptance leaves existing authorized receipt reads available.

## Telemetry reads

`REVIEWER_TELEMETRY_ENABLED=1` enables finite site-scoped GET resources under
`/v1/sites/:site/telemetry/`; grant status advertises `capabilities.telemetry`.
The API uses the same domain read services as the machine API. Configure its
Prometheus, Loki, site catalog, flow archive and optional host/VM/IDS sources as
for those services. Credentials remain in the API process. No URL or authority
selector is accepted from the browser.

The service checks the grant and site before acquisition and again before
returning data, without holding a grant transaction during source reads. Requests
have an 18-second overall deadline, source deadlines and bounded output. Configure
the reviewer database role's `statement_timeout` to bound server-side query work;
the client query timeout is 12 seconds. Existing event SELECT grants cover the
diary and history queries. Missing sources remain explicitly unavailable.

## Receipts and recovery

The service authorizes the current installation, reviewer, role and case site in
one PostgreSQL transaction. It appends one named request note and inserts the
immutable receipt/outbox atomically. Receipts are keyed by installation, actor
and operation UUID. Repeating the same normalized request returns the original
receipt; different inputs under that key conflict. Distinct intentional requests
remain distinct, including on the same case.

The relay leases deliveries for 30 seconds, sends one immutable envelope, and
uses its assigned delivery UUID on every retry. Receiver admission must deduplicate
that ID durably. Once admitted, the relay polls the receiver receipt rather than
re-enqueuing a missing job. An absent admitted receipt, conflicting envelope or
malformed result becomes `unknown` and requires operator reconciliation. Transient
transport/server failure retains the request for retry. Authentication and permanent
protocol/configuration failures become `failed`. Expired/stale lease results cannot
alter a newer delivery state, and terminal states cannot move backwards.

`accepted` means PostgreSQL committed the request; `enqueued` means durable receiver
admission; `dispatched` means a dispatch checkpoint was committed and the hook may
have been sent. `completed` means the Gateway confirmed that agent execution
finished, never merely that a case resolved or that a requested case update
succeeded. Inspect the case findings separately. The receiver may return `unknown` after an
ambiguous checkpoint, preventing automatic redispatch. Logging out revokes access
to receipts until reauthentication; it does not cancel commissioned work.

The delivery worker stores no reviewer password, grant or browser session. Its
errors contain bounded status classifications, not private receiver response bodies
or request feedback. Do not clear durable receipts as ordinary job cleanup. A
receiver database restore without matching delivery history requires reconciliation
before resuming dispatch.

## Rollback

Disable new writes by removing `REVIEWER_INVESTIGATION_ENABLED=1` and restarting
the reviewer API. Existing authorized receipt reads remain available. Stop the
relay and receiver admissions only after deciding how to handle accepted work.
Retain both databases and reconcile pending deliveries. Do not remove acceptance
records, notes or immutable audit, restore a stale database over concurrent events,
or generate fresh operation IDs to retry an ambiguous request.

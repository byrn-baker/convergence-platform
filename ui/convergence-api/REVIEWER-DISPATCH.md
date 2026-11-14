# Attributed reviewer investigation delivery

This extension is disabled by default. It supports the `reinvestigate` command;
JEV remains outside this command interface. Deployment needs both the qualified
receiver implementation and a trusted HTTPS service path before enabling writes.
The ordinary machine API and legacy receiver callers keep their current behavior.

## Database and processes

1. Back up PostgreSQL and rehearse restoration. Run the exact release's
   `node reviewer-service.js migrate` as the existing schema owner. This adds
   immutable `reviewer_investigations` and mutable `reviewer_investigation_outbox`.
   Migration is transactional and may be repeated.
2. Create a distinct restricted relay login: NOSUPERUSER, NOCREATEDB, NOCREATEROLE,
   NOREPLICATION, NOBYPASSRLS, NOINHERIT. Grant database CONNECT and set its
   search_path to the reviewer schema and pg_catalog. Keep all passwords outside
   command-line arguments and Git.
3. Retain existing `permissions.sql`; additionally run `permissions-dispatch.sql`
   as the schema owner with psql variables `reviewer_schema`, `reviewer_runtime`
   and `reviewer_relay`. Runtime may append the case note and create acceptance;
   relay may read already-authorized envelopes and advance delivery state. The
   relay cannot read accounts or cases, create commands, or mutate audit records.
4. Run `node reviewer-relay.js` using only the restricted relay PG settings plus:

   - `REVIEWER_DELIVERY_ENABLED=1`
   - `REVIEWER_RECEIVER_ORIGIN=https://<trusted-receiver-origin>` (exact origin)
   - `REVIEWER_DELIVERY_TOKEN_FILE=/protected/path/service-token`
   - Optional `REVIEWER_RECEIVER_CA=/protected/path/receiver-ca.pem` for private CA

   The receiver must use this same dedicated service credential, distinct from
   its ordinary receiver token. Receiver GET receipts are authenticated too.
   Production relay configuration refuses plaintext HTTP and redirects.
5. After delivery qualification, restart the reviewer API with
   `REVIEWER_INVESTIGATION_ENABLED=1`. Case reads advertise
   `investigation_enabled`; the HUD can then show the operator action. Neither
   the migration nor relay startup enables browser submission automatically.

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
admission; `dispatched` means the hook was sent. `completed` requires a correlated
result, never merely a resolved case. The receiver may return `unknown` after an
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

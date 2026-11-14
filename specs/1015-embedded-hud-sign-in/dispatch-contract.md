# Reviewer reinvestigation contract v1

Reinvestigation is enabled explicitly after qualification. JEV is not part of this command. The ordinary manual/JEV interfaces keep their current behavior.

## Browser and reviewer API

`POST /review/sites/:site/cases/:case/investigation` and `GET /review/sites/:site/cases/:case/investigation/:operationId` map to the identical `/v1/sites/...` service routes. POST accepts exactly `{operationId, feedback}` (UUID, trimmed 10–4000 Unicode scalar characters; no NUL or unpaired surrogates). Current operator/admin identity and site come only from the existing grant transaction. A viewer cannot submit. Each intentional request has a fresh UUID; reconciliation and retries reuse the original UUID and inputs.

Receipt fields: `operation_id`, `case_id`, `site_id`, `requested_by`, `requested_by_name`, `requested_at`, `feedback`, `delivery_id`, `state`, `job_id` (nullable). States: `accepted`, `enqueued`, `dispatched`, `completed`, `failed`, `unknown`. Reauthorize receipt reads and match installation, actor, site and case. A missing receipt is404; conflicting reuse is409. An accepted request remains commissioned after logout; logging out does not cancel it.

PostgreSQL owns the immutable acceptance receipt and one appended actor-attributed case note, atomically with an outbox row. The operation key is `(installation_id, actor_id, operation_id)`. Lock the case before appending, reject absent/foreign cases, and serialize same-key submissions across different cases. Later authority changes do not rewrite historical attribution. A distinct UUID from another reviewer or another intentional submission must remain distinct.

## Relay to receiver

Dedicated, opt-in receiver paths: `POST /reviewer/commands` and `GET /reviewer/commands/:delivery_id`. Both require the separate `REVIEWER_DELIVERY_TOKEN` service credential; the ordinary receiver token is insufficient. Delivery connection must use trusted HTTPS (loopback HTTP may be an explicitly test-only injected transport, never a production configuration default). The relay uses fixed endpoints, no redirects, bounded responses/timeouts and never forwards user sessions or grants.

POST body is exactly the envelope below (no digest field). Receiver computes and retains a SHA256 digest of UTF-8 compact JSON of the following array in this exact order:

`[version, delivery_id, installation_id, actor_id, actor_name, operation_id, site_id, case_id, command, feedback]`

Envelope keys have those names, `version` is1, `command` is`reinvestigate`, UUIDs use lowercase, identity strings are bounded, feedback is already normalized. No NUL or surrogate code points. Receiver rejects extra fields. Actor display name participates in the immutable transport envelope, not the idempotency identity. API replay after a name change uses the stored original envelope.

Successful POST/GET returns200 with `{delivery_id, job_id, state}`. POST atomically stores one receipt and one existing-inbox job, or returns the prior receipt for the same digest. Different digest409. Full capacity503 before receipt/job insertion. Receipt survives ordinary terminal-job cleanup and process restart. No legacy coalescing by case. GET requires the same dedicated service credential and returns404 when absent.

The relay leases rows with bounded expiry, sends the immutable envelope and retries ambiguous admission with the same delivery ID. A stale lease acknowledgement cannot overwrite a newer worker. Terminal states never move backwards. Poll existing admission receipts to learn receiver progress. Treat authentication/configuration failures as delivery failure without leaking private feedback/tokens into errors.

## Execution and recovery

The existing investigation lane, concurrency and hourly budget own these jobs. The dedicated worker must not append a second case note or update feedback. Persist a dispatch checkpoint immediately before invoking the external hook. Include delivery/job IDs in hook correlation metadata. On a restarted/ambiguous checkpoint do not call the hook again. A case becoming resolved is not evidence of this job's completion. Without a trustworthy result correlated to this job/delivery, retain `unknown` for operator review. An HTTP202 means dispatched, not completed. A receipt can become completed only on a validated correlated result; do not infer it from generic case state. Preserve legacy worker behavior.

## Acceptance

Real PostgreSQL tests: authorization/revocation, two replicas/concurrent requests, identical and conflicting retry, atomic rollback, exactly one note per accepted operation, immutable audit and restricted relay/runtime roles. Real SQLite/HTTP tests: distinct same-case requests, capacity before commit, restart and cleanup replay, changed envelope, auth on both methods, checkpoint ambiguity without redispatch, bounded existing worker budget. Relay tests: expired lease, lost response, repeated/late acknowledgements and terminal monotonicity. Browser: named request, no viewer action, stable operation pointer on a lost response, reconciliation after reload/sign-in, no feedback/grant in persistent browser storage. End-to-end isolated PostgreSQL→relay→receiver→counted synthetic hook before enabling production.

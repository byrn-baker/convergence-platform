# Investigation requests from a signed-in reviewer

**Reinvestigation implemented behind opt-in; production transport/result integration pending.** An operator should be able to request more evidence twice intentionally and see who requested each investigation. Recovering from a dropped connection should return the receipt for the original request. Reinvestigation is the first proposed command. JEV review needs additional rules for sharing its per-case result.

## Current behavior

The ordinary events API appends the operator's feedback before calling the receiver. The manual receiver worker appends the feedback again. The receiver currently groups active manual jobs by site and case. Two operators asking different questions about the same case can receive the same job ID, and an identical submission after completion can create another job. JEV keeps its per-case checkpoint and can reopen a failed job on submission. The reviewer HUD currently exposes only case reads and security dispositions. [Events API](../../ui/convergence-api/src/routes/events.js) · [Receiver](../../services/alert-receiver/server.py) · [Receiver inbox](../../services/alert-receiver/jobs.py)

## Proposed ownership

| Owner | Durable change | Recovery |
| --- | --- | --- |
| Reviewer API and PostgreSQL | Authorize the enrolled installation, active grant, current role and case site. Under a case row lock, record the authenticated actor ID and display name, append the feedback once, and insert the command receipt and its outbound delivery in one transaction. | The same installation, actor and operation UUID with the same normalized command returns the original receipt. Changed site, case, command or feedback with that key returns 409 after authorization. A failed transaction leaves no note or receipt. |
| Delivery worker | Claim a pending delivery with a bounded lease. Send the assigned delivery ID and immutable command envelope over an authenticated service connection. Keep the operator's session, password and grant out of storage and transport here. | On timeout or expired lease send the same ID. Persist the receiver job ID on acknowledgement. A saturated receiver returns a retryable result before committing admission. Conflicting input requires investigation. |
| Receiver and SQLite | In one transaction match the delivery ID and command digest or insert a durable receipt and one bounded job. Use the existing inbox and investigation budget. Retain each distinct reviewer's feedback on its job. | Return the original job ID and known state on retries, including after process restart and normal job cleanup. Keep a small receipt with identity, digest, job ID and result state for the lifetime in which the API can replay the delivery. Restoring a database without matching delivery history requires reconciliation before resuming dispatch. |
| Investigation worker | Keep the pre-dispatch checkpoint. Update the receiver receipt and terminal job state together. Report a result keyed to this job or delivery. | After a crash around the external hook, reconcile using a correlated result. Without one, report the outcome as unknown and require an operator decision. A resolved case alone cannot identify which of two investigations finished. |

The HUD creates one operation UUID per intentional submit. PostgreSQL assigns a globally unique delivery ID so two operators may independently use the same browser-generated UUID. Canonicalize a versioned envelope containing the verified installation and actor, operation UUID, site, case, command type and normalized feedback. Keep mutable display names out of the identity key. Reauthorize each receipt read against the current grant and site. At acceptance the operator has commissioned the work. Later logout ends access to the receipt until a new authorized sign-in. Cancellation would need its own command.

Expose the distinction between **accepted** (the case note and outgoing request committed), **enqueued** (the receiver durably admitted the job), and **completed** (a correlated result is available). Record failure and unknown outcomes explicitly. Replaying an older acknowledgement must never move a terminal receipt backwards. If the browser loses the initial response, it looks up the original operation after sign-in. Retrying an absent request uses the same inputs and key.

## Compatibility and rollout

Introduce a dedicated receiver command path for authenticated service delivery. Extend the existing inbox with transaction-aware admission so capacity checks, deduplication and job insertion share the receipt transaction. The worker for this command consumes the saved feedback without rewriting the case note. Legacy manual and JEV callers keep their current behavior during migration. The command relay only addresses the enrolled receiver and cannot forward arbitrary browser paths. Leave JEV disabled until a distinct review request can be associated with a specific advisory result, including after failures. An explicit new request and retry of a failed delivery must be distinguishable.

Before enabling a reviewer button, exercise real PostgreSQL grants and locks across replicas, simultaneous case requests, receiver capacity, restart, expired relay leases, late acknowledgements and the checkpoint immediately before the external call. Use an isolated hook and verify a single note per accepted reinvestigation. Recovery after a lost result requires job-specific evidence. Rollback stops new admissions, preserves both stores and reconciles pending deliveries before retiring the relay.

## Design fixture

Run the fixture from the repository root:

```sh
python3 -B specs/1015-embedded-hud-sign-in/queue_replay_test.py -v
```

The legacy cases use the existing receiver inbox. The proposed acceptance and delivery transactions use two temporary SQLite databases and a counted synthetic hook. PostgreSQL transaction isolation, authenticated HTTP delivery, concurrent relay workers and JEV execution require implementation-level qualification. [Replay fixture](queue_replay_test.py)

## Implementation

[Protocol and acceptance](dispatch-contract.md) · [Database/relay deployment and rollback](../../ui/convergence-api/REVIEWER-DISPATCH.md). The real receiver owns durable admission and checkpoint recovery; ordinary asynchronous hook acknowledgements remain unknown without a correlated result. See verification.md for executed database, receiver, browser and integration evidence.

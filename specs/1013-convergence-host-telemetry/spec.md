# Spec 1013: Rental VM host telemetry prototype

Status: draft capability; code-only prototype complete and independently reviewed.
Authorized by the owner-provided LocalEdge proposal; deployment remains unqualified.
Fork service work based on 52ac80f0, independent of upstream connector/spec167 work.

## Goal and trust boundary

Extend Convergence's durable IDS ingestion with a distinct typed host-event input.
The outer rental VM may be controlled by a customer with root: every guest claim
is untrusted even when the sender credential is valid. Server-side enrollment binds
site, tenant, allocation, VM and a time-versioned mapping snapshot. IP addresses
alone never establish ownership. Guest heartbeats establish visibility only.

Trusted hypervisor/vNIC/firewall observations are a separate future milestone;
this prototype cannot establish boundary-abuse detection or fill same-bridge capture
gaps. Ordinary nested EVE lab activity must not generate boundary cases.

## Acceptance

- Authenticated POST /v1/host-events, strict versioned allowlist, <=64 events and
  <=256 KiB per request; reject identity injection, private/free-form fields,
  malformed timestamps and timestamps outside enrollment validity.
- Per-enrollment revocable credentials distinct from IDS and status credentials;
  server-configured immutable allocation and mapping_version included in evidence.
  Persist credential-hash ownership so an old credential cannot be reassigned to
  a different enrollment after its previous configuration entry is removed.
  Receipt validity is checked after the complete body arrives. Case handling must
  preserve the exact server-bound site; legacy inventory alias normalization must
  not redirect host cases into another site's diary.
- Stable event IDs, transactional deduplication and retryable backpressure; reuse
  durable log/archive/case checkpoints without labelling host evidence Suricata.
- Bound pending host rows per enrollment and in aggregate, reserving inbox room
  for IDS. Reject overlapping active bindings for the same site/VM. This is queue
  isolation, not network request-rate or total database disk quota enforcement.
- Separate status: receiver last-contact vs last heartbeat observation, queue/drops,
  stale heartbeat means lost visibility, never compromise. Replay cannot freshen
  collection time or regress heartbeat data.
- Guest heartbeat agent: stdlib Python, outbound verified HTTPS only; no redirects,
  remote commands, self-update, packet capture or process/customer-content reads.
  Atomic bounded disk spool, stable retries, loss counter, backoff, bounded replies.
- Optional structured connection observations are accepted as guest claims only.
  Cases require an explicit server-configured protected destination, an outer_vm
  observation and reported attempts meeting a configured threshold. Blocked,
  established and unknown results remain distinct. Nested lab observations never
  create cases; no malicious-egress/reputation heuristics or tenant inference.
- Cases are informational investigation inputs, investigate=false, no containment.
- Synthetic tests cover spoofing/reassignment, duplicates/conflicting IDs, outages,
  queue limits/restart, stale/replayed heartbeat, privacy and nested lab activity.

## Exclusions and resource targets

No customer VM enrollment/deployment, production changes, live topology inspection,
new firewall/mirror policy, tenant UI, automated containment or model/provider calls.
32 MiB RAM/spool, 1% average vCPU and 30s heartbeat are design targets to measure,
not qualification claims. Deployment and live coverage require a separately scoped
milestone. Prototype completion does not trigger a full release/platform claim.

## Authorized operator-view follow-up

The owner requested existing Convergence Security views show enrolled VM heartbeat
health after the separately approved heartbeat pilot. This scoped follow-up
consumes the existing operator-only host-status endpoint; it is not a tenant
dashboard, a connection collector, or a security verdict.

- Add `hostTelemetry` to the existing authenticated `/api/security` response,
  using its authorized `req.site` and existing server-held IDS status credential.
- Return only display fields: VM/enrollment reference, heartbeat state/reason,
  observation/contact times, health counters, pending events, expiry and the
  `guest_reported` trust label. Exclude tenant/allocation/mapping identifiers,
  tokens, token hashes, and operator credentials from browser responses.
- Reject malformed or cross-site upstream records; fail to an explicit
  unavailable state. An old receiver without host-status must not break IDS,
  firewall observations or the Security view.
- Both the Convergence HUD Security view and installed API dashboard show VM
  heartbeat health, freshness, queue/drop health and expiry. Distinguish empty,
  unavailable, unknown, reporting, stale, degraded, inactive/expired and revoked
  states without treating missing data as zero or host safety.
- Escape displayed values and preserve existing site authorization and refresh
  behavior. Keep the operator token server-side and TLS verification enabled.
- Verify site isolation, failure independence, malformed data, representative
  health states, HTML escaping and the actual browser display using synthetic
  fixtures; verify the live pilot read-only after an approved managed rollout.

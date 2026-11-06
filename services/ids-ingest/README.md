# IDS ingestion

Single-process FastAPI service accepting Suricata EVE batches over HTTPS.
See the [complete setup, validation and recovery runbook](../../docs/runbooks/convergence-ids.md).

* `POST /v1/eve`: per-sensor Bearer credential; body `{events: [...], heartbeat: {...}}`.
  Sensor/site identity comes from the credential's server-side configuration.
  Maximum 256 records / 1 MiB per batch / 256 KiB per record. Atomic durable
  acceptance returns `{status: "accepted", received, inserted}`. Retry on 503;
  preserve the batch and investigate on 400/413.
* `GET /v1/status?site=...`: separate status-reader Bearer credential, held only
  by the trusted Convergence API. The API enforces the operator's site scope.
* `GET /healthz`: process liveness only. It does not assert capture coverage.

SQLite stores raw EVE, independent log/case delivery checkpoints, and sensor
health. Delivery retries without expiring pending records. Use one Uvicorn worker
and durable local storage. Credentials and TLS keys are generated outside source
control by `deploy/convergence/ids/prepare-ingest.py`.

For distributed switch capture, configure each sensor's `capture_sources`
using the reviewed source inventory. Each entry needs `id`, `ip`, `sessionId`,
`name` and `scope`; copy the same array to the sensor's `capture-sources.json`.
`coverage_gaps` records boundaries that packet activity cannot prove covered.
The optional header probe reports source liveness independently of engine
statistics. See the [network coverage runbook](../../docs/runbooks/convergence-network-coverage.md).

Status also reports site-scoped TLS metadata counts for the last 15 minutes.
These are observations, not a complete inventory of encrypted connections;
payload decryption is explicitly disabled.

Run tests from the repository root:

```bash
.venv/bin/python -m unittest discover -s services/ids-ingest -v
```

## Host telemetry prototype (spec 1013)

The optional `host_agents` array defaults to empty. This separate input accepts
**untrusted guest claims**, not Suricata EVE or trusted hypervisor observations:

- `POST /v1/host-events`: per-enrollment Bearer credential. Strict v1 schema,
  1–64 events, at most 256 KiB. See the [wire contract](../../specs/1013-convergence-host-telemetry/contract.md).
  `heartbeat` contains only collection/queue health; `connection` contains only
  declared scope, destination IP/port, protocol, outcome and reported attempt count.
  Identity fields, unknown fields, free text and duplicate JSON keys are rejected.
- `GET /v1/host-status?site=...`: existing **operator-only** status credential.
  Reports receiver contact separately from newest guest heartbeat observation,
  pending events, guest queue/loss counters and enrollment state. Replaying an old
  batch updates contact but cannot refresh collection time or overwrite newer
  heartbeat data. Stale means lost visibility, not compromise. These are untrusted
  guest timestamps, so a compromised guest can still lie about freshness.

Each enrollment requires `id`, `site`, `tenant_id`, `allocation_id`, `vm_id`,
`mapping_version`, `valid_from`, `valid_until` and `token_sha256`. IDs are opaque
1–128-character alphanumeric identifiers, allowing `.`, `_`, `:`, `-` after the
first character. Validity timestamps must include a timezone. The operator creates
and privately delivers a strong random credential; only its SHA-256 hash goes in
the receiver's private config. It must differ from every sensor/status/agent token.
No credential or private topology belongs in the repository.

Optional enrollment fields are `revoked` (default false),
`protected_destinations` (default empty, up to 128 exact CIDRs) and
`attempt_threshold` (default 5). For example, a synthetic test enrollment policy
could use `protected_destinations: ["192.0.2.0/24"]`. This is documentation address
space, not a suggested production policy. Overlapping nonrevoked validity intervals
for the same site/VM are rejected; adjacent intervals are allowed.

Server-side bindings are stored durably and included in every accepted record.
Reusing an enrollment ID for a different allocation, tenant, VM, mapping version
or validity interval fails startup. Use a new ID and credential for reassignments,
revoke the predecessor, and maintain the referenced time-versioned inventory outside
the guest. Retired binding records and SHA-256 credential ownership records remain as small
attribution tombstones after inbox pruning. A historical credential cannot be
assigned to a new enrollment or to the IDS/status role, even when its old
enrollment has been removed from configuration. Rotation of a credential for the **same unchanged binding** is
allowed. Configuration changes, including revocation, require a receiver restart.
Both receipt time and event observation time must fall in the configured validity
interval; delayed events after expiry are refused rather than reassigned.

Host rows reuse the existing SQLite delivery queue and checkpoints, but logs and
archives use `job="host-events"`, kinds `host_heartbeat`/`host_connection`, and an
explicit `evidence_trust="guest_reported"` envelope. VictoriaLogs stores the whole
schema-bound envelope in `host_event`, not the Suricata `eve` field. Case batches
are separately delivered as receiver `host-events`. Authentication alone never
makes the reported IP, scope, outcome or counters authoritative.

A connection report creates an informational `HostBoundaryObservation` case only
when it names an explicitly configured protected destination, declares `outer_vm`,
and reports at least the configured attempts. `nested_lab` never creates a case.
This is a **per-event reported count**, not an aggregated scan detector. The
reported outcomes `blocked`, `established`, `unknown` are preserved. Cases retain
server attribution and the exact typed evidence, say independent corroboration is
required, and set `investigate=false` (the receiver's T0/observe-only policy).
No automatic investigation or containment is authorized. Without a protected policy,
no host cases are generated. The supplied agent sends only heartbeats; connection
records are for synthetic tests or a future explicitly scoped collector.

Acceptance is transactional. Stable enrollment/event IDs deduplicate while their
inbox rows are retained. Reusing an ID with changed contents returns 409 and rejects
the entire batch. 400/401/409 require operator correction; the sender must retain
the batch. Oversized requests return 413; capacity/disk-reserve/storage failures
return 503. Retain and retry on delivery failures. Credentials never grant status
access or access to the EVE endpoint. Do not expose the operator status token to
customers or use this endpoint as a tenant-facing query service.

As with EVE, pending delivery never expires. Acknowledged host case rows remain
30 days, ordinary host rows one day; pruning is bounded and best-effort. These are
inbox retention/dedup windows, independent of central log retention. A receiver
outage can fill the guest spool, and a downstream outage can fill the shared
receiver queue; monitor both. Top-level `host_queue_capacity` defaults to half `queue_capacity` and cannot exceed
that half, reserving the remainder for EVE. `host_agent_queue_capacity` defaults to
one quarter of the host allowance (minimum one when enabled) and cannot exceed the
aggregate host limit. Both may be zero to stop new admissions; already accepted
retries remain ACK-able. Limits count rows pending any configured delivery sink.
For example, shared capacity 250000 defaults to 125000 host rows overall and 31250
per enrollment. This bounds pending host volume, not CPU/request rate or guaranteed
latency. It is per enrollment, not per tenant; tenants with several enrollments can
use several shares. Production sizing, rate admission and trusted network coverage
still need qualification before customer rollout.

The [rental VM guide](../../docs/convergence/rental-vm-telemetry.md) explains the
prototype boundary and existing Helm integration seam. Its test command also runs
all existing EVE tests; a rebuilt ingest image is required before these new routes
exist in an installation. No live installation is performed by the tests.

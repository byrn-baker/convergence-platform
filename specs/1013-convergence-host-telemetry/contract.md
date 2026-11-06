# Host event contract v1

POST /v1/host-events uses Bearer credentials from config.host_agents (default []).
Body has exactly schema_version=1 and events (1..64). Each event has exactly:
event_id (UUID string), timestamp (timezone-aware ISO8601), event_type and data.
Event types:
- heartbeat: data exactly {collection_ok: bool, queue_events: nonnegative int,
  queue_bytes: nonnegative int, dropped_events: nonnegative int}.
- connection: data exactly {scope: outer_vm|nested_lab, destination_ip: IP literal,
  destination_port: integer 1..65535, protocol: tcp|udp,
  outcome: blocked|established|unknown, attempts: integer 1..1000000}.
No arbitrary text, hostname, command, path, source identity or custom fields.
Receiver permits at most 300s future skew, but configured enrollment validity is
strict: valid_from <= observed < valid_until. Valid_until is required. Receipt must
also be in the enrollment validity interval. Revoked=true refuses credentials.
The receiver rechecks this interval when the full request body has arrived.

Host enrollment fields: id, site, tenant_id, allocation_id, vm_id, mapping_version,
valid_from, valid_until, token_sha256; optional revoked (bool),
protected_destinations (CIDR strings, default []), attempt_threshold (default 5).
The deployment operator assigns a NEW enrollment ID/credential for each allocation
or mapping version, retains historical configuration externally, and revokes the
old credential. No guest identity fields accepted. Snapshot captured in each row;
VMID/UUID/vNIC/MAC/VLAN/IP/NAT mapping inventories remain operator-owned external
records keyed by mapping_version, not inferred or collected by the guest.

Overlapping nonrevoked intervals for the same site/VM are rejected. Stored binding
and credential-hash ownership tombstones prevent ID reassignment or historical
credential reuse for a different enrollment or IDS/status role. Rotation within
the same unchanged binding is allowed.

Top-level config host_queue_capacity defaults to half queue_capacity (maximum
half, leaving room for IDS). host_agent_queue_capacity defaults to one quarter of
that host allowance, minimum one when enabled, and cannot exceed the aggregate.
Both can be zero to stop new host admissions. Limits count rows pending any sink;
already accepted duplicates remain ACK-able at the limit. These are per-enrollment
pending limits, not per-tenant rate limits or total retained database disk quotas.

ACK: {status: accepted, received: count, inserted: count}. Count includes duplicates
for received. Changed content with the same event_id/enrollment is rejected
transactionally; dedup retention is the inbox retention, not an eternal ledger.
HTTP 400/401/409 mean non-retryable without operator correction; agent retains
records and reports failure, never silently discards. 413/503 retain and retry.
GET /v1/host-status?site=... uses the existing status credential (operator-only,
not tenant-facing). No guest can query status or other guests' records.

Transport: agent config has endpoint (full HTTPS URL /v1/host-events), token_file,
spool_dir, optional ca_file, interval_seconds(default30), spool_bytes(default32MiB).
No identity values supplied by the agent. Agent collects only heartbeat health of
its own queue; connection events are synthetic/test or future collector inputs.

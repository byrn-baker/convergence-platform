# Feature Specification: Convergence for ordinary home labs

Created: 2026-10-30. Branch: `1012-convergence-home-onboarding`.
Status: User-authorized implementation; Linux first.

## User scenarios and acceptance

### US1 — Try retained traffic evidence without network equipment (P1)
A user with one Linux computer and local Docker can start an isolated example,
produce clearly labeled demonstration flows, and inspect retained summary/evidence.
No Cisco switch, Proxmox, Kubernetes, device credentials or model subscription is
required. Demo observations must never be presented as the user's network traffic.

### US2 — Observe this Linux computer (P1)
A user can explicitly select a local interface for software flow export into the
same collector/archive/API path. The instructions explain permissions and scope;
no capture starts during preparation and no host routing or firewall is changed.
An isolated Linux network namespace or strictly filtered loopback fixture with real
generated traffic qualifies the
software exporter pipeline; physical adapters and whole-home coverage are separate.

### US3 — Select capabilities honestly (P1)
Guidance distinguishes demonstration, host traffic, router-exported network flows
and optional packet IDS. Missing hardware/source is not an unhealthy core service.
The API/HUD identify demo or host-only coverage, and not-configured/empty/unavailable
states remain distinct. K3s remains the persistent option, Docker the easy entry.

## Requirements

- FR001: Reuse private bundle generation and project isolation; new collection is opt-in.
- FR002: Pin service versions; keep ingest/query endpoints private by default. Never
  expose the Docker socket or auto-discover/capture an interface during setup.
- FR003: Demonstration and host-only identities/coverage must be explicit and separate;
  no silent synthetic data injection into an existing real installation.
- FR004: Persist retained flow data across restart; bound retention and document
  collector spool/queue storage and loss/duplication limits.
- FR005: Verify software export through real collector/storage/API and existing HUD
  components, including authorization, scope and same-window evidence.
- FR006: Report measured resource use for the actual small test, without claiming a
  capacity minimum or qualifying Mac/WSL/physical interfaces without execution.
- FR007: Keep upstream's agreed two-tool/view connector scope unchanged. Record the
  user's maintainer scope approval in proposal161; integration port remains later work.

## Success criteria and exclusions

A contributor follows a reproducible Linux Docker path to nonempty retained evidence
without special networking equipment, then stops only their project. Fresh setup,
repeat preparation, restart, negative access and missing-source behavior are tested.
Existing core/K3s setup is preserved. No live network, IDS, router, hypervisor,
production telemetry migration or automated investigations are changed.

# Feature Specification: Convergence installation and upstream readiness

**Branch**: `1009-convergence-onboarding` · **Created**: 2026-10-30
**Status**: Authorized fork implementation; upstream scope agreement pending.
**Input**: Make Convergence straightforward to install and operate, recommend K3s,
provide Docker trials and an effective NetClaw setup skill, and prepare a
professional, working upstream contribution.

## User Scenarios & Testing

### US1 — Install a persistent monitoring stack (P1)
A new operator follows one K3s-first guide, selects their own cluster and site,
and obtains authenticated Convergence services, durable stores and dashboards
without the author's infrastructure or credentials.
**Independent test**: Fresh isolated cluster, alternate site/release/namespace,
local-path storage, source-built image, no existing Grafana or live network devices.
1. Given a reachable supported cluster, preparation produces a private deployment
   bundle with explicit target identity and no external notifications.
2. Given that bundle, the documented commands install successfully; readiness,
   metric/log ingestion and authenticated case persistence are verified.
3. Given a repeated preparation or upgrade, credentials and data are retained.

### US2 — Try Convergence locally (P1)
A new user follows the Docker trial guide using a Linux-container Docker engine,
without Kubernetes or network-device credentials.
**Independent test**: Clean checkout in a path with spaces, alternative ports and
site; health, Grafana and case persistence checks; teardown preserves volumes.
1. Preparation identifies port/tool prerequisites before services change.
2. The trial shows real stack telemetry and honest missing-network-data states.
3. macOS/Docker Desktop and Windows/WSL2 instructions explain engine/file-sharing
   differences; untested host combinations are explicitly identified.

### US3 — Ask NetClaw to install or extend Convergence (P1)
The setup skill discovers existing services, reads the selected deployment guide,
prepares the concrete configuration, executes authorized installation, verifies
selected layers and leaves an operator handover.
**Independent test**: Skill workflows for fresh K3s, Docker trial, existing shared
Grafana and existing data; missing access becomes an actionable prerequisite.

### US4 — Review a focused upstream contribution (P2)
A maintainer can review proposed scope, portable installation and evidence without
unrelated fork history, home topology, credentials or unqualified platform claims.
**Independent test**: Compare a clean proposal branch against upstream/main;
validate spec artifacts, links, provenance and the explicit readiness checklist.

## Requirements

- FR-001: A single landing page MUST explain value, architecture, deployment choice
  and the next action; K3s is recommended for persistent use and Docker for trials.
- FR-002: Guides MUST contain prerequisites, exact commands, expected outcomes,
  dashboard/HUD access, first telemetry, troubleshooting, upgrades and data retention.
- FR-003: Preparation MUST accept explicit context, namespace, release, site,
  storage class and image; it MUST require no author-specific endpoint or secret.
- FR-004: Generated secrets MUST be private, retained on rerun and absent from
  Helm values, stdout and source control. Different options MUST require review.
- FR-005: Defaults MUST expose services locally or inside the selected cluster;
  shared Grafana, public access, device writes and model investigations are opt-in.
- FR-006: The skill MUST distinguish service readiness, real network telemetry,
  packet capture coverage and working investigations. Empty data is not healthy.
- FR-007: Tests MUST verify fresh installation, isolation, repeat/upgrade behavior,
  persistence and meaningful error cases, including context and credential errors.
- FR-008: The support matrix MUST distinguish executed acceptance, automated
  configuration coverage and untested platform/device combinations.
- FR-009: The upstream PR MUST be based on upstream history with a coordinated
  numbered spec, clear attribution, verification and explicit remaining scope gates.

### Key entities
Deployment bundle: immutable target/options, nonsecret values, private credentials,
commands and verification report. Support matrix: environment, check, evidence,
status and limits. Contribution: scope, upstream base, selected files and evidence.

### Edge cases
Missing/ambiguous context or storage; unreachable Docker daemon; occupied ports;
ARM/AMD64 image mismatch; paths with spaces; unknown namespace ownership; existing
Secret/PVC mismatch; interrupted preparation; unsupported host or air-gapped images;
shared Grafana owned by another release; missing live device/model credentials.

## Success Criteria
- SC-001: Both documented fresh-install paths succeed in isolated acceptance runs
  without editing tracked site examples or copying the author's configuration.
- SC-002: Repeated preparation preserves credential bytes; upgrade preserves a
  synthetic case and stored telemetry; cross-site and unauthenticated access fail.
- SC-003: Every documented command is exercised or labelled as conditional/untested.
- SC-004: Every requested layer has a concrete pass/fail/not-selected result.
- SC-005: The contribution contains no unrelated fork commits or private deployment
  data; maintainer and platform qualification gaps remain visible before readiness.

## Assumptions and scope
Existing authorized fork work may proceed while upstream scope is discussed.
Core telemetry/cases/dashboards are installable without an LLM or live devices.
Existing NetClaw runtime is integrated separately; no blanket runtime reinstall.
Creating an entire production cluster, HA guarantees, automatic switch changes,
all-vendor qualification and claiming every operating system tested are excluded.

# Plan: Grafana board suite

**Retrospective reconstruction — 2026-10-30.** This artifact was added during
spec 1011 maintenance from repository evidence at `09748c37`. It is not evidence
that planning preceded the historical implementation, and it claims no new runtime
verification. Original documents and their status statements remain unchanged.

## Reconstructed implementation boundary

[spec.md](spec.md) defines the extracted concern; [research.md](research.md) maps
it to current repository paths and retained historical decisions.

The extracted scope defines Network, Security and NetClaw narratives and parks
legacy boards outside active provisioning. Panels depend on explicit collector
and datasource contracts; absent source coverage must not be read as network
health. Current Grafana ownership can be shared or per-release.

## Verification sequence for future changes

1. Inspect provisioned dashboard UIDs, active/legacy separation and datasource references.
2. Run panel queries against isolated labeled metric/log fixtures and verify cross-links.
3. Check missing-source behavior and recording-rule dependencies before presenting a board as qualified.

## Compatibility and evidence

Preserve existing API/configuration and storage ownership while making any future
change under its own authorized spec. Historical task range: T139–T144.
Live services, models, collectors and devices were not exercised by this artifact
repair. Passing the artifact checker demonstrates document presence, not runtime
qualification or that these plans originally preceded implementation.

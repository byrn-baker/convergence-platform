# Plan: SuzieQ state-history plane

**Retrospective reconstruction — 2026-10-30.** This artifact was added during
spec 1011 maintenance from repository evidence at `09748c37`. It is not evidence
that planning preceded the historical implementation, and it claims no new runtime
verification. Original documents and their status statements remain unchanged.

## Reconstructed implementation boundary

[spec.md](spec.md) defines the extracted concern; [research.md](research.md) maps
it to current repository paths and retained historical decisions.

SuzieQ is an optional historical state plane, separate from metrics, logs and
intended inventory. The original spec leaves Kubernetes packaging, alert-agent
wiring, installer exposure and change-validation guidance open. Preserve these
gaps; current directory presence does not close those acceptance requirements.

## Verification sequence for future changes

1. Verify bounded history queries expose truncation and freshness using captured or synthetic tables.
2. Render inventory from existing telemetry targets without writing a second source-of-truth inventory.
3. Qualify credential scoping, stale poller alerts and the explicitly open T166–T170 work separately.

## Compatibility and evidence

Preserve existing API/configuration and storage ownership while making any future
change under its own authorized spec. Historical task range: T158–T165 recorded complete; T166–T170 explicitly open.
Live services, models, collectors and devices were not exercised by this artifact
repair. Passing the artifact checker demonstrates document presence, not runtime
qualification or that these plans originally preceded implementation.

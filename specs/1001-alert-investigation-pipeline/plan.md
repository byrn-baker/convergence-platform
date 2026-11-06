# Plan: Alert investigation pipeline

**Retrospective reconstruction — 2026-10-30.** This artifact was added during
spec 1011 maintenance from repository evidence at `09748c37`. It is not evidence
that planning preceded the historical implementation, and it claims no new runtime
verification. Original documents and their status statements remain unchanged.

## Reconstructed implementation boundary

[spec.md](spec.md) defines the extracted concern; [research.md](research.md) maps
it to current repository paths and retained historical decisions.

The existing policy separates observe-only T0, bounded summaries and multi-tool
investigation. Receiver availability must not depend on an available model. The
old extracted `scripts/alert-receiver/` path is now `services/alert-receiver/`.

## Verification sequence for future changes

1. Validate policy loading, matching and safe observe-only fallback with isolated fixtures.
2. Verify concurrency/hourly budget clamps and missing gateway behavior without triggering models.
3. Qualify an authorized synthetic alert through diary reporting separately from optional notifications.

## Compatibility and evidence

Preserve existing API/configuration and storage ownership while making any future
change under its own authorized spec. Historical task range: T096–T110.
Live services, models, collectors and devices were not exercised by this artifact
repair. Passing the artifact checker demonstrates document presence, not runtime
qualification or that these plans originally preceded implementation.

# Plan: Deployable observability stack

**Retrospective reconstruction — 2026-10-30.** This artifact was added during
spec 1011 maintenance from repository evidence at `09748c37`. It is not evidence
that planning preceded the historical implementation, and it claims no new runtime
verification. Original documents and their status statements remain unchanged.

## Reconstructed implementation boundary

[spec.md](spec.md) defines the extracted concern; [research.md](research.md) maps
it to current repository paths and retained historical decisions.

The original scope packages core services separately from full telemetry adapters.
Later spec 1009 adds verified Helm and smaller Docker onboarding; current setup
guidance and platform evidence are in docs/convergence, not historical home values.

## Verification sequence for future changes

1. Render explicit site/namespace/storage and private credentials from current setup helpers.
2. Run isolated fresh install and authenticated core/readiness checks for the selected path.
3. Exercise restart, upgrade and data-preserving reinstall with synthetic records before live migration.

## Compatibility and evidence

Preserve existing API/configuration and storage ownership while making any future
change under its own authorized spec. Historical task range: T030–T034, T040–T042, T050–T058, T070–T073 and T080–T095.
Live services, models, collectors and devices were not exercised by this artifact
repair. Passing the artifact checker demonstrates document presence, not runtime
qualification or that these plans originally preceded implementation.

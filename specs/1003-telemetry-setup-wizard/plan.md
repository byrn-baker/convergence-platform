# Plan: Telemetry setup wizard

**Retrospective reconstruction — 2026-10-30.** This artifact was added during
spec 1011 maintenance from repository evidence at `09748c37`. It is not evidence
that planning preceded the historical implementation, and it claims no new runtime
verification. Original documents and their status statements remain unchanged.

## Reconstructed implementation boundary

[spec.md](spec.md) defines the extracted concern; [research.md](research.md) maps
it to current repository paths and retained historical decisions.

Inventory is the common input for generated SNMP/OTel sections and the operator
device checklist. Managed-section rendering separates declarative changes from
application; device-side commands remain a distinct authorized operation.

## Verification sequence for future changes

1. Render a synthetic multi-vendor inventory twice and compare managed outputs for idempotence.
2. Validate generated collector configuration before any reload; preserve unrelated sections.
3. Check named-interface metrics and checklist secret references using an authorized source fixture.

## Compatibility and evidence

Preserve existing API/configuration and storage ownership while making any future
change under its own authorized spec. Historical task range: T120–T138.
Live services, models, collectors and devices were not exercised by this artifact
repair. Passing the artifact checker demonstrates document presence, not runtime
qualification or that these plans originally preceded implementation.

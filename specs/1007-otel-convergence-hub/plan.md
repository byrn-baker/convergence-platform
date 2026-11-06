# Plan: OTel Convergence hub

**Retrospective reconstruction — 2026-10-30.** This artifact was added during
spec 1011 maintenance from repository evidence at `09748c37`. It is not evidence
that planning preceded the historical implementation, and it claims no new runtime
verification. Original documents and their status statements remain unchanged.

## Reconstructed implementation boundary

[spec.md](spec.md) defines the extracted concern; [research.md](research.md) maps
it to current repository paths and retained historical decisions.

The accepted decision record separates device syslog/SNMP ingestion from retained
host-log sources. It preserves metric naming and bounded label promotion while
exporting to distinct interactive and long-retention stores. Historical measured
results apply to their recorded environment, not every deployment.

## Verification sequence for future changes

1. Validate generated syslog/SNMP receivers and exporter configuration with synthetic inventory.
2. Send a marked synthetic log and metric through an isolated collector to the intended stores.
3. Compare metric names, label bounds and shutdown/stale-source behavior against consuming rules.

## Compatibility and evidence

Preserve existing API/configuration and storage ownership while making any future
change under its own authorized spec. Historical task range: T145–T156.
Live services, models, collectors and devices were not exercised by this artifact
repair. Passing the artifact checker demonstrates document presence, not runtime
qualification or that these plans originally preceded implementation.

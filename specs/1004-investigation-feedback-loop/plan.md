# Plan: Investigation feedback loop

**Retrospective reconstruction — 2026-10-30.** This artifact was added during
spec 1011 maintenance from repository evidence at `09748c37`. It is not evidence
that planning preceded the historical implementation, and it claims no new runtime
verification. Original documents and their status statements remain unchanged.

## Reconstructed implementation boundary

[spec.md](spec.md) defines the extracted concern; [research.md](research.md) maps
it to current repository paths and retained historical decisions.

The historical flow links operator verdicts and reinvestigation to diary/RAG
traceability. The current HUD module lives under modules/convergence rather than
the original src/views/home path. A feedback endpoint or UI button alone does not
prove better future model triage.

## Verification sequence for future changes

1. Verify authenticated feedback and case/evidence association with synthetic records.
2. Check reinvestigation permission and unavailable-model behavior without unbounded retries.
3. Evaluate downstream RAG retrieval separately before claiming feedback improves future investigations.

## Compatibility and evidence

Preserve existing API/configuration and storage ownership while making any future
change under its own authorized spec. Historical task range: T060–T063.
Live services, models, collectors and devices were not exercised by this artifact
repair. Passing the artifact checker demonstrates document presence, not runtime
qualification or that these plans originally preceded implementation.

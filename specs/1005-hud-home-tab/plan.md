# Plan: HUD Home tab

**Retrospective reconstruction — 2026-10-30.** This artifact was added during
spec 1011 maintenance from repository evidence at `09748c37`. It is not evidence
that planning preceded the historical implementation, and it claims no new runtime
verification. Original documents and their status statements remain unchanged.

## Reconstructed implementation boundary

[spec.md](spec.md) defines the extracted concern; [research.md](research.md) maps
it to current repository paths and retained historical decisions.

The historical HOME view exposes observations through a server-side API proxy
while preserving the main HUD. Its implementation has moved into the Convergence
module. Browser access controls, token secrecy and explicit unavailable states
remain separate from whether the backing services themselves are healthy.

## Verification sequence for future changes

1. Exercise navigation and rendering with synthetic API responses and no model access.
2. Verify the proxy keeps service tokens server-side and preserves site/access boundaries.
3. Use browser checks for unavailable feeds, mobile navigation and the existing main HUD.

## Compatibility and evidence

Preserve existing API/configuration and storage ownership while making any future
change under its own authorized spec. Historical task range: T010–T017, T020–T026 and H000–H010.
Live services, models, collectors and devices were not exercised by this artifact
repair. Passing the artifact checker demonstrates document presence, not runtime
qualification or that these plans originally preceded implementation.

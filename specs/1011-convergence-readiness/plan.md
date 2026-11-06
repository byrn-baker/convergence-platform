# Plan: focused proposal and qualification

Use independent, disjoint workers for upstream proposal research/rewrite and
retrospective spec-artifact repairs. Coordinator owns CI diagnosis/fixes, integration,
acceptance evidence and publication. Run independent review after writers finish.

1. Pin baseline and record current failures.
2. Inspect upstream observability and HUD contracts; revise spec 161 documents.
3. Repair missing historical artifacts honestly; fix reproduced dependency/fixture
   failures with meaningful focused regressions.
4. Exercise an isolated observation-to-evidence path using current API/HUD tests
   and add missing integration coverage where feasible.
5. Record real platform coverage and unmet access; validate and publish focused work.

No gate waivers, live cutover, model spending or remote-device changes. A maintained
fork patch version is prepared if this maintenance spec reaches merge readiness.
Upstream proposal can become ready for scope review without shipping implementation.

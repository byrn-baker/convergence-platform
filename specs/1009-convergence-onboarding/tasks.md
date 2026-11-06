# Tasks: Convergence onboarding

## Foundation
- [x] T001 Record scope, existing gaps, decisions and contracts in this spec.
- [x] T002 Add regression tests for private, explicit-context, repeatable K3s bundles in tests/unit/test_convergence_setup_k3s.py.

## US1 — K3s installation
- [x] T003 [US1] Implement K3s preparation/check commands in workspace/skills/convergence-setup/scripts/.
- [x] T004 [US1] Add optional standalone Grafana and portable storage in deploy/helm/convergence/.
- [x] T005 [US1] Exercise fresh isolated K3s install, repeat/upgrade, canaries and persistence; record verification.md.

## US2 — Docker trial
- [x] T006 [US2] Add self-contained trial dashboard configuration to the existing Docker helper.
- [x] T007 [US2] Exercise fresh Compose trial, alternate ports/site, authentication and persistence; record verification.md.

## US3 — Documentation and skill
- [x] T008 [US3] Write authoritative docs/convergence/{k3s,docker,operations,troubleshooting}.md and replace stale landing-page quickstarts.
- [x] T009 [US3] Update convergence-setup routing/references and canonical metadata; verify workflow and links.

## US4 — Upstream proposal
- [x] T010 [US4] Prepare a clean upstream scope/spec draft with contribution inventory and explicit release gates.
- [x] T011 [US4] Run artifact/security/link/installation checks and open the authorized draft PR for maintainer feedback.
- [x] T012 [US4] Record actual platform qualification and remaining upstream implementation/acceptance gates.

Dependencies: T002 precedes T003/T004, then T005. T006 precedes T007. Documentation
uses verified command interfaces. Upstream readiness requires maintainer scope
agreement and a clean tested implementation port, not only completion of this
fork packaging improvement. All work stays separate from the live main checkout.

## Provenance follow-up
- [x] T013 [US4] Inspect the author-supplied standalone Convergence repository, preserve its source/license reference, and distinguish stack ancestry from API ancestry in both draft PRs.

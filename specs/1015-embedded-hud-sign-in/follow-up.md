# Post-rollout work list

Updated 2026-11-15. User asked to start this list and hand continuation to Claude.
The deployed baseline is v0.1.12-rc.2 (f3afb8c); completed production evidence is in
[verification.md](verification.md). This list tracks remaining work, not a new
request to repeat the synthetic model calls or migrate the native HUD immediately.

## 1. Consolidate and finish the release

- [x] Verify production remains Synced/Healthy after dispatch rollout.
- [x] Reconcile stale acceptance checkboxes, separating completed HTTPS review/
  reinvestigation from native HUD and JEV work.
- [x] Fetch current remote heads and map the open PR ancestry.
- [x] Choose and review a consolidation PR into main that preserves the already
  qualified integration history. Inspect the exact resulting tree against rc2.
- [x] Merge after required checks/review, account for every component PR, and
  close or mark superseded only those whose contents are actually retained.
- [x] Publish stable v0.1.12 from the reviewed integrated commit after release
  gates pass. Keep rc tags immutable. If code differs from rc2, qualify that delta.
- [x] Update installed checkout/GitOps pins through the documented rollout and
  verify ordinary alerts, native telemetry, reviewer access and existing receipts.

Section 1 completed 2026-11-15; see the release consolidation entry in
[verification.md](verification.md). #15 merged into release/reviewer-production
(124342b); consolidation PR #16 merged it into main (6d51969) with a merge commit;
#6, #7, #8, #9 and #12 show as merged; #10, #11 and #13 were closed as superseded
with their head commits cited. v0.1.12 tags 6d51969.

### PR ancestry verified on 2026-11-15

| PR | Current base | Head | Included in release/reviewer-production? |
|---|---|---|---|
| #6 | main | stage9/hud-views | Yes |
| #7 | main | fix/site-scoped-device-inventory | Yes |
| #8 (draft) | main | design/embedded-hud-sign-in | Yes |
| #9 | main | feat/convergence-password-verification | Yes |
| #10 | feat/convergence-password-verification | feat/reviewer-service | Yes |
| #11 | stage9/hud-views | feat/hud-reviewer-adapter | Yes |
| #12 | main | feat/reviewer-helm | Yes |
| #13 | stage9/hud-views | feat/staged-hud-integration | Yes |
| #15 (draft) | release/reviewer-production | feat/reviewer-investigation-dispatch | Adds dispatch and completed proof |

`origin/main` was 160cbae and is already an ancestor of rc2. The release base is
f7a7715 (v0.1.11). All eight older PR head commits are ancestors of that release
base. #15 extends it. A reviewed promotion of #15's integrated history to main
(retarget #15 or create a consolidation PR) is a candidate route; inspect the
expanded diff before choosing it. Preserve ancestry with a merge if retaining
component-PR accounting; squashing changes how those PRs can be recognized as
merged. Re-fetch before acting: this table is a dated observation.

Completion: main contains all intended deployed functionality, every open component
PR has an accurate disposition, stable release CI passes, and the installation's
pinned revision and health are recorded. No merge or stable tag was created while
preparing this checklist.

## 2. Fix maintenance gaps

- [x] Preserve existing agent model fallback configuration in setup/netclaw.py.
  First reproduce the full-install plan's unwanted fallback removal with a fixture;
  define preservation when the primary model stays the same and when it changes.
  Keep the fix in the existing configuration-planning module. Confirm existing
  tool restrictions, secrets and removal/restore ownership are preserved.
- [ ] Design and implement certificate renewal/reload for the existing deployment:
  browser TLS is renewed by cert-manager but the HUD loads it only at startup;
  receiver TLS uses a separate private CA and currently needs manual renewal.
  Cover renewal failure, key/certificate mismatch, trust rollover and recovery.
- [ ] Validate expiry monitoring and renew/reload using short-lived test certificates
  before changing live automation; retain per-user session and rollback behavior.

Completion: reinstall does not silently change fallbacks, and certificate renewal
plus service reload is exercised with evidence. See the private operations runbook
for actual expiry dates, service names and credential locations.

## 3. Complete native HUD integration

- [ ] Recheck current upstream NetClaw changes against accepted design PR #357.
  Keep the generic host/session extension upstream and Convergence views here.
- [ ] Define the authenticated per-user telemetry path for the user's actual remote
  workstation, preserving live dashboard, VM Security and Host Heartbeat behavior.
- [ ] Build an isolated candidate and verify Classic and dashboard views, case
  reads/actions, owner/reviewer boundaries, CSRF, revocation and stale responses.
- [ ] Compare against the working native fork, then prepare a concrete cutover and
  rollback. Migrate only after the telemetry and identity acceptance checks pass.

Completion: authenticated native review works from the intended workstation with
live telemetry intact. The earlier native migration removed that telemetry and was
rolled back; the working fork remains a live dependency until this is proven.

## 4. Add reviewer JEV actions

- [ ] Specify command ownership, reviewer/site authorization, operation identity,
  immutable audit and shared JEV budget behavior using the existing dispatch seam.
- [ ] Implement modularly without conflating investigation execution with JEV
  assessment, case disposition or automatic remediation.
- [ ] Qualify replay, lost responses, restart, budget exhaustion and partial failures
  in isolation, then perform bounded production commissioning before activation.

Completion: one attributed operation yields the intended budgeted JEV assessment
without duplicate work or widening reviewer authority.

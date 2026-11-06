# Verification: Convergence readiness

## Run 2026-10-30-readiness

Start UTC: 2026-10-30T18:05:44.864418+00:00
Deadline UTC: 2026-10-30T18:50:44.864418+00:00
Budget: three acceptance cycles per task; 45 minutes shared across delegates.
Checkout: `/home/ubuntu/netclaw-convergence-readiness`, branch `1011-convergence-readiness`.
Baseline: `09748c37`. Prior run is complete; this is newly authorized readiness work.

Ownership: coordinator owns this spec, CI/test/config changes and integration.
Upstream proposal worker owns only spec 161 in its existing separate worktree.
Historical-spec worker owns only missing artifacts in identified older spec dirs.
No user modifications present at baseline. Initial checks and task attempts follow.

## T004 attempt 1 — reproduce

- `python3 scripts/check-fastmcp-compat.py`: FAIL, seven findings across three fork servers.
- `python3 scripts/run-contract-tests.py --suite fastmcp --prepare`: 1 failed/12 passed; the failure is the same owned-server declaration audit.
- `python3 scripts/run-contract-tests.py --suite reconcile --prepare`: 59 passed/5 failed. Minimal copied startup checker fails before probing with FileNotFoundError for scripts/runtime-selection.py.
- Hypotheses: incomplete fixture dependency, stale SDK imports/manifests, missing unit-runner compression dependency. No gate is weakened.

## T004 attempt 2 — fixes and affected validation

Restored the real runtime-selection helper in the startup fixture; aligned three
owned-server dependency manifests with repository-tested FastMCP/MCP pins and
GreyNoise import with FastMCP; Prisma Docker builds now use the manifest too.

- Minimal SQLite metrics test initially failed collection with missing `zstandard`.
  Added the existing exporter's dependency range and import preflight to the isolated
  unit suite. The full unit suite is being rerun in that environment.
- FastMCP suite now passes, including real in-process protocol negotiation for the
  GreyNoise and case tools in legacy and modern modes. No vendor call was made.
- Reconciliation startup contracts now pass (64 assertions); no behavior/gate was
  suppressed. Spec artifacts pass after 46 explicitly retrospective repairs.

## T005 attempts 1–2 — retained observation integration

A new real-store integration test initially exposed a fixture port assumption:
Docker can reassign an ephemeral published port on container restart. The fixture
now re-resolves its assigned loopback endpoint after restart. The complete test
then passed: persisted synthetic records, site/exporter filtering, actual API auth
and HUD proxy, production renderer, selected-window evidence, payload exclusion,
empty results and backend outage. The test removes its own container in teardown.
Added the test to both native Linux Docker CI jobs. This is component integration
with JSDOM, not actual packet export, full browser/gateway or model/case investigation
qualification. Those boundaries remain explicit in the public verification guide.

## T006 — platform prerequisite

The current execution host is Linux x86_64. Actual macOS Docker Desktop and Windows/
WSL2 access was requested; none is currently established. Native ARM64 Linux CI
cannot substitute. State: BLOCKED on platform access, with executable commands in
the verification guide; no remote host settings were changed.

## T004 attempt 3 — full-suite finding and regression

The zstandard collection error is resolved (both SQLite metrics tests pass).
The full isolated unit suite then exposed an existing core-deploy failure under
this host's umask 002. The same tests passed under 022; minimal reproduction showed
new runtime mode 0775 rejected by guarded_read as unsafe_parent. A new test with
explicit umask 002 failed before the fix. Bootstrap now creates only a new runtime
home as mode 0700; existing home permissions are not rewritten. Existing-runtime
fixtures now explicitly model private operator homes. Full affected validation follows.

The operator clarified that WSL2 is on a personal, inaccessible Windows machine
and plans a dedicated VM on Proxmox. No remote WSL target is currently available;
qualification remains blocked until that environment is reachable. No VM was
created as part of this task.

T003 completed: 46 missing historical files across 17 directories restored from
repository evidence, 163 local links checked; spec checker PASS (164 specs, four
unchanged exceptions). T002 proposal now recommends read-only retained flow evidence
with ownership, upstream capability comparison, exact source endpoints and proposed
file/dependency inventory. Independent review is in progress.

The source-version maintenance bump is 1.15.1; release metadata and its six tests
pass. No tag/publication is requested. The installation landing page now clones
maintained main rather than the already-merged onboarding review branch.

## Final local results and review

- Full isolated unit suite: PASS in 163 seconds after the bootstrap fix.
- Core deploy regression family: five tests PASS, including explicit umask 002.
- FastMCP suite: PASS (17 tests including four real protocol compatibility cases).
- Reconciliation contracts: PASS; runner contracts: PASS; all six declaration
  surfaces PASS; FastMCP audit: 40 owned servers, zero failures.
- New flow integration PASS with real restarted VictoriaLogs and mixed-direction
  address evidence, site/exporter rejection, payload exclusion and outage handling.
- Independent readiness review found one packaging issue: the new optional case
  server requirements file matched an existing vendor ignore rule. It was explicitly
  staged; committed-tree presence will be checked before publication. No further
  code blockers were found. Historical records and release limits were reviewed.
- Proposal review found two semantic ambiguities, both resolved and re-reviewed:
  address records can run in either direction, and receipt age is not collector
  health. Upstream PR #309 is now ready for scope review at `f9fc1ffe`.

- HUD unit suite: 392 tests PASS; API flow/reliability regressions: 11 tests PASS.
- Verified `git cat-file -e HEAD:mcp-servers/convergence-case-mcp/requirements.txt`
  after the implementation commit; the required manifest is in the published tree.
- Hosted CI is tracked separately; local passes are not a claim that pending jobs passed.

## T008 attempt 1 — reviewed installer digest

Hosted setup-installer contracts failed with `component_effects_changed` after the
bootstrap correction changed the reviewed installer source. This is introduced
metadata drift, not an inherited failure. Acceptance: inspect the complete installer
diff, preserve all component function/fragment hashes and adapter classifications,
update only the source digest, and pass the full setup-installer suite. No generator
exists for this reviewed manifest. The changed code only creates the already-owned
new runtime root privately before its existing subdirectories; no component install
function or deployment boundary changes.

Local runs also hit `unsafe_parent`: the development checkout and `.local/state`
ancestors were created group-writable. Changing the test process umask alone cannot
change existing directory permissions. A separate clone under a mode-0700 `/tmp`
parent, created with umask 022 and the reviewed manifest overlaid, isolated this
host condition without modifying operator paths or relaxing security checks. The
separate core-deploy regression explicitly retains umask 002.

- `PATH=<isolated setup-installer venv>/bin:$PATH bash tests/setup-installer/run-tests.sh`
  in that private clone: PASS, 274 tests in 83.900 seconds, 32 existing opt-in skips.
- Independent verifier: PASS; all 122 component hashes and one fragment hash match,
  only the full source digest changed, and effect-audit enforcement is unchanged.
- Spec artifacts, release metadata and whitespace checks PASS.
- Prior hosted head `df68e00a`: 39 checks PASS, only setup-installer failed on the
  stale digest. Final corrected-head hosted results are recorded in fork PR #4.

T008 outcome: COMPLETE. Final merge still requires review of corrected-head CI.

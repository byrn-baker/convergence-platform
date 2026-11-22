# Tasks

Each live step needs owner approval: show the change, then apply it.

## 1. Design

- [x] D1: Static mock-up of the queue, a signature's cases and the case page, in the
  theme, with synthetic data, at desktop and phone width. The owner approves it, with
  changes, before any site code.
  Approved by the owner 2026-11-22 without changes: four artboards (queue, signature
  with bulk review, case page, queue on a phone) at
  https://claude.ai/artifact/V1wiNe6rfniysZuAJDjcrr (private to the owner). The mock-up
  loads remote fonts; the build self-hosts them or uses system monospace (plan.md).
- [x] D2: Owner answers the two open questions in plan.md.
  2026-11-22: bulk review is in scope; the proposed default queue is approved.

## 2. Reviewer service

- [x] A1: Signature summary route with parameter validation, site scope and tests,
  including a reviewer without the site and a disabled account.
  2026-11-22: `GET /v1/sites/:site/signatures` (`src/reviewer/queue.js`), 6 new tests
  pass; full API suite 111 pass, 0 fail, 4 skipped for missing fixtures. Correct but
  not yet fast enough for production: see A3. Not deployable before A3.
- [ ] A2: Case list route with keyset pagination, cursor validation and tests for page
  boundaries and equal timestamps.
- [ ] A3: Measured read-only on production 2026-11-22 (site `home`, about 55,000 cases
  in 24 hours): the A1 grouping took 4.1 s once and exceeded the 5 s limit on a second
  run; 7 and 30 days exceeded it. It walks `idx_events_site_timestamp` and reads
  `source_evidence` for every row, so an index for a signature's cases will not fix the
  summary. Expect to need the signature ID outside the JSON (a stored column with an
  index, or the rollup table in plan.md). Decide from measurements on a restored copy.
  Indexes. Measure build time, lock impact and both queries on a restored copy
  of production; record the plans and timings; choose the build method.
- [ ] A4: Test that `permissions.sql` is unchanged and the runtime role needs no new
  grant.

- [ ] B1: Bulk decision table, grants (insert-only for the runtime role) and the
  permissions test.
- [ ] B2: Preview and write routes: one transaction, idempotent operation ID, confirmed
  count check, ceiling, operator or admin only. Tests for a replay, a concurrent single
  disposition, a viewer and a reviewer without the site.
- [ ] B3: A case covered by a bulk decision shows it in its history; a later single
  disposition still works.

## 3. Site

- [ ] H1: HUD proxy routes under the existing origin, session and CSRF rules, with tests.
- [ ] H2: Queue and signature views with filters, fragment routing and keyboard keys.
- [ ] H3: Case page reordered for the decision; next-case flow after a disposition;
  existing draft, receipt and retry behavior unchanged.
- [ ] H6: Bulk review panel on the signature view: scope, outcome, reason, preview
  count, confirmation step, receipt.
- [ ] H4: Theme. Contrast check, reduced-motion check, phone layout.
- [ ] H5: Browser tests: sign in, queue is populated, open a signature, open a case,
  record a disposition, go to the next case; the old `#case=` link still opens.

## 4. Release

- [ ] R1: Independent review; fix findings.
- [ ] R2: Release; build the indexes on production by the method A3 chose; move the
  reviewer digests in the ops repository. The owner signs in, works a case from the
  queue and confirms the site.

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
  pass; full API suite 111 pass, 0 fail, 4 skipped for missing fixtures. Reading
  `events` directly was too slow in production; A3 moved it to the queue table.
- [x] A2: Case list route with keyset pagination, cursor validation and tests for page
  boundaries and equal timestamps.
  2026-11-24: `GET /v1/sites/:site/cases` (`listCases` in `src/reviewer/queue.js`). The
  cursor carries microseconds, because a millisecond cursor would skip or repeat cases
  that share a millisecond. Tests page at five sizes over three cases in one
  microsecond with neighbours one microsecond either side, and cover an exact-fit last
  page, every filter, another site's cursor and forged cursors. The queue table gained
  an `outcome` column so the spec's filter by outcome works on both routes; the table
  is not in production yet, so the release steps are unchanged. Full API suite 137
  pass, 0 fail, 1 skipped. On 700,000 synthetic cases in a local container a page took
  3–27 ms, and 99 ms for the first page of reviewed `alert` cases over 30 days. Not
  measured: the same on a copy of production.
- [x] A3: Measure on a restored copy of production and choose how the queue is made
  fast enough.
  2026-11-22: chose a narrow `reviewer_case_queue` table kept by triggers on `events`
  (plan.md has the reasons, the rejected column option and the timings). The 24-hour
  summary went from 427–507 ms to 27–28 ms on the copy and 30 days from 4.9 s to
  144 ms. Implemented: `queue.sql` in `migrate`, the `backfill-queue` command, the
  startup check, and A1's summary reading the queue table. Full API suite 130 pass,
  0 fail, 1 skipped locally, including the permissions test with its restricted roles.
  Not measured: lock wait and backfill time under production load (R2).
- [x] A4: The runtime role's only new privilege is `SELECT` on the queue table.
  `test-reviewer-permissions.cjs` shows the runtime reading the queue, a runtime
  disposition updating it through the trigger, and the runtime's insert, update, delete
  and truncate refused; the provisioning role cannot read it.
- [x] B1: Bulk decision table, grants (insert-only for the runtime role) and the
  permissions test.
  2026-11-24: `reviewer_bulk_dispositions` in `src/reviewer/bulk.sql`, added to
  `migrate`. `test-reviewer-permissions.cjs` shows the runtime role writing and reading
  a decision; update, delete and truncate refused for it and for the schema owner; the
  provisioner refused; ten invalid rows refused; and an operation ID refused across
  the single and bulk commands. Full API suite 137 pass, 0 fail, 1 skipped.
- [x] B2: Preview and write routes: one transaction, idempotent operation ID, confirmed
  count check, ceiling, operator or admin only. Tests for a replay, a concurrent single
  disposition, a viewer and a reviewer without the site.
  2026-11-24: `GET …/signatures/:id/dispositions/preview` and
  `POST …/signatures/:id/dispositions` (`src/reviewer/bulk.js`; rules in plan.md).
  `test-reviewer-bulk.cjs` covers a replay, a replay with changed inputs, two
  submissions at once, a single disposition holding a case while the bulk write
  waits, a late case, cases reviewed meanwhile, the ceiling at 5,000 and 5,001, a
  viewer, another site and forged input. Full API suite 148 pass, 0 fail, 1 skipped.
  Not measured: a bulk write on a copy of production or under ingestion load.
- [x] B3: A case covered by a bulk decision shows it in its history; a later single
  disposition still works.
  2026-11-24: new table `reviewer_bulk_disposition_cases` lists what each decision
  covered; `readCase` adds those decisions to `review_history`. Tests: a covered case,
  a covered case reviewed on its own afterwards (both entries, single one current), a
  case the decision left alone (no bulk entry), the list matching each decision's
  count, and the list refusing changes. Full API suite 149 pass, 0 fail, 1 skipped.
  The case page's wording for a bulk entry is H3's.

## 3. Site

- [x] H1: HUD proxy routes under the existing origin, session and CSRF rules, with tests.
  2026-11-24: `reviewer/server.mjs` proxies the signature queue, the case list, the
  bulk preview and the bulk write; `reviewer/service-client.mjs` builds the service
  paths from validated parts. Tests in `test/reviewer-server.mjs` and
  `test/reviewer-queue-targets.mjs`: no session, another site, a viewer, a wrong or
  missing CSRF value, a foreign origin, encoded or oversized queries, malformed
  bodies, mismatched responses, and a site removed mid-read.
- [x] H2: Queue and signature views with filters, fragment routing and keyboard keys.
  Done 2026-11-25 (`reviewer/public/queue.js`; plan.md "As built in H2"). HUD suite
  102 pass, 0 fail (`~/.local/state/convergence-spec1019/h2-hud-full.log`). Checked in
  jsdom only; a real browser run is H5.
- [x] H3: Case page reordered for the decision; next-case flow after a disposition;
  existing draft, receipt and retry behavior unchanged.
  Done 2026-11-25 (plan.md "As built in H3"). The case read gained `traffic_context`.
  HUD suite 105 pass, 0 fail; API suite 149 pass, 0 fail, 1 skipped
  (`~/.local/state/convergence-spec1019/h3-*.log`). Page behavior checked in jsdom.
- [x] H6: Bulk review panel on the signature view: scope, outcome, reason, preview
  count, confirmation step, receipt.
  Done 2026-11-25 (`reviewer/public/bulk.js`; plan.md "As built in H6"). HUD suite
  108 pass, 0 fail (`~/.local/state/convergence-spec1019/h6-hud-full.log`). Checked in
  jsdom against a fake service; not yet run against the real service in a browser.
- [x] H4: Theme. Contrast check, reduced-motion check, phone layout.
  Done 2026-11-25 (plan.md "As built in H4"). HUD suite 114 pass, 0 fail
  (`~/.local/state/convergence-spec1019/h4-hud-full.log`); contrast and reduced motion
  are tested in `test/reviewer-theme.mjs`. Rendered in headless Chromium against canned
  data at 1280 and 390 px; not checked on a real phone or with a screen reader.
- [ ] H5: Browser tests: sign in, queue is populated, open a signature, open a case,
  record a disposition, go to the next case; the old `#case=` link still opens.

## 4. Release

- [ ] R1: Independent review; fix findings.
- [ ] R2: Release. On production: dump, `migrate` (creates the queue table and
  triggers; record the lock wait), `backfill-queue` (record its duration and row count
  against the count of intrusion cases), re-apply `permissions.sql`, then move the
  reviewer digests in the ops repository. The owner signs in, works a case from the
  queue and confirms the site.

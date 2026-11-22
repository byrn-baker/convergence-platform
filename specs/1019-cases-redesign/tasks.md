# Tasks

Each live step needs owner approval: show the change, then apply it.

## 1. Design

- [ ] D1: Static mock-up of the queue, a signature's cases and the case page, in the
  theme, with synthetic data, at desktop and phone width. The owner approves it, with
  changes, before any site code.
- [ ] D2: Owner answers the two open questions in plan.md.

## 2. Reviewer service

- [ ] A1: Signature summary route with parameter validation, site scope and tests,
  including a reviewer without the site and a disabled account.
- [ ] A2: Case list route with keyset pagination, cursor validation and tests for page
  boundaries and equal timestamps.
- [ ] A3: Indexes. Measure build time, lock impact and both queries on a restored copy
  of production; record the plans and timings; choose the build method.
- [ ] A4: Test that `permissions.sql` is unchanged and the runtime role needs no new
  grant.

## 3. Site

- [ ] H1: HUD proxy routes under the existing origin, session and CSRF rules, with tests.
- [ ] H2: Queue and signature views with filters, fragment routing and keyboard keys.
- [ ] H3: Case page reordered for the decision; next-case flow after a disposition;
  existing draft, receipt and retry behavior unchanged.
- [ ] H4: Theme. Contrast check, reduced-motion check, phone layout.
- [ ] H5: Browser tests: sign in, queue is populated, open a signature, open a case,
  record a disposition, go to the next case; the old `#case=` link still opens.

## 4. Release

- [ ] R1: Independent review; fix findings.
- [ ] R2: Release; build the indexes on production by the method A3 chose; move the
  reviewer digests in the ops repository. The owner signs in, works a case from the
  queue and confirms the site.

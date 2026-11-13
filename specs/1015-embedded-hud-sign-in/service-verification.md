# Reviewer service verification

Run starting 2026-11-13 04:34 UTC, handoff at 05:51 UTC. Baseline 75d868e, a stacked branch on the shared password verifier. The previous run exercised an in-memory authority. This run targets a durable installation-bound grant, shared current entitlements, a site-scoped case read and an atomic, attributable and replay-safe security disposition on a dedicated mTLS service. The API and DB tests use synthetic credentials, installations, sites and cases. No production provisioning. Acceptance and implementation follow platform design #8. The dedicated HUD host profile awaits upstream #357.

Cycle one starts with no service listener, durable grant or append-only case audit in this base. The DB authority and the case transaction are separate components. The normal web API keeps its own public routes and environment-based authentication. Qualification includes real PostgreSQL, two service instances, TLS transport and denial cases.

## Implementation and verification

The dedicated service validates mutual TLS and certificate enrollment, then issues PostgreSQL-backed opaque grants whose digests are bound to an installation, account, credential revision, initial role/sites and expiry. Case reads, immutable audit history, stable operation receipts, and transactional dispositions use the live authorization context. Named account setup and optional standalone use of the shared directory are covered. Production account provisioning and ingress remain separate.

A fresh PostgreSQL 16 fixture ran node --test test-*.cjs with REVIEWER_TEST_DATABASE_URL and SECURITY_HISTORY_PG_CONTAINER set. Independent verification passed all 73 tests, including 2 mTLS service instances, real password verification, certificate and grant rejection, site isolation, concurrent retries, transaction timeout/rollback, account rename/downgrade/deletion, immutable actor display snapshots, and legacy login regressions. CI runs these checks on an isolated PostgreSQL service. Earlier testing found that stored UUID alone could not identify the reviewer to an operator, and that waiting on a locked case could indefinitely delay revocation. Display attribution is now captured at commit and reviewer transactions use bounded database timeouts. The opt-in standalone PostgreSQL directory requires an explicit signing secret, checks directory revisions and restricts admin users to their configured sites.

No production listener, database migration, TLS files or human accounts were installed. The actual browser integration belongs to the companion downstream reviewer view.

After browser integration, grant issuance and current-grant metadata expose database idle and absolute deadlines to the trusted HUD adapter. Polling preserves the same idle expiry. Browser sessions can discard private content at the effective deadline. A fresh PostgreSQL 16 full suite passed all 73 tests after this change.

The checked-in permission grants were exercised with real non-owner PostgreSQL 16 roles. The runtime could issue, use and revoke a grant and commit a case disposition with audit. Attempts to elevate account roles or delete audit records failed. The provisioner could create and disable named reviewers; attempts to read password hashes or cases failed.

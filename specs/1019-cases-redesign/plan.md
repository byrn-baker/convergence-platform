# Design

Facts measured on production, 2026-11-22: 703,074 `SuricataIntrusion` events on site
`home` since 2026-10-24, severities `info` (534k) and `watch` (169k), nearly all status
`logged` with no disposition; 107 distinct signatures in the latest 20,000. Each case
carries `source_evidence.labels` (`signature_id`, `suricata_severity`, `sensor_id`,
`instance`) and `annotations` (`summary`, `description`, `ip_context`,
`capture_context`).

## Reviewer service (ui/convergence-api/src/reviewer)

Two read-only routes beside the existing case routes, both behind the same grant and
`requireScope` check as `readCase`:

- `GET /v1/sites/:site/signatures?window=24h|7d|30d&state=…&severity=…` returns one row
  per signature in the window: signature ID, summary text, case count, needs-review
  count, highest severity, first and last seen. At most 500 rows.
- `GET /v1/sites/:site/cases?window=…&signature=…&state=…&severity=…&before=<cursor>&limit=…`
  returns case summaries, newest first, by keyset on `(timestamp, id)`. `limit` is at
  most 100. The cursor is opaque to the browser and validated on the service.

Both select fixed columns with the same length caps as `readCase`, validate every
parameter against a closed set or a strict pattern, and run under a short statement
timeout so a bad plan fails instead of loading the database.

The runtime role already has `SELECT` on `events`, so `permissions.sql` does not change.
A test asserts that.

Indexes, added in `grants.sql` so `migrate` creates them:

- `(site_id, timestamp DESC) WHERE alert_name = 'SuricataIntrusion' AND
  security_disposition IS NULL` for the needs-review queue;
- `(site_id, (source_evidence #>> '{labels,signature_id}'), timestamp DESC) WHERE
  alert_name = 'SuricataIntrusion'` for a signature's cases.

Building them takes a lock on a 1.6 GB table that IDS ingestion writes to. Task A3
measures the build and both queries on a restored copy of production before choosing
between a plain build in the migration and a separate `CREATE INDEX CONCURRENTLY` step.
If the windowed signature summary cannot meet one second from indexes alone, the
fallback is a small per-signature rollup table maintained by the service; do not start
with it.

## Site (ui/convergence-hud/reviewer)

The HUD server proxies the two routes as `/review/sites/:site/signatures` and
`/review/sites/:site/cases` under the existing origin gate, session and CSRF rules.

The front end stays plain HTML, CSS and ES modules with no build step. The content
security policy does not change: no inline script, no remote fonts or images. Type is
the system monospace stack, or a font file served from the site itself if the mock-up
needs one and its licence allows it.

Three views, switched by the URL fragment so the back button and links work:

- `#queue` (default): filter bar, signature table, site telemetry as a compact strip.
- `#signature=<id>`: that signature's cases with the same filters.
- `#case=<id>&site=<site>`: the case page. The current sign-in handling of this link is
  kept.

Keyboard: `j`/`k` move, `Enter` opens, `u` goes up a level, `/` focuses the filter, `?`
lists the keys. Saving a disposition offers the next case in the list the reviewer came
from. Draft recovery, receipts and retry of the same operation keep their present
behavior; the existing modules (`pending-operations.js`, `jev.js`, `investigation.js`,
`telemetry.js`) are reused, not rewritten.

Theme: CSS custom properties for a near-black ground, phosphor green text, cyan and
magenta accents, and one warning colour reserved for confirmed threats. Grid and
wireframe motifs are drawn in CSS. Effects are decoration only and are removed under
`prefers-reduced-motion`. All artwork is original.

## Open questions for the owner

1. Bulk review. With about 700,000 undispositioned cases, one decision per case will not
   clear the backlog. Options: leave it out (this spec), or add a later spec for a
   signature-level decision that records one audited rule and marks its cases. Not
   decided here.
2. Default queue. Proposed: cases that need review from the last 24 hours, highest
   severity first.

## Failure and rollback

The change is additive: two read routes, two indexes, new static files. Rollback is the
previous image digests in the ops repository; the indexes stay and are harmless. No
data is rewritten, and the disposition write path is not touched.

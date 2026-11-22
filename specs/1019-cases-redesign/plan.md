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

### Case queue table (chosen in task A3)

Grouping `events` directly is too slow: a row is about 2 kB and the signature ID is
inside its JSON evidence. On production the 24-hour summary took over four seconds.
Adding a column to `events` was rejected: backfilling it would fire the history
trigger for every row (about 700,000 copies into `event_history`) and rewrite a 1.1 GB
table that ingestion writes to.

Instead `queue.sql` adds `reviewer_case_queue`, one narrow row per intrusion case: case
ID (foreign key to `events`, deleted with it), site, time, signature ID, severity and
whether it needs review. Two triggers on `events` keep it exact: after insert, and after
an update of the columns it copies. The trigger function runs as the schema owner, so
ingestion and the reviewer runtime need no write privilege on the queue. The runtime
role gets `SELECT` on it and nothing else; `permissions.sql` changes by that one grant.

Indexes: `(site_id, occurred_at DESC)` including signature, severity and review state
for the summary, and `(site_id, signature_id, occurred_at DESC, case_id DESC)` for a
signature's cases and the bulk preview count. The summary reads one `events` row per
signature for its name; case lists join `events` only for the rows on the page.

Rollout order matters. `migrate` creates the table and triggers in its transaction,
which locks `events` against writes until commit, so it sets a five-second lock timeout
and does no copying. `reviewer-service.js backfill-queue` then copies existing cases one
site and one day per statement, skipping rows the triggers already wrote. It can be
stopped and re-run. Until it finishes, older windows undercount; cases from after the
migration are always present.

Measured on a restored copy of production (2026-11-22 dump, 702,901 cases, production
memory settings, idle host), evidence in `~/.local/state/convergence-spec1019/a3-*`:

| Query | On `events` | On the queue table |
|---|---|---|
| Summary, 24 hours (56,095 cases) | 427–507 ms | 27–28 ms |
| Summary, 7 days (185,226) | 1,261 ms | 98 ms |
| Summary, 30 days (702,900) | 4,865 ms | 144 ms |
| One page of a signature's cases | not measured | 97 ms |

Production itself was about ten times slower than the copy on the first row, so the
one-second target is met with margin only on the queue table. Table 51 MB, indexes
101 MB. The real commands on the copy: `migrate` 0.2 s; `backfill-queue` 63 s in 122
statements, every intrusion case copied, `event_history` untouched, and a second run
copied nothing in 24 s. An event insert costs about 0.13 ms more (0.07 to 0.20 ms per
row over 20,000 rows). Not measured: lock wait and backfill
time under production's ingestion load. R2 measures those when it runs.

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

## Bulk review

A bulk decision is its own record, not a loop over the single-case write. New table
`reviewer_bulk_dispositions`: operation ID (the idempotency key, as for a single
disposition), installation, actor ID and name, site, signature ID, window bounds,
optional source host, outcome, reason, reviewed-at time and the number of cases covered.
It is insert-only for the runtime role, like `reviewer_case_dispositions`.

`POST /v1/sites/:site/signatures/:id/dispositions` takes the scope, outcome, reason,
operation ID and the count the reviewer confirmed. In one transaction the service locks
and updates the matching cases that still have no disposition, writes each one's
`security_disposition` with the reviewer's name and a reference to the bulk operation,
and inserts the one bulk record. The event-history trigger keeps the previous state of
every case. If the number of matching cases differs from the confirmed count by more
than cases that arrived or were reviewed meanwhile can explain, or exceeds a fixed
ceiling (to be set from A3's measurements), the request fails with a conflict and
nothing is written. Replaying the same operation returns the original receipt.

Only `operator` and `admin` roles may use it. A case page reads the bulk reference and
shows "reviewed as part of a bulk decision" with its reason. Reviewing such a case on
its own afterwards is an ordinary single disposition.

The preview count comes from `GET …/signatures/:id/dispositions/preview` with the same
scope parameters, so the number shown and the number written use one query.

## Decisions and open questions

Decided by the owner on 2026-11-22: bulk review is in scope; the default queue is cases
that need review from the last 24 hours, highest severity first; the D1 mock-up is
approved (https://claude.ai/artifact/V1wiNe6rfniysZuAJDjcrr, private to the owner).

Open: the mock-up shows a third bulk scope, "also future matches for 7 days". That is a
standing rule that decides cases nobody has seen, so it is not built until the owner
asks for it explicitly.

## Failure and rollback

The queue is additive: read routes, one table with two triggers on `events`, new static files. Bulk review adds one
table and one write route, and it changes case rows; the single-case write path is not
touched. Rollback is the previous image digests in the ops repository; the indexes and
the table stay and are harmless. A mistaken bulk decision is not undone by rollback:
its record lists what it covered, and the event history holds each case's prior state.

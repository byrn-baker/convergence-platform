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
  most 100 and defaults to 50. The cursor is opaque to the browser and validated on the
  service: it is the last row's time in microseconds and its case ID, and only the exact
  text the service issued is accepted. It is a position, not a grant, so the site and
  every filter are applied again on each page. The response's `next` is the cursor for
  the following page, or null on the last one. An empty `signature=` selects cases
  without a signature ID. A row has the case ID, time, signature ID and name, severity,
  state, and the source and destination address, port and protocol; no evidence text.

`state` is `needs_review`, `reviewed`, `all` or one outcome (`expected_activity`,
`false_positive`, `confirmed_threat`) on both routes.

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
ID (foreign key to `events`, deleted with it), site, time, signature ID, severity,
whether it needs review and, once reviewed, the outcome (added in A2 so that filtering
by outcome never reads `events`). Two triggers on `events` keep it exact: after insert, and after
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

As built in B1 (`src/reviewer/bulk.sql`): the outcome is one of the three review
outcomes, never "needs review"; the source host is a single address; the count is at
least one; and no role, including the schema owner, can update or delete a row. The
operation ID is registered in `reviewer_operation_keys` as `bulk_disposition`, so an ID
used for a single disposition cannot become a bulk decision or the reverse.
`permissions.sql` gains one grant, `SELECT, INSERT` for the runtime role.

As built in B2 (`src/reviewer/bulk.js`):

- The preview returns the count, the window as two fixed instants, the ten busiest
  source hosts with their counts, and the ceiling. The write sends those instants back.
  A case that arrives after the preview with a later time is therefore outside the
  decision.
- Count rule. The write locks the matching cases and reads their state again under the
  lock. Fewer cases than the reviewer confirmed is accepted, because others reviewed
  some meanwhile; those are left alone and the receipt carries the real count. More
  than confirmed, none at all, or more than the ceiling is a conflict and nothing is
  written; the reviewer previews again.
- Ceiling: 5,000 cases per decision. In a local container a decision of 5,000 cases of
  production size took 1.2 s, 2,000 took 0.5 s and 500 took 0.2 s
  (`b2-size.txt`). The write has a 15-second statement timeout.
- The operation ID is claimed in `reviewer_operation_keys` first, so two submissions
  of one operation give one decision and one receipt. A replay with different inputs
  is a conflict. The confirmed count is not part of the operation's identity.
- Each covered case's `security_disposition` is an ordinary version 1 disposition with
  one more field, `bulk_operation_id`. No row is added to `reviewer_case_dispositions`.
- The queue table gained `source_ip`, read from the sensor record by
  `reviewer_case_source`, which returns NULL rather than fail on evidence it cannot
  read. A case without a signature ID cannot be bulk reviewed.
- Both routes need the operator or admin role, the preview included.

Scopes: every case of the signature in the window, or only those from one source host.
The mock-up's third scope, "also future matches for 7 days", is not built (owner
decision 2026-11-24); it would be a standing rule, which the spec keeps out of scope.

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

Closed 2026-11-24: the mock-up shows a third bulk scope, "also future matches for 7
days". That is a standing rule that decides cases nobody has seen. The owner decided to
skip it; the page offers the other two scopes only.

## Failure and rollback

The queue is additive: read routes, one table with two triggers on `events`, new static files. Bulk review adds one
table and one write route, and it changes case rows; the single-case write path is not
touched. Rollback is the previous image digests in the ops repository; the indexes and
the table stay and are harmless. A mistaken bulk decision is not undone by rollback:
its record lists what it covered, and the event history holds each case's prior state.

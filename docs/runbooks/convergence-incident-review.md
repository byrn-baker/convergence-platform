# Security review and internet-provider reports

Convergence separates the sensor observation, suggested review priority and
reviewer's conclusion. In the new HUD's **Convergence → Security**, choose a
1-hour, 6-hour, 24-hour (default), or 7-day window. The API returns up to the latest
100 saved IDS cases within that window. Summary tiles distinguish urgent review,
context review, reviewer-confirmed findings, protocol/capture diagnostics and
reviewed exceptions. Confirmed findings require an explicit case review; a high
rule priority or JEV score does not establish confirmation. Diary and Triage also
explain the priority and suggested next check.

Click a summary tile, destination-network/country bar, or VLAN chip to filter the
queue. Filters combine; **Clear filters** resets them while retaining the chosen
time window and grouping. Summary counts and destination rankings describe the
whole loaded result; the queue reports the matching case and group counts.
The result-cap notice means older cases in the selected window may be omitted.
These charts count saved cases, not packets, unique attacks, traffic volume or
all historical detections. Use **Traffic** for retained firewall-flow byte rankings.

### Historical timeline and destination map

The **Historical activity** panel queries all saved IDS cases in the selected
window, independently of the latest-100 review queue. Its zero-filled time bars
count cases by recorded case time. They do not measure packet bursts or prove
that sensors were continuously healthy. The map shades destination countries by
saved case count; country is network context, not a risk verdict or actor origin.
Internal destinations and unavailable geography have explicit entries in the
country list. Small countries missing from the coarse map remain selectable there.
The public-domain Natural Earth outlines ship locally; rendering makes no map
service requests and sends no case data to a mapping provider.

Click a time bar or country (or use the keyboard/list) to reload its matching
cases from the database **before** the queue cap. Country and time selections
combine. Use **Clear history selection** to restore the full-window queue. Changing
the time-window selector clears both historical selections. The timeline/map
remain scoped to the full selected window; priority, VLAN, destination-network,
country bars below the summary and search filter only the loaded case queue.
**Clear filters** resets those local filters, retaining the historical selection.
Canvas handoffs contain exact case IDs and preserve the historical query scope.
Review drafts remain attached to their original site and case.

Historical counts cover retained database cases, including reviewed cases, not
all sensor detections or overwritten versions of a case. They use enrichment saved
with each case; historical geography is not refreshed from current IP databases.
Missing database/query results show **History unavailable** instead of zero.
The interval uses inclusive start/exclusive end; UTC-aligned bars at the edges
may be partial, and chart labels use the browser's local timezone.

Group by signature, destination network/ASN, country or observed VLAN, or choose
individual cases. A row shows the highest-priority representative case, case
count, latest case time and whether review outcomes differ. Select a row to open
its evidence alongside the list. Use **Case in this group** to inspect each
member independently. Unknown VLANs remain unknown; tags are not inferred from
IP addresses. The coverage panel uses the sensor's separate 15-minute window;
an observed tag does not prove complete same-VLAN visibility. Open **Sensor
health, firewall counts & coverage details** for the underlying diagnostics.

Priority comes from original Suricata rule metadata. Priority-1 and selected
malware/exploit families receive prompt review; protocol/decoder alerts prompt
capture and stream checks. None is automatically marked safe or compromised.
JEV advice remains a separate advisory result.

The selected case has an **Investigate in Canvas** button above its evidence.
The classic HUD retains its per-card action. The button prepares a formal investigation prompt with
that case's ID, site, timestamps, signature, endpoint/VLAN context and a bounded
summary of any JEV advice. In Canvas, choose **Add to canvas draft**, review the
prompt, then send. The model is asked to retrieve the full original case and
corroborate it with available telemetry. Opening Canvas does not run an LLM,
change the case disposition, or request another paid JEV review. After reviewing
the findings, return to **Record a review of this case** to save your conclusion.
**Deep dive group** includes only the matching members of that group, capped at
the latest 20 cases. It preserves exact case IDs and bounded evidence excerpts.
Group selection never applies a review outcome to multiple cases. Unsaved review
text stays attached to the original site and case while switching selections.

For an unencrypted HTTP Basic Auth alert, investigate the affected service and
transport before concluding that credentials were exposed. Keep credential
values and authorization headers out of the findings. JEV's abstract assessment
is context for this investigation, not a substitute for the local evidence.

An operator can record **expected activity**, **false positive**, **confirmed
threat**, or **needs review**, with a reason. Each outcome applies only to that
case. It does not suppress a signature, change investigation state, alter sensor
rules or block traffic. `security_disposition` and prior case versions retain the
review actor, timestamp and reason. Reopen a case when new evidence changes the
assessment. Broad suppression, correlation and time-limited tuning rules remain
future work.

## Provider reports

Open **Internet reports**, select the provider of the affected circuit, and
prepare a report for a stored WAN case. The provider catalog covers twelve major
US residential brands and retains official support URLs and a source-check date.
It is a selection of common providers, not a market-share ranking. Support and
credit rules must be checked again before submitting; business agreements can
have different contacts and requirements.

The catalog's sources are tracked in
[`ispProviders.json`](../../ui/convergence-api/src/lib/ispProviders.json), including
[Xfinity's outage/credit workflow](https://www.xfinity.com/support/articles/check-service-outage),
[Spectrum's Customer Commitment](https://www.spectrum.net/page/customer-commitment),
[AT&T's Guarantee](https://www.att.com/why-att/guarantee/), and official contact
pages for the remaining providers. A provider with no verified support email is
represented by its official portal/chat/phone route. No mailbox is guessed.

The API builds a local draft from:

- The original alert start and recorded clearance, with UTC and selected local
  timezone. Missing start falls back to labeled approximate case creation time.
- Historical `probe_success` and `probe_duration_seconds` for site-labeled
  `blackbox_wan_*` jobs, bounded to seven days, twelve probe targets and 650
  evaluated samples per metric/target. A five-minute context margin is included.
- Available measurements and explicit limitations. No missing metric becomes a
  zero. Target pseudonyms replace addresses and labels in the export.

The draft asks support to investigate and assess credit eligibility. It does not
claim that alert duration equals ISP downtime or that failed TCP probes measure
packet loss. Site-wide probes currently lack reliable per-circuit attribution.
Router/ONT/power and provider-side corroboration remain separate evidence needs.
The evidence source is the configured Prometheus history; older data that exists
only in VictoriaMetrics is not automatically retrieved by this first version.

Download the email draft, evidence JSON (with SHA-256 digest), and probe CSV.
Review them before sharing. The `.eml` recipient is blank until verified. Add
account details through the provider's authenticated channel. No email, claim,
browser submission, provider API request or LLM call is performed by generation.
Browser text edits survive view refreshes within the session; the database keeps
the generated draft. Regeneration creates a new case version and replaces the
current saved report. JSON/CSV downloads contain the redacted evidence, not edits
to the draft narrative.

Overlapping alerts can describe one interruption. Reuse an existing provider
ticket and avoid duplicate claims. Credit amounts, deadlines, eligibility,
submission tracking and automatic provider-outage confirmation are not inferred.

## LLM interface

The bundled `convergence-isp-report` skill uses `isp_provider_catalog` and
`case_isp_report` from `convergence-case-mcp`. Both use the configured site-scoped
case API; report generation requires the operator role. The LLM can revise the
wording while retaining evidence and limitations. Sending requires a separate
explicit user instruction; the MCP provides no sending tool.

## Deployment and verification

The historical timeline/map require rebuilding the Convergence API and the HUD
(`npm --prefix ui/netclaw-visual run build`). Use the complete active Compose
overlay set when recreating the API; reload the browser afterward. PostgreSQL 16
supports safe parsing of legacy enrichment. This update adds no schema migration
and requires no sensor restart.

Rebuild/recreate the Convergence API using the complete active Compose/overlay
set, rebuild the HUD, and refresh the deployed skills/MCP process. API startup
adds nullable JSONB columns without replacing existing case data. Back up the
database before upgrading. Rollback can retain these additive columns.

Verify site isolation, viewer write denial, malformed/truncated evidence,
unknown metrics, partial coverage, timezone conversion, escaped UI content and
draft retention. A synthetic case and metrics fixture can validate reporting
without contacting a provider. Test a real WAN case separately when available.

From the checkout root, run the focused checks:

```sh
node --test ui/convergence-api/test-reliability.cjs ui/convergence-api/test-incident-review.cjs
node --test ui/netclaw-visual/test/convergence-ids.mjs ui/netclaw-visual/test/convergence-jev.mjs ui/netclaw-visual/test/convergence-incident-review.mjs
node --test ui/netclaw-visual/test/convergence-security-explorer.mjs ui/netclaw-visual/test/convergence-explorer.mjs
node --test ui/convergence-api/test-security-history.cjs ui/netclaw-visual/test/convergence-security-history.mjs
```

For the optional real-SQL check, set `SECURITY_HISTORY_PG_CONTAINER` to the local
Postgres container name when running `test-security-history.cjs`. It uses only
connection-local temporary tables in a rolled-back transaction, covering more
than 100 cases, site isolation, legacy/malformed geography and interval edges.

For the visual explorer, check summary and geographic/VLAN filters, mixed review
outcomes within a group, individual case selection, form draft retention, and
single/group Canvas handoffs. Verify a narrow viewport, result-cap and unavailable
states. Browser verification should use synthetic API responses for review writes;
live smoke checks can open/discard Canvas context without running a model or
changing any real case.

Timeline/map acceptance passed 54 focused API/UI checks, the real PostgreSQL
temporary-table check, and the HUD production build. Synthetic browser checks
covered combined interval/country selection, keyboard map selection, unavailable
history, bounded Canvas context and mobile width. Live read-only checks verified
country and timestamp predicates against returned cases and matching timeline/
geography totals. No real case reviews or LLM investigations were submitted.

The evidence digest is SHA-256 over the UTF-8 `JSON.stringify(evidence)` content,
not the pretty-printed download wrapper or CSV bytes. It identifies that snapshot;
it does not authenticate an ISP outage or establish entitlement to a credit.

Initial acceptance exercised the additive schema and history against an isolated
PostgreSQL/API deployment, including report persistence and authorization. Browser
checks covered review filters, case outcomes, provider selection, edited email
downloads and mobile layout. The live HUD was then checked using read-only calls;
no real case verdicts or provider submissions were created by acceptance tests.

## Remaining operational work

Let the sensors collect a baseline while continuing these tasks:

1. Correlate repeated signatures into incidents and add asset/zone criticality,
   endpoint identity, evidence confidence and bounded, expiring tuning rules.
2. Prove coverage at remaining same-host VM, same-node pod and local-wireless
   boundaries; exercise sensor loss, queue replay and database restore.
3. Complete authenticated HUD access and backend exposure review for the chosen
   LAN/proxy deployment; keep dependencies and detection rules maintained.
4. Add independent per-WAN probes, local gateway/ONT/power evidence, historical
   Victoria retrieval and correlated outage windows to strengthen ISP attribution.
5. Add provider profile management, verified account contacts, ticket/claim and
   received-credit tracking, with optional explicit approval before submission.

Passive TLS metadata is available; payload inspection remains a separately
scoped pilot. These follow-ups are required before describing this as an
enterprise-grade IDS or an automatic credit-claim system.

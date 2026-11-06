# Diary investigations and firewall flow visibility

Convergence's new HUD has two connected views:

- **Diary** groups recent case records by IDS signature, destination ASN/network
  organization, destination address, country, or observed VLAN tags. Choose
  individual events for chronological rows. Search filters the loaded records.
  Counts represent cases, not packets or every sensor detection. Each query is
  capped at 100 recent cases within 1h, 6h, 24h or 7d; a full result announces
  the limit. These groups are presentation, not merged incident records.
- **Traffic** ranks the top 20 sources, destinations and endpoint pairs by
  exported bytes over 15m, 1h, 6h or 24h. It queries retained flow records in
  VictoriaLogs, independently of IDS alert counts and goflow2 receiver metrics.

Both views use the new HUD's existing Canvas handoff. A Diary row, group or up
to 20 selected cases carries exact case IDs, site, timestamps, source links and
a formal investigation brief. Canvas first displays the selected context;
**Add to canvas draft**, review, then send it. Nothing runs merely because the
operator clicked Deep dive. Large groups use their latest 20 cases. Full case
evidence remains available through `convergence-case-mcp.case_get`.

The brief calls for a timeline, evidence citations, alternative explanations,
confidence, affected assets and next checks. It treats recorded strings as
untrusted evidence and requests read-only investigation. Selection clears when
switching sites and drops records no longer present after refresh. A selected
case can remain selected while a text/group filter hides it; the selected count
remains visible and **Clear selection** resets it.

## What the measurements mean

Flow records describe source-to-destination bytes reported by an exporter.
Source does not necessarily mean connection initiator, and the direction does
not establish upload/download. Ranking uses **collector receipt time**, which
can lag the activity. Sampling rates are displayed but totals are not
extrapolated. Ingress/egress collection, NAT, repeated exports and at-least-once
log delivery can duplicate traffic. These totals are not billing usage.

The API scopes every query to both the authenticated site and that site's
explicit exporter allowlist. An empty result differs from missing configuration
and unavailable storage. No historical flow records exist before collection,
apart from logs still retained on the collector host at first startup.
Same-VLAN paths that bypass the firewall remain outside firewall-flow coverage.
Country/ASN enrichment does not prove maliciousness; passive IDS observations
do not prove the firewall blocked a connection. Application identification,
inline IPS enforcement and encrypted payload inspection are separate features.

## Connect an existing K3s goflow2 collector

1. Verify that the collector emits **snake_case JSON** with `sampler_address`,
   `src_addr`, `dst_addr`, `bytes`, `packets` and `sampling_rate`. Inspect its
   actual command/arguments and stdout; mounting a configuration file does not
   mean the installed binary reads it. Confirm the observed exporter address,
   not just its management address. Capture the existing Deployment before any
   collector setting change.
2. Select a VictoriaLogs base URL reachable from every K3s worker and a site ID
   matching Convergence. Keep ingestion/query endpoints on a trusted network
   or authenticated TLS gateway; do not publish an unauthenticated archive to
   the internet. The renderer does not embed credentials. For protected
   endpoints, mount the appropriate CA/secret and add exporter authentication
   to the rendered manifest using your deployment's secret management.
3. Render a **private**, site-specific manifest:

   ```bash
   mkdir -p deploy/convergence/generated/flows
   python3 deploy/convergence/flows/render-collector.py \
     --namespace observability --deployment goflow2 --container goflow2 \
     --site home --exporter 192.0.2.1 \
     --victorialogs http://YOUR_ARCHIVE_HOST:9428 \
     > deploy/convergence/generated/flows/collector.json
   kubectl apply --dry-run=server \
     -f deploy/convergence/generated/flows/collector.json
   kubectl apply -f deploy/convergence/generated/flows/collector.json
   kubectl -n observability rollout status daemonset/netclaw-flow-logs
   ```

   Replace the documentation address and archive hostname. Repeat `--exporter`
   for additional exporters belonging to this site. The manifest is one
   site-specific shipper; do not apply a second site over the same named
   resources. For multi-site collection use separate namespaces/collectors
   with corresponding site labels and API allowlists.

   The DaemonSet runs on eligible worker nodes so it follows the collector if
   Kubernetes reschedules it. It reads only the configured pod/container file
   pattern, parses CRI records, excludes stderr diagnostics, and rejects other
   exporters. It mounts pod logs read-only and requires root file access, but
   has no Kubernetes API token, added capabilities, or privilege escalation.
   Configure node tolerations explicitly if the collector runs on tainted nodes.

   Checkpoints and an export retry queue live under
   `/var/lib/netclaw-flow-logs` on each node. The queue is bounded to 1,000
   batches. Monitor disk space, retries, receiver/parser errors and UDP drops.
   A prolonged outage or node loss can outlast Kubernetes log rotation and
   lose records. VictoriaLogs retention and volume capacity must be sized for
   flow ingestion as well as existing IDS logs.
4. Add `flows` under the appropriate site in private
   `deploy/convergence/.env` `SITES_CONFIG`, preserving other fields:

   ```json
   {"home":{"flows":{"exporters":["192.0.2.1"]}}}
   ```

   Set `FLOW_VICTORIALOGS_URL` to the query URL reachable **from the API
   container**. Its default is the Docker full stack's
   `http://victorialogs:9428`; it may differ from the URL the K3s shipper uses.
   Recreate only `convergence-api` with all the Compose overlays and private
   environment files your installation already uses. No DB migration is needed.
5. Verify archived fields and fresh records:

   ```text
   _time:5m job:="netflow" site:="home" | stats count() as records
   _time:5m job:="netflow" site:="home" | stats by (src_addr) sum(bytes) as bytes | sort by (bytes desc) | limit 20
   ```

   Check authenticated `GET /api/flows?site=home&range=15m`, then Traffic in
   the HUD. Confirm that a nonexistent/unauthorized exporter is rejected and
   that archive loss produces an unavailable state, not zero traffic.

For a non-Kubernetes collector, use the rendered OTel pipeline as a reference:
tail its JSON output file, omit the CRI `container` and stderr operators, mount
durable checkpoint storage, and keep the same exporter filters, resource
`job=netflow`/`site` labels and VictoriaLogs exporter. The supplied renderer
specifically deploys the K3s variant; it does not provision a firewall exporter.

### Collector JSON integrity

Some goflow2 builds write the JSON object and newline separately. Concurrent
decoder workers can concatenate objects and leave blank lines. The parser
rejects malformed records instead of inventing boundaries or traffic totals.
Check the actual output with a JSON decoder, excluding stderr diagnostics.
For a lightly loaded NetFlow collector, a verified mitigation is one decoding
worker: `-listen=netflow://:2055?workers=1`. Preserve other required listener
ports when setting arguments, pin the existing image digest, and verify fresh
records and UDP/queue health after the brief restart/template relearning period.
If several listeners actively write the same stdout, they can still race:
use separate collectors/output streams or an atomic message transport for that
deployment. Do not assume one worker per listener makes all multi-listener
configurations safe, or sacrifice required throughput without measuring it.

## LLM evidence tools and API

- `flow_top_talkers(time_range, exporter)` reads the configured site's rankings.
- `flow_evidence(address, start, end, exporter)` retrieves up to 100 matching
  retained records over an explicit ISO interval of at most 24 hours.
- API equivalents: `GET /api/flows` and `GET /api/flows/records` with `site` and
  the corresponding filters. They require existing site-scoped authentication.
- All query selectors/columns are fixed by the server. Callers cannot supply
  arbitrary LogsQL or a destination URL. Evidence exports contain selected
  traffic fields, not arbitrary log bodies.

Refresh the configured case MCP process after deploying new tool definitions;
check tool discovery before assuming a long-running session sees them. Compare
the tool's returned site with the Canvas investigation scope. Existing cases,
advisory reviews and investigation history are preserved.

## Verification and rollback

Run the API `test-flow-explorer.cjs` and HUD `test/convergence-explorer.mjs`,
plus existing reliability, IDS, JEV and incident-review tests; build the HUD.
Browser checks should cover per-row and selected/group Canvas handoffs,
context escaping, grouping/search, traffic drilldown and a narrow viewport.
Tests must not send a live LLM request or change real case dispositions.

To stop this new shipper, delete only the resources in its rendered manifest;
keep existing goflow2, IDS capture and archives. Retain node checkpoint folders
to avoid replay on reinstall. Restore collector arguments/image from its saved
Deployment if necessary. Remove the added `flows` site config and rebuild the
API/HUD from the previous revision to roll back the view. Existing archived
data need not be deleted.

Design references: [UniFi traffic-flow overview](https://blog.ui.com/article/releasing-unifi-network-9-1),
[VictoriaLogs querying](https://docs.victoriametrics.com/victorialogs/querying/),
[OTLP ingestion](https://docs.victoriametrics.com/victorialogs/data-ingestion/opentelemetry/),
[goflow2 collection/transport](https://github.com/netsampler/goflow2),
[worker settings](https://github.com/netsampler/goflow2/blob/main/docs/performance.md).

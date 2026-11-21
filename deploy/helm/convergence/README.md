# Convergence Helm chart

For a complete installation, use the [K3s guide](../../../docs/convergence/k3s.md).
The chart needs an existing credentials Secret, an API image available to its
nodes, and a working storage class. It does not build images or generate secrets.

Important values:

| Value | Meaning |
|---|---|
| `existingSecret` | External Secret containing `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `JWT_SECRET`, `API_KEYS` and `USERS` |
| `api.image.repository`, `api.image.tag` | Versioned API image; deliver it before installation |
| `siteConfig` | Configured sites; API key scopes must match |
| `postgres.storage`, `loki.storage` | Case and interactive log storage |
| `victoria.metrics.server.persistentVolume`, `victoria.logs.server.persistentVolume` | Long-term store capacity/class |
| `victoria.storage` | Collection and alerting queue storage |
| `grafana.enabled` | Optional private Grafana, disabled by default |
| `ids.ipContextEnabled` | External IP-context database updater; defaults to `true` when IDS is enabled. Set `false` for isolated or unenriched installations; no updater CronJob or `IPINFO_TOKEN` is then required. |
| `monitoring.ids.expectedScrapes` | Operator-owned exact IDS scrape inventory; empty by default, so optional IDS produces no missing-series alert |
| `grafana.storage` | Private Grafana PVC; enabled Grafana also requires `GRAFANA_ADMIN_PASSWORD` in the Secret |
| `victoria.sharedGrafana` | Optional shared provisioning bundle; prefer `registrationMode: external` for GitOps ownership |

Device adapters, IDS and speedtests are opt-in. The fresh helper generates small
storage/retention settings and an API probe, disables external notifications and
keeps services cluster-internal. Standalone Grafana provisions a core-health board
and built-in Prometheus/Loki datasources; it does not assume device telemetry.

Secret values are never included in Helm values. StatefulSet PVCs are retained on
uninstall; do not delete their namespace or original credentials when retaining data.
See [operations](../../../docs/convergence/operations.md) for backups, upgrades and
existing shared Grafana. Old home profiles describe one deployment, not defaults.

## Expected IDS scrape monitoring

Declare only scrapes this release's metrics store is expected to receive. This
inventory does not create a scrape job, configure authentication, or start IDS.
The operator who owns the collector configuration also owns this list. Verify
the actual `up` series' **post-relabeling** `job` and `instance` first:

```yaml
monitoring:
  ids:
    expectedScrapes:
      - id: primary
        site: home
        job: netclaw-ids
        instance: ids.example.test:8443
```

Each entry needs a stable lowercase alphanumeric/hyphen ID (at most 63 characters),
a site present in `siteConfig`, and nonempty exact job/instance labels. The list
is bounded to 64 entries; duplicate IDs or job/instance pairs fail rendering. One
scrape can contain sensors for several sites; choose its owning site, not one
duplicate entry per sensor. Labels are literal strings, not PromQL expressions.

`IDSMetricsScrapeMissing` evaluates
`absent_over_time(up{job="...",instance="..."}[5m]) == 1` for 2 minutes per expected
target. Another healthy target cannot mask a missing one. A present `up=0` is
handled by the existing scrape-failed rules, not by this absence rule. A newly
enabled expectation with no history can fire after 2 minutes; a vanished target
must leave the 5-minute lookback and then remain pending for 2 minutes. Evaluation
intervals and notification grouping add latency. All alerts set `investigate=false`.

The list is independent of `ids.enabled`: external IDS can be monitored without
deploying an ingest workload in this release. An empty list generates no rules,
whether the optional IDS workload is enabled or disabled. Turning off an expected
scrape permanently requires removing its inventory entry too; do not remove an
entry merely to conceal an outage. Retain the inventory when testing lost scrape
configuration, otherwise the evaluator cannot know that data was expected.

The same explicitly opted-in ConfigMap also includes the separate canonical
`host-enrollment.rules.yml` asset. It warns for started, nonrevoked host bindings
expiring within 24 hours (5-minute pending period) or already expired (2-minute
pending period). It does not load the legacy IDS capture/IP-context rule bundle.
No expected scrape inventory means neither this ConfigMap nor its enrollment
rules are created. With no configured host enrollments, these rules remain quiet.

Expiry monitoring requires an IDS image exporting all three
`netclaw_ids_host_enrollment_{valid_from_timestamp_seconds,valid_until_timestamp_seconds,revoked}`
gauges. Verify them for each configured enrollment before accepting monitoring;
older images produce no expiry alert, rather than an implicit missing-inventory
alarm. Labels come from receiver configuration, not guest reports. Configuration
expiry is distinct from token rotation and TLS certificate expiry.

Rules use the existing optional ConfigMap projection and reload interval of
vmalert. If you override `victoria.alerting.extraRuleConfigMaps`, retain
`'{{ .Release.Name }}-ids-monitoring-rules'`. A nonempty inventory with alerting
disabled or its rule projection omitted fails rendering. Before operational
acceptance, verify the rule loaded in the actual evaluator and that firing and
resolution reach the intended alert destination. These rules do not monitor
failure of vmalert or its own configuration; that needs an independent monitor.

Run render and actual PromQL fixtures with an explicitly selected promtool:

```sh
helm dependency build deploy/helm/convergence --skip-refresh
PROMTOOL_BIN=/path/to/promtool python3 -m pytest -q deploy/helm/tests/test_monitoring.py
```

Without `PROMTOOL_BIN`, expression evaluation is reported skipped, not qualified.
The fixtures cover healthy/down series, one missing target out of two, stale
markers and recovery, no expected inventory, exact-label escaping and invalid
configuration. No live targets or credentials are used.

## Optional HTTPS reviewer workloads

After preparing the private Convergence API database and accounts, enable the dedicated review API and HUD in a separate values overlay. Both images need a build containing the reviewer entry points. Use verified release digests in production. Configure a canonical HTTPS DNS hostname whose final label starts with a letter.

```yaml
reviewer:
  enabled: true
  publicOrigin: https://review.example.org
  apiImage: registry.example.org/convergence-api:review-candidate
  hudImage: registry.example.org/convergence-reviewer-hud:review-candidate
  databaseSecret: review-database
  serviceTlsSecret: review-api-tls
  installationTlsSecret: review-installation-tls
  browserTlsSecret: review-browser-tls
```

Create four independent Secrets using your normal credential management process. The database Secret holds PGHOST, PGPORT, PGDATABASE, PGUSER and PGPASSWORD for a restricted runtime role. The service TLS Secret contains tls.crt, tls.key, ca.crt (client CA) and enrolled.json (installation IDs mapped to SHA-256 client certificate fingerprints). The HUD installation Secret contains tls.crt, tls.key and ca.crt (service CA). The browser TLS Secret contains tls.crt and tls.key for publicOrigin. The service certificate needs a SAN matching the configured service DNS name, RELEASE-review-api.NAMESPACE.svc. File contents stay outside values and release history.

Run the schema migration once with schema-owner credentials before starting the API, using the same image and database as the deployment:

```sh
node reviewer-service.js migrate
```

An administrator can run this command in an isolated one-off workload with access to the target database. Apply the [reviewer DB permissions](https://github.com/byrn-baker/convergence-platform/pull/10), then use the separate provisioning role to create individually scoped reviewer accounts. The runtime role needs INSERT on event_history and USAGE on event_history_sequence_seq because the existing event update trigger writes a history row. The supplied permissions script includes both. Readiness checks for the migrated schema and runtime database access. Neither pod needs schema-owner or provisioning credentials.

The chart creates ClusterIP services RELEASE-review-api:3443 and RELEASE-review-hud:3444. The backend has a NetworkPolicy admitting connections only from the same release's HUD pods. Check that the cluster CNI enforces NetworkPolicy. Site-specific routing must expose only the review HUD over trusted HTTPS. Use dedicated TCP/TLS passthrough to preserve the browser's origin and the HUD certificate. The HUD rejects forwarded identity, scheme and address headers. An ordinary HTTP Ingress with default forwarding headers cannot serve this contract.

The reviewer HUD has one replica with Recreate strategy because browser sessions are process-local. Rollout ends existing sign-ins. Named user grants, case dispositions and history are durable in PostgreSQL. Certificate changes require process restarts: enroll both client fingerprints and roll API pods, rotate the HUD credentials and roll its pod, then remove the retired enrollment and roll the API again. Renewing the browser certificate also requires restarting the HUD. Disabling reviewer removes its manifests from rendered output. Reconcile removal using the release's normal prune or decommission procedure. Preserve database history.

### Optional investigation delivery

After the dispatch migration and restricted role grants described in
[REVIEWER-DISPATCH.md](../../../ui/convergence-api/REVIEWER-DISPATCH.md), add:

```yaml
reviewer:
  investigationEnabled: false # enable only after receiver/relay qualification
  relay:
    enabled: true
    databaseSecret: review-relay-database
    deliverySecret: review-relay-delivery
    receiverOrigin: https://receiver.example.org:8446
```

The relay uses `reviewer.apiImage`, runs `reviewer-relay.js` as a separate non-root
workload and exposes no Service. Its ingress NetworkPolicy denies all incoming
connections. Outgoing database, DNS and receiver access must be permitted by the
installation's network policy; this chart does not add an egress allowlist.
The separate database Secret contains PGHOST, PGPORT, PGDATABASE, PGUSER and
PGPASSWORD for the restricted relay role. The delivery Secret contains `token`
and `ca.crt`, the trust root for the receiver server certificate. Neither Secret
may reuse the ordinary API or any other reviewer Secret. The receiver origin
must be exact HTTPS with a DNS name or canonical IPv4 address and no path.

The relay needs no reviewer password, grant, client certificate or schema-owner
credential. Rotate its delivery token and CA through the Secret and a deliberate
relay restart. Recreate strategy leaves no overlapping relay process during
ordinary upgrades; durable leases also recover after unexpected interruption.
Its process startup checks database permissions, but a Running/Ready Pod alone
does not establish delivery health. Verify one bounded attributed canary through
the receiver and inspect its durable receipt before enabling general submission.

Set `investigationEnabled: true` separately to expose the operator action. For
rollback set it false first; the relay can continue handling already accepted
work. Set `relay.enabled: false` to render its removal, then follow the deployment's
pruning policy to stop delivery. Neither flag deletes the outbox or receiver
receipts. Keep the upgraded receiver stopped before downgrading to an older
SQLite schema reader; never restore a stale queue over accepted live work.

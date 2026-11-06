# Operate Convergence

Keep the deployment bundle, repository revision, image tag/digest, selected site,
context/namespace or Compose project, and test results in a private handover.
Store credentials separately from tickets, chat and Git. A bundle is configuration,
not a backup of the database or telemetry.

## Connect an existing NetClaw HUD

Start with a working NetClaw installation. Discover its selected runtime and
active environment using [environment layout](../ENV-AND-LAYOUT.md); OpenClaw and
Hermes do not necessarily read the same files. Preserve existing model credentials
and runtime selection.

Merge the bundle's `hud.env` assignments into the active HUD environment with
`scripts/write-env.py`; read its `--help` and parse dotenv as data. Do not source
the file as shell code or replace the whole runtime environment. The important
settings are `CONVERGENCE_API_URL`, `CONVERGENCE_API_TOKEN`, `PROMETHEUS_URL` and
`ALERTMANAGER_URL`. Reconcile any older `HOME_API_*` or `NETWORK_GUARDIAN_*` aliases.

For Docker on the same host, the generated URLs already match the chosen ports.
For K3s, initial access can use these **separate, long-running terminals on the
NetClaw host** (substitute your context, namespace and release):

```bash
kubectl --context "$context" -n "$namespace" port-forward --address 127.0.0.1 "service/$release-api" 3080:3000
kubectl --context "$context" -n "$namespace" port-forward --address 127.0.0.1 "service/$release-metrics" 8428:8428
kubectl --context "$context" -n "$namespace" port-forward --address 127.0.0.1 "service/$release-alertmanager" 9093:9093
```

The three generated URLs assume those ports. Port-forwards stop when their process
or target pod exits. For permanent operation, provide supervised forwards or an
approved private service route with appropriate authentication and access controls.
Raw metrics/log/collector endpoints have no end-user authentication in this
profile; they should remain on trusted networks.

Restart only the HUD service that owns the changed environment. Open its actual
frontend URL, select **Convergence**, and verify the configured site. Test both
frontend and backend access from the intended client. An API health response alone
does not prove that the browser, Canvas chat or chosen model works.

## Add telemetry one source at a time

Use the [telemetry architecture](../CONVERGENCE-TELEMETRY-ARCHITECTURE.md) and the
selected [adapter's instructions](../../deploy/convergence/adapters/). Record the
device, site label, collector, credential source, expected interval and retention.
Validate a recent sample before enabling dependent alerts or dashboards.

- **Metrics:** add selected jobs to `victoria.agent.extraScrapeConfigs` in K3s
  `values.json`, or to the Docker bundle's `prometheus.yml`.
- **OTLP:** K3s exposes `<release>-collector` on ports 4317/4318 inside its namespace.
  The default collector sends logs to VictoriaLogs and metrics to VictoriaMetrics.
  Loki is available for integrations needing its query API, but no device log
  source is automatically wired to it.
- **Syslog/SNMP/wireless:** configure only supported devices and read credentials.
  Inventory generators may target legacy files; stage their output and merge into
  the actual bundle/chart configuration instead of editing unused examples.
- **IDS:** follow the [IDS runbook](../runbooks/convergence-ids.md). Select a capture
  host and verified mirror/TAP path, then verify ingestion, counters, lag and actual
  VLAN coverage. Kubernetes scheduling does not create packet visibility.

Keep absent feeds explicitly unknown. A zero alert count cannot establish that
collection is working. Review historical adapter examples for site-specific
addresses before enabling them.

## Enable investigations separately

The core install does not configure model hooks or outbound notifications.
For investigations, configure the [alert receiver](../../services/alert-receiver/README.md),
matching receiver/API tokens, the selected runtime and a bounded Guardian policy.
Start in observation mode. Verify one labeled synthetic alert reaches exactly the
intended case database before enabling automatic investigations.

The Docker bundle includes `receiver.env` and an observation-only policy as
starting points; the receiver itself is not started by the core helper. Linux
systemd templates require the actual account, checkout, virtualenv and writable
paths. macOS and WSL2 need appropriate process supervision; Linux service files
are not portable installers. See the setup skill's
[integration reference](../../workspace/skills/convergence-setup/references/integrations.md)
and [acceptance checklist](../../workspace/skills/convergence-setup/references/acceptance.md).

## Reuse existing Grafana

The simplest shared setup adds a datasource to the existing Grafana:

| Type | URL from Grafana inside the same cluster |
|---|---|
| Prometheus | `http://RELEASE-metrics.NAMESPACE.svc.cluster.local:8428` |
| Loki | `http://RELEASE-loki.NAMESPACE.svc.cluster.local:3100` |
| VictoriaLogs | `http://RELEASE-logs.NAMESPACE.svc.cluster.local:9428` |

Replace `RELEASE` and `NAMESPACE`. VictoriaLogs needs its Grafana datasource plugin;
Prometheus and Loki use built-in plugins. Grafana outside the cluster needs a
private reachable route instead of cluster DNS. Use unique datasource names/UIDs
per release and preserve the existing default datasource.

For GitOps-managed Grafana, the Victoria chart can emit a provisioning bundle:
set `victoria.sharedGrafana.enabled: true` and
`victoria.sharedGrafana.registrationMode: external` in the Convergence values.
Render it with `helm template`, then use
[`merge-grafana-gitops.py`](../../deploy/helm/scripts/merge-grafana-gitops.py) to merge
into the **shared Grafana owner's local manifests**. Read `--help`, install PyYAML
in a dedicated virtualenv, review the diff and deploy through that owner's normal
workflow. External mode makes no writes to the shared namespace.

Existing network/security dashboards may contain `site="home"` or expect feeds
not yet configured. Review datasource and site filters before importing them;
the fresh-install health dashboard is intentionally limited to available services.
Do not enable the chart's cross-namespace registrar on an existing GitOps-managed
Grafana merely to bypass its normal ownership.

## Back up and test recovery

Back up both private configuration and data. Keep an encrypted off-host copy of
the bundle/Secret and the exact release values and image version. PostgreSQL
contains cases and case history; telemetry stores and Grafana have separate PVCs
or Docker volumes. An enabled alert receiver also has a local SQLite queue/state
that needs its own consistent backup.

Example **K3s PostgreSQL logical backup**, after setting the same target variables
used for installation:

```bash
umask 077
backup_dir="$HOME/convergence-backups"
mkdir -p "$backup_dir"
backup_file="$backup_dir/cases-$(date -u +%Y%m%dT%H%M%SZ).dump"
kubectl --context "$context" -n "$namespace" exec "statefulset/$release-postgres" -- \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup_file"
test -s "$backup_file"
```

For **Docker**, from the checkout root, set the bundle/project actually installed
and use the same private backup directory/file variables above:

```bash
project="convergence-trial"     # your recorded Compose project
bundle="$HOME/.local/share/convergence/trial"
docker compose -p "$project" --env-file "$bundle/compose.env" \
  -f deploy/convergence/docker-compose.yml -f "$bundle/compose.override.json" \
  exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup_file"
test -s "$backup_file"
```

The quotes ensure variables are expanded inside the database container. Keep
command output private; a nonempty file is not proof that restoration works.

Restore into a **separate test release/database** first. On the test PostgreSQL
container, create an empty database, then pipe the dump through `pg_restore
--exit-on-error --no-owner -U USER -d RESTORE_DATABASE`. Read back known case IDs
and history. Never test restoration over the live case database. Full recovery
also requires the matching Secret and the application's expected schema/version.

Use storage-aware snapshots or each store's backup mechanism for telemetry and
Grafana. Do not copy a live PostgreSQL data directory as a logical backup.
Helm/PVC retention and node-local storage are not off-host disaster recovery.

## Upgrade and roll back

Before upgrade, save the current revision, values, credentials and a tested backup.
Build a new revision-tagged API image and deliver it to all eligible nodes. For
K3s, edit `api.image` in the existing bundle's `values.json`, rebuild chart
dependencies, then run the same `k3s.py install --bundle` command. It performs a
Helm upgrade while preserving credentials. Re-run `verify.py`, including the
previous canary persistence check. `deployment.json` records the initial bundle
identity; record subsequent image/values changes in your operations history.

For Docker, keep the same bundle/project, update the checkout to the selected
revision and use `setup.py run --action validate`, then `--action start` to rebuild
and reconcile that project. Do not regenerate its passwords.

K3s release history and rollback:

```bash
helm --kube-context "$context" -n "$namespace" history "$release"
# Choose a known previous revision after checking schema compatibility:
helm --kube-context "$context" -n "$namespace" rollback "$release" REVISION --wait
```

A Helm rollback reverts workloads/configuration, **not database contents or schema
migrations**. Review application compatibility and recovery before downgrading.
For Docker, restore the previous compatible code/image and retain the same bundle
and volumes. Preserve the failed state long enough to diagnose it.

## Remove services while retaining data

For the Docker trial, `setup.py run --action stop` retains containers and volumes.
To remove its containers, from the checkout root with your recorded variables:

```bash
docker compose -p "$project" --env-file "$bundle/compose.env" \
  -f deploy/convergence/docker-compose.yml -f "$bundle/compose.override.json" down
```

Omit `--volumes` and keep the bundle for resuming/recovery. These backup/removal
commands target the explicitly named project; use the helper for starts/upgrades
so conflicting shell values cannot change the generated configuration.

For K3s:

```bash
helm --kube-context "$context" -n "$namespace" uninstall "$release"
kubectl --context "$context" -n "$namespace" get pvc,secrets
```

StatefulSet data claims and the separately created credentials Secret are retained.
If you ran `helm test`, its single completed smoke pod is retained for log inspection
until the next test; Helm hooks are not ordinary release resources.
Verify this before planning reuse. Do not delete the namespace: namespace deletion
also deletes its PVCs/Secrets, and the storage reclaim policy may destroy the data.
Permanent data removal is a separate, explicit decision after recovery is verified.

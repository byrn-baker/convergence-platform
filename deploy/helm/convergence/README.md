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

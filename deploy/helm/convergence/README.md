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

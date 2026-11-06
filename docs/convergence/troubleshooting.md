# Convergence installation troubleshooting

Start with the same bundle, context/namespace or Compose project used to install.
Do not create new passwords, delete volumes or switch contexts as a generic fix.
Keep logs private: configuration and request errors can contain sensitive data.

| Symptom | Check | Next action |
|---|---|---|
| Docker server unavailable | `docker version`, `docker context show` | Start local Engine/Desktop and select its local context |
| Remote Docker endpoint rejected | `DOCKER_HOST`, `DOCKER_CONTEXT`, context inspection | Run the checkout/helper on the daemon host or use local Desktop; remote bind mounts are not supported by this trial |
| Port already allocated | Selected API/metrics/Grafana ports and existing services | For a new trial choose unused ports and a new bundle; preserve existing deployments |
| Compose configuration rejected | `docker compose version`, `run --action validate` | Upgrade Compose v2; check paths and generated files without printing resolved secrets |
| File permission/bind-mount error | Bundle below Linux/macOS home; Docker Desktop file sharing | Use an owner-controlled local filesystem; WSL2 files belong under Linux `$HOME` |
| K3s preflight cannot connect | Exact context, kubeconfig, cluster reachability and RBAC | Correct access; the helper deliberately does not fall back to another context |
| PVC Pending | `kubectl --context CONTEXT -n NAMESPACE describe pvc CLAIM` | Check storage class, provisioner and disk capacity; do not replace a bound claim casually |
| ImagePullBackOff | Pod events, image reference, registry credentials, node architecture | Import onto every eligible node or supply a reachable registry and pull Secret |
| Pod Pending | Pod events, available CPU/RAM, node selectors/taints | Provide capacity or correct placement; a valid chart does not guarantee scheduling |
| Secret mismatch | Bundle identity and matching cluster Secret | Restore the original bundle/credentials; changing a Secret does not change PostgreSQL's existing password |
| Database unavailable | PostgreSQL readiness, API logs, capacity and matching credentials | Diagnose the existing database; keep data and backups intact |
| API healthy but Grafana query empty | Wait a scrape interval; query API `probe_success` | Check vmagent/Prometheus targets, site labels and service DNS |
| Grafana login fails after editing Secret | Existing Grafana database | Initial admin environment variables do not reset a password already stored in Grafana; use its password recovery procedure |
| Flow demo or local-host setup has no records | Selected demo/host bundle, site, explicit interface, exporter expiry and collector/shipper health | Follow the [home-lab checks](home-lab.md); installing the core alone does not collect traffic |
| Network/IDS views blank | Was that source configured? Freshness, sensor capture and delivery | Verify the source separately; core health never proves network coverage |
| HUD disconnected but stack checks pass | HUD's active env, URL from HUD host, token and runtime | `127.0.0.1` refers to the HUD host; repair its route and restart only the affected service |
| K3s port-forward stops | Forward process, target pod restart, local port | Restart the forward; use supervised/private access for permanent operation |
| Local disk filling | Docker volumes or K3s PVCs, container images, collector queues, retention | Measure each store; tune retention/capacity and repair blocked shipping before deleting evidence |

For Kubernetes, collect bounded diagnostics against your explicit target:

```bash
kubectl --context "$context" -n "$namespace" get pods,pvc
kubectl --context "$context" -n "$namespace" get events --sort-by=.lastTimestamp
kubectl --context "$context" -n "$namespace" logs "deployment/$release-api" --tail=100
```

For Docker, use `setup.py run --bundle "$bundle" --action status` and the matching
project/file/env set with `logs --tail=100 SERVICE`. Do not use a bare Compose
command from a different directory.

When reporting a problem, include OS/architecture, Docker/Compose or K3s/Helm
versions, checkout revision, failing check name and redacted bounded logs. Exclude
`secret.json`, `.env` contents, API keys, kubeconfig credentials and full resolved
Compose output. State whether it is a fresh install, restart, upgrade or migration.

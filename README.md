# Convergence

Network observability for a home lab or small site: flow and device telemetry,
retained metrics and logs, alerting, an evidence API, and optional passive IDS and
hypervisor-side VM observation. NetClaw connects to it through the read-only
`convergence-read` integration; Convergence itself runs and is operated separately.

Convergence grew inside the [netclaw-convergence](https://github.com/byrn-baker/netclaw-convergence)
fork and now lives here; its design history is in [specs/](specs).

## Ways to run it

| Path | For | Start here |
|---|---|---|
| Docker trial | One Linux machine, no special hardware | [docs/convergence/home-lab.md](docs/convergence/home-lab.md) |
| Existing K3s cluster | Persistent install with Helm, or via Argo CD | [docs/convergence/k3s.md](docs/convergence/k3s.md) |
| New K3s cluster on Proxmox VE | Build the cluster first, then install | [docs/convergence/setup/cluster.md](docs/convergence/setup/cluster.md) |

VM Security findings can be tried without a hypervisor; see [docs/convergence/vm-security.md](docs/convergence/vm-security.md).

Alerts become cases through the alert receiver on the NetClaw host. Optionally,
NetClaw investigates admitted cases with a restricted agent and records its findings
on them: see [docs/convergence/setup/netclaw.md](docs/convergence/setup/netclaw.md). On a NetClaw
Border, the investigation can run in its own scoped member: see
[docs/convergence/setup/investigator.md](docs/convergence/setup/investigator.md).
The pfSense and UniFi MCP servers investigations read the firewall and Wi-Fi with
are installed on that host by [docs/convergence/setup/network-mcps.md](docs/convergence/setup/network-mcps.md).
NetClaw's token and cost metrics come from an exporter on the same host: see
[docs/convergence/setup/agent-metrics.md](docs/convergence/setup/agent-metrics.md).

NetClaw's `convergence-setup` skill can do any of these for you, following the
[setup procedures](docs/convergence/setup/): it discovers what it can, asks for the rest, shows
the plan before changing anything, and verifies each layer.

## Layout

```text
ui/convergence-api/        API and dashboards
ui/convergence-hud/        Browser views for NetClaw HUD integration
services/                  IDS ingest, alert receiver, agent metrics (NetClaw token exporter), Proxmox observer/mirror/shipper
deploy/convergence/        Docker trial (Compose), adapters, Grafana, flow demo, host agent, IDS sensor
deploy/helm/               Helm charts (convergence, victoria-stack), tooling and tests
deploy/cluster/            Proxmox VM provisioning (proxmox.py) and K3s Ansible playbooks
deploy/argocd/             Argo CD install
setup/                     Setup helpers (Docker bundle, K3s/Argo CD, receiver, NetClaw, investigator member, pfSense/UniFi MCPs, agent metrics, verification, flows)
integrations/netclaw/      Skills installed by setup/netclaw.py (alert-triage, convergence-isp-report); investigator.json, the investigator member spec
scripts/                   Telemetry inventory setup and rendering (scripts/lib: inventory import)
docs/  specs/  tests/
```

## Tests

```bash
python3 -m pytest tests/unit deploy/helm/tests
(cd services/ids-ingest && python3 -m pytest)        # needs services/ids-ingest/requirements.txt
(cd services/agent-metrics && python3 -m pytest)     # services/agent-metrics/requirements.txt for compressed events
NETCLAW_DIR=/path/to/netclaw bash tests/convergence-host/run-tests.sh
(cd ui/convergence-hud && npm ci && npm test)
```

## Images

Tagged releases publish `ghcr.io/byrn-baker/convergence-api` and
`ghcr.io/byrn-baker/convergence-ids-ingest` for amd64 and arm64. To build locally
instead, run `ui/convergence-api/build.sh`; the K3s guide shows how to load a local
image into a cluster without a registry.

## License

The platform uses the [MIT License](LICENSE). The imported HUD sources and
`scripts/write-env.py` retain Apache-2.0 licensing. [Notice](NOTICE)

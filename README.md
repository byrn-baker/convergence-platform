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
| New K3s cluster on Proxmox VE | Build the cluster first, then install | [deploy/cluster](deploy/cluster), [workspace/skills/convergence-setup/references/cluster.md](workspace/skills/convergence-setup/references/cluster.md) |

The [`convergence-setup`](workspace/skills/convergence-setup/SKILL.md) skill lets
NetClaw do any of these for you: it discovers what it can, asks for the rest, shows
the plan before changing anything, and verifies each layer.

## Layout

```text
ui/convergence-api/        API and dashboards
services/                  IDS ingest, alert receiver, Proxmox observer/mirror/shipper
deploy/convergence/        Docker trial (Compose), adapters, Grafana, flow demo, host agent, IDS sensor
deploy/helm/               Helm charts (convergence, victoria-stack), tooling and tests
deploy/cluster/            Proxmox VM provisioning (proxmox.py) and K3s Ansible playbooks
deploy/argocd/             Argo CD install
workspace/skills/          NetClaw convergence-setup skill
docs/  specs/  tests/
```

## Tests

```bash
python3 -m pytest tests/unit deploy/helm/tests
(cd services/ids-ingest && python3 -m pytest)        # needs services/ids-ingest/requirements.txt
NETCLAW_DIR=/path/to/netclaw bash tests/convergence-host/run-tests.sh
```

## Images

Tagged releases publish `ghcr.io/byrn-baker/convergence-api` and
`ghcr.io/byrn-baker/convergence-ids-ingest` for amd64 and arm64. To build locally
instead, run `ui/convergence-api/build.sh`; the K3s guide shows how to load a local
image into a cluster without a registry.

## License

MIT, see [LICENSE](LICENSE). `scripts/write-env.py` comes from NetClaw and keeps its
Apache-2.0 license; see [NOTICE](NOTICE).

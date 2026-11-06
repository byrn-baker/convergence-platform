# Convergence

Network observability for a home lab or small site: flow and device telemetry,
retained metrics and logs, alerting, an evidence API, and optional passive IDS and
hypervisor-side VM observation. NetClaw connects to it through the read-only
`convergence-read` integration; Convergence itself runs and is operated separately.

> **Status:** private while the split from `byrn-baker/netclaw-convergence` is
> reviewed. See [Before publication](#before-publication).

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

## Before publication

- Generalize home-specific content still in runbooks, the collector config
  (`deploy/convergence/otel/`), Kustomize overlays and test fixtures.
- Replace `ui/convergence-api/build.sh` (builds a retired image name).
- Publish images and charts from CI so NetClaw can pin a release.
- Choose the repository license (see below).
- Move the NetClaw HUD tests in `tests/convergence-home` and
  `tests/convergence-host-status` to NetClaw.

## License

Not settled yet; decide before publication. `ui/convergence-api/` is MIT (see its
[LICENSE](ui/convergence-api/LICENSE) and [NOTICE](ui/convergence-api/NOTICE.md)).
Everything else was developed inside the Apache-2.0 NetClaw fork, and the vendored
`scripts/write-env.py` is NetClaw's (Apache-2.0).

# Spec 1002: Deployable Observability Stack

**Status**: Complete (Phases 3–5, 7–8 shipped)  
**Mission**: Installable metrics/logs collection that works on a fresh host without the pilot k3s-observability-stack  
**Extracted from**: `080-convergence` US3, US6 (Phase 8), Phase 3–5, 7

## What this is

The Docker Compose and K3s packaging that gives an operator a working
Prometheus + Alertmanager + Blackbox + OTel + Loki + VictoriaMetrics/Logs +
Grafana stack from a single `docker compose up`. Includes the installer
profile, setup wizard, and catalog integration.

## Scope (in)

- `docker-compose.yml` (minimal: Postgres, Prometheus, AM, Blackbox, convergence-api)
- `docker-compose.full.yml` (overlay: Loki, VM, Grafana, promtail, OTel, VLogs, speedtest)
- K3s kustomize base + overlays (`deploy/convergence/k8s/`)
- Installer profile `convergence` in catalog.sh
- `install-steps.sh` component installs
- `setup.sh` adapter prompts + deploy mode selection
- `.env.example` with all keys documented
- `render-config.sh` for alertmanager URL templating
- Smoke tests (`smoke-docker.sh`, `smoke-k8s.sh`)
- `DEPRECATION-PILOT.md` migration path

## Scope (out)

- What gets collected (that's 1003/1007)
- What the dashboards show (that's 1006)
- What happens when alerts fire (that's 1001)
- The HUD UI (that's 1005)

## Key files

| Path | Role |
|------|------|
| `deploy/convergence/docker-compose.yml` | Minimal stack |
| `deploy/convergence/docker-compose.full.yml` | Full overlay |
| `deploy/convergence/k8s/` | K3s manifests |
| `scripts/lib/catalog.sh` | Component + profile registry |
| `scripts/lib/install-steps.sh` | Install logic |
| `config/convergence.example.yaml` | Adapter + telemetry config |

## Functional requirements (from 080)

FR-004, FR-010, FR-011

## Success criteria

- SC-005: Docker minimal reaches healthy convergence-api + Prometheus without external repos
- SC-006: `.env.example` documents every env key with comments

## Tasks (all complete)

T030–T034 (Phase 3), T040–T042 (Phase 4), T050–T058 (Phase 5),
T070–T073 (Phase 7), T080–T095 (Phase 8).

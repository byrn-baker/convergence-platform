# Research: Convergence Architecture Review

## Goal

Review and restructure the "Convergence" feature set (currently spec 080) into
properly scoped, mission-aligned specs. The mission: **NetClaw as an autonomous
NOC that monitors and investigates a network.**

## Problem Statement

Spec 080 accreted 12 phases and 140 tasks spanning 5+ distinct concerns:
- HUD UI work (Three.js, Home tab, triage panel)
- Deployable observability stack (Docker/K8s)
- Alert investigation pipeline (receiver → policy → delegation → diary)
- Telemetry setup (inventory → SNMP/syslog → dashboards)
- SuzieQ state-history plane (scale tier)
- Grafana board suite curation
- OTel convergence hub

These need to be broken into separate specs, each with a clear deliverable
that maps to "running and monitoring a network like a NOC should."

## Scope of This Review

1. **Document the current state** — what's actually running, what's spec-only
2. **Identify congruence** — what directly serves the NOC mission vs what sprawled
3. **Propose new spec structure** (1000-series for fork-local work)
4. **Update the convergence README** with current model assignments (spec 118 work)
5. **Validate spec 118** (iN2N Ollama Cloud) as a proper upstream contribution

## Key Questions

- What components of spec 080 are actually running in production today?
- What's in the spec but never implemented / abandoned?
- What's implemented but doesn't serve "monitor and investigate a network"?
- What belongs in the upstream project vs what's fork-local?
- How do the model assignments (spec 118) get documented in the convergence story?

## Repos to Review

| Repo | Path | Relevance |
|------|------|-----------|
| netclaw | `/home/ubuntu/netclaw` | Alert receiver, skills, iN2N members, convergence deploy |
| k3s-observability-stack | `/home/ubuntu/k3s-observability-stack` | Production OBS (what's *actually* running) |
| localedgedatacenter_page | `/home/ubuntu/localedgedatacenter_page` | Marketing (out of scope but referenced) |

## Current System State (from spec 118 work)

### iN2N Risk Architecture (byrns-risk)
- **Border**: `anthropic/claude-sonnet-5` (fallback: `ollama/deepseek-v4-flash:cloud`)
- **Alert triage**: `ollama/deepseek-v4-flash:cloud`
- **Members (active)**: pyats (nemotron-3-super), cml (qwen3.5:27b), guardian-claw (qwen3.5:27b), viz (gemma4:31b), secops (nemotron-3-nano:30b)
- **Members (cold/on-demand)**: nautobot, batfish, suzieq, github (all nemotron-3-nano:30b)
- **ollama-mcp**: Removed (replaced by iN2N member delegation)

### Alert Pipeline
- Alertmanager → alert-receiver (port 8099) → OpenClaw gateway `/hooks/alert`
- Gateway starts alert agent session → reads alert-triage skill → delegates to guardian-claw member
- Guardian-claw investigates (Prometheus, pfSense, pyATS, Grafana, RAG)
- Results posted to Discord + Network Guardian dashboard

### Observability (production, on k3s-observability-stack)
- Prometheus, VictoriaMetrics, Alertmanager, Grafana
- OTel Collector (syslog), Loki, VictoriaLogs
- UniFi exporter, Blackbox, speedtest CronJob

### Observability (spec 080 convergence deploy, in netclaw repo)
- `deploy/convergence/` — Docker Compose + K8s manifests
- Status: partially implemented, intended for upstream/lab use

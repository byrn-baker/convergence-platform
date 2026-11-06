# Convergence Troubleshooting

Quick reference for diagnosing and recovering the Convergence stack.
All commands assume you're in the repo root.

## Service health checks

```bash
# All services status
docker compose -f deploy/convergence/docker-compose.yml \
  -f deploy/convergence/docker-compose.full.yml \
  --env-file deploy/convergence/.env ps

# convergence-api
curl -s http://127.0.0.1:3080/healthz | python3 -m json.tool

# Prometheus targets
curl -s http://127.0.0.1:9090/api/v1/targets | \
  python3 -c "import sys,json; d=json.load(sys.stdin); \
  [print(f'{t[\"labels\"][\"job\"]:30} {t[\"health\"]}') for t in d['data']['activeTargets']]"

# OTel Collector
curl -s http://127.0.0.1:13133/  # health endpoint
curl -s http://127.0.0.1:8888/metrics | grep otelcol_receiver_accepted | head -5

# Loki readiness (needs ~15s after start before reporting ready)
curl -s http://127.0.0.1:3100/ready

# VictoriaLogs
curl -s http://127.0.0.1:9428/health

# Alert-receiver (host service)
curl -s http://127.0.0.1:8099/healthz
curl -s http://127.0.0.1:8099/policy/status  # investigation policy state

# Grafana
curl -s http://127.0.0.1:3300/api/health
```

## Common failures and fixes

### convergence-api unhealthy

**Symptom**: `healthz` returns error or container restarts.

```bash
docker compose -f deploy/convergence/docker-compose.yml \
  --env-file deploy/convergence/.env logs convergence-api --tail 50

# Usually: Postgres connection failed, or bad JWT_SECRET/API_KEYS format
# Fix:
docker compose -f deploy/convergence/docker-compose.yml \
  --env-file deploy/convergence/.env restart convergence-api
```

### No device syslog appearing in Loki

**Symptom**: Grafana Security board log panels empty, but devices are sending.

```bash
# 1. Is the OTel collector receiving?
curl -s http://127.0.0.1:8888/metrics | grep otelcol_receiver_accepted_log_records
# If 0 → devices aren't reaching port 1514. Check firewall, device syslog config.

# 2. Is it exporting to Loki?
curl -s http://127.0.0.1:8888/metrics | grep otelcol_exporter_sent_log_records
# If receiver shows data but exporter is 0 → Loki connection issue.

# 3. Loki has data but Grafana doesn't show it?
# Check the query time range. Syslog uses receive-time stamps — if device clocks
# are wrong, lines might be "in the future" or "6 hours ago" relative to now.
curl -s 'http://127.0.0.1:3100/loki/api/v1/query?query={job="device-syslog"}&limit=5'

# 4. Restart the collector
docker compose -f deploy/convergence/docker-compose.yml \
  -f deploy/convergence/docker-compose.full.yml \
  --env-file deploy/convergence/.env restart otel-collector
```

### No SNMP metrics (device_snmp targets missing)

**Symptom**: Prometheus has no `interface_status` series.

```bash
# 1. Is the OTel collector remote-writing?
curl -s http://127.0.0.1:8888/metrics | grep otelcol_exporter_sent_metric_points

# 2. Does Prometheus accept remote writes?
# It must have --web.enable-remote-write-receiver (check compose command args)

# 3. SNMP community correct?
# The collector reads SNMP_COMMUNITY from the environment. Verify:
docker compose -f deploy/convergence/docker-compose.yml \
  -f deploy/convergence/docker-compose.full.yml \
  --env-file deploy/convergence/.env exec otel-collector env | grep SNMP

# 4. Device reachable?
docker compose -f deploy/convergence/docker-compose.yml \
  -f deploy/convergence/docker-compose.full.yml \
  --env-file deploy/convergence/.env exec otel-collector \
  wget -q -O- http://127.0.0.1:8888/metrics | grep snmp | head

# 5. Re-apply telemetry config
./scripts/convergence-telemetry-apply.sh
```

### Alert-receiver not triggering investigations

**Symptom**: Alerts fire in Alertmanager but no diary events appear.

```bash
# 1. Is Alertmanager reaching the receiver?
docker compose -f deploy/convergence/docker-compose.yml \
  --env-file deploy/convergence/.env logs alertmanager --tail 20 | grep webhook

# 2. Check alert-receiver logs
journalctl --user -u netclaw-alert-receiver --since "1 hour ago" | tail -30

# 3. Check investigation policy
curl -s http://127.0.0.1:8099/policy/status
# If default_tier=T0 and allow_t2 is empty, investigations won't run (by design).
# This is correct behavior — T0 means observe-only.

# 4. Budget exhausted?
curl -s http://127.0.0.1:8099/metrics | grep netclaw_investigation_budget

# 5. Restart
systemctl --user restart netclaw-alert-receiver
```

### Grafana shows "No data" on all panels

**Symptom**: Grafana loads but every panel is empty.

```bash
# 1. Datasource connectivity
curl -s 'http://127.0.0.1:3300/api/datasources' | python3 -m json.tool | grep -A2 '"name"'

# 2. Prometheus has data?
curl -s 'http://127.0.0.1:9090/api/v1/query?query=up' | python3 -c \
  "import sys,json; print(len(json.load(sys.stdin)['data']['result']), 'series')"

# 3. If datasources show wrong URLs, re-provision:
docker compose -f deploy/convergence/docker-compose.yml \
  -f deploy/convergence/docker-compose.full.yml \
  --env-file deploy/convergence/.env restart grafana
```

### Loki reports "Ingester not ready" after start

**Not a bug.** Loki needs ~15s warm-up before reporting ready. If `smoke-docker.sh`
runs immediately after `docker compose up`, this is expected. Wait and retry.

### OTel collector exits with "permission denied" on queue

**Cause**: Named volume created root-owned, collector runs as UID 10001.

```bash
# The otel-queue-init service should fix this automatically. If not:
docker compose -f deploy/convergence/docker-compose.yml \
  -f deploy/convergence/docker-compose.full.yml \
  --env-file deploy/convergence/.env run --rm otel-queue-init
docker compose -f deploy/convergence/docker-compose.yml \
  -f deploy/convergence/docker-compose.full.yml \
  --env-file deploy/convergence/.env restart otel-collector
```

### Loki ruler not producing metrics in Prometheus

**Symptom**: `pfsense:filterlog_blocks:rate5m` missing from Prometheus.

```bash
# 1. Prometheus must accept remote-write (--web.enable-remote-write-receiver)
curl -s http://127.0.0.1:9090/api/v1/status/flags | grep remote-write

# 2. Loki ruler health
docker compose -f deploy/convergence/docker-compose.yml \
  -f deploy/convergence/docker-compose.full.yml \
  --env-file deploy/convergence/.env logs loki --tail 20 | grep -i ruler

# 3. WAL path writable? (must be /loki/ruler-wal on the data volume)
docker compose -f deploy/convergence/docker-compose.yml \
  -f deploy/convergence/docker-compose.full.yml \
  --env-file deploy/convergence/.env exec loki ls -la /loki/ruler-wal/
```

## Restart procedures

### Restart a single service (no data loss)

```bash
docker compose -f deploy/convergence/docker-compose.yml \
  -f deploy/convergence/docker-compose.full.yml \
  --env-file deploy/convergence/.env restart <service-name>
```

### Restart the full stack

```bash
docker compose -f deploy/convergence/docker-compose.yml \
  -f deploy/convergence/docker-compose.full.yml \
  --env-file deploy/convergence/.env down
docker compose -f deploy/convergence/docker-compose.yml \
  -f deploy/convergence/docker-compose.full.yml \
  --env-file deploy/convergence/.env --profile full --profile unifi up -d
```

**Note**: `down` stops containers but preserves volumes. Data survives.
Only `down -v` destroys volumes — **never do this in production**.

### Re-render configs after .env changes

```bash
./deploy/convergence/render-config.sh
docker compose -f deploy/convergence/docker-compose.yml \
  --env-file deploy/convergence/.env restart alertmanager

# For telemetry config changes (SNMP targets, syslog devices):
./scripts/convergence-telemetry-apply.sh
```

### Reload Prometheus rules (no restart needed)

```bash
curl -X POST http://127.0.0.1:9090/-/reload
```

## Validation scripts

```bash
./deploy/convergence/smoke-docker.sh           # Core services
./deploy/convergence/smoke-device-snmp.sh      # Named interfaces in Prom
./deploy/convergence/smoke-log-panels.sh       # Grafana log queries valid
./deploy/convergence/smoke-telemetry-setup.sh  # Wizard round-trip
./deploy/convergence/smoke-suzieq.sh           # State plane (if enabled)
```

## Key ports (host-side)

| Port | Service | Protocol |
|------|---------|----------|
| 1514 | OTel Collector (syslog) | UDP + TCP |
| 3080 | convergence-api | HTTP |
| 3300 | Grafana | HTTP |
| 8099 | alert-receiver (host) | HTTP |
| 8428 | VictoriaMetrics | HTTP |
| 8888 | OTel Collector (metrics) | HTTP |
| 9090 | Prometheus | HTTP |
| 9093 | Alertmanager | HTTP |
| 9115 | Blackbox | HTTP |
| 9428 | VictoriaLogs | HTTP |
| 9899 | UniFi exporter | HTTP |
| 13133 | OTel Collector (health) | HTTP |

## K3s pilot namespace (observability)

The pilot `observability` namespace on the K3s cluster is **scaled to 0 replicas**.
Services and PVCs still exist but nothing is running. The Convergence Docker stack
on this host is the production system.

**If you need to fully decommission the pilot:**
```bash
# WARNING: destroys PVCs and historical data from the pilot era
kubectl delete ns observability
```

**If you need to bring the pilot back temporarily:**
```bash
cd /home/ubuntu/k3s-observability-stack
ansible-playbook -i inventory/hosts.yml site.yml --tags observability
```

The dual-run posture is documented in `deploy/convergence/DEPRECATION-PILOT.md`.

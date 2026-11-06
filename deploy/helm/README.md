# Reusable observability charts

Use the **[K3s installation guide](../../docs/convergence/k3s.md)** for a fresh
Convergence deployment. It covers cluster/storage prerequisites, building and
delivering images, private credentials, verification, dashboard access and handover.

| Chart | Purpose |
|---|---|
| [`victoria-stack`](victoria-stack/) | Per-project VictoriaMetrics, VictoriaLogs, collection queues and alerting; optional shared Grafana provisioning |
| [`convergence`](convergence/) | Victoria stack plus the Convergence API, PostgreSQL, Loki and optional network adapters/private Grafana |

Each release owns its data stores and namespaced resources. Reuse shared Grafana
through its existing configuration owner; do not deploy a new Grafana for every
project unless that is intentional. The Convergence fresh-install helper enables
a private Grafana explicitly and supports `--no-grafana`.

```bash
helm repo add victoria-metrics https://victoriametrics.github.io/helm-charts/
helm dependency build deploy/helm/victoria-stack
helm dependency build deploy/helm/convergence
```

A chart's default storage class is not proof that the class exists. The setup
helper requires an explicit class and generates overrides for every persistent
store. No node addresses, registry accounts or home installation credentials are
needed for the fresh path.

[Operations](../../docs/convergence/operations.md) covers reuse, backup and upgrades.
[Historical home setup](HISTORICAL-HOME-SETUP.md), `profiles/convergence-home*.yaml`
and `HOME-CUTOVER.md` describe an existing deployment. They are retained for its
operators and should not be copied as portable installation defaults.

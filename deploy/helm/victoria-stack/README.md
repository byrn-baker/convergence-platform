# Victoria stack per purpose

Install an independent VictoriaMetrics/VictoriaLogs data plane, vmagent, vmalert,
Alertmanager and an OTel collector. One existing Grafana can browse multiple
releases. No Grafana server or cluster-wide operator is installed.

```sh
helm dependency build deploy/helm/victoria-stack
helm upgrade --install my-purpose deploy/helm/victoria-stack \
  --namespace victoria-my-purpose --create-namespace \
  -f deploy/helm/profiles/example-project.yaml --wait --timeout 10m
helm test my-purpose -n victoria-my-purpose --logs
```

Set `sharedGrafana.enabled=true` to register the release with your existing
Grafana; configure its namespace, deployment and provisioning ConfigMaps. The
registrar adds namespace/release-specific sources and dashboard copies without
replacing existing defaults. It needs permission to create narrowly scoped Roles
in the Grafana namespace. Only changed provisioning restarts Grafana.

Tune `metrics.server.retentionPeriod`, `logs.server.retentionPeriod`, their
`persistentVolume` settings, `agent.extraScrapeConfigs`, `alerting.ruleFiles` and
`collector.config` per purpose. Alerts use an empty receiver until you explicitly
configure notification delivery. Each release has independent stores and queues;
StatefulSet claims survive uninstall. Share the UI, not the project stores.

[Full usage, lifecycle and home deployment documentation](https://github.com/byrn-baker/netclaw-convergence/tree/main/deploy/helm).

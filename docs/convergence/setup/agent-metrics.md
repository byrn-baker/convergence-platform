# Agent metrics (NetClaw token and cost)

Convergence's NetClaw dashboard (`convergence-netclaw`), the legacy token and quota
boards and the `NetclawAgentMetricsDown` alert read the `netclaw_model_*` metrics of
the NetClaw host. They come from the agent metrics exporter
([services/agent-metrics](../../../services/agent-metrics/README.md)), which runs on
that host as a systemd user service and is scraped as job `netclaw-openclaw` on port
9110. It reads OpenClaw's state (`~/.openclaw` by default) read-only and needs nothing
from a NetClaw checkout.

## Choose the address it listens on

The exporter has no authentication: it shows agent and model names, token counts and
cost. Bind it to the one address Convergence scrapes, not to every interface.

| Convergence runs as | It scrapes | `--bind` |
|---|---|---|
| K3s (Helm) | the host's LAN address from the cluster, e.g. `192.0.2.10:9110` | that LAN address |
| Docker trial on this host | `host.docker.internal:9110` (job in `prometheus.yml`), which Docker maps to the default bridge gateway | that gateway: `docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}'` (usually `172.17.0.1`) |

The default, `127.0.0.1`, serves local checks only; Convergence cannot reach it and
`NetclawAgentMetricsDown` fires after 15 minutes. `0.0.0.0` works for both but listens
everywhere; use it only behind a host firewall.

For K3s, the release scrapes the host only when its values have the job. `setup/k3s.py
prepare` does not add it; add it to the bundle's `values.json` under
`victoria.agent.extraScrapeConfigs` before installing, or to your profile:

```yaml
victoria:
  agent:
    extraScrapeConfigs:
      - job_name: netclaw-openclaw
        scrape_interval: 30s
        static_configs:
          - targets: ['192.0.2.10:9110']
            labels: {site: home, instance: netclaw}
```

Keep `instance: netclaw`: the dashboards select it.

Then allow the port only from the scrapers. With ufw, for example, from the K3s node
network (`192.0.2.0/24` here) to the bound address:

```bash
sudo ufw allow from 192.0.2.0/24 to 192.0.2.10 port 9110 proto tcp
```

For the Docker trial, allow it from the Convergence Docker network
(`docker network inspect netclaw-convergence -f '{{(index .IPAM.Config 0).Subnet}}'`)
if a firewall filters traffic from containers to the host.

## Plan, install, verify

```bash
python3 setup/agent_metrics.py plan --bind 192.0.2.10
```

`plan` changes nothing. It shows the unit, the scrape target, notes about the address,
and conflicts: NetClaw's old `openclaw-token-exporter` unit still running, another
program on the port, or a missing `--openclaw-home`. It exits 1 when there are
conflicts. Show the plan to the user and wait for approval. Then:

```bash
python3 setup/agent_metrics.py install --bind 192.0.2.10 --start
python3 setup/agent_metrics.py status
```

`install` creates a virtualenv under `~/.local/state/convergence/convergence-agent-metrics/`,
installs `services/agent-metrics/requirements.txt` into it and writes the user unit
`convergence-agent-metrics` with `OPENCLAW_HOME`, `EXPORTER_BIND`, `EXPORTER_PORT`
(`--port`, default 9110) and `NETCLAW_INSTANCE` (`--instance`, default `netclaw`).
`--start` enables and restarts it, so re-running picks up a changed checkout. The
service starts at boot only with lingering enabled (`loginctl enable-linger`); `status`
reports it, along with the old unit and a probe of `/metrics`.

Check that it works, not only that it runs:

- `curl -s http://192.0.2.10:9110/metrics | grep netclaw_model_calls_total` shows your agents and models.
- In Convergence, `up{job="netclaw-openclaw"}` is 1 and the NetClaw dashboard shows recent tokens.
- Logs: `journalctl --user -u convergence-agent-metrics`. A database it cannot read
  (for example, locked) is logged; the exporter then serves the last good read, or
  fails the scrape if it has none, instead of reporting zero usage.

## Switch from NetClaw's old exporter

Older NetClaw installs run `openclaw-token-exporter` (from `scripts/openclaw-metrics`
in the NetClaw checkout) on the same port with the same metrics. `install` refuses
while it runs and never stops it for you. To switch, at a time a short scrape gap is
acceptable:

```bash
python3 setup/agent_metrics.py plan --bind 192.0.2.10        # only the old-unit conflict remains
systemctl --user disable --now openclaw-token-exporter.service
python3 setup/agent_metrics.py install --bind 192.0.2.10 --start
python3 setup/agent_metrics.py status                        # active, metrics ok
```

The job, port, metric names and labels are unchanged, so dashboards and alerts
continue; counters continue from OpenClaw's totals. The old exporter listened on
every interface. If you bind one address now, make sure it is the one in the scrape
job. To go back, run `remove`, then
`systemctl --user enable --now openclaw-token-exporter.service`.

## Quota alerts (optional)

The Helm chart can warn before a metered model provider's weekly cap runs out. The
rules are off by default: nothing is rendered until you enable them in your values
(the bundle's `values.json`, or your profile):

```yaml
agentMetrics:
  quota:
    enabled: true
    modelRegex: 'deepseek.*'        # PromQL regex of the metered models
    weeklyTokenBudget: 200000000    # calibrated input+output tokens per week
    # projectionWarnRatio: 0.8      # optional, defaults shown
    # burnCriticalRatio: 0.9
```

`modelRegex` and `weeklyTokenBudget` are required once `enabled` is true; the render
fails and names the missing setting otherwise. The chart then renders the ConfigMap
`<release>-agent-metrics-rules` with `netclaw-quota.yml`, which vmalert loads beside
the stack's rules (it re-reads rules every minute, so enabling or changing them does
not restart it):

| Rule | Fires when |
|---|---|
| `NetclawQuotaProjectionHigh` (warning) | the week projected from the last day's tokens passes `projectionWarnRatio` of the budget for 30m |
| `NetclawQuotaBurn7dCritical` (critical) | the last 7 days' tokens pass `burnCriticalRatio` of the budget for 15m |

Both carry `service: netclaw` and `investigate: "false"`. The recording rules
`netclaw:quota_tokens_1d`, `netclaw:quota_tokens_7d`,
`netclaw:quota_tokens_weekly_projection` and `netclaw:quota_weekly_token_budget` are
available for dashboards.

Token totals are a proxy for a cap that the provider may measure differently (GPU
time, requests). Calibrate once: read the metered models' 7-day tokens,
`sum(increase({__name__=~"netclaw_model_(input|output)_tokens_total", model=~"deepseek.*"}[7d]))`,
and the matching weekly percentage from the provider; the budget is
`tokens / (percent / 100)`. To turn the alerts off again, set `enabled: false`.
The Docker trial does not load these rules.

## Undo

```bash
python3 setup/agent_metrics.py remove
```

It stops and removes only what `install` recorded in its state directory: the unit,
and the virtualenv if `install` created it. It never touches OpenClaw's state or the
old `openclaw-token-exporter` unit.

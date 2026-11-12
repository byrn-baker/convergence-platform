# scripts/lib

`convergence_telemetry_inventory.py` imports a telemetry inventory (Nautobot, NetBox or YAML);
the telemetry setup scripts in `scripts/` use it.

The NetClaw-fork installer fragments that used to live here (`install-steps.d/`, `catalog.d/`)
were removed: upstream NetClaw's installer does not load fragments, and each component now has a
setup helper in [setup/](../../setup) (for example `setup/agent_metrics.py`, `setup/netclaw.py`,
`setup/k3s.py`).

# scripts/lib

`install-steps.d/` and `catalog.d/` are **legacy**: shell fragments of the NetClaw
fork's installer, kept for installs that still use that path. Convergence is installed
with the helpers in [setup/](../../setup) instead; for example, agent metrics
(`component_install_convergence_agent_metrics`) is now `setup/agent_metrics.py`
([docs](../../docs/convergence/setup/agent-metrics.md)). Do not add to them.

`convergence_telemetry_inventory.py` is current; the telemetry setup scripts in
`scripts/` use it.

# Set up Convergence

Start at [Convergence](../CONVERGENCE.md), then choose:

- **[K3s + Helm](../convergence/k3s.md)** for the recommended persistent stack.
- **[Docker trial](../convergence/docker.md)** for a local API, diary and health dashboard.
- **[Operations](../convergence/operations.md)** to connect NetClaw, add telemetry,
  reuse shared Grafana, back up, upgrade or remove services while retaining data.
- **[Troubleshooting](../convergence/troubleshooting.md)** for prerequisite and runtime failures.
- **[Verification record](../convergence/verification.md)** for tested environments and remaining gaps.

The [convergence-setup skill](../../workspace/skills/convergence-setup/SKILL.md)
uses these same guides and helpers. It discovers existing deployments first,
prepares private credentials, installs only the chosen layers and verifies the
result. Core health, network telemetry, IDS coverage and model investigations are
reported separately.

The older Docker helper commands remain available. Prefer its `run` command for
Compose operations because it isolates the bundle from conflicting shell
variables. The K3s helper uses Helm and an explicit context; legacy Kustomize and
home deployment profiles are reference material, not fresh-install defaults.

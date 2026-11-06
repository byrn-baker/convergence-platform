---
name: convergence-setup
description: Set up NetClaw Convergence on a user's network from a fresh checkout, or complete an existing installation. Discover host and network prerequisites, configure recommended K3s/Helm services or a local Docker trial, connect telemetry and the HUD, and verify Guardian and optional passive IDS/JEV integrations. Use for Convergence installation or onboarding; use alert-triage for individual incidents.
license: Apache-2.0
metadata:
  version: "1.2.0"
  openclaw:
    requires:
      bins: [python3]
---

# Convergence setup

Deliver a verified installation for **this user's network**. Determine what is
already present, finish the requested layers, and state which layers remain
unverified. The core stack running is one milestone; it does not establish IDS
coverage or a working investigator.

## Locate and discover

Resolve the NetClaw checkout from the current directory, the selected runtime's
`NETCLAW_DIR`, or the user's stated path. Verify `deploy/convergence/` and
`scripts/install.sh` exist. If the checkout is absent, obtain the user's intended
repository/revision and clone it into their chosen location. The skill also works
before installation: read this file directly from `workspace/skills/convergence-setup/`.
Run the bundled helper from its actual location, passing the checkout explicitly:

```bash
python3 /path/to/convergence-setup/scripts/setup.py --repo /path/to/netclaw preflight
```

The helper uses only the Python standard library. It reports host resources,
Docker context/availability, listeners and configuration-file presence, without
reading existing secrets or changing services. Read `scripts/runtime-selection.py`
and run it from the checkout to identify the selected runtime before wiring HUD
environment or services. Check host/service/container state using available tools;
unavailable SSH, sudo or Kubernetes access is a specific prerequisite, not proof
that the installation is broken.

Discover or ask only for unresolved choices:

- Deployment host and OS; Docker, existing K3s, or reuse of existing observability.
- Site identity, router/firewall, wireless system, managed switches, VLANs/subnets,
  and which devices and traffic the user wants monitored.
- HUD access: local, selected LAN/subnets, or an existing authenticated proxy.
- Optional IDS capture, Victoria retention, IP context, JEV, and investigations.
  These integrations can be added after the core works.

Keep the user's existing choices. Inspect existing Compose labels, env files and
overlays or Kubernetes resources before selecting a deployment path. Existing
monitoring is a reuse/migration case; never treat it as an empty host. Ask for
missing credentials through a private local file or secret store, not chat.

## Prepare and install the requested layers

Use `docs/CONVERGENCE.md` as the installation entry point. Recommend K3s for a
persistent Victoria stack and Docker for a smaller local trial. Core containers
do not require a model login, a NetClaw runtime installation or network-device
credentials. Qualify the actual OS/architecture; do not infer universal support
from image manifests or a successful Linux test.


State the concrete target: hosts, ports, services being added/reused, network
coverage, and any expected interruption. Proceed with work already authorized by
the setup request. Obtain missing scope or approval only for additional actions
such as a shared switch mirror change, replacing a running service, or public
exposure. Prepare the exact diff and rollback before that decision.

Choose source coverage separately from deployment. For a home user, a laptop trial,
or a lab without managed switches or a hypervisor, read
[Home and lab onboarding](../../../docs/convergence/home-lab.md). Offer an isolated
labeled demo first, an explicitly selected Linux host interface second, and router
exports or mirrored packet sensors only when those capabilities exist. Cisco,
Proxmox and Kubernetes are optional infrastructure, not entry requirements. Keep
demo and live-host modes in separate bundles/sites. Preparation never starts capture.

Read the applicable branch:

- **Local Docker trial or an existing host deployment:** [Docker and host setup](references/docker.md).
- **Recommended K3s/Helm or reuse of an existing monitoring stack:** [K3s and reuse](references/k3s.md).
- **Passive IDS, Victoria storage, firewall flows/top talkers, source/destination context or JEV:**
  [Optional integrations](references/integrations.md), reading only selected sections.

Use the checked-in installer, generated configuration and runbooks. Resolve paths
relative to the checkout and the actual deployment account. Named hosts, VLANs,
IP addresses and resource sizes in a runbook's **deployed example** describe one
installation, not defaults to copy. Optional credentials/services are not
prerequisites for the core; optional features stay unconfigured until selected.

Preserve working credentials, data volumes, local skills, model authentication and
runtime selection. Parse dotenv as data with `scripts/write-env.py` or the existing
dotenv reader; do not source it as shell code or print resolved Compose secrets.
Use complete Compose file/env/profile sets for every update. Apply Kubernetes
changes to the selected context and namespace with persistent storage and images
available on every scheduling node. Keep sensor capture outside an ordinary pod
unless its actual packet-access path has been verified.

## Verify and hand over

Read [Acceptance checks](references/acceptance.md). Finish each requested layer
with observations, not only generated files or running processes:

1. Run `scripts/verify.py --repo "$repo" --bundle "$bundle"` from this skill.
   Core API/database, scoped authentication, metrics and Grafana datasource queries
   must pass. Its HTTP checks are read-only; K3s uses temporary local port-forwards.
   Use `--canary` explicitly for labeled case persistence and K3s OTLP log tests.
   Repeat verification after a restart; preserve the canary record for that check.
2. For a selected flow demo or host source, run `scripts/flows.py check --bundle
   "$bundle"` and inspect matching evidence with the same window. Confirm the HUD
   labels synthetic versus host-only coverage. An empty configured source has no
   retained records; an unconfigured source is not a failed core service. Qualify
   actual Linux interfaces and platforms separately from generated demonstration.
   Selected devices produce fresh, correctly labeled telemetry. Missing telemetry
   remains unknown; a successful API liveness probe is not a network-health test.
3. HUD works from the user's intended subnet/browser, without a Grafana port
   collision. Test the configured chat model through its real authentication path.
4. A marked canary reaches the correct case database; clearance is distinct from
   investigation completion. Validate Guardian separately when requested.
5. Optional integrations pass their own checks, including actual IDS capture
   evidence for the requested VLANs and visible JEV assessment provenance.

Leave automatic IDS investigations in observation mode until the user chooses a
bounded policy after tuning. Guardian's autonomous role remains scoped to its
read tools and case interface; infrastructure installation belongs to this
operator-led workflow, not an alert-triggered investigator.

Provide service URLs, generated file locations, exact deployment command/overlay
set, enabled capabilities, verification results, outstanding prerequisites and
rollback/restart instructions. Save a setup handover in the user's chosen local
operations documentation without credentials; this is installation documentation,
not agent memory. Never claim "ready" for a selected layer that was not verified.

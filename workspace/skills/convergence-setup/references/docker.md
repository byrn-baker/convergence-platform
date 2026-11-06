# Docker and host setup

All repository paths below are relative to the verified checkout. Set `repo` to
that checkout and `bundle` to the chosen private output directory; quote paths.

## Fresh installation

Follow `docs/convergence/docker.md` from the verified checkout. It is the
canonical sequence; do not install a model/runtime just to try the core stack.

1. Inspect host preflight, Docker server/context, existing projects and selected
   ports. Require a local Linux-container daemon. On macOS use Docker Desktop;
   on Windows run inside WSL2 with Docker integration and Linux-home paths.
2. Use the bundled `scripts/setup.py --repo "$repo" prepare` with an explicit
   private output directory, unique project/site and unused ports. Include
   `--grafana --grafana-port PORT` for the trial health dashboard.
3. Use `scripts/setup.py --repo "$repo" run --bundle "$bundle" --action validate`,
   then `--action start`. This isolates Compose interpolation from conflicting
   host environment variables. Do not execute older raw manifest commands when
   host variables may override the bundle. Never print resolved Compose secrets.
4. Run `scripts/verify.py --repo "$repo" --bundle "$bundle"` after initial scrapes.
   Inspect the provisioned dashboard and its datasource query. For the authorized
   installation acceptance test, `--canary` writes/archives a synthetic case.
5. Stop/start the same project and verify again. Record the generated private
   access file location, selected URLs, retained volumes and remaining telemetry
   work. Do not expose services publicly to make local verification pass.

The guide documents exact commands and recovery. Existing bundles keep their
credentials. Avoid the legacy `full` profile for a fresh portable trial: it starts
additional collectors and has platform-specific mounts and example device data.

## Connect the host receiver and HUD

Read `docs/ENV-AND-LAYOUT.md` and `docs/CONVERGENCE.md`. The bundle contains
`receiver.env`, `hud.env` and `investigation-policy.yaml`. Its API token is scoped
to the selected site with the operator role; a separate token authenticates
receiver writes. Keep those files private. `receiver-token` is readable by the
unprivileged Alertmanager container beneath an owner-only bundle directory.

- Install `services/alert-receiver/requirements.txt` in the chosen virtualenv.
  Render `scripts/systemd/netclaw-alert-receiver.service` for the actual user,
  checkout, Python executable and bundle `EnvironmentFile`. Preserve its existing
  write restrictions and add only required data paths. The shipped unit's
  `/home/ubuntu` paths are examples. Use an explicit
  `INVESTIGATION_POLICY_PATH` from the bundle. Receiver defaults remain observe-only;
  hooks and outbound notifications are blank. Limit receiver access to the Docker
  network and the selected collectors, allowing the generated host-gateway callback.
- Merge the selected `hud.env` assignments into the active runtime's env with the
  repo's literal dotenv writer, preserving existing settings. Do not replace the
  runtime env with the snippet. If earlier `HOME_API_*` aliases exist, reconcile
  them with `CONVERGENCE_API_*` and `NETWORK_GUARDIAN_*` so every component uses one
  case database. Add the actual gateway/hook credentials to the receiver only when
  enabling an authorized investigation path.
- Build and run the Visual HUD using the `visual-hud` installer component and
  existing service templates. Discover its configured frontend/backend ports and
  binding controls; verify `ui/netclaw-visual/src/security/local-access.js` before
  changing LAN access. Keep Grafana on a separate available port. Use persistent
  service management; a temporary development process is not the handover.
- Select model/authentication through `docs/MODELS.md` and the active runtime.
  OpenClaw and Hermes have different investigator support; verify what is installed
  rather than provisioning an OpenClaw member silently on a Hermes setup.
- For an OpenClaw Guardian, inspect `scripts/ensure-guardian-claw.py` in dry-run
  mode and `docs/N2N-RISK.md`. Provision/reuse the chosen member and apply
  `deploy/convergence/config/guardian-instructions.md` plus the scoped read tools.
  Verify the member's own credentials and `convergence-case-mcp` tools; a member
  being enrolled does not prove those tools work. Keep the setup skill outside
  Guardian's autonomous tool/skill scope.

## Onboard this network

Use `config/convergence.example.yaml` as a schema, replacing example inventory with
verified devices, or generate inventory using `scripts/convergence-telemetry-setup.py`.
Optional SNMP, syslog and wireless adapters come from `deploy/convergence/README.md`
and the matching adapter README. Select the correct SNMP auth/version and local
community or SNMPv3 credentials; never enable the example `public` community as a
universal default. Confirm vendor API support before declaring an adapter ready.

For a bundle deployment, the active Prometheus config is **bundle/prometheus.yml**.
The telemetry renderer/setup scripts normally target legacy repo files. Inspect
their output options, render to staging, then merge selected jobs/rule files into
the active mounted config. Review Docker mounts to confirm the file being edited
is the file the service reads. Use per-site labels and rules; remove disabled
exporter jobs rather than interpreting missing exporters as failing devices.

Read `references/integrations.md` before adding full observability or IDS overlays.
After adding the selected telemetry, verify a real sample and the expected labels
before enabling alerts. Document the resulting complete Compose command.

## Existing installation

Use existing service names, env files, overlays, volumes and configured ports.
Read `docker compose ls` and container Compose labels to recover the deployment
definition without dumping container environments. `prepare` is a fresh-core
generator, not a migration tool. Do not point a new generated password at an
existing Postgres volume or replace the current configuration with an example.

Prepare targeted diffs and private backups. Apply only required components using
the complete active Compose file/env/profile set. For rollback, restore those
files and the prior image/command, keeping persistent volumes. Avoid `down -v`,
global Docker cleanup, cluster-wide scaling or reinstalling a working runtime.

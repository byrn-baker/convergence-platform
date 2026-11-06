# Research: deployment versus visibility

Baseline a3c68981. Docker setup currently starts six optional-Grafana core services,
without retained flow collection. Existing flow runbook describes a K3s shipper and
an already-running exporter; IDS runbook describes a particular Proxmox/ERSPAN lab.
Those are not accessible defaults. Existing API uses configured exporter IPs and
VictoriaLogs job=netflow/site labels. Existing HUD and case MCP expose summary and
same-window evidence, so an external-source onboarding extension can reuse them.

Primary references inspected 2026-10-30:
- https://github.com/irino/softflowd — software flow export from captured packets.
- https://github.com/netsampler/goflow2 — NetFlow/IPFIX/sFlow collection.
- https://docs.opnsense.org/manual/how-tos/netflow_exporter.html — possible later
  router recipe, not qualified by this work.

A service can only report traffic its source observes. Docker/K3s placement does
not grant whole-LAN packet visibility. Host export and retained metadata do not
qualify packet IDS. Demo data is explicitly labeled and isolated from host mode.

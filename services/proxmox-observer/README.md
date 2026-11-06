# Proxmox passive QEMU observer

`collector.py` is a Python 3 standard-library service for a Linux Proxmox node. By default it inventories every QEMU configuration, including stopped VMs, and observes configured live TAP interfaces. An explicit VM ID range restricts both inventory and capture. It does not inventory LXC containers. The output follows [CONTRACT.md](CONTRACT.md).

The collector exports UUID, VM ID, configured NIC MAC/bridge/VLAN, interface counters and sampled connection metadata. It does not export VM names, guest credentials, application bytes or PCAP files. The only persistent data is the current root-only JSON snapshot. Inventory reads `/etc/pve/qemu-server/*.conf`, `/run/qemu-server/*.pid`, process `comm`, and `/sys/class/net/tapVMIDiN` counters. Historical Proxmox config snapshot sections are ignored.

One nonblocking AF_PACKET socket observes guest egress (TAP receive direction), excluding Linux `PACKET_OUTGOING`. A classic BPF filter permits only discovered TAP ifindices and returns header prefixes: Ethernet + IPv4 header (including IP options) or IPv6 base header, plus TCP bytes through flags or UDP ports. ICMP returns only the IP header. It accepts TCP SYN without ACK and samples UDP/ICMP using Linux kernel random values at 1/64. No application payload is returned by the filter. Socket capture starts only after filter attachment. No promiscuous membership, NIC flags, forwarding, bridge, firewall or guest configuration is changed.

IPv4 fragments, IPv6 extensions, VLAN-tagged Ethernet, non-IP and malformed packets are omitted; tunnel inner traffic is not decoded. Outer UDP tunnel headers may appear as UDP metadata. Initial SYNs do not prove that a connection succeeded. Counters are cumulative kernel counters: TAP RX is guest egress, TAP TX guest ingress. IP addresses are observed claims. Rental allocation is unresolved. Firewall counters are null; there is no enforcement or security verdict.

## Bounds and lifecycle

The service writes every 15 seconds by fsync + atomic rename, mode 0600, within a mode 0700 state directory. Limits are 256 VMs, 1024 NICs, 256 distinct flows per interval, 128 KiB per config, 512 KiB per snapshot, and 2000 recorded sampled packets per second. The socket receive buffer requests 256 KiB (Linux normally doubles its accounting). A 256-packet processing batch keeps shutdown and inventory refresh responsive. Systemd caps the process at 5% of one CPU, 96 MiB RAM, 8 tasks and 32 open files. Saturation can lose evidence and cannot support complete historical reconstruction.

`capture_drops` accumulates kernel socket drops; `events_discarded` accumulates rejected, rate-limited, evicted and identity-invalidated metadata events. Intentional kernel sampling and filter exclusions are described in `limitations`, not counted as drops. Lifecycle queue flushes can lose an unknown number of queued records and are explicitly labeled. CPU throttling or process failure can make the snapshot stale, which the receiver must display. Inventory failure clears the capture allowlist. Invalid/oversized snapshot publication fails closed and the service restarts; the prior snapshot becomes stale.

Each flow retains the VM UUID, config content fingerprint, VM PID, TAP name and ifindex from discovery. Before publication it must match the refreshed binding, or it is discarded. Binding changes close the old socket and discard its queued headers before capture resumes. Config fingerprints are internal only. Changes that occur and revert entirely within a 15-second inventory interval cannot be detected. There is no claim of allocation-level attribution.

## Installation and canary

Review before deployment. Install `collector.py` to `/opt/netclaw-proxmox-observer/collector.py` owned by root, and the supplied unit to `/etc/systemd/system/netclaw-proxmox-observer.service`. The unit uses hostname `%H` as `node`; set an explicit `--node` matching the receiver configuration if required. The runtime requires Linux AF_PACKET, classic BPF ancillary random support, and CAP_NET_RAW. It opens no listening socket and needs no authentication secret.

For a capture canary, replace the unit's `ExecStart` via a systemd drop-in:

```ini
[Service]
ExecStart=
ExecStart=/usr/bin/python3 /opt/netclaw-proxmox-observer/collector.py --node r640-pve --capture-vm 6095
```

`--capture-vm` is repeatable and limits packet capture only: inventory and counters still include every QEMU VM, and the snapshot explicitly labels restricted coverage. Remove these flags to capture all discovered running VM TAPs. The coordinator performs installation and live verification; this repository implementation does not apply remote changes.

The default snapshot is `/var/lib/netclaw-proxmox-observer/snapshot.json`. A separate backend pulls it over the existing strictly pinned SSH connection. Do not grant the collector any backend credential. Stop/disable the systemd service to roll back; no guest or forwarding changes need reversal.

## Local checks

```sh
python3 -m unittest discover -s services/proxmox-observer/tests -v
systemd-analyze verify services/proxmox-observer/netclaw-proxmox-observer.service
```

Tests exercise a separate classic-BPF interpreter against IPv4 options, IPv6, SYN/ACK direction, sampling ratios, rejected fragments/extensions/VLAN, exact header truncation, config privacy, running/stopped inventory, lifecycle mismatches, bounded flow loss and atomic output permissions. Live kernel attach, real TAP direction, service resource use and receiver ingestion require coordinator verification on the target node.

## Restrict collection to customer VM IDs

Use `--vm-id-range 6000:6999` to collect only VM IDs 6000 through 6999, inclusive. The range selects configuration filenames before opening files or applying VM/NIC capacity limits. Out-of-range VM configurations, counters and packet metadata are excluded. New VMs in the selected range are discovered automatically. This is a collection restriction, not a dashboard filter.

Install `netclaw-proxmox-observer-6xxx.conf` as `/etc/systemd/system/netclaw-proxmox-observer.service.d/scope.conf`, run `systemctl daemon-reload`, and restart the observer. Preserve any existing overrides and reconcile conflicting ExecStart settings before installation. The override retains the service resource/capability limits. Validate a fresh snapshot and downstream view before declaring the change complete.

Without the option, the prior all-QEMU behavior is retained. `--capture-vm` further restricts packet capture within the selected inventory range. Published limitations identify the configured range. Existing historical observations are not deleted; security analysis requires current inventory and collector-session identity, so prior out-of-scope records do not become current findings. To restore the previous scope, restore the prior service override and restart; no guest or forwarding configuration changes are involved.

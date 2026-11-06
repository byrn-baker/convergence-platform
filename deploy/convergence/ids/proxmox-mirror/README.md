# Additional Proxmox mirror capture in the existing IDS guest

This is a second raw Ethernet Suricata instance. It does not receive GRE and
does not change the original ERSPAN container, configuration, services or feeds.
The host mirror must deliver to a new isolated NIC in the IDS VM, with no host bridge
or guest IP addresses, DHCP, IPv6 autoconfiguration or default route on that NIC.
Configure this isolation before preparing capture; the MAC guard fails closed
when the selected guest interface has an IP address or the wrong MAC.

Copy these files into `/opt/netclaw-ids-proxmox`, together with the updated
parent `shipper.py`. Create `sensor.env` from the example, using the actual
isolated NIC name and MAC. Provision a distinct sensor identity/token and CA
locally at the stated paths; no credentials belong in the repository. The
receiver must already recognize the additional identity. Keep the root and
credentials root-owned and private.

Review `prepare.sh` and run it as root in the IDS guest. It copies the existing
support files and derives a separate config from the original YAML. It validates
the full original ruleset using the pinned engine and creates only the second
container and units. It never starts either new unit. The configuration uses a
separate AF_PACKET cluster, reduced ring, low detection grouping profile and no
MPM disk cache. A single AF_PACKET worker handles capture and detection, using
TPACKET_V3 without the autofp mode's fallback to V2. The unused Unix command
socket is disabled; rules refresh uses an explicit service restart.
All signatures and existing stream/flow memory limits remain.
The low profile trades more candidate inspection for lower matching memory.
Alert payloads/packets, file extraction and pcap outputs are disabled by replacing
the complete output list with the established EVE metadata types. EVE logs,
durable SQLite cursor state and health files use the separate root.

CPU and memory limits are configurable in `sensor.env`. Passing `-T` alone
does not establish safe concurrent operation. Measure available capacity and
both engines during a monitored pilot. Stop the additional capture if existing
capture health, available memory or packet loss deteriorates. Keep deployment
measurements in private operational records. Do not reduce rule coverage
automatically to fit insufficient capacity. Exit 137 (SIGKILL/OOM) is excluded
from automatic capture restart and requires operator intervention.

After review, explicitly start `netclaw-ids-proxmox-capture.service` and
`netclaw-ids-proxmox-shipper.service`. Enable them for boot only after the pilot.
Inspect `docker stats netclaw-suricata-proxmox`, `journalctl`, new EVE stats
(capture drops, stream/reassembly gaps), durable acknowledgements and the new
sensor heartbeat. `IDS_CAPTURE_CONTAINER` scopes heartbeat to the new container;
the default in the shared shipper remains the original container.

VLAN tracking is disabled only in the new engine because host TAP requests are
untagged while physical uplink replies carry the configured customer VLAN. Live-device tracking is
also disabled for matching across this capture path. Keep the host selector narrow
and ensure unrelated overlapping VLAN traffic is not delivered to the mirror.
Use a known bidirectional flow to verify request/reply reassembly before relying
on alerts. No active capture probe is configured for this passive mirror.

The existing rule updater atomically replaces the shared read-only rules file.
It currently restarts only the original engine. Before production, install this
drop-in at `/etc/systemd/system/netclaw-ids-rules.service.d/proxmox-mirror.conf`
and run `systemctl daemon-reload`:

```ini
[Service]
ExecStartPost=/usr/bin/systemctl start netclaw-ids-proxmox-rules.service
```

The additional oneshot checks a digest of shared rules, config and local rules,
validates changes with the pinned image/caps, and restarts only the new capture.
It does nothing while the new capture is stopped. Run the oneshot once after
the initial pilot start. It briefly stops the new engine before `-T`, avoiding
three concurrent rulesets in guest RAM. A validation failure leaves only the new
capture stopped and fails the hook for operator review. The original feed stays
running. A five-second process check after restart detects immediate startup
failures; EVE stats must still confirm engine initialization and rule loading.
Do not run a second updater.
`rules-status.json` is a read-only symlink to the shared update record; it reports
the rules on disk, so a new-engine restart must be verified separately. Configuration/local.rules
changes require a deliberate recopy/rebuild of the new config.

Rollback: stop/disable the two new units and stop the new container. Preserve
unacknowledged EVE files and cursor state. The original ERSPAN instance continues
independently; host mirror and isolated NIC removal are separate operator steps.

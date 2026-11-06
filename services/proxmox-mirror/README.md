# Proxmox native TC mirror

Python standard-library controller for a bounded set of customer VM interfaces.
Review the deployment-specific identity and interface bindings in the controller
before use. Keep actual inventory, addresses, hardware identifiers and runtime
evidence in private deployment records. The controller never creates bridges or NICs.

Guest egress is cloned at source TAP ingress before firewall/policing. External
returns are cloned only at physical uplink ingress when destination MAC and
the configured customer VLAN match
a running selected VM. There are no destination TAP egress mirrors and no
physical-transmit mirrors. A local selected-VM-to-selected-VM frame therefore
has one copy, from its source TAP. External broadcasts/multicasts, host-local
responses, traffic from unselected local VMs, guest MAC changes/spoofing, other
NICs, stopped VMs, containers, trunks, and other VLANs are outside this coverage.
This is observation before firewall verdict, not proof of delivery.

Existing HTB root qdiscs, ingress qdiscs, firewall configuration, and pref50
policers are preserved. The controller adds `clsact` only when no ingress or
clsact qdisc exists. It never replaces/deletes a qdisc, so an added empty clsact
remains after cleanup. Each filter uses software `skip_hw`, chain 0, pref10,
handle `0x4e430000 + VMID`, a random persistent ownership cookie, a mirred clone,
then `gact continue`. The final action resumes later filters; `pass` would skip
the existing policer. A pre-existing filter at pref10 or earlier causes refusal.

Deployment prerequisites:

- Dedicated capture bridge has only the configured capture TAP, no host IPv4/IPv6 addresses and
  no uplink. Guest capture NIC has no IP and is not used for management/routing.
- Guest capture NIC is promiscuous, has a matching MTU, and is serviced by a separate
  Suricata instance with `vlan.use-for-tracking: false` and a distinct sensor ID.
  TAP egress clones are untagged while uplink return clones can carry the configured customer VLAN.
- Proxmox source configs use only net0, the configured customer bridge and VLAN, direct TAP or the
  standard fwbr/fwln/fwpr topology. Unknown selected-running topology fails closed.
- `tc` supports JSON, matchall, flower, mirred, gact, and action cookies.

Replace the example MAC and pilot placeholder with private deployment values.
Read-only plan (default; no state files created):

```sh
python3 controller.py --target-mac 02:00:00:00:00:01 --capture-vm "$PILOT_VM_ID"
```

Explicit pilot apply and removal:

```sh
python3 controller.py --apply --once --target-mac 02:00:00:00:00:01 --capture-vm "$PILOT_VM_ID"
python3 controller.py --apply --cleanup --target-mac 02:00:00:00:00:01
```

`--apply --once` intentionally leaves rules for inspection and is not lifecycle
supervision. Production must use the daemon. Install the files under
`/opt/netclaw/proxmox-mirror`, the unit under `/etc/systemd/system`, and a root-owned
0600 `/etc/netclaw/proxmox-mirror.env` containing:

```ini
TARGET_MAC=02:00:00:00:00:01
MIRROR_SCOPE="--capture-vm <PILOT_VM_ID>"
```

After validating the pilot, changing `MIRROR_SCOPE=--all-6xxx` and restarting the
unit selects running VMs in 6000–6999. No dynamic configuration can widen this
range. Maximum 256 running/configured selected VMs, two rules each. Commands
time out after 10 seconds and retained output is bounded to 2 MiB. Default
reconcile interval is two seconds; kernel traffic is not sampled or rate-limited
by this controller. Full mirroring adds host/sensor load and can lose clones
under pressure. Do not infer complete capture from rule existence.
Successful status is logged initially and when its counts/mode change. Errors
are logged immediately when they change and at most once per minute while the
same error persists; recovery is logged immediately.

The root-owned 0700 state directory and 0600 journal file persist ownership and
write intent before each add. A lock excludes concurrent apply/cleanup.
Reconciliation binds config hash, QEMU PID/start-time/command hash, target UUID,
target MAC, interface indices, bridge isolation, and host boot ID. Restart,
scope change, or an invalid/missing target removes known owned rules before
rebinding. A stale source ifindex is never used to delete a new device's rules.
Cleanup checks exact handles and cookies, and retains unresolved ownership
collisions for operator inspection. Never delete the state file to recover a
collision: that discards the evidence needed for safe cleanup.

Polling is not an atomic lock against concurrent privileged network/QEMU
changes; guard snapshots are checked before additions and after reconciliation.
An administrator moving a live target without destroying it can expose a short
polling window. Kernel mirred holds the target device reference and invalidates
it on unregister; an installed action cannot silently follow a reused ifindex.
The daemon removes the now-stale actions on its next successful cycle.

Validation:

```sh
python3 -m unittest discover -s services/proxmox-mirror/tests -p 'test_*.py' -v
sudo unshare --net python3 services/proxmox-mirror/tests/kernel_probe.py
```

The kernel probe refuses the initial network namespace and creates only
disposable veths in a fresh namespace. It verifies clone delivery, original
packets reaching an existing policer, preserved HTB/ingress configuration,
MAC/VLAN selection, target deletion/recreation and precise filter cleanup.
It does not validate the Proxmox node's NIC driver or hardware VLAN stripping.

Before expanding beyond the pilot VM, record `tc -j -s qdisc/filter` for pilot TAP and
the physical uplink before/after; verify target ingress sees guest outbound and external return
packets, one local-flow copy per direction, and no nonselected destination MACs.
Confirm physical RX VLAN offload still yields a flower match for the configured customer VLAN.
Check existing policer counters continue advancing and rate caps/firewall
behavior remain intact. Compare IDS alerts, flow pairing, packet loss and CPU.
Restart the pilot and IDS VM and verify cleanup/rebind, then stop the service
and verify only its filters disappear. These node/guest checks are deployment
acceptance criteria, not asserted by the local tests.

Primary implementation references:
[iproute2 mirred parser](https://github.com/iproute2/iproute2/blob/main/tc/m_mirred.c),
[tc action controls](https://man7.org/linux/man-pages/man8/tc-actions.8.html),
[flower matching](https://man7.org/linux/man-pages/man8/tc-flower.8.html), and
[kernel mirred lifecycle](https://github.com/torvalds/linux/blob/master/net/sched/act_mirred.c).

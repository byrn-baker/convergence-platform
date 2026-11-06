# Convergence on an ordinary home network

Start on one Linux computer with Docker Engine. A Cisco switch, Proxmox, Kubernetes,
router credentials and a model subscription are not required. K3s remains the
[recommended persistent deployment](k3s.md); this guide provides a smaller way to
try retained traffic evidence before changing any network equipment.

Choose what you want to observe:

| Path | What you need | What the observations mean |
|---|---|---|
| **Try it** | Linux computer and local Docker Engine | Synthetic demonstration traffic, isolated from your actual network |
| **Monitor this computer** | Linux computer and permission to capture a selected interface | Traffic visible on that interface; not every conversation in your home |
| **Monitor my network** | A compatible router exporting flows, or a sensor with an actual capture path | Traffic visible at those collection points; coverage depends on placement |

Installing a collector does not make other devices' traffic visible. Flow records
provide connection metadata and counters; they do not replace packet capture for
IDS inspection. A healthy installation with no selected source is **not configured**,
not a failed sensor or proof of an attack-free network.

The flow helper currently targets **Linux Docker Engine**. Its collector uses Linux
host networking but binds only to loopback. macOS Docker Desktop and Windows/WSL2
flow collection remain unqualified; do not infer support from the core Docker guide.
See the [qualification record](verification.md) for executed tests and their limits.

## Try retained flow evidence without changing your network

Follow the prerequisites in [Try with Docker](docker.md), then run these commands
from the maintained fork checkout. Choose unused service ports if you already have
an installation. The selected flow port must be free for both UDP collection and loopback TCP
health/metrics, and must differ from the core service ports.

```bash
bundle="$HOME/.local/share/convergence/flow-demo"
helper="workspace/skills/convergence-setup/scripts/setup.py"
flows="workspace/skills/convergence-setup/scripts/flows.py"
python3 "$helper" prepare --output "$bundle" \
  --site demo --project convergence-demo --grafana --flows demo --flow-port 2055
python3 "$helper" run --bundle "$bundle" --action validate
python3 "$helper" run --bundle "$bundle" --action start
python3 workspace/skills/convergence-setup/scripts/verify.py --bundle "$bundle"
python3 "$flows" demo --bundle "$bundle"
python3 "$flows" check --bundle "$bundle"
```

The demo creates a bounded synthetic packet capture and sends it through softflowd,
the actual flow collector, retained storage and authenticated summary/evidence
queries. It does not capture your host interface. The generated addresses and bytes
are demonstration inputs, not observations of your home. Repeating the demo adds
another set of example records; totals need not match the first run.

The flow option adds an archive, collector and shipper to the core Docker services.
Configuration and credentials stay in the private bundle. Demonstration and host
modes are separate bundle identities; use a new bundle, site and project when moving
from the demo to actual traffic. Do not relabel a demo archive as real observations.
Containers, volumes and the network retain that identity. Setup refuses an existing
project owned by another bundle, including retained volumes after containers stop.
Keep the private bundle at its original path and back it up with the data. Recreating
or moving it requires an explicit recovery plan; deleting its manifest is not a
way to adopt an old project. Use an unused flow port for concurrently running
projects, and stop an independently running exporter before reusing its port.

The core Grafana dashboard still reports installation health. It is not a traffic
viewer. To see the traffic rankings in NetClaw, follow
[Connect an existing NetClaw HUD](operations.md#connect-an-existing-netclaw-hud),
using this bundle's `hud.env`. Select this bundle's site in Convergence, then open
its traffic view. It should display **Synthetic demonstration** above the rankings.
The Docker helper does not install a NetClaw runtime, configure a model or start
an investigation. You can verify the retained evidence with `flows.py check`
without any of those components.

Stop and restart only this project:

```bash
python3 "$helper" run --bundle "$bundle" --action stop
python3 "$helper" run --bundle "$bundle" --action start
python3 "$flows" check --bundle "$bundle"
```

Stopping retains the project's data volumes. The API's traffic windows are relative
to the current time, so old demo records can fall outside the selected window even
though retention has not yet removed them.

## Monitor this Linux computer

Create a **new** private bundle with `--flows host`, a different site/project and
unused ports. For example, stop the demo first and then reuse its service ports:

```bash
host_bundle="$HOME/.local/share/convergence/host-flows"
helper="workspace/skills/convergence-setup/scripts/setup.py"
flows="workspace/skills/convergence-setup/scripts/flows.py"
python3 "$helper" prepare --output "$host_bundle" \
  --site workstation --project convergence-workstation --grafana --flows host --flow-port 2055
python3 "$helper" run --bundle "$host_bundle" --action validate
python3 "$helper" run --bundle "$host_bundle" --action start
ip -brief link
python3 "$flows" host-command --bundle "$host_bundle" --interface eth0
```

Replace `eth0` with the interface you intend to observe. `host-command` prints the
exporter command for review; it does not start packet capture. Run that command
only after selecting the correct interface. It uses a software exporter with the
capture permissions needed for that interface (`NET_RAW`, without `NET_ADMIN`),
not a switch mirror or hypervisor.
No routing or firewall changes are made by preparation. The printed `shell_command`
uses the helper to discard conflicting Compose settings. For the reviewed interface,
it runs the equivalent of:

```bash
python3 "$flows" capture --bundle "$host_bundle" --interface eth0
```

This is an explicit foreground capture; stop it with **Ctrl-C**. It disables
promiscuous capture and excludes its own UDP export port from the observed traffic.
You can further restrict collection with `--capture-filter` using a BPF expression,
for example `--capture-filter 'tcp port 443'`. The filter narrows visibility; it does
not identify an application or decrypt the traffic.

Generate ordinary traffic on that interface, allow the exporter to expire its
flows, and then run:

```bash
python3 "$flows" check --bundle "$host_bundle"
```

The HUD should display **Local interface traffic**. An interface on your workstation
usually observes that workstation's traffic; it does not automatically receive
another computer's unicast traffic. A bridge/router interface can have different
coverage, which you must verify explicitly. A VM observes what its virtual interface
receives. A Docker interface does not imply visibility of every host or LAN packet.

Packet capture may require elevated permissions even though only flow metadata is
retained. Do not capture an interface you are not authorized to monitor. Stop the
exporter with the lifecycle described by its printed command, as well as stopping
the bundle when you finish. Stopping storage alone does not stop an independently
started exporter.

## Add broader network coverage later

Keep this local starter receiver bound to loopback. Connecting a router is a
separate receiver/network configuration, with explicit source allowlists and a
private reachable route; do not simply expose its UDP listener to the Internet.

Some home routers can export flow metadata directly. For example, OPNsense provides
an [interface-based NetFlow exporter](https://docs.opnsense.org/manual/how-tos/netflow_exporter.html).
That is an option to evaluate, not a claim that every OPNsense version or router
has been qualified with this starter. Routers also miss conversations that remain
on the same switch or access point without crossing their interfaces.

For packet IDS, use a verified capture path: selected host traffic, a suitable
mirror/TAP, or a virtual network that actually delivers the desired packets. The
existing [IDS runbook](../runbooks/convergence-ids.md) documents a Proxmox/ERSPAN
example; those products are not prerequisites for Convergence itself. Generic IDS
installation and additional router recipes are separate qualification work.

## Capacity and troubleshooting

Use the Docker guide's initial resource allowance, then measure this installation's
containers and volumes. Flow rate and retention determine storage needs; a small
demo's measurements are not a minimum or a busy-network capacity guarantee. The
[verification record](verification.md) identifies measured environments.

The starter archive retains approximately one day of records. Its raw collector
spool rotates three files of up to 16 MiB each; the shipper has a durable queue of
128 batches. These are finite buffers, not a lossless packet archive. Sustained
load or a long shipping outage can overwrite undelivered records; retry/restart
can also repeat observations. The archive's one-day retention bounds time, not
maximum bytes on disk. Monitor volume growth and delivery errors before leaving
host capture running continuously.

| Observation | Meaning and next step |
|---|---|
| Not configured | No exporter is configured for this site; choose a flow path |
| Empty window | No matching retained records in that interval; generate demo traffic or verify the selected live source |
| Archive unavailable | The query failed; totals are unknown, not zero |
| Demo banner on a real setup | Check which bundle/site the HUD uses; keep demo and host archives separate |
| No host flows | Check the selected interface, capture permissions, exporter expiry and loopback receiver; quiet traffic alone does not establish collector failure |
| Busy receiver but no records | Inspect collector decoding and shipper/archive errors; an open UDP port is not ingestion proof |

See [troubleshooting](troubleshooting.md) for private, bounded diagnostics. Keep raw
captures, addresses, credentials and full resolved Compose configuration out of
public issue reports.

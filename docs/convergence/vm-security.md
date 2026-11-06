# VM Security

Advisory findings about virtual machines, from packet metadata sampled outside the
guests on a Proxmox VE node. NetClaw shows them read-only through its Convergence view.

## What it reports

`GET /api/security/vms` (site-scoped API key) returns, per source, the last five
minutes:

| Pattern | Default rule |
|---|---|
| `port_scan` | 20 or more TCP destination ports on one address |
| `network_sweep` | 20 or more destination addresses on one TCP port |
| `protected_network_contact` | a monitored VM reached a configured protected range |

Every finding has severity `review`. `no_patterns_observed` means no pattern in the
sampled, covered traffic, never "safe". `insufficient_evidence` means the source can't
support either statement. Coverage (sampling, drops, snapshot gaps, unobserved VMs) is
returned with every source. The full field contract is
[VM-SECURITY-CONTRACT.md](../../services/ids-ingest/VM-SECURITY-CONTRACT.md); the API
adds two fields:

- `synthetic` (top level): `true` only for the demonstration below.
- `kind` (per source): `proxmox` for a live observer, `synthetic` for the demonstration.

## Try it without a hypervisor

The API can serve a labelled synthetic assessment: one finding of each pattern, a quiet
VM, a stopped VM and a stale source. No traffic is observed and nothing is stored.

- Docker trial: `python3 setup/setup.py --repo . prepare ... --vm-security-demo`
- Helm: set `api.env.VM_SECURITY_DEMO: "1"` in the release values.

Use a separate site or bundle for the demonstration, as with the flow demo.

## Live source

1. Install the observer on the Proxmox node
   ([services/proxmox-observer](../../services/proxmox-observer/README.md)) and the
   shipper that sends its snapshots to `ids-ingest`.
2. Give the API the status reader: `IDS_STATUS_URL` and `IDS_STATUS_TOKEN`.
3. Optionally configure protected ranges and allowlisted tuples (see the contract).

Proxmox VE is the only hypervisor with an observer today; elsewhere the view reports
"not configured".

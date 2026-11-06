# New K3s cluster on Proxmox VE, with optional Argo CD

Builds the cluster first, then installs Convergence into it with
[K3s and reuse](k3s.md). Every step below changes real infrastructure: state the
plan and get the user's go-ahead before `apply`, `site.yml` or `destroy`.

## Proxmox access

- **Discovery:** the [mcp-proxmox](https://github.com/gilby125/mcp-proxmox) server
  (`proxmox_get_nodes`, `proxmox_get_storage`, `proxmox_get_vms`,
  `proxmox_list_templates`, `proxmox_get_next_vmid`) answers the questions below
  read-only. Keep `PROXMOX_ALLOW_ELEVATED=false` on that server; NetClaw does not
  need its write tools for this workflow. `PROXMOX_HOST` is a host name, without
  `https://`.
- **Provisioning:** `deploy/cluster/proxmox.py` in the Convergence checkout. It reads
  the same `PROXMOX_*` keys from a private env file, verifies TLS, and changes only
  VMIDs inside the spec's range whose names start with its prefix. It deletes only
  VMs it tagged `convergence-managed`.
- **Recommended token:** a dedicated API token whose role (VM.Allocate, VM.Clone,
  VM.Config.*, VM.PowerMgmt, VM.Audit, Datastore.AllocateSpace, SDN.Use) is granted
  only on a pool for these VMs, on the template and on the chosen storage and bridge.
  Ask the user to place it in a private env file (mode 600); never ask for it in chat.

## Ask only what discovery cannot answer

| Topic | Ask or discover |
|---|---|
| Node, storage, bridge/VLAN | Discover node and storage; ask which bridge and VLAN the cluster belongs on |
| Template | A cloud-init Ubuntu/Debian template; discover candidates, ask which |
| VMID range, name prefix | Propose an unused range (`proxmox_get_vms`) and a prefix; the user confirms |
| Shape | 1 server for a trial; 3 servers (+ optional agents) for HA. Sizes: 4 vCPU / 8 GiB / 40 GB per node is enough for the core stack |
| Addresses | Static addresses outside the DHCP pool, plus one more for the API VIP when there are 3 servers. Confirm none answer ping/ARP before use |
| Gateway, DNS, SSH key | Discover from the NetClaw host where possible; confirm |
| GitOps | Helm directly, or Argo CD from the user's repository (URL, revision, read-only deploy key file) |

## Steps

1. Write the spec (see `deploy/cluster/cluster.example.json`), then
   `python3 deploy/cluster/proxmox.py --env-file ENV --spec SPEC check` and `plan`.
   `plan` is read-only and fails on any VMID already used by a VM it does not manage.
2. After the user approves: `proxmox.py ... apply --state-dir STATE`. It clones full
   copies, sets cloud-init (user, key, address), resizes the disk, starts the VMs,
   waits for SSH and writes `STATE/inventory.json`. Re-running it is safe.
3. `cd deploy/cluster/ansible && ansible-playbook -i STATE/inventory.json
   -e @STATE/cluster-secrets.json site.yml`. It installs the pinned K3s version
   with embedded etcd, the keepalived VIP for 3+ servers, and writes
   `STATE/kubeconfig` (mode 600) with the context named after the prefix.
4. Deliver images without a registry: build them on the NetClaw host, `docker save`,
   then `ansible-playbook -i STATE/inventory.json import-images.yml -e
   '{"image_archives": ["/path/api.tar"]}'`.
5. Install Convergence with [K3s and reuse](k3s.md) using
   `KUBECONFIG=STATE/kubeconfig` and the new context. On a fresh K3s cluster the
   storage class is `local-path`.
6. **Argo CD (optional):** `KUBECONFIG=STATE/kubeconfig deploy/argocd/install.sh CONTEXT`,
   then replace step 5's `install` with
   `python3 setup/k3s.py --repo "$repo" argocd --bundle B --repo-url URL --revision REV
   [--repo-key-file KEY]`. Values go into the Application from the private bundle;
   the database and API credentials stay in a cluster Secret, never in Git. Wait for
   the Application to be `Synced` and `Healthy`, then run the normal verification.

## Tear down

`ansible-playbook ... reset.yml` removes K3s from the nodes.
`proxmox.py ... destroy --confirm PREFIX` stops and deletes the spec's tagged VMs.
Both destroy data; confirm with the user first. Report every VM created or removed.

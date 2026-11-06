#!/usr/bin/env python3
"""Provision K3s node VMs on Proxmox VE from a cloud-init template.

Every change is limited to VMIDs inside the spec's `vmid_range` whose names start
with `name_prefix`. Destroy also requires the `convergence-managed` tag, which only
`apply` sets, so a VM this tool did not create is never stopped or deleted.

Credentials use the same keys as the Proxmox MCP: PROXMOX_HOST, PROXMOX_PORT,
PROXMOX_USER, PROXMOX_TOKEN_NAME and PROXMOX_TOKEN_VALUE, from the environment
or `--env-file` (parsed as data, never executed). Values are never printed.
TLS is always verified; pass `--ca-file` for a private CA.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MANAGED_TAG = 'convergence-managed'
NAME = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$')
KEYS = ('PROXMOX_HOST', 'PROXMOX_PORT', 'PROXMOX_USER', 'PROXMOX_TOKEN_NAME', 'PROXMOX_TOKEN_VALUE')


class SpecError(ValueError):
    pass


def env_values(path):
    """Read KEY=value lines without shell expansion."""
    values = {}
    for line in Path(path).expanduser().read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.removeprefix('export ').split('=', 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        values[key.strip()] = value
    return values


def credentials(env_file=None):
    source = dict(os.environ)
    if env_file:
        source = {**env_values(env_file), **{k: v for k, v in os.environ.items() if k in KEYS}}
    missing = [k for k in KEYS if k != 'PROXMOX_PORT' and not source.get(k)]
    if missing:
        raise SpecError('Missing Proxmox settings: ' + ', '.join(missing))
    host = source['PROXMOX_HOST'].split('://', 1)[-1].rstrip('/')
    return {'host': host, 'port': source.get('PROXMOX_PORT') or '8006', 'user': source['PROXMOX_USER'],
            'token_name': source['PROXMOX_TOKEN_NAME'], 'token_value': source['PROXMOX_TOKEN_VALUE']}


def load_spec(path):
    spec = json.loads(Path(path).read_text())
    for key in ('node', 'template', 'storage', 'bridge', 'vmid_range', 'name_prefix', 'ssh_public_key', 'nodes'):
        if key not in spec:
            raise SpecError(f'Spec is missing {key}')
    low, high = spec['vmid_range']
    if not (100 <= low <= high <= 999999999):
        raise SpecError('vmid_range must be [low, high] with 100 <= low <= high')
    if not NAME.match(spec['name_prefix'].rstrip('-') or '-'):
        raise SpecError('name_prefix must be a lowercase DNS label prefix')
    servers = [n for n in spec['nodes'] if n.get('role') == 'server']
    if len(servers) not in (1, 3, 5):
        raise SpecError('Use 1, 3 or 5 server nodes (etcd quorum)')
    if len(servers) > 1 and not spec.get('vip'):
        raise SpecError('A multi-server cluster needs a vip for the API')
    seen_ids, seen_names, seen_ips = set(), set(), set()
    for n in spec['nodes']:
        vmid, name = n.get('vmid'), n.get('name', '')
        if not isinstance(vmid, int) or not low <= vmid <= high:
            raise SpecError(f'{name or vmid}: vmid must be inside vmid_range {low}-{high}')
        if not name.startswith(spec['name_prefix']) or not NAME.match(name):
            raise SpecError(f'{name}: name must start with {spec["name_prefix"]} and be a DNS label')
        if n.get('role') not in ('server', 'agent'):
            raise SpecError(f'{name}: role must be server or agent')
        ip = n.get('ip')
        if ip != 'dhcp':
            try:
                ipaddress.ip_address(ip)
            except (TypeError, ValueError):
                raise SpecError(f'{name}: ip must be an address or "dhcp"') from None
            if not spec.get('gateway') or not spec.get('cidr'):
                raise SpecError('Static addresses need gateway and cidr')
        for value, seen in ((vmid, seen_ids), (name, seen_names), (ip if ip != 'dhcp' else None, seen_ips)):
            if value is not None and value in seen:
                raise SpecError(f'Duplicate {value}')
            seen.add(value)
    if spec.get('vip') and spec['vip'] in seen_ips:
        raise SpecError('vip must differ from every node address')
    return spec


class Proxmox:
    def __init__(self, creds, ca_file=None, opener=None):
        # Leave out :443 so the Host header is the bare name a reverse proxy matches on.
        port = '' if str(creds['port']) == '443' else f":{creds['port']}"
        self.base = f"https://{creds['host']}{port}/api2/json"
        self._auth = f"PVEAPIToken={creds['user']}!{creds['token_name']}={creds['token_value']}"
        context = ssl.create_default_context(cafile=ca_file)
        self._open = opener or (lambda request, timeout: urllib.request.urlopen(request, timeout=timeout, context=context))

    def call(self, method, path, data=None, timeout=60):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        request = urllib.request.Request(self.base + path, data=body, method=method,
                                         headers={'Authorization': self._auth})
        try:
            with self._open(request, timeout) as response:
                return json.load(response).get('data')
        except urllib.error.HTTPError as err:
            # Proxmox puts the reason in the status line; never echo request data.
            raise RuntimeError(f'{method} {path}: HTTP {err.code} {err.reason}') from None

    def wait_task(self, node, upid, timeout=900):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.call('GET', f'/nodes/{node}/tasks/{urllib.parse.quote(upid, safe="")}/status')
            if status.get('status') == 'stopped':
                if status.get('exitstatus') != 'OK':
                    raise RuntimeError(f'Proxmox task failed: {status.get("exitstatus")}')
                return
            time.sleep(2)
        raise TimeoutError('Proxmox task did not finish')


def tags(config):
    return set(filter(None, re.split(r'[;, ]+', config.get('tags', '') or '')))


def owned(vm, spec):
    low, high = spec['vmid_range']
    return (low <= int(vm['vmid']) <= high and str(vm.get('name', '')).startswith(spec['name_prefix'])
            and MANAGED_TAG in tags(vm))


def plan(api, spec):
    """Read-only checks; returns the actions apply would take."""
    vms = {int(vm['vmid']): vm for vm in api.call('GET', '/cluster/resources?type=vm') or []}
    template = vms.get(int(spec['template']))
    if not template or not template.get('template'):
        raise SpecError(f'VMID {spec["template"]} is not a template')
    if template.get('node') != spec['node']:
        raise SpecError(f'Template {spec["template"]} is on {template.get("node")}, not {spec["node"]}')
    storage = {s['storage']: s for s in api.call('GET', f'/nodes/{spec["node"]}/storage') or []}
    if spec['storage'] not in storage or 'images' not in storage[spec['storage']].get('content', ''):
        raise SpecError(f'Storage {spec["storage"]} on {spec["node"]} does not hold VM images')
    try:
        bridges = sorted(b['iface'] for b in api.call('GET', f'/nodes/{spec["node"]}/network?type=any_bridge') or [])
    except RuntimeError:
        bridges = None  # listing needs Sys.Audit; the clone still validates the bridge
    if bridges is not None and spec['bridge'] not in bridges:
        raise SpecError(f'Bridge {spec["bridge"]} does not exist on {spec["node"]}: {", ".join(bridges)}')
    actions = []
    for n in spec['nodes']:
        vm = vms.get(n['vmid'])
        if vm is None:
            actions.append({'vmid': n['vmid'], 'name': n['name'], 'action': 'create'})
        elif owned(vm, spec) and vm.get('name') == n['name']:
            actions.append({'vmid': n['vmid'], 'name': n['name'], 'action': 'exists'})
        else:
            raise SpecError(f'VMID {n["vmid"]} is already used by a VM this tool does not manage')
    return actions


def describe(api, node, vmid):
    config = api.call('GET', f'/nodes/{node}/qemu/{vmid}/config') or {}
    keep = re.compile(r'^(name|template|cores|memory|agent|tags|net\d+|scsi\d+|virtio\d+|sata\d+|ide\d+|ipconfig\d+|ciuser)$')
    return {'vmid': vmid, 'config': {k: v for k, v in config.items() if keep.match(k)}}


def net0(spec):
    value = f'virtio,bridge={spec["bridge"]}'
    if spec.get('vlan') is not None:
        value += f',tag={int(spec["vlan"])}'
    return value


def node_config(spec, n, key_text):
    config = {'name': n['name'], 'cores': n.get('cores', 2), 'memory': n.get('memory_mb', 4096),
              'net0': net0(spec), 'agent': 1, 'onboot': 1,
              'ciuser': spec.get('ci_user', 'ubuntu'),
              # Proxmox expects the key list URL-encoded inside the form value.
              'sshkeys': urllib.parse.quote(key_text.strip() + '\n', safe=''),
              'tags': ';'.join(sorted({MANAGED_TAG, *spec.get('tags', [])})),
              'description': f'Convergence cluster node ({n["role"]}); managed by deploy/cluster/proxmox.py'}
    if n['ip'] == 'dhcp':
        config['ipconfig0'] = 'ip=dhcp'
    else:
        config['ipconfig0'] = f'ip={n["ip"]}/{spec["cidr"]},gw={spec["gateway"]}'
    if spec.get('nameservers'):
        config['nameserver'] = ' '.join(spec['nameservers'])
    return config


def boot_disk(config):
    for key in ('scsi0', 'virtio0', 'sata0', 'ide0'):
        if key in config and 'media=cdrom' not in config[key]:
            return key
    raise RuntimeError('Template has no boot disk')


def wait_ssh(address, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((address, 22), timeout=5):
                return
        except OSError:
            time.sleep(5)
    raise TimeoutError(f'SSH did not open on {address}')


def agent_address(api, spec, n, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = api.call('GET', f'/nodes/{spec["node"]}/qemu/{n["vmid"]}/agent/network-get-interfaces')
        except RuntimeError:
            result = None
        for iface in (result or {}).get('result', []):
            if iface.get('name') == 'lo':
                continue
            for addr in iface.get('ip-addresses', []):
                if addr.get('ip-address-type') == 'ipv4':
                    return addr['ip-address']
        time.sleep(5)
    raise TimeoutError(f'{n["name"]}: no address from the guest agent (is qemu-guest-agent in the template?)')


def apply(api, spec, wait=True):
    actions = plan(api, spec)
    key_text = Path(spec['ssh_public_key']).expanduser().read_text()
    if not key_text.startswith(('ssh-', 'ecdsa-', 'sk-')):
        raise SpecError('ssh_public_key is not a public key file')
    node = spec['node']
    addresses = {}
    for n, action in zip(spec['nodes'], actions):
        if action['action'] == 'create':
            upid = api.call('POST', f'/nodes/{node}/qemu/{spec["template"]}/clone',
                            {'newid': n['vmid'], 'name': n['name'], 'full': 1, 'storage': spec['storage'],
                             **({'pool': spec['pool']} if spec.get('pool') else {})})
            api.wait_task(node, upid)
            api.call('POST', f'/nodes/{node}/qemu/{n["vmid"]}/config', node_config(spec, n, key_text))
            config = api.call('GET', f'/nodes/{node}/qemu/{n["vmid"]}/config')
            if n.get('disk_gb'):
                api.call('PUT', f'/nodes/{node}/qemu/{n["vmid"]}/resize',
                         {'disk': boot_disk(config), 'size': f'{int(n["disk_gb"])}G'})
        status = api.call('GET', f'/nodes/{node}/qemu/{n["vmid"]}/status/current')
        if status.get('status') != 'running':
            api.wait_task(node, api.call('POST', f'/nodes/{node}/qemu/{n["vmid"]}/status/start', {}))
        addresses[n['name']] = n['ip'] if n['ip'] != 'dhcp' else (agent_address(api, spec, n) if wait else None)
    if wait:
        for address in addresses.values():
            wait_ssh(address)
    return {'actions': actions, 'addresses': addresses}


def destroy(api, spec):
    vms = {int(vm['vmid']): vm for vm in api.call('GET', '/cluster/resources?type=vm') or []}
    removed = []
    for n in spec['nodes']:
        vm = vms.get(n['vmid'])
        if vm is None:
            continue
        if not owned(vm, spec) or vm.get('name') != n['name']:
            raise SpecError(f'Refusing to delete VMID {n["vmid"]}: not created by this tool for this spec')
    for n in spec['nodes']:
        vm = vms.get(n['vmid'])
        if vm is None:
            continue
        base = f'/nodes/{vm["node"]}/qemu/{n["vmid"]}'
        if vm.get('status') == 'running':
            api.wait_task(vm['node'], api.call('POST', base + '/status/stop', {}))
        api.wait_task(vm['node'], api.call('DELETE', base + '?purge=1&destroy-unreferenced-disks=1'))
        removed.append(n['vmid'])
    return removed


def inventory(spec, addresses, cluster_vars):
    """Ansible inventory for deploy/cluster/ansible."""
    def host(n):
        entry = {'ansible_host': addresses.get(n['name']) or n['ip'], 'ansible_user': spec.get('ci_user', 'ubuntu'),
                 'k3s_role': n['role']}
        return entry
    servers = [n for n in spec['nodes'] if n['role'] == 'server']
    agents = [n for n in spec['nodes'] if n['role'] == 'agent']
    data = {'all': {'vars': cluster_vars, 'children': {
        'k3s_servers': {'hosts': {n['name']: host(n) for n in servers}},
        'k3s_agents': {'hosts': {n['name']: host(n) for n in agents}},
        'k3s_init_node': {'hosts': {servers[0]['name']: {}}},
        'k3s_cluster': {'children': {'k3s_servers': {}, 'k3s_agents': {}}}}}}
    return data


def cluster_vars(spec, state_dir):
    """Private, generated once: the join token and VRRP password stay in the state dir."""
    state_dir = Path(state_dir).expanduser()
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret_file = state_dir / 'cluster-secrets.json'
    if secret_file.exists():
        stored = json.loads(secret_file.read_text())
    else:
        stored = {'k3s_token': secrets.token_hex(32), 'keepalived_auth_pass': secrets.token_hex(4)}
        fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle:
            json.dump(stored, handle)
    values = {'k3s_version': spec.get('k3s_version', 'v1.35.6+k3s1'),
              'kubeconfig_local_path': str(state_dir / 'kubeconfig'),
              'kube_context': spec.get('kube_context', spec['name_prefix'].rstrip('-')),
              'secrets_file': str(secret_file)}
    if spec.get('vip'):
        values.update(virtual_ip=spec['vip'], virtual_ip_cidr=spec.get('cidr', 24),
                      keepalived_router_id=spec.get('vrrp_router_id', 51))
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--env-file')
    parser.add_argument('--ca-file')
    parser.add_argument('--spec', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check', help='validate the spec only (no API calls)')
    sub.add_parser('plan', help='read-only checks against Proxmox')
    look = sub.add_parser('describe', help="read-only: a VM's network and disks, e.g. to reuse its bridge")
    look.add_argument('--vmid', type=int, required=True)
    up = sub.add_parser('apply', help='create/start the VMs and write the Ansible inventory')
    up.add_argument('--state-dir', required=True)
    down = sub.add_parser('destroy', help='stop and delete the managed VMs in this spec')
    down.add_argument('--confirm', required=True, help='repeat the name_prefix to confirm')
    args = parser.parse_args(argv)
    try:
        spec = load_spec(args.spec)
        if args.command == 'check':
            print(json.dumps({'ok': True, 'nodes': [n['name'] for n in spec['nodes']]}))
            return 0
        api = Proxmox(credentials(args.env_file), args.ca_file)
        if args.command == 'plan':
            print(json.dumps({'ok': True, 'actions': plan(api, spec)}, indent=2))
        elif args.command == 'describe':
            print(json.dumps({'ok': True, **describe(api, spec['node'], args.vmid)}, indent=2))
        elif args.command == 'apply':
            result = apply(api, spec)
            state = Path(args.state_dir).expanduser()
            values = cluster_vars(spec, state)
            (state / 'inventory.json').write_text(json.dumps(inventory(spec, result['addresses'], values), indent=2))
            print(json.dumps({'ok': True, **result, 'inventory': str(state / 'inventory.json')}, indent=2))
        elif args.command == 'destroy':
            if args.confirm != spec['name_prefix']:
                raise SpecError('--confirm must equal the spec name_prefix')
            print(json.dumps({'ok': True, 'removed': destroy(api, spec)}))
    except (SpecError, RuntimeError, TimeoutError, OSError) as err:
        print(json.dumps({'ok': False, 'error': str(err)}))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

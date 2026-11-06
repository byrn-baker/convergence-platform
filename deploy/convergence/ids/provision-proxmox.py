#!/usr/bin/env python3
"""Clone a dedicated IDS VM from an existing cloud-init template.

Dry-run by default. Credentials come from the environment, never CLI arguments.
No existing VM is modified; a partial deployment is left stopped for inspection.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import time
from urllib.parse import quote, urlsplit

import requests


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--node', required=True)
    p.add_argument('--template', required=True, type=int)
    p.add_argument('--vmid', required=True, type=int)
    p.add_argument('--name', default='netclaw-ids-01')
    p.add_argument('--storage', required=True)
    p.add_argument('--bridge', default='vmbr0')
    p.add_argument('--vlan', required=True, type=int)
    p.add_argument('--address', required=True)
    p.add_argument('--gateway', required=True)
    p.add_argument('--dns', required=True)
    p.add_argument('--ssh-key', type=Path, required=True)
    p.add_argument('--cores', type=int, default=4)
    p.add_argument('--memory', type=int, default=8192)
    p.add_argument('--disk-gb', type=int, default=80)
    p.add_argument('--reset-cloud-init', action='store_true',
                   help='Discard inherited custom cloud-init snippets on the NEW clone')
    p.add_argument('--apply', action='store_true')
    args = p.parse_args()
    address = ipaddress.ip_interface(args.address)
    assert ipaddress.ip_address(args.gateway) in address.network
    assert 1 <= args.vlan <= 4094 and args.vmid != args.template
    endpoint = os.environ['PROXMOX_HOST']
    if '://' not in endpoint:
        endpoint = 'https://' + endpoint
    parsed = urlsplit(endpoint)
    base = f'https://{parsed.hostname}:{parsed.port or os.getenv("PROXMOX_PORT", "8006")}/api2/json'
    session = requests.Session()
    # Prefer a CA bundle for private PKI. Insecure TLS must be explicitly selected.
    session.verify = os.getenv('PROXMOX_CA_FILE') or os.getenv('PROXMOX_VERIFY_SSL', 'true').lower() != 'false'
    if session.verify is False:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    session.headers['Authorization'] = ('PVEAPIToken=' + os.environ['PROXMOX_USER'] + '!'
        + os.environ['PROXMOX_TOKEN_NAME'] + '=' + os.environ['PROXMOX_TOKEN_VALUE'])

    def api(method, path, **kwargs):
        response = session.request(method, base + path, timeout=60, **kwargs)
        if not response.ok:
            # Do not print request headers, credentials or cloud-init data.
            raise RuntimeError(f'Proxmox {method} {path}: HTTP {response.status_code}')
        return response.json()['data']

    def wait(task):
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline:
            status = api('GET', f'/nodes/{args.node}/tasks/{quote(task, safe="")}/status')
            if status['status'] == 'stopped':
                if status.get('exitstatus') != 'OK':
                    raise RuntimeError(f'Proxmox task failed: {status.get("exitstatus")}')
                return
            time.sleep(3)
        raise TimeoutError('Proxmox task still running; inspect before retrying')

    vms = api('GET', '/cluster/resources', params={'type': 'vm'})
    if any(v['vmid'] == args.vmid for v in vms):
        raise RuntimeError(f'VM {args.vmid} already exists; refusing to modify it')
    template = api('GET', f'/nodes/{args.node}/qemu/{args.template}/config')
    if not template.get('template') or 'cloudinit' not in str(template.get('ide2')):
        raise RuntimeError('Source must be a cloud-init template with ide2 cloudinit drive')
    if any(k.startswith(('hostpci', 'usb', 'net1')) for k in template):
        raise RuntimeError('Template has extra devices; use a clean OS template')
    if template.get('cicustom') and not args.reset_cloud_init:
        raise RuntimeError('Template uses custom cloud-init; use --reset-cloud-init to discard it on the clone')
    print(json.dumps({'vmid': args.vmid, 'name': args.name, 'node': args.node,
        'template': args.template, 'storage': args.storage, 'bridge': args.bridge,
        'vlan': args.vlan, 'address': args.address, 'cores': args.cores,
        'memoryMiB': args.memory, 'diskGiB': args.disk_gb, 'apply': args.apply}), flush=True)
    if not args.apply:
        return
    wait(api('POST', f'/nodes/{args.node}/qemu/{args.template}/clone', data={
        'newid': args.vmid, 'name': args.name, 'full': 1, 'storage': args.storage,
        'description': 'NetClaw passive IDS sensor. See docs/runbooks/convergence-ids.md.'}))
    path = f'/nodes/{args.node}/qemu/{args.vmid}'
    settings = {'cores': args.cores, 'sockets': 1, 'memory': args.memory, 'balloon': 0,
        'net0': f'virtio,bridge={args.bridge},tag={args.vlan},queues=4,firewall=0',
        'ipconfig0': f'ip={args.address},gw={args.gateway}', 'nameserver': args.dns,
        'ciuser': 'ubuntu', 'sshkeys': quote(args.ssh_key.read_text().strip(), safe=''),
        'agent': 'enabled=1', 'onboot': 1, 'boot': 'order=scsi0',
        'tags': 'netclaw;ids;passive', 'cpu': 'host'}
    discard = [k for k in ('cipassword', 'cicustom') if k in template]
    if discard:
        settings['delete'] = ','.join(discard)
    api('PUT', path + '/config', data=settings)
    api('PUT', path + '/resize', data={'disk': 'scsi0', 'size': f'{args.disk_gb}G'})
    wait(api('POST', path + '/status/start'))
    print(f'VM {args.vmid} started. Verify its SSH host key through the guest agent before connecting.', flush=True)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Fail closed unless the configured NIC has the expected MAC and no IPs."""
import json
import os
from pathlib import Path
import subprocess


def main():
    interface = os.environ['IDS_INTERFACE']
    mac = os.environ['IDS_INTERFACE_MAC'].lower()
    if interface in ('eth0', 'lo') or '/' in interface:
        raise SystemExit('Refusing management/invalid interface')
    address = Path('/sys/class/net') / interface / 'address'
    if address.read_text().strip().lower() != mac:
        raise SystemExit('Capture MAC does not match the isolated NIC')
    addresses = json.loads(subprocess.check_output(
        ['ip', '-j', 'address', 'show', 'dev', interface], text=True))
    if not addresses or any(x.get('addr_info') for x in addresses):
        raise SystemExit('Capture NIC must have no IPv4 or IPv6 addresses')
    subprocess.run(['ip', 'link', 'set', 'dev', interface, 'up', 'promisc', 'on'], check=True)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Derive the isolated raw-Ethernet capture config; never modify the source."""
import argparse
import re
from pathlib import Path

import yaml


def build(base, interface):
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', interface) or interface in ('eth0', 'lo'):
        raise ValueError('Require a dedicated capture interface, never eth0 or lo')
    c = yaml.safe_load(base)
    c.pop('bpf-filter', None)
    # Only AF_PACKET is selected by the container command. Remove stale capture
    # alternatives to prevent accidental reuse of original management interfaces.
    for key in ('pcap', 'pfring', 'netmap', 'dpdk', 'af-xdp'):
        c.pop(key, None)
    c['af-packet'] = [{'interface': interface, 'threads': 1, 'cluster-id': 100,
        'cluster-type': 'cluster_flow', 'defrag': True, 'use-mmap': True,
        'tpacket-v3': True, 'ring-size': 8192, 'checksum-checks': 'no'}]
    # Host TAP ingress is untagged; the same flow's physical reply has VLAN301.
    c.setdefault('vlan', {})['use-for-tracking'] = False
    c.setdefault('livedev', {})['use-for-tracking'] = False
    c.setdefault('detect', {})['profile'] = 'low'
    c['detect']['sgh-mpm-caching'] = False
    # A single AF_PACKET worker performs receive and detection and supports
    # TPACKET_V3; autofp silently falls back to V2 with this engine.
    c['runmode'] = 'workers'
    c['unix-command'] = {'enabled': False}
    c['default-log-dir'] = '/var/log/suricata'
    c['default-rule-path'] = '/var/lib/suricata/rules'
    c['outputs'] = [{'eve-log': {'enabled': True, 'filetype': 'regular',
        'filename': 'eve-%Y%m%d%H%M.json', 'rotate-interval': 3600,
        'community-id': True, 'types': [
            {'alert': {'payload': False, 'packet': False}},
            {'stats': {'totals': True, 'threads': False}}, 'flow',
            {'dns': {'requests': True, 'responses': True}}, {'tls': {'extended': True}}]}}]
    return c


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--interface', required=True)
    args = p.parse_args()
    if args.base.resolve() == args.output.resolve():
        p.error('Source and destination must differ')
    args.output.write_text('%YAML 1.1\n---\n' + yaml.safe_dump(
        build(args.base.read_text(), args.interface), sort_keys=False))


if __name__ == '__main__':
    main()

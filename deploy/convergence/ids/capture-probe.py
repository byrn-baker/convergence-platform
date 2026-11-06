#!/usr/bin/env python3
"""Observe ERSPAN II headers for per-source liveness; never retain payloads.

This independent header probe does not claim Suricata decoded a packet. Counts
are observations, not authoritative capture throughput. Both checks are needed.
"""
import ipaddress
import json
import os
from pathlib import Path
import socket
import struct
import time


def decode_header(packet):
    """Return (origin, destination, ERSPAN session, VLAN) for complete headers."""
    if len(packet) < 14:
        return None
    ether, offset = struct.unpack_from('!H', packet, 12)[0], 14
    for _ in range(3):
        if ether not in (0x8100, 0x88a8):
            break
        if len(packet) < offset+4:
            return None
        ether = struct.unpack_from('!H', packet, offset+2)[0]
        offset += 4
    if ether != 0x0800 or len(packet) < offset+20:
        return None
    ihl = (packet[offset] & 15)*4
    if packet[offset] >> 4 != 4 or ihl < 20 or packet[offset+9] != 47:
        return None
    # Fragmented outer IP headers cannot reliably identify a complete mirror.
    if struct.unpack_from('!H', packet, offset+6)[0] & 0x3fff:
        return None
    if len(packet) < offset+ihl+4:
        return None
    origin = socket.inet_ntoa(packet[offset+12:offset+16])
    destination = socket.inet_ntoa(packet[offset+16:offset+20])
    flags, protocol = struct.unpack_from('!HH', packet, offset+ihl)
    if protocol != 0x88be or flags & ~0x1000:  # Type II, optional sequence only
        return None
    offset += ihl+4+(4 if flags & 0x1000 else 0)
    if len(packet) < offset+8:
        return None
    version_vlan, session = struct.unpack_from('!HH', packet, offset)
    if version_vlan >> 12 != 1:
        return None
    return origin, destination, session & 0x3ff, version_vlan & 0xfff


def main():
    root = Path(os.getenv('IDS_SENSOR_ROOT', '/opt/netclaw-ids'))
    configured = json.loads((root/'capture-sources.json').read_text())
    destination = str(ipaddress.IPv4Address(os.environ['IDS_SENSOR_IP']))
    expected = {(str(ipaddress.IPv4Address(s['ip'])), int(s['sessionId'])): s['id'] for s in configured}
    if len(expected) != len(configured) or len({s['id'] for s in configured}) != len(configured):
        raise ValueError('Capture source identities must be unique')
    observations = {s['id']: {'lastSeen': 0, 'packets': 0, 'vlans': {}} for s in configured}
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    sock.bind((os.environ['IDS_INTERFACE'], 0))
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4*1024*1024)
    sock.settimeout(1)
    last_write = 0
    while True:
        try:
            packet = sock.recv(128)
        except socket.timeout:
            packet = b''
        now = time.time()
        header = decode_header(packet)
        if header and header[1] == destination:
            source = expected.get((header[0], header[2]))
            if source:
                row = observations[source]
                row['lastSeen'] = now
                row['packets'] += 1
                row['vlans'][str(header[3])] = now
        if time.monotonic()-last_write >= 10:
            pending = root/'capture-status.json.new'
            pending.write_text(json.dumps({'checkedAt': now, 'sources': observations}))
            pending.replace(root/'capture-status.json')
            last_write = time.monotonic()


if __name__ == '__main__':
    main()

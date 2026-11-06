#!/usr/bin/env python3
"""Send one harmless ICMP echo through a monitored path (requires CAP_NET_RAW)."""
import argparse
import ipaddress
import os
import socket
import struct
import time

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--destination', required=True, help='An owned, reachable IPv4 host across the monitored firewall')
p.add_argument('--payload-size', type=int, default=1472, help='1472 gives a 1500-byte IPv4 packet')
args = p.parse_args()
ipaddress.IPv4Address(args.destination)
if not 32 <= args.payload_size <= 1472:
    p.error('payload-size must be 32..1472')
identity = os.getpid() & 0xffff
payload = ('NETCLAW-IDS-CANARY '+str(time.time_ns())).encode().ljust(args.payload_size, b'.')
packet = struct.pack('!BBHHH', 8, 0, 0, identity, 1)+payload
data = packet + (b'\0' if len(packet)%2 else b'')
total = sum(struct.unpack('!'+str(len(data)//2)+'H', data))
while total >> 16:
    total = (total & 0xffff)+(total >> 16)
packet = struct.pack('!BBHHH', 8, 0, (~total)&0xffff, identity, 1)+payload
with socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP) as sock:
    sock.setsockopt(socket.IPPROTO_IP, 10, 2)  # Linux IP_MTU_DISCOVER / IP_PMTUDISC_DO
    sock.settimeout(5)
    sock.sendto(packet, (args.destination, 0))
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        try:
            data, peer = sock.recvfrom(65535)
        except TimeoutError:
            raise SystemExit('No matching echo reply received; inspect capture and routing')
        offset = (data[0]&15)*4
        kind, _, _, received_id, sequence = struct.unpack('!BBHHH', data[offset:offset+8])
        if kind == 0 and received_id == identity and sequence == 1 and peer[0] == args.destination:
            print(f'Canary echo reply received: {len(data)}-byte IPv4 packet. Verify SID 9000001 in EVE and Convergence.')
            break
    else:
        raise SystemExit('No matching echo reply received; inspect capture and routing')

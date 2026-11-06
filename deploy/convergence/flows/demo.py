#!/usr/bin/env python3
"""Generate documentation-only packets in a file; softflowd exports their flows.

No interface capture or network traffic to the example addresses occurs.
"""
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time


def checksum(data):
    if len(data) % 2: data += b'\0'
    value = sum(struct.unpack('!'+str(len(data)//2)+'H', data))
    while value >> 16: value = (value & 65535) + (value >> 16)
    return (~value) & 65535


def packet(source, destination, size=64):
    payload = b'CONVERGENCE SYNTHETIC DEMO '.ljust(size,b'.')
    udp = struct.pack('!HHHH', 49152, 9999, 8+len(payload), 0) + payload
    header = struct.pack('!BBHHHBBH4s4s',0x45,0,20+len(udp),1,0,64,17,0,
        socket.inet_aton(source),socket.inet_aton(destination))
    header = header[:10]+struct.pack('!H',checksum(header))+header[12:]
    return bytes.fromhex('0200000000020200000000010800')+header+udp


def capture(path):
    now = int(time.time())
    rows = [('192.0.2.10','198.51.100.20',128)]*12 + [('198.51.100.20','192.0.2.10',64)]*3
    with Path(path).open('wb') as file:
        file.write(struct.pack('<IHHIIII',0xa1b2c3d4,2,4,0,0,65535,1))
        for index, args in enumerate(rows):
            data = packet(*args)
            file.write(struct.pack('<IIII',now,index*1000,len(data),len(data))+data)


def main():
    port = int(os.environ.get('FLOW_PORT','2055'))
    if not 1024 <= port <= 65535: raise ValueError('Invalid flow port')
    with tempfile.TemporaryDirectory(prefix='convergence-demo-') as directory:
        path = Path(directory)/'synthetic.pcap'
        capture(path)
        subprocess.run(['softflowd','-r',str(path),'-v','5','-n','127.0.0.1:'+str(port)],check=True)


if __name__ == '__main__': main()

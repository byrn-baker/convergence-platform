#!/usr/bin/env python3
"""Suppress kernel GRE rejects for the same allowlist used by AF_PACKET."""
import ipaddress
import os
import subprocess

destination = str(ipaddress.IPv4Address(os.environ['IDS_SENSOR_IP']))
for origin in dict.fromkeys(os.environ['IDS_SWITCH_IP'].split(',')):
    origin = str(ipaddress.IPv4Address(origin.strip()))
    rule = ['INPUT', '-s', origin, '-d', destination, '-p', '47',
            '-m', 'comment', '--comment', 'netclaw-ids', '-j', 'DROP']
    if subprocess.run(['iptables', '-C', *rule], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL, check=False).returncode:
        subprocess.run(['iptables', '-I', *rule], check=True)

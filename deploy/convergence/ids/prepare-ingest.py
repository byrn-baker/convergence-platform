#!/usr/bin/env python3
"""Create private IDS credentials and TLS trust for one sensor. Never overwrites."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess

from dotenv import dotenv_values

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--receiver-env', type=Path, required=True)
p.add_argument('--server-ip', required=True)
p.add_argument('--sensor-id', required=True)
p.add_argument('--site', required=True)
p.add_argument('--vlans', required=True)
p.add_argument('--state-dir', default='/var/lib/netclaw-ids-ingest')
args = p.parse_args()
ipaddress.ip_address(args.server_ip)
vlans = [int(v) for v in args.vlans.split(',')]
assert all(1 <= v <= 4094 for v in vlans)
os.umask(0o077)
args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
root = args.output.resolve()
receiver_token = dotenv_values(args.receiver_env).get('ALERT_RECEIVER_TOKEN')
if not receiver_token:
    raise SystemExit('Existing alert receiver token required')
sensor_token, status_token = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
(root/'sensor-token').write_text(sensor_token+'\n')
(root/'api.env').write_text(f'IDS_STATUS_URL=https://{args.server_ip}:8445\nIDS_STATUS_TOKEN={status_token}\nIDS_METRICS_TARGET={args.server_ip}:8445\n')
digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
config = {'state_dir': args.state_dir, 'queue_capacity': 250000,
    'loki_url': 'http://127.0.0.1:3100', 'receiver_url': 'http://127.0.0.1:8099/webhook',
    'receiver_token': receiver_token, 'status_token_sha256': digest(status_token),
    'sensors': [{'id': args.sensor_id, 'site': args.site, 'token_sha256': digest(sensor_token),
        'vlans': vlans, 'auto_investigate': False,
        'scope': 'Firewall-facing port-channel in both directions; traffic staying within a VLAN outside this path is not covered'}]}
(root/'config.json').write_text(json.dumps(config, indent=2)+'\n')
def openssl(*argv):
    subprocess.run(['openssl', *argv], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
openssl('req', '-x509', '-newkey', 'rsa:3072', '-nodes', '-keyout', 'ca.key', '-out', 'ca.crt',
        '-days', '3650', '-subj', '/CN=NetClaw IDS private CA',
        '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
openssl('req', '-newkey', 'rsa:3072', '-nodes', '-keyout', 'server.key', '-out', 'server.csr',
        '-subj', '/CN=NetClaw IDS ingestion')
(root/'server.ext').write_text(f'subjectAltName=IP:{args.server_ip},IP:127.0.0.1,DNS:localhost\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n')
openssl('x509', '-req', '-in', 'server.csr', '-CA', 'ca.crt', '-CAkey', 'ca.key', '-CAcreateserial',
        '-out', 'server.crt', '-days', '365', '-sha256', '-extfile', 'server.ext')
# The API container runs under a different UID. This certificate is public;
# its private signing key and the directory remain restricted.
(root/'ca.crt').chmod(0o644)
print(f'Private credentials created in {root}. Copy only ca.crt and sensor-token to the sensor.')

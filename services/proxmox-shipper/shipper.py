#!/usr/bin/env python3
"""Read passive Proxmox snapshots over pinned SSH; publish using a separate role."""
import argparse
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 512 * 1024
MAX_ACK_BYTES = 4096
RELAY_COMMAND = ('/usr/bin/python3 -I /home/ubuntu/.local/lib/netclaw-proxmox-shipper/shipper.py '
                 '--config /home/ubuntu/.config/netclaw-proxmox-publisher/config.json --publish-stdin')
STOP = False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def strict_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('Duplicate JSON key')
        out[key] = value
    return out


def load_json(raw):
    def finite_float(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError('Nonfinite JSON')
        return parsed
    return json.loads(raw, object_pairs_hook=strict_object, parse_float=finite_float,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def validate_host(host, user):
    if not isinstance(host, str) or not host.startswith(user + '@'):
        raise ValueError('Invalid SSH host')
    name = host[len(user) + 1:]
    if not re.fullmatch(r'[A-Za-z0-9.:-]{1,253}', name):
        raise ValueError('Invalid SSH host')
    try:
        ipaddress.ip_address(name)
    except ValueError:
        if (len(name) > 253 or not name or any(not re.fullmatch(
                r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                for label in name.split('.'))):
            raise ValueError('Invalid SSH host') from None


def validate_config(config, publish_stdin=False):
    if not isinstance(config, dict):
        raise ValueError('Configuration must be an object')
    if not isinstance(config.get('node'), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', config['node']):
        raise ValueError('Invalid source node')
    relay = 'relay_host' in config and not publish_stdin
    if publish_stdin and 'relay_host' in config:
        raise ValueError('Publisher cannot relay')
    if not relay:
        url = urllib.parse.urlsplit(config['url'])
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.path != '/v1/hypervisor-events' or url.query or url.fragment):
            raise ValueError('A fixed HTTPS hypervisor ingestion URL is required')
    files = []
    if not publish_stdin:
        validate_host(config['ssh_host'], 'root')
        files += ['identity_file', 'known_hosts_file']
    if relay:
        validate_host(config['relay_host'], 'ubuntu')
    else:
        files.append('ca_file')
    if publish_stdin:
        files.append('token_file')
    for key in files:
        if not isinstance(config.get(key), str) or not Path(config[key]).is_absolute() or not Path(config[key]).is_file():
            raise ValueError('Missing trusted local file')
    if type(config.get('interval_seconds', 15)) is not int or not 5 <= config.get('interval_seconds', 15) <= 60:
        raise ValueError('Invalid polling interval')
    return config


def ssh_args(config, host):
    return ['ssh', '-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=5', '-o', 'IdentitiesOnly=yes', '-o', 'ForwardAgent=no',
            '-o', 'ClearAllForwardings=yes', '-o', 'GlobalKnownHostsFile=/dev/null',
            '-o', 'UpdateHostKeys=no', '-o', 'UserKnownHostsFile=' + config['known_hosts_file'],
            '-i', config['identity_file'], host]


def validate_snapshot(raw, config):
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError('Snapshot exceeds bound')
    data = load_json(raw)
    if (not isinstance(data, dict) or data.get('node') != config['node']
            or type(data.get('schema_version')) is not int or data['schema_version'] != 1):
        raise ValueError('Snapshot identity mismatch')
    stamp = data.get('observed_at')
    now = time.time()
    if type(stamp) not in (int, float) or not now - 120 <= stamp <= now + 30:
        raise ValueError('Snapshot is not current')
    return data


def read_snapshot(config):
    args = ssh_args(config, config['ssh_host']) + ['head -c 524289 /var/lib/netclaw-proxmox-observer/snapshot.json']
    result = subprocess.run(args, capture_output=True, timeout=12)
    if result.returncode:
        raise RuntimeError('Snapshot read failed')
    raw = result.stdout
    return raw, validate_snapshot(raw, config)


def bounded_exchange(args, raw, timeout=20):
    """Bound both directions without communicate() buffering arbitrary remote output."""
    deadline = time.monotonic() + timeout
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    output = bytearray()
    offset = 0
    try:
        with selectors.DefaultSelector() as ready:
            for stream in (process.stdin, process.stdout):
                os.set_blocking(stream.fileno(), False)
            ready.register(process.stdout, selectors.EVENT_READ)
            if raw:
                ready.register(process.stdin, selectors.EVENT_WRITE)
            else:
                process.stdin.close()
            while ready.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Relay timed out')
                for key, _ in ready.select(remaining):
                    stream = key.fileobj
                    if stream is process.stdin:
                        offset += os.write(stream.fileno(), raw[offset:offset + 65536])
                        if offset == len(raw):
                            ready.unregister(stream)
                            stream.close()
                    else:
                        chunk = os.read(stream.fileno(), MAX_ACK_BYTES + 1 - len(output))
                        output.extend(chunk)
                        if len(output) > MAX_ACK_BYTES:
                            raise ValueError('Relay acknowledgment exceeds bound')
                        if not chunk:
                            ready.unregister(stream)
                            stream.close()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Relay timed out')
            if process.wait(timeout=remaining):
                raise RuntimeError('Relay publication failed')
        return bytes(output)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        for stream in (process.stdin, process.stdout):
            if not stream.closed:
                stream.close()


def relay_publish(config, raw):
    validate_snapshot(raw, config)
    args = ssh_args(config, config['relay_host']) + [RELAY_COMMAND]
    ack = load_json(bounded_exchange(args, raw, timeout=20))
    if ack != {'status': 'accepted'}:
        raise RuntimeError('Invalid relay acknowledgment')


def read_token(config, local_only=False):
    credentials = None if local_only else os.environ.get('CREDENTIALS_DIRECTORY')
    token_path = Path(credentials) / 'ingest-token' if credentials else Path(config['token_file'])
    token = token_path.read_text().strip()
    if len(token) < 32 or len(token) > 256 or any(c.isspace() for c in token):
        raise ValueError('Invalid credential')
    return token


def publish_stdin(config, stream):
    raw = stream.read(MAX_BYTES + 1)
    validate_snapshot(raw, config)
    token = read_token(config, local_only=True)
    publish(opener(config), config, token, raw)
    print(json.dumps({'status': 'accepted'}), flush=True)
    return 0


def opener(config):
    context = ssl.create_default_context(cafile=config['ca_file'])
    return urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))


def publish(client, config, token, raw):
    request = urllib.request.Request(config['url'], data=raw,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, method='POST')
    with client.open(request, timeout=8) as response:
        body = response.read(4097)
        if response.status != 200 or len(body) > 4096:
            raise RuntimeError('Invalid receiver acknowledgment')
        ack = load_json(body)
        if ack.get('status') != 'accepted':
            raise RuntimeError('Receiver did not acknowledge snapshot')


def status_file(path, status):
    if not path:
        return
    target = Path(path)
    temporary = target.with_name(target.name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(status, stream)
        stream.write('\n')
    os.replace(temporary, target)


def run(args):
    config = validate_config(load_json(Path(args.config).read_bytes()), publish_stdin=args.publish_stdin)
    if args.publish_stdin:
        return publish_stdin(config, sys.stdin.buffer)
    relay = 'relay_host' in config
    # A relay backend never opens a publisher token or HTTPS context.
    token = None if relay else read_token(config)
    client = None if relay else opener(config)
    status = {'last_success': None, 'failures': 0, 'last_error_type': None}
    previous = None
    while not STOP:
        started = time.monotonic()
        try:
            raw, data = read_snapshot(config)
            identity = (data['collector_started_at'], data['sequence'], hashlib.sha256(raw).hexdigest())
            if identity != previous:
                if relay:
                    relay_publish(config, raw)
                else:
                    publish(client, config, token, raw)
                previous = identity
                status.update(last_success=time.time(), sequence=data['sequence'], last_error_type=None)
            status['checked_at'] = time.time()
        except Exception as exc:
            status['failures'] += 1
            status['last_error_type'] = type(exc).__name__
            status['checked_at'] = time.time()
            print(json.dumps({'event': 'publication_failed', 'error_type': type(exc).__name__}), flush=True)
        status_file(config.get('status_file'), status)
        if args.once:
            return 1 if status['last_error_type'] else 0
        remaining = config.get('interval_seconds', 15) - (time.monotonic() - started)
        while remaining > 0 and not STOP:
            time.sleep(min(remaining, 0.5))
            remaining = config.get('interval_seconds', 15) - (time.monotonic() - started)
    return 0


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('Invalid command line')


def main():
    parser = SafeArgumentParser()
    parser.add_argument('--config', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--once', action='store_true')
    mode.add_argument('--publish-stdin', action='store_true')
    try:
        args = parser.parse_args()
        return run(args)
    except Exception as exc:
        # Startup/config/TLS errors can also include secrets; never print exception text.
        print(json.dumps({'event': 'publication_failed', 'error_type': type(exc).__name__}), file=sys.stderr, flush=True)
        return 1


def stop(*_):
    global STOP
    STOP = True


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    raise SystemExit(main())

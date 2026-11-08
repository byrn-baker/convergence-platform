#!/usr/bin/env python3
"""Run Convergence's agent metrics exporter on the NetClaw host as a systemd user service.

The exporter (services/agent-metrics) reads OpenClaw's per-agent usage, read-only,
and serves the netclaw_model_* metrics that Convergence scrapes as job
netclaw-openclaw on port 9110:

  python3 setup/agent_metrics.py plan --bind 192.0.2.10
  python3 setup/agent_metrics.py install --bind 192.0.2.10 --start
  python3 setup/agent_metrics.py status
  python3 setup/agent_metrics.py remove

`plan` changes nothing. The unit runs this checkout's exporter from a private
virtualenv under ~/.local/state/convergence/<name>/. The default bind address,
127.0.0.1, is not reachable by Convergence: pass the address its scrape job uses
(the host's LAN address for K3s, the Docker bridge gateway for the Docker trial;
see docs/convergence/setup/agent-metrics.md). Install refuses while NetClaw's old
openclaw-token-exporter unit runs or anything else holds the port; it never stops
that unit itself. `remove` undoes only what install recorded.
A user service runs at boot only with lingering enabled (`loginctl enable-linger`); status reports it.
"""
import argparse
import errno
import getpass
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT/'services/agent-metrics'
NAME = re.compile(r'[a-z][a-z0-9-]{0,40}')
INSTANCE = re.compile(r'[A-Za-z0-9._-]{1,64}')
OLD_UNIT = 'openclaw-token-exporter.service'
MANIFEST = 'install.json'
SWITCH = (f'Stop it first: systemctl --user disable --now {OLD_UNIT} (this ends its scrape until the new '
          'service starts; its unit file and NetClaw checkout can stay). Then run install again; the job, '
          'port and metrics stay the same, so dashboards and alerts continue after a short gap.')


def unit(venv, openclaw_home, bind, port, instance):
    for value in (SERVICE, venv, openclaw_home):
        if any(c in str(value) for c in '\n\r%$"\'\\') or ' ' in str(value):
            raise ValueError(f'Unsupported character in path {value}')
    lines = ['[Unit]', 'Description=Convergence agent metrics (OpenClaw token and cost exporter)',
             'After=network-online.target', 'Wants=network-online.target', '',
             '[Service]', 'Type=simple', f'WorkingDirectory={SERVICE}',
             f'ExecStart={venv}/bin/python exporter.py',
             f'Environment=OPENCLAW_HOME={openclaw_home}', f'Environment=EXPORTER_BIND={bind}',
             f'Environment=EXPORTER_PORT={port}', f'Environment=NETCLAW_INSTANCE={instance}',
             # Retry while a LAN address is not configured yet at boot.
             'Restart=on-failure', 'RestartSec=5', 'NoNewPrivileges=true', 'UMask=0077', '',
             '[Install]', 'WantedBy=default.target', '']
    return '\n'.join(lines)


def paths(args):
    config = Path(os.environ.get('XDG_CONFIG_HOME', '~/.config')).expanduser()
    state_home = Path(os.environ.get('XDG_STATE_HOME', '~/.local/state')).expanduser()
    return config/'systemd/user'/f'{args.name}.service', state_home/'convergence'/args.name


def systemctl(*args, check=True):
    result = subprocess.run(['systemctl', '--user', *args], capture_output=True, text=True, check=False)
    if check and result.returncode:
        raise RuntimeError(f'systemctl --user {" ".join(args)}: {result.stderr.strip()[-400:]}')
    return result.stdout.strip()


def linger():
    result = subprocess.run(['loginctl', 'show-user', getpass.getuser(), '-p', 'Linger', '--value'],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() == 'yes'


def bind_problem(bind, port):
    """Why the exporter could not listen on bind:port now (same socket options as its server), or None."""
    family = socket.AF_INET6 if ':' in bind else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((bind, port))
        except OSError as exc:
            if exc.errno == errno.EADDRNOTAVAIL:
                return f'{bind} is not an address of this host (--bind)'
            if exc.errno == errno.EADDRINUSE:
                return (f'{bind}:{port} is in use by another program (see `ss -ltnp "sport = :{port}"`); '
                        'stop it or choose --port and change the netclaw-openclaw scrape target to match')
            return f'cannot listen on {bind}:{port}: {exc.strerror}'
    return None


def unit_settings(unit_path):
    """EXPORTER_* settings of an installed unit, to recognise the port as our own."""
    try:
        text = unit_path.read_text()
    except OSError:
        return {}
    return dict(re.findall(r'^Environment=(EXPORTER_BIND|EXPORTER_PORT)=(\S+)$', text, re.M))


def options(args):
    try:
        bind = str(ipaddress.ip_address(args.bind))
    except ValueError:
        raise ValueError(f'--bind must be an IP address, not {args.bind!r}') from None
    if not 0 < args.port < 65536:
        raise ValueError('--port must be 1-65535')
    if not INSTANCE.fullmatch(args.instance):
        raise ValueError('--instance may use letters, digits, ".", "_" and "-"')
    home = Path(args.openclaw_home).expanduser().absolute()
    return bind, home


def plan(args):
    bind, home = options(args)
    unit_path, state = paths(args)
    venv = Path(args.venv).expanduser().absolute() if args.venv else state/'venv'
    text = unit(venv, home, bind, args.port, args.instance)
    conflicts, notes = [], []
    if not home.is_dir():
        conflicts.append(f'OPENCLAW_HOME {home} is not a directory (--openclaw-home)')
    elif not (home/'agents').is_dir():
        notes.append(f'{home}/agents does not exist yet; the exporter reports no usage until OpenClaw creates it')
    if systemctl('is-active', OLD_UNIT, check=False) == 'active':
        conflicts.append(f'NetClaw\'s old exporter {OLD_UNIT} is running. {SWITCH}')
    ours = (systemctl('is-active', args.name+'.service', check=False) == 'active'
            and unit_settings(unit_path) == {'EXPORTER_BIND': bind, 'EXPORTER_PORT': str(args.port)})
    problem = None if ours else bind_problem(bind, args.port)
    if problem:
        conflicts.append(problem)
    address = ipaddress.ip_address(bind)
    host = f'[{bind}]' if address.version == 6 else bind
    if address.is_loopback:
        notes.append('Bound to loopback, Convergence cannot scrape it: K3s scrapes the host\'s LAN address and '
                     'the Docker trial host.docker.internal (the Docker bridge gateway). Use --bind with that address.')
    elif address.is_unspecified:
        notes.append(f'Listens on every interface; allow port {args.port} only from the Convergence scrapers '
                     '(host firewall), or bind the one address they use.')
    else:
        notes.append(f'Allow port {args.port}/tcp on {bind} only from the Convergence scrapers '
                     '(K3s node addresses, or the Docker bridge network).')
    return {'unit': str(unit_path), 'state': str(state), 'venv': str(venv), 'openclaw_home': str(home),
            'listen': f'{host}:{args.port}', 'scrape_target': f'{host}:{args.port}',
            'conflicts': conflicts, 'notes': notes, 'unit_text': text}


def read_manifest(state):
    try:
        return json.loads((state/MANIFEST).read_text())
    except (OSError, ValueError):
        return {}


def install(args):
    result = plan(args)
    if result['conflicts']:
        raise ValueError('; '.join(result['conflicts']))
    unit_path, state = Path(result['unit']), Path(result['state'])
    venv = Path(result['venv'])
    record = read_manifest(state)
    if not state.exists():
        record['state_created'] = True
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not (venv/'bin/python').exists():
        if not venv.exists():
            record['venv_created'] = True
        subprocess.run([args.python, '-m', 'venv', str(venv)], check=True)
    record['venv'] = str(venv)
    (state/MANIFEST).write_text(json.dumps(record, indent=2)+'\n')
    subprocess.run([str(venv/'bin/python'), '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
                    '-r', str(SERVICE/'requirements.txt')], check=True)
    if not unit_path.exists():
        record['unit_created'] = True
    record['unit'] = str(unit_path)
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(result['unit_text'])
    (state/MANIFEST).write_text(json.dumps(record, indent=2)+'\n')
    systemctl('daemon-reload')
    if args.start:
        # Restart even with an unchanged unit: the checkout's exporter may have changed.
        systemctl('enable', args.name+'.service')
        systemctl('restart', args.name+'.service')
    return {'status': 'installed', 'unit': str(unit_path), 'state': str(state), 'venv': str(venv),
            'scrape_target': result['scrape_target'], 'started': bool(args.start), 'linger': linger(),
            'notes': result['notes']}


def probe(settings):
    bind = settings.get('EXPORTER_BIND', '127.0.0.1')
    address = ipaddress.ip_address(bind)
    if address.is_unspecified:
        bind = '::1' if address.version == 6 else '127.0.0.1'
    host = f'[{bind}]' if ':' in bind else bind
    url = f'http://{host}:{settings.get("EXPORTER_PORT", "9110")}/metrics'
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            body = response.read().decode('utf-8', 'replace')
    except OSError as exc:
        return {'url': url, 'ok': False, 'error': str(getattr(exc, 'reason', exc))[:200]}
    sessions = re.search(r'^netclaw_token_exporter_sessions (\d+)$', body, re.M)
    return {'url': url, 'ok': True, 'sessions': int(sessions.group(1)) if sessions else None}


def status(args):
    unit_path, _ = paths(args)
    active = systemctl('is-active', args.name+'.service', check=False) or 'unknown'
    result = {'unit': str(unit_path), 'installed': unit_path.exists(), 'active': active,
              'enabled': systemctl('is-enabled', args.name+'.service', check=False), 'linger': linger(),
              'old_exporter': systemctl('is-active', OLD_UNIT, check=False) or 'unknown'}
    if unit_path.exists():
        result['metrics'] = probe(unit_settings(unit_path))
    return result


def remove(args):
    unit_path, state = paths(args)
    record = read_manifest(state)
    removed, kept = [], []
    if record.get('unit_created') and unit_path.exists():
        systemctl('disable', '--now', args.name+'.service', check=False)
        unit_path.unlink()
        systemctl('daemon-reload')
        removed.append(str(unit_path))
    elif unit_path.exists():
        kept.append(f'{unit_path} (not created by this helper)')
    venv = Path(record['venv']) if record.get('venv') else None
    if venv and record.get('venv_created') and venv.is_dir():
        shutil.rmtree(venv)
        removed.append(str(venv))
    elif venv and venv.exists():
        kept.append(f'{venv} (existed before install)')
    (state/MANIFEST).unlink(missing_ok=True)
    if record.get('state_created') and state.is_dir() and not any(state.iterdir()):
        state.rmdir()
        removed.append(str(state))
    return {'status': 'removed', 'removed': removed, 'kept': kept}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('command', choices=('plan', 'install', 'status', 'remove'))
    parser.add_argument('--name', default='convergence-agent-metrics')
    parser.add_argument('--bind', default='127.0.0.1',
                        help='listen address: the one Convergence scrapes (default 127.0.0.1, local checks only)')
    parser.add_argument('--port', type=int, default=9110)
    parser.add_argument('--openclaw-home', default=os.environ.get('OPENCLAW_HOME') or '~/.openclaw')
    parser.add_argument('--instance', default='netclaw', help='NETCLAW_INSTANCE, for the service log')
    parser.add_argument('--venv', help='virtualenv for the exporter (default: under the state directory)')
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--start', action='store_true', help='enable and restart the service')
    args = parser.parse_args(argv)
    if not NAME.fullmatch(args.name):
        parser.error('invalid --name')
    try:
        result = {'plan': plan, 'install': install, 'status': status, 'remove': remove}[args.command](args)
        print(json.dumps(result, indent=2))
        return 1 if args.command == 'plan' and result['conflicts'] else 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'agent-metrics: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

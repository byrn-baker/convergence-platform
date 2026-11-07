#!/usr/bin/env python3
"""Run Convergence's alert receiver on this host as a systemd user service.

The receiver turns Alertmanager notifications into Convergence cases and, when NetClaw
is connected (setup/netclaw.py), hands admitted cases to it. Docker and K3s bundles
both write its environment as receiver.env; this installs the receiver for that file:

  python3 setup/receiver.py install --env-file BUNDLE/receiver.env --start
  python3 setup/receiver.py status
  python3 setup/receiver.py remove

The unit runs this checkout's services/alert-receiver from a private virtualenv and
keeps its job queue under ~/.local/state/convergence/. Pass --netclaw-dir when the
receiver uses NetClaw pieces: JEV case reviews, RAG priors and snapshots (rag-mcp) and
skill scoping (scripts/skill-selector) all come from that checkout. Pass --rag-python
(an existing executable, only together with --netclaw-dir) so those RAG calls run in
NetClaw's RAG runtime: the command of mcp.servers["rag-mcp"] in ~/.openclaw/openclaw.json.
A local device inventory goes in the env file as ALERT_RECEIVER_INVENTORY=/path/inventory.yaml.
A user service runs at boot only with lingering enabled (`loginctl enable-linger`); status reports it.
"""
import argparse
import getpass
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT/'services/alert-receiver'
NAME = re.compile(r'[a-z][a-z0-9-]{0,40}')


def unit(name, env_file, venv, state, netclaw_dir=None, jev_env=None, rag_python=None):
    lines = ['[Unit]', 'Description=Convergence alert receiver (Alertmanager -> cases -> NetClaw)',
             'After=network-online.target', 'Wants=network-online.target', '',
             '[Service]', 'Type=simple', f'WorkingDirectory={SERVICE}',
             f'ExecStart={venv}/bin/python server.py', f'EnvironmentFile={env_file}',
             f'Environment=ALERT_JOBS_PATH={state}/jobs.sqlite']
    if netclaw_dir:
        lines.append(f'Environment=NETCLAW_DIR={netclaw_dir}')
    if jev_env:
        lines.append(f'Environment=CONVERGENCE_JEV_ENV_FILE={jev_env}')
    if rag_python:
        lines.append(f'Environment=RAG_PYTHON={rag_python}')
    lines += ['Restart=on-failure', 'RestartSec=5', 'NoNewPrivileges=true', 'UMask=0077', '',
              '[Install]', 'WantedBy=default.target', '']
    for value in (env_file, venv, state, netclaw_dir or '', jev_env or '', rag_python or ''):
        if any(c in str(value) for c in '\n\r%$"\'\\') or ' ' in str(value):
            raise ValueError(f'Unsupported character in path {value}')
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


def install(args):
    env_file = Path(args.env_file).expanduser().resolve()
    if env_file.stat().st_mode & 0o077:
        raise ValueError(f'{env_file} must be private (chmod 600)')
    keys = {line.partition('=')[0] for line in env_file.read_text().splitlines() if '=' in line}
    missing = {'ALERT_RECEIVER_TOKEN', 'NETWORK_GUARDIAN_URL', 'NETWORK_GUARDIAN_TOKEN'} - keys
    if missing:
        raise ValueError(f'{env_file} lacks {", ".join(sorted(missing))}')
    netclaw = Path(args.netclaw_dir).expanduser().resolve() if args.netclaw_dir else None
    if netclaw and not (netclaw/'mcp-servers/jev-mcp/core.py').is_file():
        raise ValueError(f'{netclaw} is not a NetClaw checkout with mcp-servers/jev-mcp')
    rag_python = _rag_python(getattr(args, 'rag_python', None), netclaw)
    unit_path, state = paths(args)
    venv = Path(args.venv).expanduser() if args.venv else state/'venv'
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not (venv/'bin/python').exists():
        subprocess.run([args.python, '-m', 'venv', str(venv)], check=True)
    subprocess.run([str(venv/'bin/python'), '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
                    '-r', str(SERVICE/'requirements.txt')], check=True)
    jev_env = Path(args.jev_env_file).expanduser().resolve() if args.jev_env_file else None
    if jev_env and (not netclaw or jev_env.stat().st_mode & 0o077):
        raise ValueError('--jev-env-file needs --netclaw-dir and a private file (chmod 600)')
    text = unit(args.name, env_file, venv, state, netclaw, jev_env, rag_python)
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(text)
    systemctl('daemon-reload')
    if args.start:
        # Restart even with an unchanged unit: the environment file may have changed.
        systemctl('enable', args.name+'.service')
        systemctl('restart', args.name+'.service')
    return {'status': 'installed', 'unit': str(unit_path), 'state': str(state), 'venv': str(venv),
            'started': bool(args.start), 'linger': linger()}


def _rag_python(raw, netclaw):
    if not raw:
        return None
    if not netclaw:
        raise ValueError('--rag-python needs --netclaw-dir')
    # absolute(), not resolve(): a venv python is a symlink to the system interpreter.
    path = Path(raw).expanduser().absolute()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError(f'{path} is not an executable')
    return path


def linger():
    result = subprocess.run(['loginctl', 'show-user', getpass.getuser(), '-p', 'Linger', '--value'],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() == 'yes'


def status(args):
    unit_path, _ = paths(args)
    active = systemctl('is-active', args.name+'.service', check=False) or 'unknown'
    return {'unit': str(unit_path), 'installed': unit_path.exists(), 'active': active,
            'enabled': systemctl('is-enabled', args.name+'.service', check=False), 'linger': linger()}


def remove(args):
    unit_path, state = paths(args)
    if unit_path.exists():
        systemctl('disable', '--now', args.name+'.service', check=False)
        unit_path.unlink()
        systemctl('daemon-reload')
    return {'status': 'removed', 'kept': [str(state)+' (job queue and venv)']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('command', choices=('install', 'status', 'remove'))
    parser.add_argument('--name', default='convergence-alert-receiver')
    parser.add_argument('--env-file', help="the bundle's receiver.env")
    parser.add_argument('--venv', help='virtualenv for the receiver (default: under the state directory)')
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--netclaw-dir', help='NetClaw checkout, for JEV case reviews')
    parser.add_argument('--jev-env-file', help="file with NetClaw's JEV settings (default: the checkout's .env)")
    parser.add_argument('--rag-python', help='Python of NetClaw\'s RAG runtime '
                        '(mcp.servers["rag-mcp"].command in ~/.openclaw/openclaw.json); needs --netclaw-dir')
    parser.add_argument("--start", action="store_true", help="enable and restart the service")
    args = parser.parse_args(argv)
    if not NAME.fullmatch(args.name):
        parser.error('invalid --name')
    try:
        if args.command == 'install':
            if not args.env_file:
                parser.error('install needs --env-file')
            result = install(args)
        else:
            result = {'status': status, 'remove': remove}[args.command](args)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'receiver: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

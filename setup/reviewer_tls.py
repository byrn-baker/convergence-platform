#!/usr/bin/env python3
"""Renew the reviewer and receiver private-CA TLS leaves from a systemd user timer.

  python3 setup/reviewer_tls.py plan    --config ~/.config/convergence/reviewer/tls-renewal.json
  python3 setup/reviewer_tls.py renew   --config ...
  python3 setup/reviewer_tls.py install --config ... [--start]
  python3 setup/reviewer_tls.py status  --config ...
  python3 setup/reviewer_tls.py remove

`plan` and `status` change nothing. `renew` advances each leaf by at most one step (see
setup/tls_renewal.py) and pushes expiry metrics when the config names a Pushgateway. The
running reviewer API, HUD, relay and receiver reload renewed files in place. `install`
writes a timer that runs `renew` from this checkout every six hours; `remove` undoes only
what install recorded. The config file names CA and target paths, never key contents; see
docs/convergence/setup/reviewer-tls.md. Design: specs/1016-tls-renewal-reload/spec.md.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tls_renewal

ROOT = Path(__file__).resolve().parents[1]
UNIT = 'convergence-reviewer-tls'


def unit_dir():
    return Path(os.environ.get('XDG_CONFIG_HOME', '~/.config')).expanduser()/'systemd/user'


def units(config):
    command = f'{sys.executable} {ROOT/"setup/reviewer_tls.py"} renew --config {config}'
    service = ['[Unit]', 'Description=Renew Convergence reviewer and receiver TLS certificates',
               'After=network-online.target', '', '[Service]', 'Type=oneshot', f'ExecStart={command}',
               'UMask=0077', 'NoNewPrivileges=yes', '']
    timer = ['[Unit]', 'Description=Renew Convergence reviewer and receiver TLS certificates every six hours', '',
             '[Timer]', 'OnCalendar=*-*-* 00/6:17:00', 'Persistent=true', '', '[Install]', 'WantedBy=timers.target', '']
    return {UNIT + '.service': '\n'.join(service), UNIT + '.timer': '\n'.join(timer)}


def systemctl(*args, check=True):
    result = subprocess.run(['systemctl', '--user', *args], capture_output=True, text=True, check=False)
    if check and result.returncode:
        raise RuntimeError(f'systemctl --user {" ".join(args)}: {result.stderr.strip()[-400:]}')
    return result.stdout.strip()


def push(settings, kube, rows, now):
    target = settings.get('pushgateway')
    if not target:
        return None
    path = (f'/api/v1/namespaces/{target["namespace"]}/services/{target["service"]}/proxy'
            f'/metrics/job/{target.get("job", "convergence-tls-renewal")}')
    try:
        kube.post_raw(path, tls_renewal.metrics(rows, now))
    except tls_renewal.RenewalError:
        return 'metrics push failed'
    return None


def manifest_path(settings_dir):
    return settings_dir/'install.json'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('command', choices=('plan', 'renew', 'install', 'status', 'remove'))
    parser.add_argument('--config', default='~/.config/convergence/reviewer/tls-renewal.json')
    parser.add_argument('--start', action='store_true', help='install: enable and start the timer')
    args = parser.parse_args(argv)
    config = Path(args.config).expanduser().resolve()
    state_home = Path(os.environ.get('XDG_STATE_HOME', '~/.local/state')).expanduser()/'convergence'/UNIT

    if args.command == 'remove':
        manifest = manifest_path(state_home)
        if not manifest.exists():
            print(json.dumps({'removed': [], 'note': 'nothing recorded by install'}))
            return 0
        recorded = json.loads(manifest.read_text())
        systemctl('disable', '--now', UNIT + '.timer', check=False)
        for name in recorded['units']:
            (unit_dir()/name).unlink(missing_ok=True)
        systemctl('daemon-reload')
        manifest.unlink()
        print(json.dumps({'removed': recorded['units'], 'kept': 'state, archive and pending certificates'}))
        return 0

    try:
        settings, leaves, state_dir, kube = tls_renewal.load_config(config)
    except (tls_renewal.RenewalError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'error': f'config: {error}' if isinstance(error, tls_renewal.RenewalError)
                          else f'config: {type(error).__name__}'}))
        return 2
    renewal = tls_renewal.Renewal(settings, leaves, state_dir)

    if args.command == 'plan':
        rows = renewal.plan()
        print(json.dumps({'leaves': rows}, indent=2))
        return 1 if any('error' in row for row in rows) else 0
    if args.command == 'renew':
        rows = renewal.run()
        pushed = push(settings, kube, rows, renewal.now)
        print(json.dumps({'leaves': rows, **({'push': pushed} if pushed else {})}, indent=2))
        return 1 if pushed or any('error' in row for row in rows) else 0
    if args.command == 'install':
        written = units(config)
        unit_dir().mkdir(parents=True, exist_ok=True)
        for name, text in written.items():
            (unit_dir()/name).write_text(text)
        tls_renewal.private_dir(state_home)
        manifest_path(state_home).write_text(json.dumps({'units': sorted(written), 'config': str(config)}))
        systemctl('daemon-reload')
        if args.start:
            systemctl('enable', '--now', UNIT + '.timer')
        print(json.dumps({'installed': sorted(written), 'started': args.start}))
        return 0
    timer = {'active': systemctl('is-active', UNIT + '.timer', check=False) or 'unknown',
             'enabled': systemctl('is-enabled', UNIT + '.timer', check=False) or 'unknown',
             'last_result': systemctl('show', UNIT + '.service', '-p', 'Result', '--value', check=False)}
    rows = renewal.plan()
    print(json.dumps({'timer': timer, 'leaves': rows}, indent=2))
    return 1 if any('error' in row for row in rows) else 0


if __name__ == '__main__':
    sys.exit(main())

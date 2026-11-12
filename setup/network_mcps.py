#!/usr/bin/env python3
"""Install the pfSense and UniFi MCP servers for NetClaw (OpenClaw) at pinned commits.

Convergence investigations read the edge firewall and the Wi-Fi network through two
community MCP servers. For each one this helper, as one reversible unit:
  - clones the server at a pinned commit (verified after checkout);
  - builds a private virtualenv for it;
  - checks that it starts and lists its tools over MCP stdio (no credentials used);
  - registers it in OpenClaw's mcp.servers through `openclaw config patch`, with every
    credential as a ${VAR} reference to <openclaw-dir>/.env, never a value.

  python3 setup/network_mcps.py plan
  python3 setup/network_mcps.py apply --replace-existing --pfsense-version CE_2_8_1 --restart
  python3 setup/network_mcps.py status [--probe]
  python3 setup/network_mcps.py remove

`plan` changes nothing. An existing server entry with the same name (pfsense-mcp,
unifi-network) is replaced only with --replace-existing, and `remove` puts it back.
pfSense runs read-only (MCP_READ_ONLY=true) unless --pfsense-read-write. UniFi has no
read-only mode; its configuration-changing tools need a preview and confirm=True, and
the agents' tool allowlists decide what they may call.
"""
import argparse
import datetime
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

STATE = 'convergence-network-mcps.json'
MARKER = '.convergence-network-mcp'
VAR = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
# PfSenseVersion members accepted by pfsense-mcp-server v1.1.0 (src/models.py).
PFSENSE_VERSIONS = ('CE_2_8_1', 'CE_2_9_0', 'PLUS_25_11_1', 'PLUS_26_03', 'PLUS_26_03_1', 'PLUS_26_07',
                    'CE_2_8_0', 'PLUS_24_11', 'PLUS_25_11', 'CE_26_03')
SERVERS = {
    'pfsense': {
        'name': 'pfsense-mcp',
        'repo': 'https://github.com/gensecaihq/pfsense-mcp-server.git',
        'ref': 'v1.1.0',
        'commit': '3b2ea69e504f4fe6dec5974ee804f5fdc1c19e05',
        'python': (3, 11),
        'install': ['-r', 'requirements.txt'],
        'args': ['-u', '-m', 'src.main'],
        'cwd': True,
        # server variable -> .env variable; required ones must be in .env.
        'env': {'PFSENSE_URL': 'PFSENSE_URL', 'PFSENSE_VERSION': 'PFSENSE_VERSION',
                'AUTH_METHOD': 'PFSENSE_AUTH_METHOD', 'PFSENSE_API_KEY': 'PFSENSE_API_KEY',
                'PFSENSE_USERNAME': 'PFSENSE_USERNAME', 'PFSENSE_PASSWORD': 'PFSENSE_PASSWORD',
                'VERIFY_SSL': 'PFSENSE_VERIFY_SSL', 'PFSENSE_CA_FILE': 'PFSENSE_CA_FILE'},
        'required': ('PFSENSE_URL', 'PFSENSE_VERSION'),
        'probe_env': {'PFSENSE_URL': 'https://192.0.2.1', 'PFSENSE_VERSION': 'CE_2_8_1',
                      'AUTH_METHOD': 'api_key', 'PFSENSE_API_KEY': 'probe', 'MCP_ENABLE_LOG_FILES': 'false'},
    },
    'unifi': {
        'name': 'unifi-network',
        # Integration API device tools, the UniFi OS Server probe fix and
        # UNIFI_PRELOAD_GROUPS (chris2ao/unifi-mcp#9); move to the upstream release
        # once it is merged.
        'repo': 'https://github.com/byrn-baker/unifi-mcp.git',
        'ref': 'integration-devices',
        'commit': 'cb2b1f1de4b770ffcae5affc3fb88432955daa91',
        'python': (3, 12),
        'install': ['.'],
        'args': ['-u', '-m', 'unifi_mcp'],
        'cwd': False,
        'env': {'UNIFI_HOST': 'UNIFI_HOST', 'UNIFI_API_KEY': 'UNIFI_API_KEY', 'UNIFI_SITE': 'UNIFI_SITE',
                'UNIFI_VERIFY_SSL': 'UNIFI_VERIFY_SSL', 'UNIFI_TOOL_GROUPS': 'UNIFI_TOOL_GROUPS',
                'UNIFI_PRELOAD_GROUPS': 'UNIFI_PRELOAD_GROUPS'},
        'required': ('UNIFI_HOST', 'UNIFI_API_KEY'),
        'probe_env': {'UNIFI_HOST': 'https://192.0.2.1', 'UNIFI_API_KEY': 'probe'},
    },
}


def read_env(path):
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.strip().removeprefix('export ').partition('=')
            if sep and VAR.fullmatch(key.strip()):
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env(path, updates):
    """Set or remove (value None) keys, keeping every other line; mode 600."""
    lines = path.read_text().splitlines() if path.exists() else []
    done, out = set(), []
    for line in lines:
        key = line.strip().removeprefix('export ').partition('=')[0].strip()
        if key in updates:
            if updates[key] is not None and key not in done:
                out.append(f'{key}={updates[key]}')
            done.add(key)
        else:
            out.append(line)
    out += [f'{k}={v}' for k, v in updates.items() if v is not None and k not in done]
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.'+path.name+'.')
    with os.fdopen(fd, 'w') as handle:
        handle.write('\n'.join(out)+'\n')
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def entry(key, root, env, opts):
    """The OpenClaw mcp.servers entry for one server: paths, and ${VAR} references only."""
    spec = SERVERS[key]
    source = root/key/'source'
    server = {'command': str(root/key/'venv/bin/python'), 'args': list(spec['args'])}
    if spec['cwd']:
        server['cwd'] = str(source)
    refs = {name: '${'+var+'}' for name, var in spec['env'].items() if var in env}
    if key == 'pfsense':
        refs['MCP_READ_ONLY'] = 'false' if opts.get('pfsense_read_write') else 'true'
        refs['MCP_ENABLE_LOG_FILES'] = 'false'
    if key == 'unifi' and 'UNIFI_PRELOAD_GROUPS' not in env:
        # The investigator may call integration_* reads but not the loaders, so
        # register that group at startup.
        refs['UNIFI_PRELOAD_GROUPS'] = 'network:integration'
    server['env'] = refs
    return server


def replacing(old, new):
    """A merge patch that turns old into exactly new: OpenClaw's config patch merges objects,
    so keys of old that new lacks are set to null (deleted)."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return new
    patch = {k: None for k in old if k not in new}
    patch.update({k: replacing(old.get(k), v) for k, v in new.items()})
    return patch


def plan(config, env, opts, root):
    """Return what apply would do. Pure: reads only its arguments and the install directory."""
    conflicts, actions, patch = [], [], {}
    state = opts.get('state') or {}
    servers = (config.get('mcp') or {}).get('servers') or {}
    env = dict(env)
    version = opts.get('pfsense_version')
    env_updates = {}
    for key in opts['servers']:
        spec = SERVERS[key]
        name, recorded = spec['name'], (state.get('servers') or {}).get(key) or {}
        if key == 'pfsense':
            if version:
                if version not in PFSENSE_VERSIONS:
                    conflicts.append(f'--pfsense-version {version}: not a pfsense-mcp-server {spec["ref"]} '
                                     f'version ({", ".join(PFSENSE_VERSIONS)})')
                elif env.get('PFSENSE_VERSION') != version:
                    env_updates['PFSENSE_VERSION'] = version
                    env['PFSENSE_VERSION'] = version
            elif env.get('PFSENSE_VERSION') and env['PFSENSE_VERSION'] not in PFSENSE_VERSIONS:
                conflicts.append(f'PFSENSE_VERSION in .env is not accepted by pfsense-mcp-server {spec["ref"]}; '
                                 'pass --pfsense-version with the firewall\'s version '
                                 f'({", ".join(PFSENSE_VERSIONS)})')
            method = (env.get('PFSENSE_AUTH_METHOD') or 'api_key').lower()
            needed = {'basic': ('PFSENSE_USERNAME', 'PFSENSE_PASSWORD')}.get(method, ('PFSENSE_API_KEY',))
        else:
            needed = ()
        missing = [v for v in (*spec['required'], *needed) if not env.get(v)]
        if missing:
            conflicts.append(f'{name}: {", ".join(missing)} not set in .env')
        if name in servers and not recorded and not opts.get('replace_existing'):
            conflicts.append(f'MCP server {name} is already configured; re-run with --replace-existing to '
                             'replace it (remove puts it back)')
        directory = root/key
        if directory.exists() and not (directory/MARKER).exists():
            conflicts.append(f'{directory} exists and was not created by this tool')
        server = entry(key, root, env, opts)
        patch[name] = replacing(servers.get(name), server)
        actions.append(f'{"replace" if name in servers and not recorded else "register"} MCP server {name}: '
                       f'{spec["repo"]} {spec["ref"]} ({spec["commit"][:12]}) in {directory}, '
                       f'env {", ".join(sorted(server["env"]))}')
    if env_updates:
        actions.append(f'set PFSENSE_VERSION={env_updates["PFSENSE_VERSION"]} in .env')
    return {'conflicts': conflicts, 'actions': actions, 'patch': {'mcp': {'servers': patch}},
            'env_updates': env_updates, 'previous': {SERVERS[k]['name']: servers.get(SERVERS[k]['name'])
                                                     for k in opts['servers']}}


def run(cmd, **kwargs):
    result = subprocess.run(cmd, text=True, capture_output=True, check=False, **kwargs)
    if result.returncode:
        detail = '\n'.join(t.strip() for t in (result.stdout, result.stderr) if t.strip())
        raise RuntimeError(f'{" ".join(str(c) for c in cmd[:4])} failed: {detail[-600:]}')
    return result.stdout


def python_version(python):
    out = run([python, '-c', 'import sys; print(*sys.version_info[:2])'])
    return tuple(int(part) for part in out.split())


def install(key, root, python):
    """Clone at the pinned commit and build the virtualenv. Idempotent for a directory we created."""
    spec, directory = SERVERS[key], root/key
    if python_version(python) < spec['python']:
        raise RuntimeError(f'{SERVERS[key]["name"]} needs Python {".".join(map(str, spec["python"]))}+; '
                           'pass --python')
    directory.mkdir(parents=True, exist_ok=True)
    (directory/MARKER).write_text(json.dumps({'server': key, 'commit': spec['commit']})+'\n')
    source, venv = directory/'source', directory/'venv'
    if not (source/'.git').exists():
        run(['git', 'clone', '--quiet', '--no-checkout', spec['repo'], str(source)])
    run(['git', '-C', str(source), 'fetch', '--quiet', 'origin', spec['commit']])
    run(['git', '-C', str(source), 'checkout', '--quiet', '--force', '--detach', spec['commit']])
    head = run(['git', '-C', str(source), 'rev-parse', 'HEAD']).strip()
    if head != spec['commit']:
        raise RuntimeError(f'{source} is at {head}, not the pinned {spec["commit"]}')
    if not (venv/'bin/python').exists():
        run([python, '-m', 'venv', str(venv)])
    run([str(venv/'bin/python'), '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
         *spec['install']], cwd=source)


def probe(command, args, cwd, env, timeout=60):
    """Start an MCP stdio server, initialize it and list its tools. Returns the tool names."""
    proc = subprocess.Popen([command, *args], cwd=cwd, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    deadline = time.monotonic()+timeout

    def send(message):
        proc.stdin.write(json.dumps(message)+'\n')
        proc.stdin.flush()

    def receive(wanted):
        while time.monotonic() < deadline:
            ready, _, _ = select.select([proc.stdout], [], [], max(0.0, deadline-time.monotonic()))
            if not ready:
                break
            line = proc.stdout.readline()
            if not line:
                break
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get('id') == wanted:
                if 'error' in message:
                    raise RuntimeError(f'MCP error: {str(message["error"])[:300]}')
                return message.get('result') or {}
        raise RuntimeError('the server did not answer over MCP stdio in time')

    try:
        send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
              'params': {'protocolVersion': '2025-06-18', 'capabilities': {},
                         'clientInfo': {'name': 'convergence-setup', 'version': '1'}}})
        receive(1)
        send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        send({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}})
        return [tool.get('name') for tool in receive(2).get('tools') or []]
    finally:
        proc.kill()
        proc.wait()


def probe_server(key, root, opts, server=None):
    """Probe with placeholder credentials: listing tools contacts no device.

    `server` is a registered entry to probe as configured (its literal env values, such as
    MCP_READ_ONLY, apply); by default the entry apply would write."""
    spec = SERVERS[key]
    server = server or entry(key, root, {}, opts)
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': os.environ.get('HOME', '/'),
           **spec['probe_env'], **{k: v for k, v in server['env'].items() if not v.startswith('${')}}
    tools = probe(server['command'], server['args'], server.get('cwd'), env)
    if not tools:
        raise RuntimeError(f'{spec["name"]} started but listed no tools')
    return tools


def openclaw_env(home):
    """Point the openclaw CLI at --openclaw-dir, whatever the caller's shell selects.

    OPENCLAW_HOME is left alone: it replaces the user's home directory, not the state directory.
    """
    return {**os.environ, 'OPENCLAW_STATE_DIR': str(home), 'OPENCLAW_CONFIG_PATH': str(home/'openclaw.json')}


def openclaw_patch(openclaw, patch, home):
    with tempfile.NamedTemporaryFile('w', suffix='.json5', delete=False) as handle:
        json.dump(patch, handle)
    try:
        run([openclaw, 'config', 'patch', '--file', handle.name, '--dry-run'], env=openclaw_env(home))
        run([openclaw, 'config', 'patch', '--file', handle.name], env=openclaw_env(home))
    finally:
        os.unlink(handle.name)


def load(home):
    config = json.loads((home/'openclaw.json').read_text())
    state_path = home/STATE
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    return config, read_env(home/'.env'), state


def options(args, state):
    return {'servers': args.server or list(SERVERS), 'replace_existing': args.replace_existing,
            'pfsense_version': args.pfsense_version, 'pfsense_read_write': args.pfsense_read_write,
            'state': state}


def report(result, outcome):
    shown = {k: v for k, v in result.items() if k not in ('patch', 'previous', 'env_updates')}
    shown['status'] = outcome
    shown['config_patch'] = result['patch']
    print(json.dumps(shown, indent=2))
    return 0 if outcome in ('plan', 'applied') else 2


def apply(args, home, root):
    config, env, state = load(home)
    opts = options(args, state)
    result = plan(config, env, opts, root)
    if result['conflicts']:
        return report(result, 'blocked')
    tools = {}
    for key in opts['servers']:
        install(key, root, args.python)
        tools[SERVERS[key]['name']] = len(probe_server(key, root, opts))
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    (home/'backups').mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix=f'convergence-network-mcps-{stamp}-', dir=home/'backups'))
    for name in ('openclaw.json', '.env', STATE):
        if (home/name).exists():
            shutil.copy2(home/name, backup/name)
    old_env = {k: env.get(k) for k in result['env_updates']}
    if result['env_updates']:
        write_env(home/'.env', result['env_updates'])
    try:
        openclaw_patch(args.openclaw, result['patch'], home)
    except RuntimeError:
        if result['env_updates']:
            write_env(home/'.env', old_env)
        raise
    recorded = dict(state.get('servers') or {})
    for key in opts['servers']:
        name = SERVERS[key]['name']
        previous = (recorded.get(key) or {}).get('previous', result['previous'][name])
        recorded[key] = {'name': name, 'commit': SERVERS[key]['commit'], 'directory': str(root/key),
                         'previous': previous}
    env_restore = {**state.get('env_restore', {}),
                   **{k: v for k, v in old_env.items() if k not in state.get('env_restore', {})}}
    state = {'servers': recorded, 'env_restore': env_restore, 'backup': str(backup)}
    (home/STATE).write_text(json.dumps(state, indent=2)+'\n')
    os.chmod(home/STATE, 0o600)
    result['tools_listed'] = tools
    if args.restart:
        run(['systemctl', '--user', 'restart', 'openclaw-gateway.service'])
        result['actions'].append('restarted openclaw-gateway.service')
    result['backup'] = str(backup)
    return report(result, 'applied')


def remove(args, home):
    config, _, state = load(home)
    if not state:
        print(json.dumps({'status': 'nothing recorded', 'state': str(home/STATE)}))
        return 0
    servers = (config.get('mcp') or {}).get('servers') or {}
    keys = args.server or list(state['servers'])
    patch, kept, removed = {}, [], []
    for key in keys:
        info = state['servers'].get(key)
        if not info:
            continue
        name = info['name']
        if name in servers and servers[name].get('command') != str(Path(info['directory'])/'venv/bin/python'):
            kept.append(f'MCP server {name} (changed since install; left as is)')
        else:
            # The replaced entry, exactly, or None to delete ours.
            patch[name] = replacing(servers.get(name), info.get('previous'))
    if patch:
        openclaw_patch(args.openclaw, {'mcp': {'servers': patch}}, home)
    if 'pfsense' in keys and state.get('env_restore'):
        write_env(home/'.env', state['env_restore'])
    for key in keys:
        info = state['servers'].pop(key, None)
        if not info:
            continue
        directory = Path(info['directory'])
        if (directory/MARKER).exists():
            shutil.rmtree(directory)
            removed.append(str(directory))
    if 'pfsense' in keys:
        state.pop('env_restore', None)
    if state['servers']:
        (home/STATE).write_text(json.dumps(state, indent=2)+'\n')
    else:
        (home/STATE).unlink()
    print(json.dumps({'status': 'removed', 'restored_or_deleted': sorted(patch), 'deleted': removed,
                      'kept': kept, 'note': 'restart the OpenClaw gateway (openclaw-gateway.service)'}, indent=2))
    return 0


def status(args, home, root):
    config, env, state = load(home)
    servers = (config.get('mcp') or {}).get('servers') or {}
    opts = options(args, state)
    out, ok = {}, True
    for key in opts['servers']:
        spec, directory = SERVERS[key], root/key
        configured = servers.get(spec['name']) or {}
        head = ''
        if (directory/'source/.git').exists():
            head = subprocess.run(['git', '-C', str(directory/'source'), 'rev-parse', 'HEAD'],
                                  capture_output=True, text=True, check=False).stdout.strip()
        checks = {
            'registered': configured.get('command') == str(directory/'venv/bin/python'),
            'pinned_commit': head == spec['commit'],
            'venv': (directory/'venv/bin/python').exists(),
            'env': all(env.get(v) for v in spec['required']),
        }
        if args.probe and checks['venv']:
            try:
                checks['probe_tools'] = len(probe_server(key, root, opts,
                                                         configured if checks['registered'] else None))
            except (OSError, RuntimeError) as exc:
                checks['probe_tools'] = 0
                checks['probe_error'] = str(exc)[:300]
        ok = ok and all(checks[k] for k in ('registered', 'pinned_commit', 'venv', 'env')) \
            and checks.get('probe_tools', 1) > 0
        out[spec['name']] = checks
    print(json.dumps({'status': 'ready' if ok else 'incomplete', 'servers': out}, indent=2))
    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('command', choices=('plan', 'apply', 'status', 'remove'))
    parser.add_argument('--server', action='append', choices=sorted(SERVERS),
                        help='pfsense or unifi (repeatable; default both)')
    parser.add_argument('--openclaw-dir', default='~/.openclaw')
    parser.add_argument('--openclaw', default=shutil.which('openclaw') or 'openclaw', help='openclaw CLI')
    parser.add_argument('--install-dir', default='~/.local/share/convergence/mcp',
                        help='where the sources and virtualenvs go')
    parser.add_argument('--python', default=sys.executable, help='Python for the virtualenvs')
    parser.add_argument('--replace-existing', action='store_true',
                        help='replace pfsense-mcp / unifi-network entries this tool did not create')
    parser.add_argument('--pfsense-version', help="the firewall's PFSENSE_VERSION, written to .env")
    parser.add_argument('--pfsense-read-write', action='store_true',
                        help='do not set MCP_READ_ONLY=true for pfSense')
    parser.add_argument('--probe', action='store_true', help='status: start each server and list its tools')
    parser.add_argument('--restart', action='store_true', help='restart openclaw-gateway.service (user unit)')
    args = parser.parse_args(argv)
    home = Path(os.path.expanduser(args.openclaw_dir))
    root = Path(os.path.expanduser(args.install_dir))
    try:
        if args.command == 'plan':
            config, env, state = load(home)
            result = plan(config, env, options(args, state), root)
            return report(result, 'plan' if not result['conflicts'] else 'blocked')
        if args.command == 'apply':
            return apply(args, home, root)
        if args.command == 'status':
            return status(args, home, root)
        return remove(args, home)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f'network_mcps: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Build a Convergence HUD from an upstream NetClaw checkout and activate its user units.

build creates a separate artifact. plan prints the intended installation. apply
optionally restarts both units, verifies their effective build and restores the
previous installation on failure. remove deletes unchanged managed files.
"""
import argparse
import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from hud_activation import JOURNAL, activate, locked, recover, snapshot, validate_private_state, write_file

REPO = Path(__file__).resolve().parent.parent
HUD = REPO/'integrations/netclaw/hud'
OVERLAY = HUD/'overlay'
PACKAGE = REPO/'ui/convergence-hud'
UI_DIR = Path('ui/netclaw-visual')
MODULE_DIR = UI_DIR/'modules/convergence'
PATCHES = HUD/'patches'
STATE = 'convergence-hud.json'
MARKER = '.convergence-hud'
DROPIN = 'zz-convergence-hud.conf'
UNITS = ('netclaw-hud.service', 'netclaw-hud-ui.service')


def run(cmd, **kwargs):
    result = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, check=False, **kwargs)
    if result.returncode:
        raise RuntimeError(f"{' '.join(map(str, cmd))} failed: {(result.stderr or result.stdout)[-800:]}")
    return result.stdout


# ── merge ────────────────────────────────────────────────────────────────────

def host_patches():
    """A patch may modify exactly the existing regular file named by its path."""
    for path in sorted(PATCHES.rglob('*.patch')):
        rel = path.relative_to(PATCHES).with_suffix('')
        headers = path.read_bytes().splitlines()
        expected = f'diff --git a/{rel} b/{rel}'.encode()
        index = re.fullmatch(rb'index ([0-9a-f]{40})\.\.[0-9a-f]{40} 100(?:644|755)', headers[1]) if len(headers) > 1 else None
        if (path.is_symlink() or len(headers) < 5 or headers[0] != expected or not index
                or headers[2:4] != [f'--- a/{rel}'.encode(), f'+++ b/{rel}'.encode()]
                or not headers[4].startswith(b'@@ ')
                or any(line.startswith(b'diff --git ') for line in headers[5:])):
            raise ValueError(f'{path}: expected a single full-index HUD file patch')
        yield rel, path, index.group(1).decode()


def git_environment():
    """Ignore repository and index overrides inherited from Git hooks."""
    return {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}


def merge_patched(hud, netclaw):
    """Apply full-index patches in an isolated index with upstream's Git blob history."""
    patches = list(host_patches())
    if not patches:
        return []
    env = git_environment()
    objects = run(['git', '-C', netclaw, 'rev-parse', '--path-format=absolute', '--git-path', 'objects'], env=env)
    env['GIT_ALTERNATE_OBJECT_DIRECTORIES'] = objects.strip()
    merged = []
    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        run(['git', 'init', '-q', work], env=env)
        for rel, path, base_blob in patches:
            target = hud/rel
            if target.is_symlink() or target.parent.resolve() != target.parent.absolute():
                raise ValueError(f'{rel}: symlink in upstream HUD patch destination')
            if not target.is_file():
                raise ValueError(f'{rel}: no longer in upstream')
            staged = work/rel
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, staged)
            run(['git', 'add', '-f', '--', rel], cwd=work, env=env)
            outcome = subprocess.run(['git', 'apply', '--3way', '--whitespace=nowarn', '--', path.absolute()],
                                     cwd=work, env=env, capture_output=True)
            if outcome.returncode:
                raise ValueError(f'{rel}: conflict with upstream; fetch the pinned HUD baseline or rebase the patch')
            if run(['git', 'hash-object', '--', target], cwd=work, env=env).strip() != base_blob:
                merged.append(str(rel))
        # Publish only after every patch applies successfully.
        for rel, _, _ in patches:
            (hud/rel).write_bytes((work/rel).read_bytes())
    return merged


# ── build ────────────────────────────────────────────────────────────────────

def overlay_files(overlay=None):
    overlay = overlay or OVERLAY
    return sorted(p for p in overlay.rglob('*') if p.is_file() and '__pycache__' not in p.parts)


def build_inputs(overlay):
    """Map each added source to its unique location in the NetClaw archive."""
    sources = ((overlay, UI_DIR), (PACKAGE/'modules/convergence', MODULE_DIR))
    for tree, destination in sources:
        for source in overlay_files(tree):
            yield source, destination/source.relative_to(tree)
    for notice in ('LICENSE', 'NOTICE'):
        yield PACKAGE/notice, MODULE_DIR/notice


def overlay_digest(overlay=None):
    h = hashlib.sha256(b'convergence-hud-build-v2\0')
    for source, destination in build_inputs(overlay or OVERLAY):
        h.update(os.fsencode(destination) + b'\0' + source.read_bytes() + b'\0')
        h.update(str(source.stat().st_mode & 0o111).encode() + b'\0')
    for relative, patch, _ in host_patches():
        h.update(b'patch\0' + os.fsencode(relative) + b'\0' + patch.read_bytes() + b'\0')
    return h.hexdigest()


def checkout(netclaw):
    if not (netclaw/'ui/netclaw-visual/server.js').exists():
        raise ValueError(f'{netclaw} is not a NetClaw checkout (no ui/netclaw-visual/server.js)')
    env = git_environment()
    head = run(['git', '-C', netclaw, 'rev-parse', 'HEAD'], env=env).strip()
    dirty = run(['git', '-C', netclaw, 'status', '--porcelain', '--untracked-files=no'], env=env).strip()
    return head, bool(dirty)


def tree_for(root, head, digest):
    return root/f'{head[:12]}-{digest[:12]}'


def build(netclaw, root, node, overlay=None):
    """Build (or reuse) the HUD tree. Returns its path."""
    overlay = overlay or OVERLAY
    head, dirty = checkout(netclaw)
    if dirty:
        raise ValueError(f'{netclaw} has uncommitted changes; the HUD is built from a clean commit')
    digest = overlay_digest(overlay)
    target = tree_for(root, head, digest)

    def verified_target():
        if target.is_symlink() or (target/MARKER).is_symlink():
            raise ValueError(f'{target}: unsafe build tree')
        record = json.loads((target/MARKER).read_bytes())
        if (record.get('netclaw_commit') != head or record.get('overlay_digest') != digest
                or not (target/UI_DIR/'dist/index.html').is_file()):
            raise ValueError(f'{target}: incomplete or mismatched build')
        return target

    if target.exists() or target.is_symlink():
        return verified_target()
    node_bin = shutil.which(node)
    if not node_bin:
        raise ValueError('Cannot find the requested Node executable')
    node = os.path.abspath(node_bin)
    root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='.build-', dir=root))
    try:
        with subprocess.Popen(['git', '-C', str(netclaw), 'archive', head], env=git_environment(), stdout=subprocess.PIPE) as archive:
            try:
                subprocess.run(['tar', '-x', '-C', str(scratch)], stdin=archive.stdout, check=True)
            finally:
                archive.stdout.close()
            if archive.wait():
                raise RuntimeError('git archive failed')
        hud = scratch/UI_DIR
        for source, relative in build_inputs(overlay):
            dest = scratch/relative
            if dest.parent.resolve() != dest.parent.absolute() or dest.is_symlink() or source.is_symlink():
                raise ValueError(f'{relative}: symlink in overlay destination or source')
            if dest.exists():
                raise ValueError(f'{relative} now exists upstream or in another build input')
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        merge_patched(hud, netclaw)
        npm = Path(node).parent/'npm'
        env = {**os.environ, 'PATH': f'{Path(node).parent}:{os.environ.get("PATH", "")}'}
        run([npm, 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=hud, env=env)
        run([node, 'node_modules/vite/bin/vite.js', 'build'], cwd=hud, env=env)
        if not (hud/'dist/index.html').exists():
            raise RuntimeError('vite build produced no dist/index.html')
        if overlay_digest(overlay) != digest:
            raise RuntimeError('Overlay changed during the build; retry against the new source')
        (scratch/MARKER).write_text(json.dumps({'netclaw_commit': head, 'overlay_digest': digest,
                                                'netclaw_dir': str(netclaw)}, indent=2)+'\n')
        try:
            os.rename(scratch, target)
        except OSError:
            # Another independent builder may have atomically published this artifact.
            if not target.exists():
                raise
            verified_target()
            shutil.rmtree(scratch)
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    return target


# ── units ────────────────────────────────────────────────────────────────────

def dropin(tree, env_file):
    def systemd_path(path):
        # Both directives take a literal path; systemd expands percent specifiers.
        text = str(path)
        if not path.is_absolute() or any(ord(char) < 32 for char in text) or '\\' in text:
            raise ValueError('systemd paths must be absolute and free of backslashes and control characters')
        return text.replace('%', '%%')
    return ('# Managed by convergence-platform setup/hud.py\n'
            '[Service]\n'
            f'WorkingDirectory={systemd_path(tree/UI_DIR)}\n'
            f'EnvironmentFile={systemd_path(env_file)}\n')


def env_text(values):
    lines = ['# Managed by convergence-platform setup/hud.py.']
    for key, value in sorted(values.items()):
        if not re.fullmatch(r'[a-zA-Z_][a-zA-Z_0-9]*', key):
            raise ValueError('Invalid environment variable name')
        if any(ord(char) < 32 for char in value):
            raise ValueError(f'{key}: newline or control character in value')
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
        lines.append(f'{key}="{escaped}"')
    return '\n'.join(lines) + '\n'


def hud_env(args):
    values = {'NETCLAW_ENV_FILE': str(Path(args.openclaw_dir).expanduser().absolute()/'.env')}
    testbed = Path(args.testbed).expanduser().absolute()
    if testbed.exists():
        values['NETCLAW_TESTBED_FILE'] = str(testbed)
    for item in args.env or []:
        key, sep, value = item.partition('=')
        if not sep or not re.fullmatch(r'[a-zA-Z_][a-zA-Z_0-9]*', key):
            raise ValueError('--env expects KEY=VALUE')
        values[key] = value
    return values


def load_state(home):
    record = validate_private_state(home/STATE)
    return json.loads(record['text']) if record else {}


def managed_paths(unit_dir, env_file):
    return [*(unit_dir/f'{unit}.d'/DROPIN for unit in UNITS), env_file]


def plan(args, unit_dir, root, env_file, state):
    netclaw = Path(args.netclaw_dir).expanduser().absolute()
    head, dirty = checkout(netclaw)
    digest = overlay_digest()
    tree = tree_for(root, head, digest)
    paths = managed_paths(unit_dir, env_file)
    home = Path(args.openclaw_dir).expanduser().absolute()
    managed = [*paths, home/STATE, home/JOURNAL]
    if len({path.resolve() for path in managed}) != len(managed):
        raise ValueError('The environment, drop-in and installation records need separate paths')
    files = {str(path): dropin(tree, env_file) for path in paths[:-1]}
    files[str(env_file)] = env_text(hud_env(args))
    conflicts = []
    if state.get('files') and set(state['files']) != set(files):
        conflicts.append('Managed paths differ from the previous installation; remove it before moving the files')
    for name in files:
        path = Path(name)
        previous = state.get('files', {}).get(name)
        current = snapshot(path)
        if current is not None and previous is None:
            conflicts.append(f'{path} exists and was not written by this tool')
        elif previous is not None and (not current or current['text'] != previous):
            conflicts.append(f'{path} changed since installation')
    if dirty:
        conflicts.append(f'{netclaw} has uncommitted changes')
    return {'netclaw_dir': str(netclaw), 'netclaw_commit': head, 'overlay_digest': digest,
            'tree': str(tree), 'tree_built': (tree/MARKER).is_file(), 'files': files,
            'conflicts': conflicts}


def working_directories():
    """Read the working directories of the systemd service processes, or None for an idle unit."""
    result = {}
    for unit in UNITS:
        pid = int(run(['systemctl', '--user', 'show', unit, '--property=MainPID', '--value']).strip())
        result[unit] = Path(os.readlink(f'/proc/{pid}/cwd')) if pid else None
    return result


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class DocumentScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.entries = set()

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            source = dict(attrs).get('src', '')
            if re.fullmatch(r'/assets/[A-Za-z0-9_.-]+\.js', source):
                self.entries.add(source)


def bundled_entries(dist, documents):
    entries = DocumentScripts()
    for document in documents:
        entries.feed(document.decode('utf-8'))
    entries.close()
    return {asset: (dist/asset[1:]).read_bytes() for asset in entries.entries}


def probe(tree, api_port, ui_host, ui_port, attempts=30):
    """Verify both service processes, module registration and both built HTML entries."""
    host = ipaddress.ip_address(ui_host)
    if host.is_unspecified:
        raise ValueError('Use a concrete UI listener address')
    authority = f'[{host}]' if host.version == 6 else str(host)
    api = f'http://127.0.0.1:{api_port}'
    ui = f'http://{authority}:{ui_port}'
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirects())
    dist = tree/UI_DIR/'dist'
    expected = {route: (dist/route).read_bytes() for route in ('index.html', 'classic.html')}
    bundles = bundled_entries(dist, expected.values())
    if not bundles:
        raise RuntimeError('Selected build has no dashboard JavaScript entry bundles')
    last = 'HUD startup timed out'
    for attempt in range(attempts):
        try:
            if any(cwd != tree/UI_DIR for cwd in working_directories().values()):
                raise RuntimeError('A HUD service is not running from the selected build')
            with http.open(api+'/api/modules', timeout=3) as response:
                modules = json.loads(response.read(131073))['modules']
            entry = next((m for m in modules if m.get('id') == 'convergence'), {})
            if entry.get('configured') is not True or entry.get('routes') is not True or entry.get('error'):
                raise RuntimeError('Convergence routes are unavailable in the HUD API')
            with http.open(api + '/api/home/status', timeout=3) as response:
                status = json.loads(response.read(16385))
            if (not isinstance(status, dict) or status.get('configured') is not False
                    or not isinstance(status.get('localOwnerMode'), bool) or 'reviewerOrigin' not in status):
                raise RuntimeError('Convergence status route did not return an anonymous HUD status')
            for name, document in {**expected, **bundles}.items():
                with http.open(ui + '/' + name.lstrip('/'), timeout=3) as response:
                    if response.read(len(document)+1) != document:
                        raise RuntimeError(f'HUD UI did not serve the selected {name}')
            return sorted(m['id'] for m in modules)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            last = str(exc)
        if attempt + 1 < attempts:
            time.sleep(2)
    raise RuntimeError(f'HUD verification failed: {last}')


def restart():
    run(['systemctl', '--user', 'daemon-reload'])
    for unit in UNITS:
        run(['systemctl', '--user', 'restart', unit])


def service_activity():
    previous = {}
    for unit in UNITS:
        value = run(['systemctl', '--user', 'show', unit, '--property=ActiveState', '--value']).strip()
        if value not in ('active', 'inactive', 'failed'):
            raise RuntimeError(f'{unit} is {value or "unavailable"}; retry after its current operation')
        previous[unit] = value == 'active'
    return previous


def restore_services(previous):
    run(['systemctl', '--user', 'daemon-reload'])
    for unit in UNITS:
        action = 'restart' if previous[unit] else 'stop'
        run(['systemctl', '--user', action, unit])


def restore_installation(home, previous):
    restore_services(previous)
    state = load_state(home)
    # A prior verified activation records its own endpoints. Legacy or staged
    # installations and previously inactive units only have a service state to restore.
    endpoints = state.get('verified_endpoints')
    if endpoints and all(previous.values()):
        probe(Path(state['tree']), endpoints['api_port'], endpoints['ui_host'], endpoints['ui_port'])


def state_file(state):
    return {'mode': 0o600, 'text': json.dumps(state, indent=2)+'\n'} if state else None


def apply(args, home, unit_dir, root, env_file):
    paths = managed_paths(unit_dir, env_file)
    with locked(home):
        restore = lambda previous: restore_installation(home, previous)
        recover(home, restore, [home/STATE, *paths])
        before_state = validate_private_state(home/STATE)
        state = json.loads(before_state['text']) if before_state else {}
        result = plan(args, unit_dir, root, env_file, state)
        if result['conflicts']:
            return report(result, 'blocked')
        tree = build(Path(result['netclaw_dir']), root, args.node)
        if str(tree) != result['tree']:
            raise RuntimeError('Build inputs changed during planning; retry with the new revision')
        result['tree_built'] = True
        changes = {}
        for name, text in result['files'].items():
            path = Path(name)
            before = snapshot(path)
            if (before['text'] if before else None) != state.get('files', {}).get(name):
                raise RuntimeError(f'{path} changed during the build')
            changes[path] = (before, {'text': text, 'mode': 0o600 if path == env_file else 0o644})
        new_state = {'files': result['files'], 'tree': str(tree), 'netclaw_commit': result['netclaw_commit'],
                     'overlay_digest': result['overlay_digest'],
                     'trees': sorted(set(state.get('trees', [])) | {str(tree)})}
        if args.restart:
            new_state['verified_endpoints'] = {'api_port': args.api_port, 'ui_host': args.ui_host, 'ui_port': args.ui_port}
        changes[home/STATE] = (before_state, state_file(new_state))
        previous = service_activity() if args.restart else None
        try:
            modules = activate(home, changes, restore, previous_services=previous,
                               restart=restart if args.restart else None,
                               verify=(lambda: probe(tree, args.api_port, args.ui_host, args.ui_port)) if args.restart else None)
        except (OSError, RuntimeError, ValueError) as exc:
            if (home/JOURNAL).exists():
                raise
            result['rolled_back'] = str(exc)
            return report(result, 'failed')
        if args.restart:
            result.update(modules=modules, restarted=list(UNITS))
        else:
            result['note'] = 'Reload systemd and restart both HUD units to activate'
        return report(result, 'applied')


def removable_tree(tree, root):
    if tree.parent != root or tree.is_symlink() or not re.fullmatch(r'[0-9a-f]{12}-[0-9a-f]{12}', tree.name):
        return False
    marker = snapshot(tree/MARKER)
    if not marker:
        return False
    record = json.loads(marker['text'])
    return tree == tree_for(root, record['netclaw_commit'], record['overlay_digest'])


def remove(args, home, root):
    paths = managed_paths(Path(args.unit_dir).expanduser().absolute(), Path(args.env_file).expanduser().absolute())
    with locked(home):
        restore = lambda previous: restore_installation(home, previous)
        recover(home, restore, [home/STATE, *paths])
        before_state = validate_private_state(home/STATE)
        state = json.loads(before_state['text']) if before_state else {}
        if not state:
            print(json.dumps({'status': 'nothing recorded', 'state': str(home/STATE)}))
            return 0
        if not set(state['files']).issubset({str(path) for path in paths}):
            raise ValueError('Use the managed unit and environment paths from this installation')
        changes, kept = {}, []
        for name, text in state['files'].items():
            path = Path(name)
            current = snapshot(path)
            if not current:
                continue
            if current['text'] == text:
                changes[path] = (current, None)
            else:
                kept.append(name)
        # An edited drop-in can still load the managed env file. Retain its dependencies
        # until the operator has reconciled it; other unchanged drop-ins can be removed.
        if any(name.endswith('/' + DROPIN) for name in kept):
            env = paths[-1]
            if env in changes:
                del changes[env]
                kept.append(str(env))
        removed = list(map(str, changes))
        remaining = dict(state, files={name: state['files'][name] for name in kept})
        tracked = bool(kept or args.purge)
        changes[home/STATE] = (before_state, state_file(remaining if tracked else None))
        previous = service_activity() if args.restart else None
        activate(home, changes, restore, previous_services=previous, restart=restart if args.restart else None)
        purged = []
        if args.purge and not kept:
            active = working_directories().values()
            for name in list(remaining['trees']):
                tree = Path(name)
                if not tree.exists():
                    remaining['trees'].remove(name)
                elif removable_tree(tree, root) and all(cwd is None or not cwd.is_relative_to(tree) for cwd in active):
                    shutil.rmtree(tree)
                    remaining['trees'].remove(name)
                    purged.append(name)
            write_file(home/STATE, state_file(remaining if remaining['trees'] else None))
        print(json.dumps({'status': 'removed', 'deleted': removed, 'kept': kept, 'purged': purged,
                          'retained_trees': remaining['trees'] if tracked else []}, indent=2))
        return 0


def status(args, home, unit_dir):
    if (home/JOURNAL).exists():
        print(json.dumps({'status': 'interrupted activation; retry apply or remove for recovery'}))
        return 1
    state = load_state(home)
    if not state:
        print(json.dumps({'status': 'not installed'}))
        return 1
    tree = Path(state['tree'])
    checks = {'tree': (tree/MARKER).is_file(),
              'files': len(state['files']) == 3 and all(
                  (snapshot(Path(p)) or {}).get('text') == text for p, text in state['files'].items())}
    try:
        for unit, cwd in working_directories().items():
            checks[f'{unit} running from tree'] = cwd == tree/UI_DIR
        if args.probe:
            checks['modules'] = probe(tree, args.api_port, args.ui_host, args.ui_port, attempts=1)
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({'status': 'incomplete', 'tree': str(tree), 'checks': checks,
                          'error': str(exc)}, indent=2))
        return 1
    ok = all(checks.values())
    print(json.dumps({'status': 'ready' if ok else 'incomplete', 'tree': str(tree),
                      'netclaw_commit': state['netclaw_commit'], 'checks': checks}, indent=2))
    return 0 if ok else 1


def report(result, outcome):
    shown = dict(result, files=sorted(result['files']), status=outcome)
    print(json.dumps(shown, indent=2))
    return 0 if outcome in ('plan', 'applied') else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('command', choices=('plan', 'build', 'apply', 'status', 'remove'))
    parser.add_argument('--netclaw-dir', default=os.environ.get('NETCLAW_DIR', '~/netclaw'))
    parser.add_argument('--openclaw-dir', default='~/.openclaw', help='installation state directory')
    parser.add_argument('--install-dir', default='~/.local/share/convergence/hud')
    parser.add_argument('--unit-dir', default='~/.config/systemd/user')
    parser.add_argument('--env-file', default='~/.config/convergence/hud.env')
    parser.add_argument('--node', default=shutil.which('node') or 'node')
    parser.add_argument('--testbed', default='~/.openclaw/workspace/testbed/testbed.yaml')
    parser.add_argument('--env', action='append', metavar='KEY=VALUE', help='extra HUD setting (repeatable)')
    parser.add_argument('--api-port', type=int, default=os.environ.get('HUD_PORT', '3001'))
    parser.add_argument('--ui-host', default=os.environ.get('HUD_HOST', '127.0.0.1'))
    parser.add_argument('--ui-port', type=int, default=os.environ.get('HUD_UI_PORT', '3000'))
    parser.add_argument('--restart', action='store_true', help='restart both HUD units after applying or removing')
    parser.add_argument('--purge', action='store_true', help='also remove inactive managed builds during removal')
    parser.add_argument('--probe', action='store_true', help='verify actual API and both served HUD entry points')
    args = parser.parse_args(argv)
    home, unit_dir, root, env_file = (Path(value).expanduser().absolute() for value in
                                      (args.openclaw_dir, args.unit_dir, args.install_dir, args.env_file))
    try:
        if args.command == 'build':
            print(build(Path(args.netclaw_dir).expanduser().absolute(), root, args.node))
            return 0
        if args.command == 'plan':
            result = plan(args, unit_dir, root, env_file, load_state(home))
            if (home/JOURNAL).exists():
                result['conflicts'].append('Interrupted operation; the next apply or remove will recover first')
            return report(result, 'blocked' if result['conflicts'] else 'plan')
        if args.command == 'apply':
            return apply(args, home, unit_dir, root, env_file)
        if args.command == 'status':
            return status(args, home, unit_dir)
        return remove(args, home, root)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'hud: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

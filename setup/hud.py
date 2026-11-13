#!/usr/bin/env python3
"""Run NetClaw's Visual HUD from an upstream checkout with the Convergence overlay.

Upstream NetClaw's HUD (ui/netclaw-visual) has no plug-in point. This helper builds a
separate HUD tree from a clean NetClaw checkout and adds, as one reversible unit:
  - the overlay in integrations/netclaw/hud/overlay: the module loader, the
    Convergence module (HOME tab, diary, security, incident and JEV review), the
    retro-theme and scene-quality modules, local extensions (token footer, model
    picker, RAG URL/crawl ingest, SSH terminal) and the PWA files;
  - Convergence's changes to 23 upstream HUD files (integrations/netclaw/hud/patched),
    among them the hooks in server.js and src/main.js, the dashboard's Convergence
    view and the org-chart drag and zones. Each is merged three ways onto the
    checkout's version; a conflict stops the build and names the file;
  - a `vite build` of the result.

The tree goes to <install-dir>/<netclaw-commit>-<overlay-digest>/ and the checkout
itself is never modified. `apply` points netclaw-hud.service and
netclaw-hud-ui.service (systemd user units) at the tree with one drop-in each, plus
an environment file for HUD settings that used to live in the checkout's .env.

  python3 setup/hud.py plan   --netclaw-dir ~/netclaw
  python3 setup/hud.py apply  --netclaw-dir ~/netclaw --restart
  python3 setup/hud.py status [--probe]
  python3 setup/hud.py remove [--restart] [--purge]

`plan` builds nothing and changes nothing. `apply --restart` checks that the restarted
HUD lists the Convergence module and removes its drop-ins again if it does not.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HUD = REPO/'integrations/netclaw/hud'
OVERLAY = HUD/'overlay'
REPO_OVERLAY = HUD/'repo-overlay'  # files outside ui/netclaw-visual
PATCHED = HUD/'patched'
PATCHED_BASE = HUD/'patched-base'
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

def patched_files(patched=None):
    patched = patched or PATCHED
    return sorted(p.relative_to(patched) for p in patched.rglob('*') if p.is_file())


def merge_patched(hud, patched=None, base=None):
    """Carry Convergence's changes to upstream HUD files onto this checkout's versions.

    patched/<f> is our version of <f>; patched-base/<f> is the upstream version it was
    made from. Where upstream still matches, ours is used as is. Otherwise the change
    is merged three ways (git merge-file); any conflict stops the build and names the
    file, so the overlay is rebased instead of silently dropping either side.
    """
    patched, base = patched or PATCHED, base or PATCHED_BASE
    merged, conflicts = [], []
    for rel in patched_files(patched):
        target = hud/rel
        if not target.exists():
            conflicts.append(f'{rel}: no longer in upstream')
            continue
        upstream, ours, origin = target.read_bytes(), (patched/rel).read_bytes(), (base/rel).read_bytes()
        if upstream == origin:
            target.write_bytes(ours)
            continue
        with tempfile.TemporaryDirectory() as scratch:
            o, b_, u = Path(scratch)/'ours', Path(scratch)/'base', Path(scratch)/'upstream'
            o.write_bytes(ours); b_.write_bytes(origin); u.write_bytes(upstream)
            rc = subprocess.run(['git', 'merge-file', '-q', str(o), str(b_), str(u)], check=False).returncode
            if rc:
                conflicts.append(f'{rel}: {rc} conflict(s) with upstream')
                continue
            target.write_bytes(o.read_bytes())
            merged.append(str(rel))
    if conflicts:
        raise ValueError('upstream HUD changed under the overlay; rebase integrations/netclaw/hud: '
                         + '; '.join(conflicts))
    return merged


# ── build ────────────────────────────────────────────────────────────────────

def overlay_files(overlay=None):
    overlay = overlay or OVERLAY
    return sorted(p for p in overlay.rglob('*') if p.is_file() and '__pycache__' not in p.parts)


def overlay_digest(overlay=None):
    overlay = overlay or OVERLAY
    h = hashlib.sha256()
    for tree in (overlay, REPO_OVERLAY):
        for p in overlay_files(tree):
            h.update(str(p.relative_to(tree)).encode()+b'\0'+p.read_bytes()+b'\0')
    for rel in patched_files():
        for tree in (PATCHED, PATCHED_BASE):
            h.update(b'patch\0'+str(rel).encode()+b'\0'+(tree/rel).read_bytes()+b'\0')
    return h.hexdigest()


def checkout(netclaw):
    if not (netclaw/'ui/netclaw-visual/server.js').exists():
        raise ValueError(f'{netclaw} is not a NetClaw checkout (no ui/netclaw-visual/server.js)')
    head = run(['git', '-C', netclaw, 'rev-parse', 'HEAD']).strip()
    dirty = run(['git', '-C', netclaw, 'status', '--porcelain', '--untracked-files=no']).strip()
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
    if (target/MARKER).exists():
        return target
    root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='.build-', dir=root))
    try:
        archive = subprocess.Popen(['git', '-C', str(netclaw), 'archive', head], stdout=subprocess.PIPE)
        subprocess.run(['tar', '-x', '-C', str(scratch)], stdin=archive.stdout, check=True)
        if archive.wait():
            raise RuntimeError('git archive failed')
        hud = scratch/'ui/netclaw-visual'
        for tree, dest_root in ((overlay, hud), (REPO_OVERLAY, scratch)):
            for src in overlay_files(tree):
                dest = dest_root/src.relative_to(tree)
                if dest.exists():
                    # Upstream now ships a file we add: decide which wins, do not overwrite.
                    raise ValueError(f'{dest.relative_to(scratch)} now exists upstream; '
                                     'move it to patched/ or drop it from the overlay')
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
        merge_patched(hud)
        npm = Path(node).parent/'npm'
        env = {**os.environ, 'PATH': f'{Path(node).parent}:{os.environ.get("PATH", "")}'}
        run([npm, 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=hud, env=env)
        run([node, 'node_modules/vite/bin/vite.js', 'build'], cwd=hud, env=env)
        if not (hud/'dist/index.html').exists():
            raise RuntimeError('vite build produced no dist/index.html')
        (scratch/MARKER).write_text(json.dumps({'netclaw_commit': head, 'overlay_digest': digest,
                                                'netclaw_dir': str(netclaw)}, indent=2)+'\n')
        os.rename(scratch, target)
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    return target


# ── units ────────────────────────────────────────────────────────────────────

def dropin(tree, env_file):
    return ('# Managed by convergence-platform setup/hud.py; `hud.py remove` deletes it.\n'
            '[Service]\n'
            f'WorkingDirectory={tree}/ui/netclaw-visual\n'
            f'EnvironmentFile={env_file}\n')


def env_text(values):
    lines = ['# HUD settings, managed by convergence-platform setup/hud.py.']
    for key, value in sorted(values.items()):
        if '\n' in value:
            raise ValueError(f'{key}: value contains a newline')
        lines.append(f'{key}={value}')
    return '\n'.join(lines)+'\n'


def hud_env(args):
    values = {}
    testbed = Path(os.path.expanduser(args.testbed))
    if testbed.exists():
        values['NETCLAW_TESTBED_FILE'] = str(testbed)
    if args.ssh_terminal:
        values['HUD_SSH_ENABLED'] = '1'
    # The model picker's source of truth; the checkout's own .env is not used.
    values['NETCLAW_ENV_FILE'] = str(Path(os.path.expanduser(args.openclaw_dir))/'.env')
    for item in args.env or []:
        key, sep, value = item.partition('=')
        if not sep or not key.isidentifier():
            raise ValueError(f'--env {item!r}: expected KEY=VALUE')
        values[key] = value
    return values


def plan(args, unit_dir, root, env_file, state):
    netclaw = Path(os.path.expanduser(args.netclaw_dir))
    head, dirty = checkout(netclaw)
    digest = overlay_digest()
    tree = tree_for(root, head, digest)
    conflicts, files = [], {}
    for unit in UNITS:
        path = unit_dir/f'{unit}.d'/DROPIN
        files[str(path)] = dropin(tree, env_file)
        if path.exists() and str(path) not in state.get('files', {}):
            conflicts.append(f'{path} exists and was not written by this tool')
    files[str(env_file)] = env_text(hud_env(args))
    if env_file.exists() and str(env_file) not in state.get('files', {}):
        conflicts.append(f'{env_file} exists and was not written by this tool')
    if dirty:
        conflicts.append(f'{netclaw} has uncommitted changes')
    return {'netclaw_dir': str(netclaw), 'netclaw_commit': head, 'overlay_digest': digest,
            'tree': str(tree), 'tree_built': (tree/MARKER).exists(), 'files': files,
            'conflicts': conflicts}


def probe(port, attempts=30):
    """The restarted API must list the Convergence module as configured."""
    request = urllib.request.Request(f'http://127.0.0.1:{port}/api/modules',
                                     headers={'Host': f'127.0.0.1:{port}'})
    last = None
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                modules = {m['id']: m for m in json.load(response)['modules']}
            if modules.get('convergence', {}).get('configured'):
                return sorted(modules)
            last = f"convergence module not configured: {modules.get('convergence')}"
        except (OSError, ValueError, KeyError) as exc:
            last = str(exc)
        time.sleep(2)
    raise RuntimeError(f'HUD probe failed: {last}')


def write_private(path, text, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.tmp')
    tmp.write_text(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def restart():
    run(['systemctl', '--user', 'daemon-reload'])
    for unit in UNITS:
        run(['systemctl', '--user', 'restart', unit])


def load_state(home):
    path = home/STATE
    return json.loads(path.read_text()) if path.exists() else {}


def remove_files(state):
    removed, kept = [], []
    for path, text in state.get('files', {}).items():
        p = Path(path)
        if not p.exists():
            continue
        if p.read_text() != text:
            kept.append(f'{p} (changed since install; left as is)')
            continue
        p.unlink()
        removed.append(str(p))
    return removed, kept


def apply(args, home, unit_dir, root, env_file):
    state = load_state(home)
    result = plan(args, unit_dir, root, env_file, state)
    if result['conflicts']:
        return report(result, 'blocked')
    tree = build(Path(result['netclaw_dir']), root, args.node)
    for path, text in result['files'].items():
        write_private(Path(path), text, 0o600 if Path(path) == env_file else 0o644)
    new_state = {'files': result['files'], 'tree': str(tree), 'netclaw_commit': result['netclaw_commit'],
                 'overlay_digest': result['overlay_digest'],
                 'trees': sorted(set(state.get('trees', [])) | {str(tree)})}
    write_private(home/STATE, json.dumps(new_state, indent=2)+'\n', 0o600)
    result['tree_built'] = True
    if args.restart:
        try:
            restart()
            result['modules'] = probe(args.api_port)
        except RuntimeError as exc:
            remove_files(new_state)
            (home/STATE).unlink()
            restart()
            result['rolled_back'] = str(exc)
            return report(result, 'failed')
        result['restarted'] = list(UNITS)
    else:
        result['note'] = 'run systemctl --user daemon-reload and restart both HUD units'
    return report(result, 'applied')


def remove(args, home, root):
    state = load_state(home)
    if not state:
        print(json.dumps({'status': 'nothing recorded', 'state': str(home/STATE)}))
        return 0
    removed, kept = remove_files(state)
    purged = []
    if args.purge:
        for tree in state.get('trees', []):
            t = Path(tree)
            if (t/MARKER).exists() and t.parent == root:
                shutil.rmtree(t)
                purged.append(tree)
    (home/STATE).unlink()
    if args.restart:
        restart()
    print(json.dumps({'status': 'removed', 'deleted': removed, 'kept': kept, 'purged': purged,
                      'note': None if args.restart else 'reload systemd and restart both HUD units'},
                     indent=2))
    return 0


def status(args, home, unit_dir):
    state = load_state(home)
    if not state:
        print(json.dumps({'status': 'not installed'}))
        return 1
    tree = Path(state['tree'])
    checks = {'tree': (tree/MARKER).exists(),
              'files': all(Path(p).exists() and Path(p).read_text() == t for p, t in state['files'].items())}
    for unit in UNITS:
        pid = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'MainPID', '--value'],
                             capture_output=True, text=True, check=False).stdout.strip()
        try:
            cwd = os.readlink(f'/proc/{pid}/cwd')
        except OSError:
            cwd = ''
        checks[f'{unit} running from tree'] = cwd == str(tree/'ui/netclaw-visual')
    if args.probe:
        try:
            checks['modules'] = probe(args.api_port, attempts=1)
        except RuntimeError as exc:
            checks['modules'] = False
            checks['probe_error'] = str(exc)[:300]
    ok = all(bool(v) for k, v in checks.items() if k != 'probe_error')
    print(json.dumps({'status': 'ready' if ok else 'incomplete', 'tree': str(tree),
                      'netclaw_commit': state['netclaw_commit'], 'checks': checks}, indent=2))
    return 0 if ok else 1


def report(result, outcome):
    shown = dict(result)
    shown['files'] = sorted(result['files'])
    shown['status'] = outcome
    print(json.dumps(shown, indent=2))
    return 0 if outcome in ('plan', 'applied') else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('command', choices=('plan', 'apply', 'status', 'remove'))
    parser.add_argument('--netclaw-dir', default=os.environ.get('NETCLAW_DIR', '~/netclaw'),
                        help='clean NetClaw checkout to build the HUD from (default $NETCLAW_DIR)')
    parser.add_argument('--openclaw-dir', default='~/.openclaw', help='where the install record goes')
    parser.add_argument('--install-dir', default='~/.local/share/convergence/hud', help='HUD build trees')
    parser.add_argument('--unit-dir', default='~/.config/systemd/user')
    parser.add_argument('--env-file', default='~/.config/convergence/hud.env')
    parser.add_argument('--node', default=shutil.which('node') or 'node', help='node used to build')
    parser.add_argument('--testbed', default='~/.openclaw/workspace/testbed/testbed.yaml',
                        help='pyATS testbed the HUD shows devices from (NETCLAW_TESTBED_FILE)')
    parser.add_argument('--ssh-terminal', action='store_true', help='enable the HUD SSH terminal')
    parser.add_argument('--env', action='append', metavar='KEY=VALUE', help='extra HUD setting (repeatable)')
    parser.add_argument('--api-port', type=int, default=int(os.environ.get('HUD_PORT', 3001)))
    parser.add_argument('--restart', action='store_true', help='restart both HUD units (and verify)')
    parser.add_argument('--purge', action='store_true', help='remove: also delete the build trees')
    parser.add_argument('--probe', action='store_true', help='status: query the running HUD')
    args = parser.parse_args(argv)
    home = Path(os.path.expanduser(args.openclaw_dir))
    unit_dir = Path(os.path.expanduser(args.unit_dir))
    root = Path(os.path.expanduser(args.install_dir))
    env_file = Path(os.path.expanduser(args.env_file))
    try:
        if args.command == 'plan':
            result = plan(args, unit_dir, root, env_file, load_state(home))
            return report(result, 'blocked' if result['conflicts'] else 'plan')
        if args.command == 'apply':
            return apply(args, home, unit_dir, root, env_file)
        if args.command == 'status':
            return status(args, home, unit_dir)
        return remove(args, home, root)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f'hud: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

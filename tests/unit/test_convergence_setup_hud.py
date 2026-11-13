"""HUD overlay: builds from a clean checkout, merges our changes three ways, reversible units."""
import argparse
import importlib.util
import json
import subprocess
import sys
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('convergence_hud', ROOT/'setup/hud.py')
hud = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(ROOT/'setup'))
try:
    spec.loader.exec_module(hud)
    import hud_activation
finally:
    sys.path.pop(0)
restore_service_states = hud.restore_services


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout


def netclaw_repo(tmp_path, files=None):
    repo = tmp_path/'netclaw'
    ui = repo/'ui/netclaw-visual'
    (ui/'src').mkdir(parents=True)
    for rel, text in {'server.js': 'const a = 1;\n', 'src/main.js': 'one\ntwo\nthree\n', **(files or {})}.items():
        (ui/rel).parent.mkdir(parents=True, exist_ok=True)
        (ui/rel).write_text(text)
    git(repo, 'init', '-q')
    git(repo, '-c', 'user.email=t@example.com', '-c', 'user.name=t', 'add', '.')
    git(repo, '-c', 'user.email=t@example.com', '-c', 'user.name=t', 'commit', '-qm', 'base')
    return repo


def overlay(tmp_path, patched=None, base=None, files=None):
    """A private overlay layout; the module constants point at it for the test."""
    root = tmp_path/'hud-overlay'
    for rel, text in (files or {'modules/x/module.json': '{}'}).items():
        (root/'overlay'/rel).parent.mkdir(parents=True, exist_ok=True)
        (root/'overlay'/rel).write_text(text)
    repo = root/'patch-git'
    repo.mkdir()
    git(repo, 'init', '-q')
    for rel, text in (base or {}).items():
        (repo/rel).parent.mkdir(parents=True, exist_ok=True)
        (repo/rel).write_text(text)
    git(repo, 'add', '.')
    for rel, text in (patched or {}).items():
        (repo/rel).write_text(text)
        patch = root/'patches'/(rel+'.patch')
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_text(git(repo, 'diff', '--no-ext-diff', '--full-index', '--', rel))
    return root


@pytest.fixture
def layout(tmp_path, monkeypatch):
    def use(**kwargs):
        root = overlay(tmp_path, **kwargs)
        package = root/'views'
        (package/'modules/convergence').mkdir(parents=True, exist_ok=True)
        (package/'modules/convergence/ui.js').write_text('export const browser = true;')
        (package/'modules/convergence/module.json').write_text('{"id":"convergence"}')
        for name in ('LICENSE', 'NOTICE'):
            (package/name).write_text(name)
        monkeypatch.setattr(hud, 'PACKAGE', package)
        monkeypatch.setattr(hud, 'OVERLAY', root/'overlay')
        monkeypatch.setattr(hud, 'PATCHES', root/'patches')
        return root
    return use


@pytest.fixture
def no_npm(monkeypatch):
    """Skip npm and vite; a 'build' just writes dist/index.html."""
    calls, real = [], hud.run

    def fake(cmd, **kwargs):
        cmd = [str(c) for c in cmd]
        if Path(cmd[0]).name == 'git':
            return real(cmd, **kwargs)
        calls.append(cmd)
        if cmd[-1] == 'build':
            (Path(kwargs['cwd'])/'dist').mkdir(exist_ok=True)
            (Path(kwargs['cwd'])/'dist/index.html').write_text('<html>')
        return ''
    monkeypatch.setattr(hud, 'run', fake)
    return calls


@pytest.fixture(autouse=True)
def isolated_services(monkeypatch):
    # No test uses the operator's service manager.
    monkeypatch.setattr(hud, 'service_activity', lambda: dict.fromkeys(hud.UNITS, True))
    monkeypatch.setattr(hud, 'restore_services', lambda previous: hud.restart())


def test_shipped_overlay_is_consistent():
    """Every patched file has its upstream base, and no path is both added and patched."""
    patched = {rel: patch for rel, patch, _ in hud.host_patches()}
    added = {p.relative_to(hud.OVERLAY) for p in hud.overlay_files(hud.OVERLAY)}
    assert not set(patched) & added
    assert Path('server.js') in patched and Path('src/main.js') in patched
    for rel, patch in patched.items():
        assert '+<<<<<<<' not in patch.read_text(), rel
    assert not (hud.HUD/'patched').exists() and not (hud.HUD/'patched-base').exists()
    assert (hud.PACKAGE/'modules/convergence/module.json').exists()
    assert (hud.PACKAGE/'LICENSE').exists() and (hud.PACKAGE/'NOTICE').exists()
    assert not (hud.OVERLAY/'modules/convergence/module.json').exists()
    assert not (hud.HUD/'repo-overlay/scripts/netclaw-apply-models.sh').exists()


def test_merge_uses_ours_when_upstream_is_unchanged(tmp_path, layout):
    layout(patched={'src/main.js': 'one\nOURS\nthree\n'}, base={'src/main.js': 'one\ntwo\nthree\n'})
    ui = tmp_path/'ui'
    (ui/'src').mkdir(parents=True)
    (ui/'src/main.js').write_text('one\ntwo\nthree\n')
    assert hud.merge_patched(ui, tmp_path/'hud-overlay/patch-git') == []
    assert (ui/'src/main.js').read_text() == 'one\nOURS\nthree\n'


def test_merge_carries_our_change_onto_a_changed_upstream(tmp_path, layout):
    base = 'a\nb\nc\nd\ne\nf\ng\n'
    layout(patched={'f.js': base.replace('b\n', 'OURS\n')}, base={'f.js': base})
    ui = tmp_path/'ui'
    ui.mkdir()
    (ui/'f.js').write_text(base.replace('f\n', 'UPSTREAM\n'))
    assert hud.merge_patched(ui, tmp_path/'hud-overlay/patch-git') == ['f.js']
    assert (ui/'f.js').read_text() == 'a\nOURS\nc\nd\ne\nUPSTREAM\ng\n'


def test_merge_conflict_stops_and_names_the_file(tmp_path, layout):
    layout(patched={'f.js': 'a\nOURS\nc\n'}, base={'f.js': 'a\nb\nc\n'})
    ui = tmp_path/'ui'
    ui.mkdir()
    (ui/'f.js').write_text('a\nTHEIRS\nc\n')
    with pytest.raises(ValueError, match='f.js: conflict'):
        hud.merge_patched(ui, tmp_path/'hud-overlay/patch-git')
    assert (ui/'f.js').read_text() == 'a\nTHEIRS\nc\n'


def test_merge_refuses_a_file_upstream_removed(tmp_path, layout):
    layout(patched={'gone.js': 'x\n'}, base={'gone.js': 'y\n'})
    (tmp_path/'ui').mkdir()
    with pytest.raises(ValueError, match='gone.js: no longer in upstream'):
        hud.merge_patched(tmp_path/'ui', tmp_path/'hud-overlay/patch-git')


def test_build_assembles_the_tree_without_touching_the_checkout(tmp_path, layout, no_npm):
    repo = netclaw_repo(tmp_path)
    layout(patched={'src/main.js': 'one\nOURS\nthree\n'}, base={'src/main.js': 'one\ntwo\nthree\n'})
    tree = hud.build(repo, tmp_path/'install', 'node')
    ui = tree/'ui/netclaw-visual'
    assert (ui/'modules/x/module.json').exists()
    assert (ui/'modules/convergence/module.json').read_text() == '{"id":"convergence"}'
    assert (ui/'modules/convergence/LICENSE').read_text() == 'LICENSE'
    assert (ui/'modules/convergence/NOTICE').read_text() == 'NOTICE'
    assert (ui/'src/main.js').read_text() == 'one\nOURS\nthree\n'
    assert not (tree/'scripts/netclaw-apply-models.sh').exists()
    marker = json.loads((tree/hud.MARKER).read_text())
    assert marker['netclaw_commit'] == git(repo, 'rev-parse', 'HEAD').strip()
    assert git(repo, 'status', '--porcelain') == ''
    assert [c[-1] for c in no_npm] == ['--no-fund', 'build']
    # Same commit and overlay: the tree is reused, nothing is rebuilt.
    assert hud.build(repo, tmp_path/'install', 'node') == tree
    assert len(no_npm) == 2


def test_build_refuses_a_dirty_checkout(tmp_path, layout, no_npm):
    repo = netclaw_repo(tmp_path)
    layout()
    (repo/'ui/netclaw-visual/server.js').write_text('changed\n')
    with pytest.raises(ValueError, match='uncommitted'):
        hud.build(repo, tmp_path/'install', 'node')


def test_build_failure_leaves_no_tree(tmp_path, layout, no_npm):
    repo = netclaw_repo(tmp_path)
    layout(patched={'src/main.js': 'x\n'}, base={'src/main.js': 'not upstream\n'})
    with pytest.raises(ValueError):
        hud.build(repo, tmp_path/'install', 'node')
    assert list((tmp_path/'install').iterdir()) == []


def test_adapter_cannot_overwrite_a_canonical_view(tmp_path, layout, no_npm):
    repo = netclaw_repo(tmp_path)
    layout(files={'modules/convergence/ui.js': 'stale duplicate'})
    with pytest.raises(ValueError, match='another build input'):
        hud.build(repo, tmp_path/'install', 'node')
    assert list((tmp_path/'install').iterdir()) == []


def test_overlay_refuses_a_file_upstream_now_ships(tmp_path, layout, no_npm):
    repo = netclaw_repo(tmp_path, {'modules/x/module.json': '{"upstream": true}'})
    layout()
    with pytest.raises(ValueError, match='ui/netclaw-visual/modules/x/module.json now exists upstream'):
        hud.build(repo, tmp_path/'install', 'node')
    assert list((tmp_path/'install').iterdir()) == []


def args_for(tmp_path, repo, **changes):
    values = {'netclaw_dir': str(repo), 'openclaw_dir': str(tmp_path/'oc'), 'testbed': str(tmp_path/'testbed.yaml'),
              'env': None, 'restart': False, 'purge': False, 'probe': False,
              'api_port': 3001, 'ui_host': '127.0.0.1', 'ui_port': 3000, 'unit_dir': str(tmp_path/'units'),
              'env_file': str(tmp_path/'hud.env'), 'node': 'node'}
    values.update(changes)
    return argparse.Namespace(**values)


def test_plan_writes_one_dropin_per_unit_and_the_env_file(tmp_path, layout):
    repo = netclaw_repo(tmp_path)
    layout()
    (tmp_path/'testbed.yaml').write_text('devices: {}\n')
    env_file = tmp_path/'hud.env'
    result = hud.plan(args_for(tmp_path, repo, env=['RAG_MAX_ROUNDS=3']), tmp_path/'units', tmp_path/'install',
                      env_file, {})
    assert result['conflicts'] == []
    tree = result['tree']
    for unit in hud.UNITS:
        text = result['files'][str(tmp_path/'units'/f'{unit}.d'/hud.DROPIN)]
        assert f'WorkingDirectory={tree}/ui/netclaw-visual\n' in text
        assert f'EnvironmentFile={env_file}\n' in text
    env = result['files'][str(env_file)]
    assert f'NETCLAW_TESTBED_FILE="{tmp_path / "testbed.yaml"}"\n' in env
    assert 'HUD_SSH_ENABLED' not in env and 'RAG_MAX_ROUNDS="3"\n' in env
    assert f'NETCLAW_ENV_FILE="{tmp_path / "oc" / ".env"}"\n' in env


def test_plan_blocks_on_files_it_did_not_write(tmp_path, layout):
    repo = netclaw_repo(tmp_path)
    layout()
    foreign = tmp_path/'units/netclaw-hud.service.d'/hud.DROPIN
    foreign.parent.mkdir(parents=True)
    foreign.write_text('[Service]\n')
    result = hud.plan(args_for(tmp_path, repo), tmp_path/'units', tmp_path/'install', tmp_path/'hud.env', {})
    assert any('not written by this tool' in c for c in result['conflicts'])


def test_env_values_are_validated(tmp_path):
    with pytest.raises(ValueError, match='KEY=VALUE'):
        hud.hud_env(args_for(tmp_path, tmp_path, env=['no-equals']))
    with pytest.raises(ValueError, match='newline'):
        hud.env_text({'A': 'x\ny'})


def test_apply_then_remove_is_exact(tmp_path, layout, no_npm, capsys):
    repo = netclaw_repo(tmp_path)
    layout()
    (tmp_path/'oc').mkdir()
    args = args_for(tmp_path, repo)
    env_file = tmp_path/'hud.env'
    assert hud.apply(args, tmp_path/'oc', tmp_path/'units', tmp_path/'install', env_file) == 0
    state = json.loads((tmp_path/'oc'/hud.STATE).read_text())
    assert (Path(state['tree'])/hud.MARKER).exists()
    for path, text in state['files'].items():
        assert Path(path).read_text() == text
    assert oct(env_file.stat().st_mode & 0o777) == '0o600'
    # Re-applying our own files is not a conflict.
    assert hud.apply(args, tmp_path/'oc', tmp_path/'units', tmp_path/'install', env_file) == 0
    # A drop-in edited by hand is kept on remove; the build it could still reference is retained.
    edited = tmp_path/'units/netclaw-hud-ui.service.d'/hud.DROPIN
    edited.write_text('[Service]\n# operator edit\n')
    capsys.readouterr()
    assert hud.remove(args_for(tmp_path, repo, purge=True), tmp_path/'oc', tmp_path/'install') == 0
    out = json.loads(capsys.readouterr().out)
    assert edited.exists() and env_file.exists()
    assert not (tmp_path/'units/netclaw-hud.service.d'/hud.DROPIN).exists()
    assert out['purged'] == [] and Path(state['tree']).is_dir()
    remaining = hud.load_state(tmp_path/'oc')
    assert set(remaining['files']) == {str(edited), str(env_file)} and remaining['trees'] == [state['tree']]


def test_failed_probe_rolls_back(tmp_path, layout, no_npm, monkeypatch, capsys):
    repo = netclaw_repo(tmp_path)
    layout()
    (tmp_path/'oc').mkdir()
    restarts = []
    monkeypatch.setattr(hud, 'restart', lambda: restarts.append(1))

    def broken(*args, **kwargs):
        raise RuntimeError('HUD probe failed: convergence module not configured')
    monkeypatch.setattr(hud, 'probe', broken)
    env_file = tmp_path/'hud.env'
    assert hud.apply(args_for(tmp_path, repo, restart=True), tmp_path/'oc', tmp_path/'units',
                     tmp_path/'install', env_file) == 2
    assert 'rolled_back' in json.loads(capsys.readouterr().out)
    assert len(restarts) == 2
    assert not env_file.exists() and not (tmp_path/'oc'/hud.STATE).exists()
    assert not any((tmp_path/'units').rglob(hud.DROPIN))


def staged_install(tmp_path, layout):
    repo = netclaw_repo(tmp_path)
    layout()
    args = args_for(tmp_path, repo, env=['EXAMPLE=original'])
    home, units, root, env = tmp_path/'oc', tmp_path/'units', tmp_path/'install', tmp_path/'hud.env'
    assert hud.apply(args, home, units, root, env) == 0
    return args, home, units, root, env


def installed_snapshot(home, units, env):
    return {path: (path.read_bytes(), path.stat().st_mode & 0o777)
            for path in [*hud.managed_paths(units, env), home/hud.STATE]}


def test_upgrade_rolls_back_exact_files_permissions_and_record(tmp_path, layout, no_npm, monkeypatch):
    args, home, units, root, env = staged_install(tmp_path, layout)
    env.chmod(0o400)
    archive = installed_snapshot(home, units, env)
    restored = []
    monkeypatch.setattr(hud, 'restart', lambda: restored.append(hud.load_state(home)['tree']))

    def reject(*args):
        raise RuntimeError('UI unavailable')
    monkeypatch.setattr(hud, 'probe', reject)
    # Rebuilding from a different overlay changes the drop-ins, env and state.
    (hud.PACKAGE/'modules/convergence/ui.js').write_text('export const browser = 2;')
    args.restart = True
    args.env = ['EXAMPLE=replaced']
    assert hud.apply(args, home, units, root, env) == 2
    assert installed_snapshot(home, units, env) == archive
    assert len(restored) == 2 and restored[1] == hud.load_state(home)['tree'] != restored[0]
    assert not (home/hud_activation.JOURNAL).exists()


def test_partial_upgrade_is_rolled_back_before_restart(tmp_path, layout, no_npm, monkeypatch):
    args, home, units, root, env = staged_install(tmp_path, layout)
    original = installed_snapshot(home, units, env)
    args.restart = True
    args.env = ['EXAMPLE=replaced']
    (hud.PACKAGE/'NOTICE').write_text('updated attribution')
    real = hud_activation.write_file
    failed = []

    def interrupted(path, value):
        if path == env and not failed:
            failed.append(path)
            raise OSError('fixture simulated full filesystem')
        real(path, value)
    monkeypatch.setattr(hud_activation, 'write_file', interrupted)
    monkeypatch.setattr(hud, 'restart', lambda: pytest.fail('services should still have the old configuration'))
    assert hud.apply(args, home, units, root, env) == 2
    assert failed and installed_snapshot(home, units, env) == original
    assert not (home/hud_activation.JOURNAL).exists()


def test_previous_interrupted_activation_recovers_on_next_apply(tmp_path, layout, no_npm, monkeypatch):
    args, home, units, root, env = staged_install(tmp_path, layout)
    original = installed_snapshot(home, units, env)
    previous = hud.snapshot(env)
    replacement = dict(previous, text='TEMP="incomplete"\n')
    journal = {'previous_services': dict.fromkeys(hud.UNITS, True), 'changes': {str(env): {'before': previous, 'after': replacement}}}
    hud_activation.save_record(home/hud_activation.JOURNAL, journal)
    hud.write_file(env, replacement)
    recovered = []
    monkeypatch.setattr(hud, 'restart', lambda: recovered.append(env.read_text()))
    assert hud.apply(args, home, units, root, env) == 0
    assert recovered == [previous['text']]
    assert installed_snapshot(home, units, env) == original
    assert not (home/hud_activation.JOURNAL).exists()


def test_drift_blocks_reapply_before_any_config_write(tmp_path, layout, no_npm):
    args, home, units, root, env = staged_install(tmp_path, layout)
    env.write_text('EXAMPLE="intentional local change"\n')
    original = installed_snapshot(home, units, env)
    args.env = ['EXAMPLE=replacement']
    assert hud.apply(args, home, units, root, env) == 2
    assert installed_snapshot(home, units, env) == original


def test_symlinked_managed_file_is_not_overwritten(tmp_path, layout, no_npm):
    args, home, units, root, env = staged_install(tmp_path, layout)
    private = tmp_path/'other.env'
    private.write_text('DO_NOT_CHANGE=yes\n')
    env.unlink()
    env.symlink_to(private)
    with pytest.raises(OSError):
        hud.apply(args, home, units, root, env)
    assert private.read_text() == 'DO_NOT_CHANGE=yes\n' and env.is_symlink()


def test_purge_retains_running_build_until_both_units_leave(tmp_path, layout, no_npm, monkeypatch):
    args, home, units, root, env = staged_install(tmp_path, layout)
    tree = Path(hud.load_state(home)['tree'])
    args.purge = True
    cwd = {hud.UNITS[0]: None, hud.UNITS[1]: tree/hud.UI_DIR}
    monkeypatch.setattr(hud, 'working_directories', lambda: cwd)
    assert hud.remove(args, home, root) == 0
    assert tree.exists() and hud.load_state(home)['trees'] == [str(tree)]
    assert not env.exists()
    cwd[hud.UNITS[1]] = None
    assert hud.remove(args, home, root) == 0
    assert not tree.exists() and not (home/hud.STATE).exists()


def test_probe_requires_both_units_real_routes_and_both_ui_pages(tmp_path, monkeypatch):
    dist = tmp_path/hud.UI_DIR/'dist'
    dist.mkdir(parents=True)
    (dist/'assets').mkdir()
    pages = {'index.html': b'<script type="module" src="/assets/dashboard-abcd.js"></script>',
             'classic.html': b'<script type="module" src="/assets/classic-abcd.js"></script>',
             'assets/dashboard-abcd.js': b'console.log("dashboard")',
             'assets/classic-abcd.js': b'console.log("classic")'}
    for name, page in pages.items():
        (dist/name).write_bytes(page)
    module = {'id': 'convergence', 'configured': True, 'routes': True, 'error': None}
    status = {'configured': False, 'localOwnerMode': False, 'reviewerOrigin': None}

    class Fixture(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/api/modules':
                data = json.dumps({'modules': [module]}).encode()
            elif self.path == '/api/home/status':
                data = json.dumps(status).encode()
            else:
                data = pages[self.path[1:]]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    worker = Thread(target=server.serve_forever)
    worker.start()
    try:
        port = server.server_address[1]
        locations = dict.fromkeys(hud.UNITS, tmp_path/hud.UI_DIR)
        monkeypatch.setattr(hud, 'working_directories', lambda: locations)

        def check():
            return hud.probe(tmp_path, port, '127.0.0.1', port, attempts=1)
        assert check() == ['convergence']
        module['routes'] = False
        with pytest.raises(RuntimeError, match='routes'):
            check()
        module.update(routes=True, error='registration failed')
        with pytest.raises(RuntimeError, match='routes'):
            check()
        module['error'] = None
        status['configured'] = True
        with pytest.raises(RuntimeError, match='anonymous HUD status'):
            check()
        status['configured'] = False
        correct_bundle = pages['assets/dashboard-abcd.js']
        pages['assets/dashboard-abcd.js'] = b'stale bundle'
        with pytest.raises(RuntimeError, match='assets/dashboard-abcd.js'):
            check()
        pages['assets/dashboard-abcd.js'] = correct_bundle
        pages['classic.html'] = b'<html>old classic</html>'
        with pytest.raises(RuntimeError, match='classic.html'):
            check()
        locations[hud.UNITS[1]] = tmp_path/'old-ui'
        with pytest.raises(RuntimeError, match='selected build'):
            check()
    finally:
        server.shutdown()
        worker.join()
        server.server_close()


def test_env_file_roundtrip_sensitive_characters():
    data = {'EXAMPLE': '  leading \\ " $ # % with spaces  '}
    assert hud.env_text(data).splitlines()[1] == 'EXAMPLE="  leading \\\\ \\" $ # % with spaces  "'
    with pytest.raises(ValueError, match='control'):
        hud.env_text({'EXAMPLE': 'unguarded\rline'})


def test_concurrent_activation_waits_on_installation_directory(tmp_path):
    # Probe the OS lock from a separate interpreter, including a fresh install.
    with hud.locked(tmp_path/'oc'):
        probe = subprocess.run([sys.executable, '-c', '''
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDONLY)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(0)
sys.exit(1)
''', str(tmp_path/'oc')])
        assert probe.returncode == 0


def test_build_digest_tracks_canonical_source_and_attribution(tmp_path, layout, no_npm):
    repo = netclaw_repo(tmp_path)
    layout(files={'modules/convergence/server.js': 'export const register = () => {};'})
    first = hud.build(repo, tmp_path/'install', 'node')
    for name, content in [('modules/convergence/ui.js', 'export const browser = "updated";'), ('NOTICE', 'new attribution')]:
        (hud.PACKAGE/name).write_text(content)
        second = hud.build(repo, tmp_path/'install', 'node')
        assert first != second
        target = second/hud.MODULE_DIR/Path(name).name
        assert target.read_text() == content
        assert (second/hud.MODULE_DIR/'server.js').exists()
        first = second


def test_preexisting_build_with_wrong_marker_is_refused(tmp_path, layout, no_npm):
    repo = netclaw_repo(tmp_path)
    layout()
    tree = hud.build(repo, tmp_path/'install', 'node')
    record = json.loads((tree/hud.MARKER).read_text())
    record['overlay_digest'] = '0'*64
    (tree/hud.MARKER).write_text(json.dumps(record))
    with pytest.raises(ValueError, match='mismatched build'):
        hud.build(repo, tmp_path/'install', 'node')


def test_failed_remove_restart_restores_working_installation(tmp_path, layout, no_npm, monkeypatch):
    args, home, units, root, env = staged_install(tmp_path, layout)
    args.restart = True
    original = installed_snapshot(home, units, env)
    calls = []

    def restart():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('restart failure')
    monkeypatch.setattr(hud, 'restart', restart)
    with pytest.raises(RuntimeError, match='restart failure'):
        hud.remove(args, home, root)
    assert calls == [1, 1] and installed_snapshot(home, units, env) == original
    assert not (home/hud_activation.JOURNAL).exists()


def test_rollback_leaves_previously_stopped_service_stopped(monkeypatch):
    commands = []
    monkeypatch.setattr(hud, 'run', lambda cmd, **kwargs: commands.append(cmd))
    restore_service_states({hud.UNITS[0]: True, hud.UNITS[1]: False})
    assert commands == [['systemctl', '--user', 'daemon-reload'],
                        ['systemctl', '--user', 'restart', hud.UNITS[0]],
                        ['systemctl', '--user', 'stop', hud.UNITS[1]]]


def test_interrupted_recovery_preserves_operator_edit(tmp_path, layout, no_npm):
    args, home, units, root, env = staged_install(tmp_path, layout)
    before = hud.snapshot(env)
    next_value = dict(before, text='SAMPLE="pending"\n')
    dropin = hud.managed_paths(units, env)[0]
    old_unit = hud.snapshot(dropin)
    new_unit = dict(old_unit, text='[Service]\n# pending\n')
    journal = {'previous_services': None, 'changes': {str(env): {'before': before, 'after': next_value},
        str(dropin): {'before': old_unit, 'after': new_unit}}}
    hud_activation.save_record(home/hud_activation.JOURNAL, journal)
    hud.write_file(dropin, new_unit)
    env.write_text('SAMPLE="operator edit"\n')
    with pytest.raises(RuntimeError, match='changed during activation'):
        hud.apply(args, home, units, root, env)
    assert env.read_text() == 'SAMPLE="operator edit"\n'
    assert hud.snapshot(dropin) == old_unit
    assert (home/hud_activation.JOURNAL).exists()


def test_patch_content_changes_build_digest(tmp_path, layout, no_npm):
    repo = netclaw_repo(tmp_path)
    root = layout(base={'src/main.js': 'one\ntwo\nthree\n'}, patched={'src/main.js': 'one\nv1\nthree\n'})
    first = hud.build(repo, tmp_path/'install', 'node')
    patched = root/'patch-git/src/main.js'
    patched.write_text('one\nv2\nthree\n')
    (root/'patches/src/main.js.patch').write_text(git(root/'patch-git', 'diff', '--full-index', '--', 'src/main.js'))
    second = hud.build(repo, tmp_path/'install', 'node')
    assert first != second
    assert (first/hud.UI_DIR/'src/main.js').read_text() == 'one\nv1\nthree\n'
    assert (second/hud.UI_DIR/'src/main.js').read_text() == 'one\nv2\nthree\n'


def test_patch_target_cannot_change_or_traverse_a_symlink(tmp_path, layout):
    root = layout(base={'src/main.js': 'base\n'}, patched={'src/main.js': 'changed\n'})
    repo = netclaw_repo(tmp_path)
    target = repo/hud.UI_DIR/'src/main.js'
    original = tmp_path/'operator-js'
    target.rename(original)
    target.symlink_to(original)
    with pytest.raises(ValueError, match='symlink'):
        hud.merge_patched(repo/hud.UI_DIR, repo)
    assert original.read_text() == 'one\ntwo\nthree\n'
    patch = root/'patches/src/main.js.patch'
    patch.rename(root/'patches/other.js.patch')
    with pytest.raises(ValueError, match='single full-index'):
        hud.overlay_digest()


def test_multiple_patch_conflict_preserves_entire_input(tmp_path, layout):
    repo = netclaw_repo(tmp_path, {'a.js': 'old\n', 'z.js': 'competing\n'})
    root = layout(base={'a.js': 'old\n', 'z.js': 'base\n'}, patched={'a.js': 'new\n', 'z.js': 'ours\n'})
    with pytest.raises(ValueError, match='z.js: conflict'):
        hud.merge_patched(repo/hud.UI_DIR, root/'patch-git')
    assert (repo/hud.UI_DIR/'a.js').read_text() == 'old\n'
    assert not git(repo, 'status', '--porcelain')


def test_inherited_git_index_will_not_modify_callers_repository(tmp_path, layout, no_npm, monkeypatch):
    repo = netclaw_repo(tmp_path)
    layout(base={'src/main.js': 'one\ntwo\nthree\n'}, patched={'src/main.js': 'one\nnew\nthree\n'})
    unrelated = tmp_path/'hooks'/'index'
    unrelated.parent.mkdir()
    unrelated.write_bytes(b'other repository index')
    monkeypatch.setenv('GIT_INDEX_FILE', str(unrelated))
    tree = hud.build(repo, tmp_path/'install', 'node')
    assert (tree/hud.UI_DIR/'src/main.js').read_text() == 'one\nnew\nthree\n'
    assert unrelated.read_bytes() == b'other repository index'


def test_symlinked_dropin_parent_is_not_modified(tmp_path, layout, no_npm):
    repo = netclaw_repo(tmp_path)
    layout()
    args = args_for(tmp_path, repo)
    units = tmp_path/'units'
    outside = tmp_path/'operator-owned'
    units.mkdir()
    outside.mkdir()
    (units/(hud.UNITS[1] + '.d')).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='managed parent'):
        hud.apply(args, tmp_path/'oc', units, tmp_path/'install', tmp_path/'hud.env')
    assert not list(outside.iterdir())


def test_state_drift_while_building_preserves_operator_record(tmp_path, layout, no_npm, monkeypatch):
    args, home, units, root, env = staged_install(tmp_path, layout)
    original = hud.build
    state = hud.load_state(home)
    state['operator_note'] = 'intervening change'

    def build_and_update(*args):
        result = original(*args)
        hud.write_file(home/hud.STATE, hud.state_file(state))
        return result

    monkeypatch.setattr(hud, 'build', build_and_update)
    args.env = ['EXAMPLE=changed']
    before_env = env.read_bytes()
    assert hud.apply(args, home, units, root, env) == 2
    assert env.read_bytes() == before_env
    assert hud.load_state(home) == state
    assert not (home/hud_activation.JOURNAL).exists()


def test_verified_upgrade_waits_for_old_endpoints_after_rollback(tmp_path, layout, no_npm, monkeypatch):
    args, home, units, root, env = staged_install(tmp_path, layout)
    args.restart = True
    previous = Path(hud.load_state(home)['tree'])
    probes = []
    monkeypatch.setattr(hud, 'restart', lambda: None)
    def check(tree, api, host, ui):
        probes.append((tree, api, host, ui))
        if tree != previous:
            raise RuntimeError('new tree did not become ready')
        return ['convergence']
    monkeypatch.setattr(hud, 'probe', check)
    args.api_port, args.ui_port = 31231, 31232
    assert hud.apply(args, home, units, root, env) == 0
    (hud.PACKAGE/'NOTICE').write_text('different')
    args.api_port, args.ui_port = 32441, 32442
    assert hud.apply(args, home, units, root, env) == 2
    assert [ports[1:] for ports in probes] == [(31231, '127.0.0.1', 31232), (32441, '127.0.0.1', 32442),
                                                (31231, '127.0.0.1', 31232)]
    assert probes[0][0] == probes[-1][0] == previous
    assert not (home/hud_activation.JOURNAL).exists()

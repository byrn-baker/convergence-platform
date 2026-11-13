"""HUD overlay: builds from a clean checkout, merges our changes three ways, reversible units."""
import argparse
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('convergence_hud', ROOT/'setup/hud.py')
hud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hud)


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


def overlay(tmp_path, patched=None, base=None, files=None, repo_files=None):
    """A private overlay layout; the module constants point at it for the test."""
    root = tmp_path/'hud-overlay'
    for name, content in (('overlay', files or {'modules/x/module.json': '{}'}), ('patched', patched or {}),
                          ('patched-base', base or {}), ('repo-overlay', repo_files or {})):
        (root/name).mkdir(parents=True, exist_ok=True)
        for rel, text in content.items():
            (root/name/rel).parent.mkdir(parents=True, exist_ok=True)
            (root/name/rel).write_text(text)
    return root


@pytest.fixture
def layout(tmp_path, monkeypatch):
    def use(**kwargs):
        root = overlay(tmp_path, **kwargs)
        monkeypatch.setattr(hud, 'OVERLAY', root/'overlay')
        monkeypatch.setattr(hud, 'PATCHED', root/'patched')
        monkeypatch.setattr(hud, 'PATCHED_BASE', root/'patched-base')
        monkeypatch.setattr(hud, 'REPO_OVERLAY', root/'repo-overlay')
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


def test_shipped_overlay_is_consistent():
    """Every patched file has its upstream base, and no path is both added and patched."""
    patched = set(hud.patched_files(hud.PATCHED))
    assert patched == set(hud.patched_files(hud.PATCHED_BASE))
    added = {p.relative_to(hud.OVERLAY) for p in hud.overlay_files(hud.OVERLAY)}
    assert not patched & added
    assert Path('server.js') in patched and Path('src/main.js') in patched
    for rel in patched:
        assert '<<<<<<<' not in (hud.PATCHED/rel).read_text(errors='ignore'), rel
    assert (hud.OVERLAY/'modules/convergence/module.json').exists()
    assert (hud.REPO_OVERLAY/'scripts/netclaw-apply-models.sh').exists()


def test_merge_uses_ours_when_upstream_is_unchanged(tmp_path, layout):
    layout(patched={'src/main.js': 'one\nOURS\nthree\n'}, base={'src/main.js': 'one\ntwo\nthree\n'})
    ui = tmp_path/'ui'
    (ui/'src').mkdir(parents=True)
    (ui/'src/main.js').write_text('one\ntwo\nthree\n')
    assert hud.merge_patched(ui) == []
    assert (ui/'src/main.js').read_text() == 'one\nOURS\nthree\n'


def test_merge_carries_our_change_onto_a_changed_upstream(tmp_path, layout):
    base = 'a\nb\nc\nd\ne\nf\ng\n'
    layout(patched={'f.js': base.replace('b\n', 'OURS\n')}, base={'f.js': base})
    ui = tmp_path/'ui'
    ui.mkdir()
    (ui/'f.js').write_text(base.replace('f\n', 'UPSTREAM\n'))
    assert hud.merge_patched(ui) == ['f.js']
    assert (ui/'f.js').read_text() == 'a\nOURS\nc\nd\ne\nUPSTREAM\ng\n'


def test_merge_conflict_stops_and_names_the_file(tmp_path, layout):
    layout(patched={'f.js': 'a\nOURS\nc\n'}, base={'f.js': 'a\nb\nc\n'})
    ui = tmp_path/'ui'
    ui.mkdir()
    (ui/'f.js').write_text('a\nTHEIRS\nc\n')
    with pytest.raises(ValueError, match='f.js: 1 conflict'):
        hud.merge_patched(ui)
    assert (ui/'f.js').read_text() == 'a\nTHEIRS\nc\n'


def test_merge_refuses_a_file_upstream_removed(tmp_path, layout):
    layout(patched={'gone.js': 'x\n'}, base={'gone.js': 'y\n'})
    (tmp_path/'ui').mkdir()
    with pytest.raises(ValueError, match='gone.js: no longer in upstream'):
        hud.merge_patched(tmp_path/'ui')


def test_build_assembles_the_tree_without_touching_the_checkout(tmp_path, layout, no_npm):
    repo = netclaw_repo(tmp_path)
    layout(patched={'src/main.js': 'one\nOURS\nthree\n'}, base={'src/main.js': 'one\ntwo\nthree\n'},
           repo_files={'scripts/tool.sh': '#!/bin/sh\n'})
    tree = hud.build(repo, tmp_path/'install', 'node')
    ui = tree/'ui/netclaw-visual'
    assert (ui/'modules/x/module.json').exists()
    assert (ui/'src/main.js').read_text() == 'one\nOURS\nthree\n'
    assert (tree/'scripts/tool.sh').exists()
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


def test_repo_overlay_refuses_a_path_upstream_now_has(tmp_path, layout, no_npm):
    repo = netclaw_repo(tmp_path, {'../../scripts/tool.sh': 'upstream\n'})
    layout(repo_files={'scripts/tool.sh': 'ours\n'})
    with pytest.raises(ValueError, match='now exists upstream'):
        hud.build(repo, tmp_path/'install', 'node')


def test_overlay_refuses_a_file_upstream_now_ships(tmp_path, layout, no_npm):
    repo = netclaw_repo(tmp_path, {'modules/x/module.json': '{"upstream": true}'})
    layout()
    with pytest.raises(ValueError, match='ui/netclaw-visual/modules/x/module.json now exists upstream'):
        hud.build(repo, tmp_path/'install', 'node')
    assert list((tmp_path/'install').iterdir()) == []


def args_for(tmp_path, repo, **changes):
    values = {'netclaw_dir': str(repo), 'openclaw_dir': str(tmp_path/'oc'), 'testbed': str(tmp_path/'testbed.yaml'),
              'ssh_terminal': True, 'env': None, 'restart': False, 'purge': False, 'probe': False,
              'api_port': 3001, 'node': 'node'}
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
    assert f"NETCLAW_TESTBED_FILE={tmp_path/'testbed.yaml'}\n" in env
    assert 'HUD_SSH_ENABLED=1\n' in env and 'RAG_MAX_ROUNDS=3\n' in env
    assert f"NETCLAW_ENV_FILE={tmp_path/'oc'/'.env'}\n" in env


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
    # A drop-in edited by hand is kept on remove; the rest goes.
    edited = tmp_path/'units/netclaw-hud-ui.service.d'/hud.DROPIN
    edited.write_text('[Service]\n# operator edit\n')
    capsys.readouterr()
    assert hud.remove(args_for(tmp_path, repo, purge=True), tmp_path/'oc', tmp_path/'install') == 0
    out = json.loads(capsys.readouterr().out)
    assert edited.exists() and not env_file.exists()
    assert not (tmp_path/'units/netclaw-hud.service.d'/hud.DROPIN).exists()
    assert out['purged'] == [state['tree']] and not Path(state['tree']).exists()
    assert not (tmp_path/'oc'/hud.STATE).exists()


def test_failed_probe_rolls_back(tmp_path, layout, no_npm, monkeypatch, capsys):
    repo = netclaw_repo(tmp_path)
    layout()
    (tmp_path/'oc').mkdir()
    restarts = []
    monkeypatch.setattr(hud, 'restart', lambda: restarts.append(1))

    def broken(port, attempts=30):
        raise RuntimeError('HUD probe failed: convergence module not configured')
    monkeypatch.setattr(hud, 'probe', broken)
    env_file = tmp_path/'hud.env'
    assert hud.apply(args_for(tmp_path, repo, restart=True), tmp_path/'oc', tmp_path/'units',
                     tmp_path/'install', env_file) == 2
    assert 'rolled_back' in json.loads(capsys.readouterr().out)
    assert len(restarts) == 2
    assert not env_file.exists() and not (tmp_path/'oc'/hud.STATE).exists()
    assert not any((tmp_path/'units').rglob(hud.DROPIN))

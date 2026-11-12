"""pfSense/UniFi MCP install: pinned sources, ${VAR} references only, exact and reversible config changes."""
import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('convergence_network_mcps', ROOT/'setup/network_mcps.py')
mcps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcps)

ENV = {'PFSENSE_URL': 'https://192.0.2.1:440', 'PFSENSE_VERSION': 'CE_2_8_1', 'PFSENSE_AUTH_METHOD': 'api_key',
       'PFSENSE_API_KEY': 'p'*40, 'PFSENSE_VERIFY_SSL': 'false',
       'UNIFI_HOST': 'https://192.0.2.2', 'UNIFI_API_KEY': 'u'*40, 'UNIFI_SITE': 'default'}
PRODUCTION_PFSENSE = {'command': '${NETCLAW_DIR}/.venv/bin/python3', 'enabled': True,
                      'args': ['-u', '-c', 'from src.main import main; main()'],
                      'env': {'PFSENSE_URL': '${PFSENSE_URL}', 'PFSENSE_VERSION': '${PFSENSE_VERSION}',
                              'PFSENSE_PASSWORD': '${PFSENSE_PASSWORD}'}}
PRODUCTION_UNIFI = {'command': '/home/op/netclaw/scripts/run-unifi-mcp.sh', 'args': []}


def opts(**changes):
    return {'servers': ['pfsense', 'unifi'], 'replace_existing': False, 'pfsense_version': None,
            'pfsense_read_write': False, 'state': {}, **changes}


def test_plan_registers_both_servers_with_references_only(tmp_path):
    result = mcps.plan({}, ENV, opts(), tmp_path)
    assert result['conflicts'] == []
    servers = result['patch']['mcp']['servers']
    pf, unifi = servers['pfsense-mcp'], servers['unifi-network']
    assert pf['command'] == str(tmp_path/'pfsense/venv/bin/python')
    assert pf['args'] == ['-u', '-m', 'src.main'] and pf['cwd'] == str(tmp_path/'pfsense/source')
    assert pf['env'] == {'PFSENSE_URL': '${PFSENSE_URL}', 'PFSENSE_VERSION': '${PFSENSE_VERSION}',
                         'AUTH_METHOD': '${PFSENSE_AUTH_METHOD}', 'PFSENSE_API_KEY': '${PFSENSE_API_KEY}',
                         'VERIFY_SSL': '${PFSENSE_VERIFY_SSL}', 'MCP_READ_ONLY': 'true',
                         'MCP_ENABLE_LOG_FILES': 'false'}
    assert unifi == {'command': str(tmp_path/'unifi/venv/bin/python'), 'args': ['-u', '-m', 'unifi_mcp'],
                     'env': {'UNIFI_HOST': '${UNIFI_HOST}', 'UNIFI_API_KEY': '${UNIFI_API_KEY}',
                             'UNIFI_SITE': '${UNIFI_SITE}', 'UNIFI_PRELOAD_GROUPS': 'network:integration'}}
    text = json.dumps(result)
    assert 'p'*40 not in text and 'u'*40 not in text and '192.0.2' not in text
    assert result['env_updates'] == {}


def test_pins_are_full_commits():
    for server in mcps.SERVERS.values():
        assert len(server['commit']) == 40 and int(server['commit'], 16) >= 0
        assert server['repo'].startswith('https://github.com/')


def test_plan_refuses_missing_credentials_and_foreign_state(tmp_path):
    (tmp_path/'unifi').mkdir()
    config = {'mcp': {'servers': {'pfsense-mcp': PRODUCTION_PFSENSE}}}
    env = {k: v for k, v in ENV.items() if k not in ('PFSENSE_API_KEY', 'UNIFI_API_KEY')}
    conflicts = ' | '.join(mcps.plan(config, env, opts(), tmp_path)['conflicts'])
    for needle in ('pfsense-mcp: PFSENSE_API_KEY not set', 'unifi-network: UNIFI_API_KEY not set',
                   'pfsense-mcp is already configured', 'was not created by this tool'):
        assert needle in conflicts


def test_basic_auth_needs_username_and_password(tmp_path):
    env = {**ENV, 'PFSENSE_AUTH_METHOD': 'basic'}
    conflicts = mcps.plan({}, env, opts(servers=['pfsense']), tmp_path)['conflicts']
    assert conflicts == ['pfsense-mcp: PFSENSE_USERNAME, PFSENSE_PASSWORD not set in .env']
    env.update(PFSENSE_USERNAME='admin', PFSENSE_PASSWORD='x')
    pf = mcps.plan({}, env, opts(servers=['pfsense']), tmp_path)['patch']['mcp']['servers']['pfsense-mcp']
    assert pf['env']['PFSENSE_PASSWORD'] == '${PFSENSE_PASSWORD}'


def test_pfsense_version_must_be_one_the_pinned_release_accepts(tmp_path):
    env = {**ENV, 'PFSENSE_VERSION': 'CE_2_8_2'}
    (conflict,) = mcps.plan({}, env, opts(servers=['pfsense']), tmp_path)['conflicts']
    assert 'PFSENSE_VERSION in .env is not accepted' in conflict and '--pfsense-version' in conflict
    result = mcps.plan({}, env, opts(servers=['pfsense'], pfsense_version='PLUS_26_03_1'), tmp_path)
    assert result['conflicts'] == [] and result['env_updates'] == {'PFSENSE_VERSION': 'PLUS_26_03_1'}
    bad = mcps.plan({}, env, opts(servers=['pfsense'], pfsense_version='CE_2_8_2'), tmp_path)['conflicts']
    assert '--pfsense-version CE_2_8_2: not a pfsense-mcp-server v1.1.0 version' in bad[0]


def test_read_write_opt_out(tmp_path):
    pf = mcps.plan({}, ENV, opts(servers=['pfsense'], pfsense_read_write=True), tmp_path)
    assert pf['patch']['mcp']['servers']['pfsense-mcp']['env']['MCP_READ_ONLY'] == 'false'


def test_replace_existing_patch_is_exact(tmp_path):
    config = {'mcp': {'servers': {'pfsense-mcp': PRODUCTION_PFSENSE, 'unifi-network': PRODUCTION_UNIFI}}}
    result = mcps.plan(config, ENV, opts(replace_existing=True), tmp_path)
    assert result['conflicts'] == []
    pf = result['patch']['mcp']['servers']['pfsense-mcp']
    # Keys the old entry had and the new one lacks are deleted, nested ones too.
    assert pf['enabled'] is None and pf['env']['PFSENSE_PASSWORD'] is None
    assert pf['args'] == ['-u', '-m', 'src.main']
    assert result['previous'] == {'pfsense-mcp': PRODUCTION_PFSENSE, 'unifi-network': PRODUCTION_UNIFI}


def merge(target, patch):
    """RFC 7396 merge patch, as OpenClaw's config patch applies it."""
    if not isinstance(patch, dict):
        return patch
    out = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = merge(out.get(key), value)
    return out


def test_replacing_round_trips_through_a_merge_patch(tmp_path):
    new = mcps.entry('pfsense', tmp_path, ENV, {})
    assert merge(PRODUCTION_PFSENSE, mcps.replacing(PRODUCTION_PFSENSE, new)) == new
    assert merge(new, mcps.replacing(new, PRODUCTION_PFSENSE)) == PRODUCTION_PFSENSE
    assert mcps.replacing(new, None) is None


FAKE_SERVER = r'''
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get('method') == 'initialize':
        print(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': {'protocolVersion': '2025-06-18'}}), flush=True)
    elif msg.get('method') == 'tools/list':
        # Deliver noise and the reply in the same pipe write.
        print('not json', flush=False)
        print(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': {'tools': [{'name': 'a'}, {'name': 'b'}]}}), flush=True)
'''


def test_probe_lists_tools_over_stdio(tmp_path):
    server = tmp_path/'server.py'
    server.write_text(FAKE_SERVER)
    assert mcps.probe(sys.executable, [str(server)], None, {}, timeout=3) == ['a', 'b']


def test_probe_times_out_on_a_silent_server(tmp_path):
    server = tmp_path/'server.py'
    server.write_text('import time; time.sleep(30)\n')
    with pytest.raises(RuntimeError, match='did not answer'):
        mcps.probe(sys.executable, [str(server)], None, {}, timeout=1)


def test_probe_server_uses_the_registered_entry(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(mcps, 'probe', lambda command, args, cwd, env, timeout=60: seen.append(env) or ['t'])
    registered = mcps.entry('pfsense', tmp_path, ENV, opts(pfsense_read_write=True))
    mcps.probe_server('pfsense', tmp_path, opts(), registered)
    mcps.probe_server('pfsense', tmp_path, opts())
    assert [e['MCP_READ_ONLY'] for e in seen] == ['false', 'true']
    assert all(not v.startswith('${') for e in seen for v in e.values())

def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def test_install_checks_out_the_pinned_commit(tmp_path, monkeypatch):
    origin = tmp_path/'origin'
    origin.mkdir()
    git('init', '-q', cwd=origin)
    (origin/'requirements.txt').write_text('')
    git('add', '.', cwd=origin)
    git('-c', 'user.email=t@example.com', '-c', 'user.name=t', 'commit', '-qm', 'pinned', cwd=origin)
    pinned = git('rev-parse', 'HEAD', cwd=origin)
    (origin/'later.txt').write_text('later')
    git('add', '.', cwd=origin)
    git('-c', 'user.email=t@example.com', '-c', 'user.name=t', 'commit', '-qm', 'later', cwd=origin)
    fake = {**mcps.SERVERS['pfsense'], 'repo': str(origin), 'commit': pinned, 'python': (3, 8),
            'install': ['--no-index', '-r', 'requirements.txt']}
    monkeypatch.setitem(mcps.SERVERS, 'pfsense', fake)
    root = tmp_path/'mcp'
    mcps.install('pfsense', root, sys.executable)
    assert git('rev-parse', 'HEAD', cwd=root/'pfsense/source') == pinned
    assert not (root/'pfsense/source/later.txt').exists()
    assert (root/'pfsense/venv/bin/python').exists() and (root/'pfsense'/mcps.MARKER).exists()
    monkeypatch.setitem(mcps.SERVERS, 'pfsense', {**fake, 'commit': '0'*40})
    with pytest.raises(RuntimeError):
        mcps.install('pfsense', root, sys.executable)


def test_install_refuses_an_old_python(tmp_path, monkeypatch):
    monkeypatch.setitem(mcps.SERVERS, 'unifi', {**mcps.SERVERS['unifi'], 'python': (99, 0)})
    with pytest.raises(RuntimeError, match='needs Python 99.0'):
        mcps.install('unifi', tmp_path, sys.executable)


def args(**changes):
    return argparse.Namespace(**{'server': None, 'replace_existing': True, 'pfsense_version': 'CE_2_8_1',
                                 'pfsense_read_write': False, 'python': sys.executable, 'openclaw': 'openclaw',
                                 'restart': False, **changes})


def home_with(tmp_path, servers, env):
    home = tmp_path/'oc'
    home.mkdir()
    (home/'openclaw.json').write_text(json.dumps({'mcp': {'servers': servers}}))
    (home/'.env').write_text(''.join(f'{k}={v}\n' for k, v in env.items()))
    return home


def test_apply_then_remove_restores_the_previous_entries_and_env(tmp_path, monkeypatch, capsys):
    servers = {'pfsense-mcp': PRODUCTION_PFSENSE, 'unifi-network': PRODUCTION_UNIFI}
    home = home_with(tmp_path, servers, {**ENV, 'PFSENSE_VERSION': 'CE_2_8_2'})
    root = tmp_path/'mcp'

    def fake_install(key, root, python):
        (root/key).mkdir(parents=True, exist_ok=True)
        (root/key/mcps.MARKER).write_text('{}')

    def fake_patch(cli, patch, home_dir):
        assert home_dir == home
        config = json.loads((home/'openclaw.json').read_text())
        (home/'openclaw.json').write_text(json.dumps(merge(config, patch)))

    monkeypatch.setattr(mcps, 'install', fake_install)
    monkeypatch.setattr(mcps, 'probe_server', lambda key, root, opts: ['tool'])
    monkeypatch.setattr(mcps, 'openclaw_patch', fake_patch)
    assert mcps.apply(args(), home, root) == 0
    config = json.loads((home/'openclaw.json').read_text())['mcp']['servers']
    assert config['pfsense-mcp'] == mcps.entry('pfsense', root, ENV, {})
    assert mcps.read_env(home/'.env')['PFSENSE_VERSION'] == 'CE_2_8_1'
    state = json.loads((home/mcps.STATE).read_text())
    assert state['servers']['pfsense']['previous'] == PRODUCTION_PFSENSE
    assert (home/mcps.STATE).stat().st_mode & 0o777 == 0o600
    # A second apply keeps the original previous entry, not our own.
    assert mcps.apply(args(), home, root) == 0
    assert json.loads((home/mcps.STATE).read_text())['servers']['pfsense']['previous'] == PRODUCTION_PFSENSE
    assert json.loads((home/mcps.STATE).read_text())['env_restore'] == {'PFSENSE_VERSION': 'CE_2_8_2'}
    capsys.readouterr()
    assert mcps.remove(args(), home) == 0
    assert json.loads((home/'openclaw.json').read_text())['mcp']['servers'] == servers
    assert mcps.read_env(home/'.env')['PFSENSE_VERSION'] == 'CE_2_8_2'
    assert not (root/'pfsense').exists() and not (root/'unifi').exists()
    assert not (home/mcps.STATE).exists()


def test_remove_deletes_entries_it_created_and_keeps_changed_ones(tmp_path, monkeypatch, capsys):
    root = tmp_path/'mcp'
    ours = mcps.entry('unifi', root, ENV, {})
    changed = {**mcps.entry('pfsense', root, ENV, {}), 'command': '/elsewhere/python'}
    home = home_with(tmp_path, {'pfsense-mcp': changed, 'unifi-network': ours}, ENV)
    (home/mcps.STATE).write_text(json.dumps({'servers': {
        'pfsense': {'name': 'pfsense-mcp', 'directory': str(root/'pfsense'), 'previous': None},
        'unifi': {'name': 'unifi-network', 'directory': str(root/'unifi'), 'previous': None}}}))
    patches = []
    monkeypatch.setattr(mcps, 'openclaw_patch', lambda cli, patch, home_dir: patches.append(patch))
    assert mcps.remove(args(), home) == 0
    assert patches == [{'mcp': {'servers': {'unifi-network': None}}}]
    assert 'changed since install' in ' '.join(json.loads(capsys.readouterr().out)['kept'])


def test_openclaw_cli_is_pointed_at_the_given_home(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENCLAW_CONFIG_PATH', '/somewhere/else/openclaw.json')
    calls = []
    monkeypatch.setattr(mcps, 'run', lambda cmd, **kw: calls.append(kw['env']))
    mcps.openclaw_patch('openclaw', {'mcp': {}}, tmp_path)
    assert len(calls) == 2
    for env in calls:
        assert env['OPENCLAW_CONFIG_PATH'] == str(tmp_path/'openclaw.json')
        assert env['OPENCLAW_STATE_DIR'] == str(tmp_path)


def test_unifi_preload_can_be_overridden_from_env(tmp_path):
    env = {**ENV, 'UNIFI_PRELOAD_GROUPS': 'network:integration,network:insights'}
    unifi = mcps.plan({}, env, opts(servers=['unifi']), tmp_path)['patch']['mcp']['servers']['unifi-network']
    assert unifi['env']['UNIFI_PRELOAD_GROUPS'] == '${UNIFI_PRELOAD_GROUPS}'

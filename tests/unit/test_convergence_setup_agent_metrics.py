"""The agent metrics unit runs this checkout's exporter, refuses to fight for its port, and undoes only itself."""
import importlib.util
import json
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('convergence_agent_metrics', ROOT/'setup/agent_metrics.py')
agent_metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_metrics)


class Host:
    """Fake systemd and pip, recording calls; real files under tmp XDG directories."""

    def __init__(self, monkeypatch, tmp_path):
        self.calls, self.active, self.enabled = [], set(), set()
        monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path/'config'))
        monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path/'state'))
        monkeypatch.setattr(agent_metrics, 'systemctl', self.systemctl)
        monkeypatch.setattr(agent_metrics, 'linger', lambda: False)
        monkeypatch.setattr(agent_metrics.subprocess, 'run', self.run)
        self.home = tmp_path/'openclaw'
        (self.home/'agents').mkdir(parents=True)

    def systemctl(self, *args, check=True):
        self.calls.append(('systemctl', *args))
        if args[0] == 'is-active':
            return 'active' if args[1] in self.active else 'inactive'
        if args[0] == 'is-enabled':
            return 'enabled' if args[1] in self.enabled else 'disabled'
        return ''

    def run(self, command, check=False, **_kwargs):
        self.calls.append(tuple(command))
        if command[1:3] == ['-m', 'venv']:
            (Path(command[3])/'bin').mkdir(parents=True)
            (Path(command[3])/'bin/python').write_text('')
            (Path(command[3])/'pyvenv.cfg').write_text('home = /usr/bin\n')

    def args(self, command='install', **overrides):
        values = dict(command=command, name='convergence-agent-metrics', bind='127.0.0.1', port=free_port(),
                      openclaw_home=str(self.home), instance='netclaw', venv=None, python='python3', start=False)
        values.update(overrides)
        return agent_metrics.argparse.Namespace(**values)


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture
def host(monkeypatch, tmp_path):
    return Host(monkeypatch, tmp_path)


def test_unit_runs_the_checkout_exporter_with_its_settings():
    text = agent_metrics.unit(Path('/s/venv'), Path('/home/u/.openclaw'), '192.0.2.10', 9110, 'netclaw')
    assert f'WorkingDirectory={ROOT}/services/agent-metrics' in text
    assert 'ExecStart=/s/venv/bin/python exporter.py' in text
    for line in ('Environment=OPENCLAW_HOME=/home/u/.openclaw', 'Environment=EXPORTER_BIND=192.0.2.10',
                 'Environment=EXPORTER_PORT=9110', 'Environment=NETCLAW_INSTANCE=netclaw'):
        assert line in text.splitlines()
    assert 'WantedBy=default.target' in text and 'User=' not in text and 'EnvironmentFile' not in text


def test_unit_rejects_paths_systemd_would_reinterpret():
    for bad in ('/h/my home', '/h/%h', '/h/$X', '/h/"q'):
        with pytest.raises(ValueError):
            agent_metrics.unit(Path('/v'), Path(bad), '127.0.0.1', 9110, 'netclaw')


def test_plan_changes_nothing_and_explains_the_scrape_address(host):
    result = agent_metrics.plan(host.args('plan'))
    assert result['conflicts'] == []
    assert any('Convergence cannot scrape it' in note for note in result['notes'])
    assert not Path(result['unit']).exists() and not Path(result['state']).exists()
    assert all(call[0] == 'systemctl' and call[1] in ('is-active', 'is-enabled') for call in host.calls)
    lan = agent_metrics.plan(host.args('plan', bind='0.0.0.0'))
    assert any('every interface' in note for note in lan['notes'])
    assert 'Environment=EXPORTER_BIND=0.0.0.0' in lan['unit_text']


@pytest.mark.parametrize('field,value,message', [
    ('bind', 'my-host', 'IP address'), ('port', 0, '1-65535'), ('instance', 'a b', 'instance'),
])
def test_invalid_options_are_refused(host, field, value, message):
    with pytest.raises(ValueError, match=message):
        agent_metrics.plan(host.args('plan', **{field: value}))


def test_install_refuses_while_the_old_exporter_runs_and_says_how_to_switch(host):
    host.active.add('openclaw-token-exporter.service')
    with pytest.raises(ValueError) as refused:
        agent_metrics.install(host.args())
    assert 'systemctl --user disable --now openclaw-token-exporter.service' in str(refused.value)
    assert not any(call[:2] in (('systemctl', 'stop'), ('systemctl', 'disable')) for call in host.calls)
    assert not (Path(agent_metrics.paths(host.args())[1])).exists()


def test_install_refuses_a_port_held_by_another_program(host):
    with socket.socket() as other:
        other.bind(('127.0.0.1', 0))
        other.listen()
        port = other.getsockname()[1]
        with pytest.raises(ValueError, match='in use by another program'):
            agent_metrics.install(host.args(port=port))
        assert agent_metrics.main(['plan', '--port', str(port), '--openclaw-home', str(host.home)]) == 1


def test_install_refuses_an_address_this_host_does_not_have(host):
    with pytest.raises(ValueError, match='not an address of this host'):
        agent_metrics.install(host.args(bind='192.0.2.254'))   # TEST-NET-1, never local


def test_install_refuses_a_missing_openclaw_home(host, tmp_path):
    with pytest.raises(ValueError, match='not a directory'):
        agent_metrics.install(host.args(openclaw_home=str(tmp_path/'nope')))


def test_install_writes_the_unit_and_start_restarts(host):
    args = host.args(start=True, bind='127.0.0.1')
    result = agent_metrics.install(args)
    unit_path, state = agent_metrics.paths(args)
    assert result['status'] == 'installed' and Path(result['venv']) == state/'venv'
    assert f'Environment=EXPORTER_PORT={args.port}' in unit_path.read_text()
    assert ('python3', '-m', 'venv', str(state/'venv')) in host.calls
    assert any(call[-1].endswith('services/agent-metrics/requirements.txt') for call in host.calls)
    assert host.calls[-3:] == [('systemctl', 'daemon-reload'), ('systemctl', 'enable', 'convergence-agent-metrics.service'),
                               ('systemctl', 'restart', 'convergence-agent-metrics.service')]
    assert oct(state.stat().st_mode & 0o777) == '0o700'


def test_reinstall_over_our_own_running_service_is_not_a_port_conflict(host):
    args = host.args()
    agent_metrics.install(args)
    host.active.add('convergence-agent-metrics.service')
    with socket.socket() as ours:   # stands in for the running exporter
        ours.bind(('127.0.0.1', args.port))
        ours.listen()
        assert agent_metrics.install(args)['status'] == 'installed'
        with pytest.raises(ValueError, match='in use'):   # a changed address is checked again
            agent_metrics.install(host.args(port=args.port, bind='0.0.0.0'))


def test_remove_undoes_only_what_install_created(host, tmp_path):
    own_venv = tmp_path/'own-venv'
    (own_venv/'bin').mkdir(parents=True)
    (own_venv/'bin/python').write_text('')
    args = host.args(venv=str(own_venv))
    agent_metrics.install(args)
    unit_path, state = agent_metrics.paths(args)
    assert json.loads((state/'install.json').read_text())['unit_created'] is True
    result = agent_metrics.remove(args)
    assert not unit_path.exists() and not state.exists()
    assert own_venv.exists() and any('existed before install' in k for k in result['kept'])
    assert ('systemctl', 'disable', '--now', 'convergence-agent-metrics.service') in host.calls
    assert not any('openclaw-token-exporter' in ' '.join(call) for call in host.calls
                   if call[1] not in ('is-active', 'is-enabled'))


def test_remove_deletes_the_venv_it_created_and_keeps_a_foreign_unit(host):
    args = host.args()
    agent_metrics.install(args)
    unit_path, state = agent_metrics.paths(args)
    assert agent_metrics.remove(args)['removed'] == [str(unit_path), str(state/'venv'), str(state)]
    unit_path.write_text('[Unit]\n')   # someone else's file under our name
    result = agent_metrics.remove(args)
    assert unit_path.exists() and result['kept'] == [f'{unit_path} (not created by this helper)']


def test_status_probes_the_installed_exporter(host, monkeypatch):
    args = host.args()
    agent_metrics.install(args)
    monkeypatch.setattr(agent_metrics, 'probe', lambda settings: {'settings': settings})
    result = agent_metrics.status(args)
    assert result['installed'] and result['old_exporter'] == 'inactive'
    assert result['metrics'] == {'settings': {'EXPORTER_BIND': '127.0.0.1', 'EXPORTER_PORT': str(args.port)}}


def test_probe_reports_an_unreachable_exporter():
    result = agent_metrics.probe({'EXPORTER_BIND': '0.0.0.0', 'EXPORTER_PORT': str(free_port())})
    assert result['ok'] is False and result['url'].startswith('http://127.0.0.1:')


def test_install_refuses_while_the_old_exporter_is_enabled_even_if_stopped(host):
    host.enabled.add('openclaw-token-exporter.service')
    with pytest.raises(ValueError, match='is enabled .*disable --now openclaw-token-exporter'):
        agent_metrics.install(host.args())


def test_bind_with_an_ipv6_scope_id_is_refused(host):
    with pytest.raises(ValueError, match='scope ID'):
        agent_metrics.plan(host.args('plan', bind='fe80::1%eth0'))
    with pytest.raises(ValueError):   # systemd would read % as a specifier
        agent_metrics.unit(Path('/v'), Path('/h'), 'fe80::1%eth0', 9110, 'netclaw')


def test_install_refuses_a_unit_file_it_did_not_create(host):
    args = host.args()
    unit_path, state = agent_metrics.paths(args)
    unit_path.parent.mkdir(parents=True)
    unit_path.write_text('[Unit]\nDescription=someone else\n')
    with pytest.raises(ValueError, match='was not created by this helper'):
        agent_metrics.install(args)
    assert unit_path.read_text() == '[Unit]\nDescription=someone else\n' and not state.exists()


def test_reinstall_with_another_venv_never_removes_that_venv(host, tmp_path):
    args = host.args()
    agent_metrics.install(args)
    _, state = agent_metrics.paths(args)
    own = tmp_path/'own-venv'
    (own/'bin').mkdir(parents=True)
    (own/'bin/python').write_text('')
    (own/'pyvenv.cfg').write_text('home = /usr/bin\n')
    agent_metrics.install(host.args(port=args.port, venv=str(own)))
    record = agent_metrics.read_manifest(state)
    assert record['venv'] == str(own) and record['created_venvs'] == [str(state/'venv')]
    result = agent_metrics.remove(args)
    assert own.is_dir() and f'{own} (existed before install)' in result['kept']
    assert str(state/'venv') in result['removed'] and not (state/'venv').exists()


def test_reinstall_reuses_the_venv_it_created_and_records_it_once(host):
    args = host.args()
    agent_metrics.install(args)
    agent_metrics.install(args)
    _, state = agent_metrics.paths(args)
    assert agent_metrics.read_manifest(state)['created_venvs'] == [str(state/'venv')]

"""The investigator helper drives NetClaw's member-spec tooling, keeps the token private and undoes only itself."""
import importlib.util
import json
import os
import stat
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('convergence_investigator', ROOT/'setup/investigator.py')
investigator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(investigator)

SHIPPED = json.loads((ROOT/'integrations/netclaw/investigator.json').read_text())
ALL_SERVERS = SHIPPED['servers'] + ['memory-mcp', 'gait-mcp', 'n2n-mcp']
TOKEN = 'enroll-SECRET-0123456789'

# Stand-ins for NetClaw's scripts: they run as real subprocesses and log argv and runtime selection.
PROFILES = '''
import json, os, sys
# def load_member_spec  (marks member spec support, NetClaw 1.21.0+)
log = os.path.join(os.path.dirname(__file__), 'calls.jsonl')
open(log, 'a').write(json.dumps({'argv': sys.argv[1:], 'runtime': os.environ.get('NETCLAW_RUNTIME'),
                                 'home': os.environ.get('OPENCLAW_HOME')}) + '\\n')
spec = json.load(open(sys.argv[2]))
border = set(json.load(open(sys.argv[sys.argv.index('--border-config') + 1]))['mcp']['servers'])
missing = [s for s in spec['servers'] if s not in border]
if missing:
    print('error: servers not in the Border template: ' + ', '.join(missing), file=sys.stderr); sys.exit(1)
print('warning: skill not installed: pfsense-threat-intel', file=sys.stderr)
spec['scope'] = list(spec.get('skills', []))
print(json.dumps(spec))
'''
MIGRATE = '''
import json, os, sys
log = os.path.join(os.path.dirname(__file__), 'calls.jsonl')
open(log, 'a').write(json.dumps({'argv': sys.argv[1:], 'runtime': os.environ.get('NETCLAW_RUNTIME'),
                                 'home': os.environ.get('OPENCLAW_HOME')}) + '\\n')
a = sys.argv
name = json.load(open(a[a.index('--spec') + 1]))['name']
d = os.path.join(a[a.index('--staging') + 1], 'members', name)
os.makedirs(d, exist_ok=True)
fd = os.open(os.path.join(d, '.env'), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, b'N2N_ROLE=member\\n# N2N_ENROLLMENT_TOKEN=<paste>\\n'); os.close(fd)
open(os.path.join(d, 'run.sh'), 'w').write('#!/usr/bin/env bash\\n')
'''
MEMBER_HOME = '''
import json, os, sys
log = os.path.join(os.path.dirname(__file__), 'calls.jsonl')
open(log, 'a').write(json.dumps({'argv': sys.argv[1:], 'runtime': os.environ.get('NETCLAW_RUNTIME'),
                                 'home': os.environ.get('OPENCLAW_HOME')}) + '\\n')
if os.environ.get('FAIL_MEMBER_HOME'):
    os.makedirs(os.path.expanduser('~/.openclaw-r1-alert-investigator'), exist_ok=True)
    print('error: refused', file=sys.stderr); sys.exit(3)
a = sys.argv
name = json.load(open(a[a.index('--spec') + 1]))['name']
home = os.path.expanduser('~/.openclaw-%s-%s' % (a[a.index('--risk') + 1], name))
os.makedirs(home)
open(os.path.join(home, 'openclaw.json'), 'w').write('{}')
'''


class Daemon:
    """The mesh daemon's loopback API."""

    def __init__(self, role='border', members=()):
        self.role, self.members, self.posts = role, {m: {'member_id': m, 'state': 'active'} for m in members}, []

    def __call__(self, base, path, body=None, timeout=30):
        if body is None and path == '/n2n/risk':
            return {'role': self.role, 'risk_name': 'r1'}
        if body is None and path == '/n2n/members':
            return {'members': list(self.members.values())}
        self.posts.append((path, body))
        if path == '/n2n/members/add':
            member_id = 'r1/' + body['name']
            self.members[member_id] = {'member_id': member_id, 'state': 'provisioned', 'profile': 'custom'}
            return {'member_id': member_id, 'enrollment_token': TOKEN}
        if path == '/n2n/members/remove':
            return {'removed': self.members.pop(body['member_id'], None) is not None}
        raise AssertionError(path)


class Host:
    def __init__(self, monkeypatch, tmp_path, servers=ALL_SERVERS, daemon=None, spec_support=True):
        monkeypatch.setenv('HOME', str(tmp_path/'home'))
        monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path/'state'))
        monkeypatch.delenv('FAIL_MEMBER_HOME', raising=False)
        (tmp_path/'home').mkdir()
        self.netclaw = tmp_path/'netclaw'
        (self.netclaw/'scripts').mkdir(parents=True)
        profiles = PROFILES if spec_support else PROFILES.replace('def load_member_spec', 'no specs')
        for name, text in (('in2n-profiles.py', profiles), ('in2n-migrate.py', MIGRATE),
                           ('in2n-member-home.py', MEMBER_HOME)):
            (self.netclaw/'scripts'/name).write_text(textwrap.dedent(text))
        self.openclaw = tmp_path/'openclaw'
        self.openclaw.mkdir()
        (self.openclaw/'openclaw.json').write_text(json.dumps({'mcp': {'servers': {s: {} for s in servers}}}))
        self.daemon = daemon or Daemon()
        monkeypatch.setattr(investigator, 'api', self.daemon)
        self.state = tmp_path/'state/convergence/investigator-alert-investigator'
        self.member_home = tmp_path/'home/.openclaw-r1-alert-investigator'

    def main(self, *args, capsys=None):
        code = investigator.main([args[0], '--netclaw-dir', str(self.netclaw), '--openclaw-dir', str(self.openclaw),
                                  *args[1:]])
        out = capsys.readouterr() if capsys else None
        return code, out

    def calls(self):
        log = self.netclaw/'scripts/calls.jsonl'
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_shipped_spec_is_a_valid_member_spec_shape():
    assert set(SHIPPED) <= {'version', 'name', 'description', 'servers', 'skills', 'tools', 'model'}
    assert SHIPPED['version'] == 1 and SHIPPED['model'] is None and set(SHIPPED['tools']) == {'allow'}
    assert 'convergence-case-mcp' in SHIPPED['servers'] and 'alert-triage' in SHIPPED['skills']
    usable = set(SHIPPED['servers']) | set(investigator.BASE_SERVERS)
    for entry in SHIPPED['tools']['allow']:
        assert '__' not in entry or entry.split('__', 1)[0] in usable, entry
    builtins = [e for e in SHIPPED['tools']['allow'] if '__' not in e]
    assert builtins == ['session_status', 'read']  # nothing that writes, runs or messages
    assert not any(s.startswith('home-') for s in SHIPPED['skills'])  # no site-specific skills


def test_effective_spec_drops_servers_the_border_lacks_with_their_tools_and_skills():
    border = {'convergence-case-mcp', 'prometheus-mcp', 'memory-mcp'}
    spec, dropped, dropped_skills = investigator.effective_spec(SHIPPED, border, name='inv2',
                                                                model='claude-sonnet-5')
    assert spec['servers'] == ['convergence-case-mcp', 'prometheus-mcp']
    assert set(dropped) == set(SHIPPED['servers']) - border
    assert all(e.split('__')[0] in {'convergence-case-mcp', 'prometheus-mcp', 'memory-mcp'}
               for e in spec['tools']['allow'] if '__' in e)
    assert 'memory-mcp__memory_recall' in spec['tools']['allow'] and 'read' in spec['tools']['allow']
    assert spec['skills'] == ['alert-triage', 'prometheus-monitoring']
    assert 'pyats-network' in dropped_skills and 'grafana-observability' in dropped_skills
    assert spec['name'] == 'inv2' and spec['model'] == 'claude-sonnet-5'
    assert SHIPPED['servers'][1] == 'prometheus-mcp' and len(SHIPPED['tools']['allow']) == 29  # input unchanged


def test_plan_validates_with_netclaw_against_this_border_and_changes_nothing(monkeypatch, tmp_path, capsys):
    host = Host(monkeypatch, tmp_path, servers=['convergence-case-mcp', 'prometheus-mcp', 'memory-mcp'])
    code, out = host.main('plan', capsys=capsys)
    result = json.loads(out.out)
    assert code == 0 and result['conflicts'] == []
    assert result['member_id'] == 'r1/alert-investigator' and result['home'] == str(host.member_home)
    assert any(n.startswith('left out (not on the Border): grafana-mcp, pyats-mcp') for n in result['notes'])
    assert 'warning: skill not installed: pfsense-threat-intel' in result['notes']
    assert result['scope'] == ['alert-triage', 'prometheus-monitoring']
    [call] = host.calls()
    assert call['argv'][0] == 'spec' and call['argv'][-2:] == ['--border-config', str(host.openclaw/'openclaw.json')]
    assert call['runtime'] == 'openclaw' and call['home'] == str(host.openclaw)
    assert not host.state.exists() and not host.member_home.exists() and host.daemon.posts == []


@pytest.mark.parametrize('setup,expected', [
    (dict(spec_support=False), 'NetClaw 1.21.0 or later is required'),
    (dict(servers=['prometheus-mcp']), 'run `python3 setup/netclaw.py apply` first'),
    (dict(daemon=Daemon(role='standalone')), 'not a Border'),
    (dict(daemon=Daemon(members=['r1/alert-investigator'])), 'was not added by this helper'),
])
def test_plan_refuses(monkeypatch, tmp_path, capsys, setup, expected):
    host = Host(monkeypatch, tmp_path, **setup)
    code, out = host.main('plan', capsys=capsys)
    assert code == 1 and any(expected in c for c in json.loads(out.out)['conflicts'])
    assert host.calls() == []  # NetClaw is not asked to validate a refused plan


def test_plan_refuses_an_existing_member_home_and_bad_options(monkeypatch, tmp_path, capsys):
    host = Host(monkeypatch, tmp_path)
    host.member_home.mkdir()
    code, out = host.main('plan', capsys=capsys)
    assert code == 1 and any('already exists and was not created' in c for c in json.loads(out.out)['conflicts'])
    assert host.main('plan', '--model', 'openai/gpt-6', capsys=capsys)[0] == 2
    assert host.main('plan', '--name', '../x', capsys=capsys)[0] == 2


def test_netclaw_refusal_is_a_conflict(monkeypatch, tmp_path, capsys):
    host = Host(monkeypatch, tmp_path)
    spec_file = tmp_path/'bad.json'
    spec_file.write_text(json.dumps({**SHIPPED, 'servers': SHIPPED['servers'] + ['not-on-border']}))
    # effective_spec drops unknown servers, so force a NetClaw-side refusal through a stricter Border check.
    monkeypatch.setattr(investigator, 'effective_spec', lambda s, b, n=None, m=None: (s, [], []))
    code, out = host.main('plan', '--spec', str(spec_file), capsys=capsys)
    assert code == 1 and any('NetClaw refused the spec' in c and 'not-on-border' in c
                             for c in json.loads(out.out)['conflicts'])


def test_apply_provisions_an_on_demand_member_and_never_prints_the_token(monkeypatch, tmp_path, capsys):
    host = Host(monkeypatch, tmp_path)
    code, out = host.main('apply', capsys=capsys)
    assert code == 0 and TOKEN not in out.out and TOKEN not in out.err
    result = json.loads(out.out)
    assert result['status'] == 'applied' and result['mode'] == 'on-demand'
    staged = host.state/'staging/members/alert-investigator'
    calls = host.calls()
    assert [c['argv'][0].rsplit('/', 1)[-1] if '/' in c['argv'][0] else c['argv'][0] for c in calls] == [
        'spec', '--risk', '--runtime']
    migrate, member_home = calls[1]['argv'], calls[2]['argv']
    assert migrate[migrate.index('--spec') + 1] == str(host.state/'spec.json')
    assert '--specs-only' in migrate and migrate[migrate.index('--hot') + 1] == ''
    assert member_home[member_home.index('--member-env') + 1] == str(staged/'.env')
    assert member_home[member_home.index('--border-config') + 1] == str(host.openclaw/'openclaw.json')
    assert all(c['runtime'] == 'openclaw' and c['home'] == str(host.openclaw) for c in calls)
    [(path, body)] = host.daemon.posts
    assert path == '/n2n/members/add' and body['on_demand'] is True and body['profile'] == 'custom'
    assert body['launch_cmd'] == f'bash {staged/"run.sh"}'
    assert body['specialty'][0] == 'alert-triage'
    env = staged/'.env'
    assert env.read_text().endswith(f'N2N_ENROLLMENT_TOKEN={TOKEN}\n')
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    record = json.loads((host.state/'investigator.json').read_text())
    assert TOKEN not in json.dumps(record) and record['member_added'] and record['token_written']
    assert json.loads((host.state/'spec.json').read_text())['servers'] == SHIPPED['servers']
    # A second apply changes nothing.
    code, out = host.main('apply', capsys=capsys)
    assert code == 0 and json.loads(out.out)['status'] == 'already applied' and len(host.daemon.posts) == 1


def test_token_is_not_written_into_a_widened_env_file(monkeypatch, tmp_path):
    host = Host(monkeypatch, tmp_path)
    env = tmp_path/'shared.env'
    env.write_text('')
    env.chmod(0o644)
    with pytest.raises(RuntimeError, match='not a private file'):
        investigator.secure_append(env, 'N2N_ENROLLMENT_TOKEN=x')
    assert env.read_text() == ''
    link = tmp_path/'link.env'
    link.symlink_to(host.openclaw/'openclaw.json')
    with pytest.raises(OSError):
        investigator.secure_append(link, 'N2N_ENROLLMENT_TOKEN=x')


def test_status_and_remove_undo_only_what_apply_recorded(monkeypatch, tmp_path, capsys):
    host = Host(monkeypatch, tmp_path)
    assert host.main('apply', capsys=capsys)[0] == 0
    code, out = host.main('status', capsys=capsys)
    assert json.loads(out.out)['member'] == {'state': 'provisioned', 'profile': 'custom',
                                             'specialty_count': None, 'node_type': None}
    code, out = host.main('remove', capsys=capsys)
    result = json.loads(out.out)
    assert code == 0 and result['kept'] == []
    assert host.daemon.posts[-1] == ('/n2n/members/remove', {'member_id': 'r1/alert-investigator'})
    assert not host.member_home.exists() and not host.state.exists()
    assert host.openclaw.joinpath('openclaw.json').exists() and host.netclaw.exists()
    assert json.loads(host.main('remove', capsys=capsys)[1].out)['status'] == 'nothing to remove'


def test_remove_keeps_a_home_whose_recorded_path_was_changed(monkeypatch, tmp_path, capsys):
    host = Host(monkeypatch, tmp_path)
    assert host.main('apply', capsys=capsys)[0] == 0
    record = json.loads((host.state/'investigator.json').read_text())
    elsewhere = tmp_path/'home/precious'
    elsewhere.mkdir()
    record['home'] = str(elsewhere)
    (host.state/'investigator.json').write_text(json.dumps(record))
    result = json.loads(host.main('remove', capsys=capsys)[1].out)
    assert elsewhere.exists() and any('precious' in k for k in result['kept'])


def test_failed_apply_is_reported_and_remove_cleans_up(monkeypatch, tmp_path, capsys):
    host = Host(monkeypatch, tmp_path)
    monkeypatch.setenv('FAIL_MEMBER_HOME', '1')
    code, out = host.main('apply', capsys=capsys)
    assert code == 2 and 'in2n-member-home.py failed (3)' in out.err and host.daemon.posts == []
    record = json.loads((host.state/'investigator.json').read_text())
    assert record['home_created'] and record['staging_created'] and 'member_added' not in record
    monkeypatch.delenv('FAIL_MEMBER_HOME')
    # Re-running plan accepts the partial home it created itself.
    assert host.main('plan', capsys=capsys)[0] == 0
    result = json.loads(host.main('remove', capsys=capsys)[1].out)
    assert not host.member_home.exists() and not host.state.exists() and result['kept'] == []


def test_a_registered_member_without_its_token_must_be_removed_first(monkeypatch, tmp_path, capsys):
    host = Host(monkeypatch, tmp_path)
    monkeypatch.setattr(investigator, 'secure_append', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    assert host.main('apply', capsys=capsys)[0] == 2
    code, out = host.main('plan', capsys=capsys)
    assert code == 1 and any('run remove, then apply again' in c for c in json.loads(out.out)['conflicts'])
    host.main('remove', capsys=capsys)
    assert 'r1/alert-investigator' not in host.daemon.members

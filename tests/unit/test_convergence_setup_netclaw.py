"""NetClaw wiring: restricted agent, no secrets in config, refuses to take over foreign state."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('convergence_netclaw', ROOT/'setup/netclaw.py')
netclaw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(netclaw)
SKILL = netclaw.SKILL.read_text()


def fleet(**changes):
    config = {'agents': {'ownership': 'explicit', 'defaults': {},
                         'entries': {'main': {'workspace': '/ws'}, 'border': {}}},
              'gateway': {'port': 18789}, 'mcp': {'servers': {}}}
    config.update(changes)
    return config


def opts(**changes):
    return {'agent_id': 'alert', 'api_url': 'http://127.0.0.1:3000', 'site': 'lab',
            'case_token': 'k'*64, 'allow_tools': [], 'state': {}, **changes}


def test_plan_wires_restricted_agent_hook_and_mcp_without_secrets(tmp_path):
    result = netclaw.plan(fleet(), {}, opts(allow_tools=['prometheus-mcp__*']), tmp_path, SKILL)
    assert result['conflicts'] == []
    patch = result['patch']
    agent = patch['agents']['entries']['alert']
    assert agent['skills'] == ['alert-triage']
    assert agent['tools']['allow'] == ['read', 'convergence-case-mcp__*', 'prometheus-mcp__*']
    assert {'exec', 'write', 'message', 'gateway'} <= set(agent['tools']['deny'])
    hooks = patch['hooks']
    assert hooks['enabled'] and hooks['allowedAgentIds'] == ['alert']
    assert hooks['token'] == '${CONVERGENCE_HOOK_TOKEN}'
    (mapping,) = hooks['mappings']
    assert mapping['match'] == {'path': 'alert'} and mapping['agentId'] == 'alert'
    assert mapping['deliver'] is False and 'sessionKey' not in mapping
    server = patch['mcp']['servers']['convergence-case-mcp']
    assert server['env']['CONVERGENCE_API_TOKEN'] == '${CONVERGENCE_CASE_TOKEN}'
    assert server['env']['GUARDIAN_DEFAULT_SITE'] == 'lab'
    text = json.dumps(patch)
    assert 'k'*64 not in text and result['env'][netclaw.HOOK_ENV] not in text
    assert result['hook_token'] == result['env'][netclaw.HOOK_ENV]
    assert result['skill_target'] == '/ws/skills/alert-triage/SKILL.md'
    assert result['gateway_url'] == 'http://127.0.0.1:18789'


def test_plan_reuses_existing_hooks_and_keeps_other_mappings(tmp_path):
    other = {'id': 'gmail', 'match': {'path': 'gmail'}, 'action': 'agent'}
    config = fleet(hooks={'enabled': True, 'token': '${MY_HOOKS}', 'allowedAgentIds': ['main'],
                          'mappings': [other]})
    result = netclaw.plan(config, {'MY_HOOKS': 'h'*40}, opts(), tmp_path, SKILL)
    assert result['conflicts'] == []
    hooks = result['patch']['hooks']
    assert 'token' not in hooks and result['hook_token'] == 'h'*40
    assert netclaw.HOOK_ENV not in result['env']
    assert hooks['allowedAgentIds'] == ['main', 'alert']
    assert hooks['mappings'][0] == other and len(hooks['mappings']) == 2


def test_plan_refuses_foreign_state(tmp_path):
    foreign = fleet(hooks={'enabled': True, 'token': 't'*40, 'path': '/in',
                           'allowedSessionKeyPrefixes': ['alerts:'],
                           'mappings': [{'match': {'path': 'alert'}, 'action': 'agent'}]},
                    mcp={'servers': {'convergence-case-mcp': {}}})
    foreign['agents']['entries']['alert'] = {}
    conflicts = ' | '.join(netclaw.plan(foreign, {}, opts(allow_tools=['exec', '*']), tmp_path, SKILL)['conflicts'])
    for needle in ('already exists and was not created', 'hooks.path', 'generated hook: keys',
                   'mapping for path "alert"', 'already configured', '--allow-tool exec', '--allow-tool *'):
        assert needle in conflicts


def test_plan_needs_consent_to_turn_a_single_agent_into_a_fleet(tmp_path):
    single = {'agents': {'entries': {'main': {}}}}
    assert 'single agent' in netclaw.plan(single, {}, opts(), tmp_path, SKILL)['conflicts'][0]
    assert netclaw.plan(single, {}, opts(allow_new_fleet=True), tmp_path, SKILL)['conflicts'] == []


def test_plan_protects_a_changed_skill(tmp_path):
    target = tmp_path/'ws/skills/alert-triage/SKILL.md'
    target.parent.mkdir(parents=True)
    target.write_text('local edits')
    config = fleet()
    config['agents']['entries']['main']['workspace'] = str(tmp_path/'ws')
    assert 'different content' in netclaw.plan(config, {}, opts(), tmp_path, SKILL)['conflicts'][0]
    assert netclaw.plan(config, {}, opts(replace_skill=True), tmp_path, SKILL)['conflicts'] == []


def test_write_env_updates_in_place_and_is_private(tmp_path):
    env = tmp_path/'.env'
    env.write_text('# keep\nA=1\nCONVERGENCE_CASE_TOKEN=old\nB=2\n')
    netclaw.write_env(env, {'CONVERGENCE_CASE_TOKEN': 'new', 'C': '3'})
    assert env.read_text() == '# keep\nA=1\nCONVERGENCE_CASE_TOKEN=new\nB=2\nC=3\n'
    assert env.stat().st_mode & 0o777 == 0o600
    netclaw.write_env(env, {'CONVERGENCE_CASE_TOKEN': None})
    assert netclaw.read_env(env) == {'A': '1', 'B': '2', 'C': '3'}


def test_case_key_file_must_be_private(tmp_path):
    key = tmp_path/'key'
    key.write_text('a'*64+'\n')
    key.chmod(0o644)
    try:
        netclaw.private_token(key)
        raise AssertionError('readable key accepted')
    except ValueError as exc:
        assert 'chmod 600' in str(exc)
    key.chmod(0o600)
    assert netclaw.private_token(key) == 'a'*64


def test_skill_is_text_only_and_names_the_case_tools():
    assert not [p for p in netclaw.SKILL.parent.iterdir() if p.name != 'SKILL.md']
    for tool in ('case_get', 'case_update', 'case_jev_review', 'flow_evidence'):
        assert tool in SKILL


def test_remove_undoes_only_recorded_changes(tmp_path, monkeypatch):
    home = tmp_path/'oc'
    ws = tmp_path/'ws'
    skill = ws/'skills/alert-triage/SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_text(SKILL)
    home.mkdir()
    other = {'id': 'other', 'match': {'path': 'other'}, 'action': 'agent'}
    mine = {'id': netclaw.MAPPING_ID, 'match': {'path': 'alert'}, 'action': 'agent', 'agentId': 'alert'}
    config = fleet(hooks={'enabled': True, 'token': '${CONVERGENCE_HOOK_TOKEN}',
                          'allowedAgentIds': ['alert', 'main'], 'mappings': [other, mine]})
    config['agents']['entries'].update({'main': {'workspace': str(ws)}, 'alert': {'workspace': str(ws)}})
    (home/'openclaw.json').write_text(json.dumps(config))
    (home/'.env').write_text('KEEP=1\nCONVERGENCE_CASE_TOKEN=a\nCONVERGENCE_HOOK_TOKEN=b\n')
    (home/netclaw.STATE).write_text(json.dumps({
        'agent_id': 'alert', 'agent_created': True, 'hooks_enabled_by_us': True,
        'skill_target': str(skill), 'skill_sha256': netclaw.digest(skill.read_bytes()),
        'env_keys': ['CONVERGENCE_CASE_TOKEN', 'CONVERGENCE_HOOK_TOKEN']}))
    calls = []
    monkeypatch.setattr(netclaw, 'openclaw_patch', lambda cli, patch: calls.append(('patch', patch)))
    monkeypatch.setattr(netclaw, 'run', lambda cmd: calls.append(('run', cmd[1:4])))
    args = netclaw.argparse.Namespace(openclaw='openclaw')
    assert netclaw.remove(args, home) == 0
    patch = calls[0][1]
    assert patch['mcp'] == {'servers': {'convergence-case-mcp': None}}
    assert patch['hooks'] == {'mappings': [other], 'allowedAgentIds': ['main']}
    assert calls[1] == ('run', ['agents', 'delete', 'alert'])
    assert not skill.exists() and not (home/netclaw.STATE).exists()
    # Another mapping still uses hooks, so the hook token stays.
    assert netclaw.read_env(home/'.env') == {'KEEP': '1', 'CONVERGENCE_HOOK_TOKEN': 'b'}


ISP = netclaw.WORKSPACE_SKILLS['convergence-isp-report']


def test_isp_report_skill_is_text_only_and_names_its_case_tools():
    assert [p.name for p in ISP.parent.iterdir()] == ['SKILL.md']
    text = ISP.read_text()
    assert text.startswith('---\nname: convergence-isp-report\n')
    for tool in ('case_get', 'isp_provider_catalog', 'case_isp_report'):
        assert tool in text
    server = (ROOT/'mcp-servers/convergence-case-mcp/server.py').read_text()
    assert 'async def isp_provider_catalog(' in server and 'async def case_isp_report(' in server


def test_plan_installs_the_isp_report_skill_for_the_operator(tmp_path):
    config = fleet()
    config['agents']['entries']['main']['workspace'] = str(tmp_path/'ws')
    result = netclaw.plan(config, {}, opts(), tmp_path, SKILL)
    assert result['conflicts'] == []
    target = tmp_path/'ws/skills/convergence-isp-report/SKILL.md'
    assert result['skills'] == {'convergence-isp-report': {'target': str(target), 'created': True}}
    # Only the alert agent has a skills allowlist; it does not get the ISP report skill.
    assert result['patch']['agents']['entries']['alert']['skills'] == ['alert-triage']


def test_plan_adopts_an_identical_isp_report_skill_and_protects_a_changed_one(tmp_path):
    config = fleet()
    config['agents']['entries']['main']['workspace'] = str(tmp_path/'ws')
    target = tmp_path/'ws/skills/convergence-isp-report/SKILL.md'
    target.parent.mkdir(parents=True)
    target.write_bytes(ISP.read_bytes())
    result = netclaw.plan(config, {}, opts(), tmp_path, SKILL)
    assert result['conflicts'] == [] and result['skills']['convergence-isp-report']['created'] is False
    assert any('adopted' in a for a in result['actions'])
    target.write_text('local edits')
    assert 'different content' in netclaw.plan(config, {}, opts(), tmp_path, SKILL)['conflicts'][0]
    assert netclaw.plan(config, {}, opts(replace_skill=True), tmp_path, SKILL)['conflicts'] == []
    # A skill this tool installed and recorded may be replaced without --replace-skill.
    state = {'skills': {'convergence-isp-report': {'target': str(target), 'created': True,
                                                   'sha256': netclaw.digest(target.read_bytes())}}}
    assert netclaw.plan(config, {}, opts(state=state), tmp_path, SKILL)['conflicts'] == []


def test_remove_deletes_created_skills_only(tmp_path, monkeypatch):
    home, ws = tmp_path/'oc', tmp_path/'ws'
    home.mkdir()
    created = ws/'skills/convergence-isp-report/SKILL.md'
    adopted = ws/'skills/adopted/SKILL.md'
    changed = ws/'skills/changed/SKILL.md'
    for path in (created, adopted, changed):
        path.parent.mkdir(parents=True)
        path.write_text('skill')
    sha = netclaw.digest(b'skill')
    config = fleet(hooks={'enabled': True, 'token': '${CONVERGENCE_HOOK_TOKEN}', 'mappings': []})
    (home/'openclaw.json').write_text(json.dumps(config))
    (home/'.env').write_text('')
    (home/netclaw.STATE).write_text(json.dumps({
        'agent_id': 'alert', 'skill_target': str(ws/'skills/alert-triage/SKILL.md'), 'skill_sha256': '0',
        'skills': {'convergence-isp-report': {'target': str(created), 'created': True, 'sha256': sha},
                   'adopted': {'target': str(adopted), 'created': False, 'sha256': sha},
                   'changed': {'target': str(changed), 'created': True, 'sha256': '0'}}}))
    monkeypatch.setattr(netclaw, 'openclaw_patch', lambda cli, patch: None)
    printed = []
    monkeypatch.setattr('builtins.print', lambda text, **kw: printed.append(text))
    assert netclaw.remove(netclaw.argparse.Namespace(openclaw='openclaw'), home) == 0
    assert not created.exists() and not created.parent.exists()
    assert adopted.exists() and changed.exists()
    kept = ' | '.join(json.loads(printed[-1])['kept'])
    assert 'adopted, not created' in kept and 'changed since install' in kept


def test_openclaw_cli_acts_on_the_given_openclaw_dir(tmp_path, monkeypatch):
    home = tmp_path/'oc'
    home.mkdir()
    (home/'openclaw.json').write_text('{}')
    monkeypatch.setenv('OPENCLAW_CONFIG_PATH', '/somewhere/else/openclaw.json')
    monkeypatch.setenv('OPENCLAW_STATE_DIR', '/somewhere/else')
    monkeypatch.setattr('builtins.print', lambda *a, **k: None)
    assert netclaw.main(['status', '--openclaw-dir', str(home)]) == 1
    assert netclaw.os.environ['OPENCLAW_CONFIG_PATH'] == str(home/'openclaw.json')
    assert netclaw.os.environ['OPENCLAW_STATE_DIR'] == str(home)


def merge(base, patch):
    """RFC 7396, as `openclaw config patch` applies it."""
    if not isinstance(patch, dict):
        return patch
    out = dict(base) if isinstance(base, dict) else {}
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = merge(out.get(key), value)
    return out


LEGACY_AGENT = {'name': 'Alert Orchestrator', 'workspace': '/ws', 'skills': ['alert-triage', 'home'],
                'model': {'primary': 'openai/m1', 'fallbacks': ['openai/m1']},
                'tools': {'fs': {'workspaceOnly': True}, 'allow': ['read', 'n2n-mcp__n2n_route']}}
LEGACY_MAPPING = {'match': {'path': 'alert'}, 'action': 'agent', 'name': 'NetClaw Alert Triage',
                  'sessionKey': 'hook:alert:{{alerts[0].fingerprint}}', 'messageTemplate': 'old',
                  'model': 'openai/m1', 'agentId': 'alert', 'deliver': False}
RECONCILE = {'match': {'path': 'reconcile'}, 'action': 'agent', 'messageTemplate': 'r'}
LEGACY_MCP = {'command': '/py', 'args': ['/old/server.py'],
              'env': {'CONVERGENCE_API_URL': '${HOME_API_URL}', 'CONVERGENCE_API_TOKEN': '${HOME_API_TOKEN}',
                      'GUARDIAN_DEFAULT_SITE': 'home'}}


def legacy(tmp_path):
    config = fleet(hooks={'enabled': True, 'token': '${OPENCLAW_HOOKS_TOKEN}', 'allowedAgentIds': ['main', 'alert'],
                          'mappings': [LEGACY_MAPPING, RECONCILE]},
                   mcp={'servers': {'convergence-case-mcp': LEGACY_MCP}})
    config['agents']['entries'] = {'main': {'workspace': str(tmp_path/'ws')}, 'alert': LEGACY_AGENT}
    (tmp_path/'ws/skills/home').mkdir(parents=True)
    (tmp_path/'ws/skills/home/SKILL.md').write_text('home skill')
    return config


def test_existing_alert_setup_needs_replace_existing(tmp_path):
    config = legacy(tmp_path)
    conflicts = ' | '.join(netclaw.plan(config, {'OPENCLAW_HOOKS_TOKEN': 't'}, opts(), tmp_path, SKILL)['conflicts'])
    assert conflicts.count('--replace-existing') == 3


def test_replace_existing_takes_over_exactly(tmp_path):
    config = legacy(tmp_path)
    result = netclaw.plan(config, {'OPENCLAW_HOOKS_TOKEN': 't'},
                          opts(replace_existing=True, model='openai/m2', agent_skills=['home'],
                               allow_tools=['n2n-mcp__n2n_route']), tmp_path, SKILL)
    assert result['conflicts'] == []
    after = merge(config, result['patch'])
    agent = after['agents']['entries']['alert']
    assert agent == {'workspace': '/ws', 'name': 'Convergence alert triage', 'skills': ['alert-triage', 'home'],
                     'model': {'primary': 'openai/m2'},
                     'tools': {'allow': ['read', 'convergence-case-mcp__*', 'n2n-mcp__n2n_route'],
                               'deny': netclaw.DENY_TOOLS, 'fs': {'workspaceOnly': True}}}
    mappings = after['hooks']['mappings']
    assert [m.get('id') for m in mappings] == [netclaw.MAPPING_ID, None] and mappings[1] == RECONCILE
    assert mappings[0]['model'] == 'openai/m2' and 'sessionKey' not in mappings[0]
    assert after['hooks']['allowedAgentIds'] == ['main', 'alert'] and result['allowed_added'] is False
    server = after['mcp']['servers']['convergence-case-mcp']
    assert server['env']['CONVERGENCE_API_TOKEN'] == '${CONVERGENCE_CASE_TOKEN}' and server['args'][0] == '-u'
    assert result['previous'] == {'agent': LEGACY_AGENT, 'mappings': [{'index': 0, 'mapping': LEGACY_MAPPING}],
                                  'mcp': LEGACY_MCP}


def test_plan_finds_workspace_skills_below_configured_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    config = fleet()
    config['agents']['entries']['main']['workspace'] = '${HOME}/ws'
    target = tmp_path/'ws/skills/home/SKILL.md'
    target.parent.mkdir(parents=True)
    target.write_text('home context')
    result = netclaw.plan(config, {}, opts(agent_skills=['home']), tmp_path, SKILL)
    assert result['conflicts'] == []
    assert Path(result['skill_target']) == tmp_path/'ws/skills/alert-triage/SKILL.md'


def test_plan_checks_model_and_agent_skills(tmp_path):
    config = fleet()
    config['agents']['entries']['main']['workspace'] = str(tmp_path/'ws')
    conflicts = ' | '.join(netclaw.plan(config, {}, opts(model='no-slash', agent_skills=['missing', 'Bad!']),
                                        tmp_path, SKILL)['conflicts'])
    assert '--model no-slash' in conflicts and 'no such skill' in conflicts and 'Bad!: not a skill name' in conflicts


def test_new_agent_reads_only_its_workspace(tmp_path):
    entry = netclaw.plan(fleet(), {}, opts(), tmp_path, SKILL)['patch']['agents']['entries']['alert']
    assert entry['tools']['fs'] == {'workspaceOnly': True} and 'model' not in entry


def test_remove_restores_replaced_entries_and_skill(tmp_path, monkeypatch):
    home = tmp_path/'oc'
    home.mkdir()
    config = legacy(tmp_path)
    env = {'OPENCLAW_HOOKS_TOKEN': 't'}
    result = netclaw.plan(config, env, opts(replace_existing=True, agent_skills=['home']), tmp_path, SKILL)
    applied = merge(config, result['patch'])
    (home/'openclaw.json').write_text(json.dumps(applied))
    (home/'.env').write_text('OPENCLAW_HOOKS_TOKEN=t\nCONVERGENCE_CASE_TOKEN=k\n')
    target = tmp_path/'ws/skills/alert-triage/SKILL.md'
    target.parent.mkdir(parents=True)
    target.write_text(SKILL)
    old_skill = tmp_path/'backup-alert-triage.md'
    old_skill.write_text('the old alert-triage')
    (home/netclaw.STATE).write_text(json.dumps({
        'agent_id': 'alert', 'agent_created': False, 'mcp_created': False, 'allowed_agent_added': False,
        'hooks_enabled_by_us': False, 'previous': result['previous'], 'skill_target': str(target),
        'skill_sha256': netclaw.digest(target.read_bytes()), 'skill_previous': str(old_skill),
        'env_keys': ['CONVERGENCE_CASE_TOKEN']}))
    patches, runs = [], []
    monkeypatch.setattr(netclaw, 'openclaw_patch', lambda cli, patch: patches.append(patch))
    monkeypatch.setattr(netclaw, 'run', lambda cmd: runs.append(cmd))
    monkeypatch.setattr('builtins.print', lambda *a, **k: None)
    assert netclaw.remove(netclaw.argparse.Namespace(openclaw='openclaw'), home) == 0
    assert merge(applied, patches[0]) == config
    assert runs == []  # an adopted agent is restored, never deleted
    assert target.read_text() == 'the old alert-triage'
    assert netclaw.read_env(home/'.env') == {'OPENCLAW_HOOKS_TOKEN': 't'}


def test_alert_hook_selects_dispatcher_or_local_investigation(tmp_path):
    for tools, dispatch in (([], False), (['n2n-mcp__n2n_member_health'], False),
                            (['n2n-mcp__n2n_route'], True), (['n2n-mcp__*'], True)):
        result = netclaw.plan(fleet(), {}, opts(allow_tools=tools), tmp_path, SKILL)
        assert not result['conflicts']
        message = result['patch']['hooks']['mappings'][0]['messageTemplate']
        assert ('First use n2n_route' in message) == dispatch
        assert ('investigate with your read-only tools' in message) != dispatch
        assert message.endswith('{{alerts[0].annotations.investigation_prompt}}')


def test_dispatcher_uses_granted_task_tracking_tools(tmp_path):
    for tracking, expected in (([], 'status cannot be confirmed'),
                               (['n2n_task_status'], 'Poll n2n_task_status'),
                               (['n2n_task_wait'], 'resume its run ID')):
        tools = ['n2n-mcp__' + name for name in ['n2n_route', *tracking]]
        result = netclaw.plan(fleet(), {}, opts(allow_tools=tools), tmp_path, SKILL)
        message = result['patch']['hooks']['mappings'][0]['messageTemplate']
        assert expected.lower() in message.lower()

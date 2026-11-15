"""Policy export preserves the managed agent boundary without copying secrets."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('reviewer_gateway_setup', ROOT/'setup/reviewer_gateway.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
helper = setup.module('reviewer_gateway_test_helper', ROOT/'setup/netclaw.py')


def managed():
    extra = ['n2n-mcp__n2n_route', 'n2n-mcp__n2n_task_wait']
    entry = {'skills':['alert-triage'], 'model':{'primary':'fixture/model'},
             'tools':{'allow':helper.BASE_TOOLS+extra, 'deny':helper.DENY_TOOLS,
                      'fs':{'workspaceOnly':True}}}
    mapping = {'id':helper.MAPPING_ID, 'match':{'path':'alert'}, 'action':'agent', 'agentId':'alert',
               'name':'Convergence alert triage', 'wakeMode':'now', 'deliver':False,
               'timeoutSeconds':900, 'messageTemplate':helper.alert_message(extra), 'model':'fixture/model'}
    config = {'hooks':{'enabled':True, 'path':'/hooks', 'allowedAgentIds':['alert'],
                       'allowRequestSessionKey':True, 'allowedSessionKeyPrefixes':['hook:'],
                       'mappings':[mapping], 'token':'fixture-secret-not-for-export'},
              'agents':{'entries':{'alert':entry}}}
    return config, {'agent_id':'alert', 'agent_created':True}


def test_export_check_and_file_drift(tmp_path):
    config, state = managed()
    (tmp_path/'openclaw.json').write_text(json.dumps(config))
    (tmp_path/'convergence-netclaw.json').write_text(json.dumps(state))
    target = tmp_path/'policy.json'
    args = SimpleNamespace(command='export', openclaw_dir=str(tmp_path), policy_file=str(target), replace=False)
    result = setup.execute(args)
    assert target.stat().st_mode & 0o777 == 0o600
    assert result['sha256'] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert 'fixture-secret-not-for-export' not in target.read_text()
    assert set(json.loads(target.read_text())) == {'version','agent_id','model','timeout_seconds','message_template'}
    before = target.read_bytes()
    args.command = 'check'
    assert setup.execute(args)['status'] == 'matches'
    assert target.read_bytes() == before
    config['hooks']['mappings'][0]['timeoutSeconds'] = 600
    (tmp_path/'openclaw.json').write_text(json.dumps(config))
    with pytest.raises(ValueError, match='differs'):
        setup.execute(args)
    args.command = 'export'
    with pytest.raises(ValueError, match='replace'):
        setup.execute(args)
    assert target.read_bytes() == before
    args.replace = True
    assert setup.execute(args)['sha256'] != result['sha256']


@pytest.mark.parametrize('change', [
    'foreign-state', 'duplicate-mapping', 'transform', 'delivery', 'agent', 'model',
    'template', 'unsafe-tools', 'missing-deny', 'workspace', 'sessions', 'prefix', 'agent-allowlist',
])
def test_rejects_unsupported_or_drifted_managed_configuration(change):
    config, state = copy.deepcopy(managed())
    hooks = config['hooks']
    mapping = hooks['mappings'][0]
    entry = config['agents']['entries']['alert']
    if change == 'foreign-state': state = {}
    elif change == 'duplicate-mapping': hooks['mappings'].append(dict(mapping))
    elif change == 'transform': mapping['transform'] = {'module':'arbitrary.js'}
    elif change == 'delivery': mapping['deliver'] = True
    elif change == 'agent': mapping['agentId'] = 'main'
    elif change == 'model': mapping['model'] = 'fixture/different'
    elif change == 'template': mapping['messageTemplate'] += 'changed'
    elif change == 'unsafe-tools': entry['tools']['allow'].append('exec')
    elif change == 'missing-deny': entry['tools']['deny'].remove('exec')
    elif change == 'workspace': entry['tools']['fs']['workspaceOnly'] = False
    elif change == 'sessions': hooks['allowRequestSessionKey'] = False
    elif change == 'prefix': hooks['allowedSessionKeyPrefixes'] = ['hook:other:']
    elif change == 'agent-allowlist': hooks['allowedAgentIds'] = ['main']
    with pytest.raises(ValueError):
        setup.managed_policy(config, state)


def test_policy_supports_taken_over_agent_and_preserves_exact_wrapper():
    config, _state = managed()
    policy = setup.managed_policy(config, {'agent_id':'alert', 'previous':{'agent':{}}})
    assert policy['message_template'] == config['hooks']['mappings'][0]['messageTemplate']
    assert policy['agent_id'] == 'alert' and policy['timeout_seconds'] == 900


def test_export_refuses_symlink_without_touching_target(tmp_path):
    config, state = managed()
    (tmp_path/'openclaw.json').write_text(json.dumps(config))
    (tmp_path/'convergence-netclaw.json').write_text(json.dumps(state))
    original = tmp_path/'original'
    original.write_text('keep')
    link = tmp_path/'policy'
    link.symlink_to(original)
    with pytest.raises(ValueError, match='symlink'):
        setup.execute(SimpleNamespace(command='export', openclaw_dir=str(tmp_path), policy_file=str(link), replace=True))
    assert original.read_text() == 'keep'

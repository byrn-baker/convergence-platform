#!/usr/bin/env python3
"""Export or check the restricted Gateway policy for reviewer investigations.

  python3 setup/reviewer_gateway.py export --policy-file /private/reviewer-gateway.json
  python3 setup/reviewer_gateway.py check --policy-file /private/reviewer-gateway.json

Reads the helper-managed alert mapping; never copies credentials into the policy.
Export prints its SHA-256 for REVIEWER_GATEWAY_POLICY_SHA256. Set the absolute
policy path as REVIEWER_GATEWAY_POLICY_FILE. Check again before rollout or after
Gateway configuration changes. Neither command changes Gateway configuration.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def managed_policy(config, state):
    helper = module('convergence_netclaw_setup', ROOT/'setup/netclaw.py')
    adapter = module('convergence_gateway_policy', ROOT/'services/alert-receiver/gateway_investigations.py')
    agent = state.get('agent_id')
    if not agent or not (state.get('agent_created') or 'agent' in (state.get('previous') or {})):
        raise ValueError('No helper-managed alert agent is recorded')
    hooks = config.get('hooks') or {}
    if hooks.get('enabled') is not True or hooks.get('path', '/hooks') != '/hooks':
        raise ValueError('Expected enabled hooks at /hooks')
    if agent not in (hooks.get('allowedAgentIds') or []):
        raise ValueError('The alert agent must be explicitly allowed for hooks')
    prefix = 'hook:convergence-reviewer:'
    prefixes = hooks.get('allowedSessionKeyPrefixes') or []
    if hooks.get('allowRequestSessionKey') is not True or not any(
            isinstance(p, str) and p.strip() and prefix.startswith(p.strip().lower()) for p in prefixes):
        raise ValueError('Hooks must permit the deterministic reviewer session prefix')
    mappings = [m for m in hooks.get('mappings') or [] if isinstance(m, dict) and
                (m.get('id') == helper.MAPPING_ID or (m.get('match') or {}).get('path') == 'alert')]
    if len(mappings) != 1:
        raise ValueError('Expected one unambiguous managed alert mapping')
    mapping = mappings[0]
    expected = {'id', 'match', 'action', 'agentId', 'name', 'wakeMode', 'deliver',
                'timeoutSeconds', 'messageTemplate', 'model'}
    if (set(mapping) != expected or mapping['id'] != helper.MAPPING_ID or
            mapping['match'] != {'path': 'alert'} or mapping['action'] != 'agent' or
            mapping['agentId'] != agent or mapping['wakeMode'] != 'now' or mapping['deliver'] is not False):
        raise ValueError('Unsupported alert mapping; use the managed restricted mapping with a pinned model')
    entry = ((config.get('agents') or {}).get('entries') or {}).get(agent) or {}
    tools = entry.get('tools') or {}
    allowed, denied = tools.get('allow'), tools.get('deny')
    if (not isinstance(allowed, list) or not all(isinstance(t, str) for t in allowed) or
            not isinstance(denied, list) or not all(isinstance(t, str) for t in denied) or
            not set(helper.BASE_TOOLS).issubset(allowed) or not set(helper.DENY_TOOLS).issubset(denied) or
            (tools.get('fs') or {}).get('workspaceOnly') is not True or
            'alert-triage' not in (entry.get('skills') or [])):
        raise ValueError('Alert agent restrictions do not match the managed policy')
    extra = [t for t in allowed if t not in helper.BASE_TOOLS]
    if any(not helper.TOOL.fullmatch(t) or t.split('__')[0] in helper.DENY_TOOLS or t in ('*', '**') for t in extra):
        raise ValueError('Alert agent has unsupported tool permissions')
    if (entry.get('model') or {}).get('primary') != mapping['model']:
        raise ValueError('The alert agent and hook must use the same pinned model')
    if mapping['messageTemplate'] != helper.alert_message(extra):
        raise ValueError('Alert prompt wrapper differs from the managed policy')
    policy = {'version': 1, 'agent_id': agent, 'model': mapping['model'],
              'timeout_seconds': mapping['timeoutSeconds'], 'message_template': mapping['messageTemplate']}
    adapter.validate_policy(policy)
    return policy


def selected_policy(home):
    # Do not load .env: neither hook nor operator credentials belong in this export.
    try:
        config = json.loads((home/'openclaw.json').read_text())
        state = json.loads((home/'convergence-netclaw.json').read_text())
        return managed_policy(config, state)
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError):
        raise ValueError('Malformed managed Gateway configuration') from None


def policy_bytes(policy):
    return (json.dumps(policy, sort_keys=True, indent=2)+'\n').encode('utf-8')


def execute(args):
    home = Path(args.openclaw_dir).expanduser().resolve()
    target = Path(args.policy_file).expanduser().absolute()
    policy = selected_policy(home)
    data = policy_bytes(policy)
    adapter = module('convergence_gateway_policy', ROOT/'services/alert-receiver/gateway_investigations.py')
    if args.command == 'check':
        # Compare semantic policy as well as file integrity; a check never rewrites it.
        raw = target.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if adapter.load_policy(str(target), digest) != policy:
            raise ValueError('Exported reviewer policy differs from current managed Gateway configuration')
    else:
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError('Policy must be a regular file, not a symlink')
        if target.exists() and target.read_bytes() != data and not args.replace:
            raise ValueError('Policy already differs; use --replace and update its receiver digest together')
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix='.reviewer-policy-', dir=target.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        digest = hashlib.sha256(data).hexdigest()
        adapter.load_policy(str(target), digest)
    return {'status': 'matches' if args.command == 'check' else 'exported',
            'policy_file': str(target), 'sha256': digest, 'agent_id': policy['agent_id']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('export', 'check'))
    parser.add_argument('--openclaw-dir', default='~/.openclaw')
    parser.add_argument('--policy-file', required=True)
    parser.add_argument('--replace', action='store_true')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(execute(args), indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(f'reviewer gateway: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

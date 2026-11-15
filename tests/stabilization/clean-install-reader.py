#!/usr/bin/env python3
"""Qualify the stock connector against an already isolated Convergence service.

Run with the connector's isolated Python. Installs/registers only beneath --state;
never starts a Gateway, reads a user's runtime environment, or calls a model.
The caller owns lab creation, synthetic records and service lifecycle. This is
component qualification, not a claim of a full NetClaw/OpenClaw installation.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit

UPSTREAM = 'a5e7c3a3e07d8c8c4f2f26d22b4bd854dd1ba42e'


def run(argv, *, cwd, env, log):
    subprocess.run(argv, cwd=cwd, env=env, stdout=log,
                   stderr=subprocess.STDOUT, check=True)


async def qualify(args):
    from fastmcp import Client
    stock = Path(args.stock).resolve()
    state = Path(args.state).resolve()
    if state == Path.home() or stock == state or stock in state.parents:
        raise ValueError('Use a dedicated state directory outside the source checkout')
    origin = urlsplit(args.api)
    if origin.scheme != 'http' or origin.hostname != '127.0.0.1' or origin.path or origin.username:
        raise ValueError('This synthetic fixture accepts only a loopback HTTP API origin')
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=stock, text=True).strip()
    if actual != UPSTREAM:
        raise ValueError('Unexpected stock NetClaw revision')
    subprocess.run(['git', 'diff', '--exit-code', UPSTREAM, '--'], cwd=stock, check=True,
                   stdout=subprocess.DEVNULL)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_home = state / 'home'
    private_home.mkdir(exist_ok=True, mode=0o700)
    runtime = state / 'runtime'
    runtime.mkdir(exist_ok=True, mode=0o700)
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(private_home), 'LANG': 'C.UTF-8',
           'NETCLAW_RUNTIME': 'openclaw', 'OPENCLAW_STATE_DIR': str(runtime),
           'NETCLAW_RUNTIME_ROOT': str(runtime / 'python-runtimes'),
           'NETCLAW_RUNTIME_ENV': str(runtime / '.env'),
           'NETCLAW_INSTALL_COMPONENT': 'convergence-read', 'NETCLAW_DIR': str(stock),
           'NETCLAW_PY': '/usr/bin/python3'}
    envfile = runtime / '.env'
    if not envfile.exists():
        envfile.write_text('UNRELATED_RUNTIME_SETTING=preserved-fixture\n')
        envfile.chmod(0o600)
    installer = ['bash', '-c', 'source "$NETCLAW_DIR/scripts/lib/common.sh"; '
                 'source "$NETCLAW_DIR/scripts/lib/pip-helper.sh"; '
                 'source "$NETCLAW_DIR/scripts/lib/install-steps.sh"; component_install_convergence_read']
    with (state / 'install.log').open('w') as log:
        run(installer, cwd=stock, env=env, log=log)
        before = envfile.read_bytes()
        run(installer, cwd=stock, env=env, log=log)
        assert before == envfile.read_bytes(), 'Repeat install changed runtime settings'
        config = runtime / 'openclaw.json'
        config.write_text(json.dumps({'model': 'fixture/no-provider',
                                      'mcpServers': {'unrelated': {'command': 'fixture-only'}}}))
        config.chmod(0o600)
        token = Path(args.token_file).read_text().strip()
        settings = {'CONVERGENCE_ENABLED': 'true', 'CONVERGENCE_SITE': 'demo',
                    'CONVERGENCE_API_URL': args.api, 'CONVERGENCE_API_TOKEN': token,
                    'CONVERGENCE_ALLOW_LOOPBACK_HTTP': 'true', 'CONVERGENCE_EXPORTERS': '127.0.0.1'}
        envfile.write_text('UNRELATED_RUNTIME_SETTING=preserved-fixture\n' + ''.join(
            key + '=' + json.dumps(value) + '\n' for key, value in settings.items()))
        run(['/usr/bin/python3', str(stock / 'scripts/install-mcp-config.py'), '--repo', str(stock),
             '--runtime-root', env['NETCLAW_RUNTIME_ROOT'], '--components', 'convergence-read',
             '--output', str(runtime / 'registered.json'), '--config', str(config)],
            cwd=stock, env={**env, **settings}, log=log)
    installed = json.loads(config.read_text())
    assert installed['model'] == 'fixture/no-provider'
    assert installed['mcpServers']['unrelated'] == {'command': 'fixture-only'}
    assert token not in config.read_text(), 'Credential embedded in runtime configuration'
    entry = installed['mcpServers']['convergence-read-mcp']

    def connection(overrides=None):
        supplied = {**env, **settings, **(overrides or {})}
        resolved = {key: re.sub(r'\$\{([A-Z_][A-Z0-9_]*)\}',
                               lambda match: supplied.get(match[1], ''), value)
                    for key, value in entry['env'].items()}
        return {'mcpServers': {'convergence': {**entry, 'env': {**supplied, **resolved}}}}

    async with Client(connection()) as mcp:
        tools = await mcp.list_tools()
        assert {tool.name for tool in tools} == {
            'convergence_flow_summary', 'convergence_flow_evidence', 'convergence_vm_security'}
        vm = (await mcp.call_tool('convergence_vm_security', {})).data
        assert vm['ok'] and vm['data']['site'] == 'demo' and vm['data']['synthetic'] is True
        summary = (await mcp.call_tool('convergence_flow_summary', {'range': '15m'})).data
        assert summary['ok'], summary
        data = summary['data']
        assert data['coverage']['mode'] == 'demo' and data['total']['records'] > 0
        evidence = (await mcp.call_tool('convergence_flow_evidence', {
            'address': data['sources'][0]['src_addr'], 'start': data['start'],
            'end': data['end'], 'exporter': '127.0.0.1'})).data
        assert evidence['ok'] and evidence['data']['records'], evidence
        assert evidence['data']['start'] == data['start'] and evidence['data']['end'] == data['end']
    for override, code in [({'CONVERGENCE_API_TOKEN': 'wrong-synthetic-key'}, 'unauthorized'),
                           ({'CONVERGENCE_SITE': 'other'}, 'unauthorized'),
                           ({'CONVERGENCE_ENABLED': 'false'}, 'disabled')]:
        async with Client(connection(override)) as mcp:
            result = (await mcp.call_tool('convergence_flow_summary', {})).data
            assert result['error']['code'] == code, result
    result = {'status': 'PASS', 'stock_revision': actual, 'site': 'demo',
              'flow_records': data['total']['records'], 'evidence_records': len(evidence['data']['records']),
              'checks': ['component install', 'repeat install', 'isolated registration',
                         'three real MCP tools', 'same-window retained evidence', 'synthetic labels',
                         'wrong key denied', 'wrong site denied', 'disabled denied'],
              'not_qualified': ['Gateway/model execution', 'physical capture', 'RAG', 'full installer']}
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('stock', 'state', 'api', 'token-file', 'output'):
        parser.add_argument('--' + name, required=True)
    asyncio.run(qualify(parser.parse_args()))

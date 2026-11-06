#!/usr/bin/env python3
"""Disposable Linux Docker acceptance; captures only a private loopback HTTP fixture."""
import argparse
import asyncio
import http.server
import importlib.util
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'workspace/skills/convergence-setup/scripts'))
import setup
import flows


def execute(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def port(kind=socket.SOCK_STREAM):
    with socket.socket(type=kind) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Traffic(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'convergence isolated loopback qualification\n' * 100
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


async def mcp_check(bundle, mode):
    from fastmcp import Client
    env = setup.env_writer(ROOT).values((bundle/'hud.env').read_text())
    os.environ.update(CONVERGENCE_API_URL=env['CONVERGENCE_API_URL'],
                      CONVERGENCE_API_TOKEN=env['CONVERGENCE_API_TOKEN'],
                      GUARDIAN_DEFAULT_SITE=mode)
    spec = importlib.util.spec_from_file_location('home_case_mcp', ROOT/'mcp-servers/convergence-case-mcp/server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    async with Client(module.mcp) as client:
        result = await client.call_tool('flow_top_talkers', {'time_range':'15m'})
        summary = json.loads(result.data) if isinstance(result.data, str) else result.data
        assert summary['coverage']['mode'] == mode and summary['total']['records'] > 0
        result = await client.call_tool('flow_evidence', {
            'address':summary['sources'][0]['src_addr'], 'start':summary['start'],
            'end':summary['end'], 'exporter':'127.0.0.1'})
        evidence = json.loads(result.data) if isinstance(result.data, str) else result.data
        assert evidence['records'] and evidence['coverage']['mode'] == mode
        assert evidence['start'] == summary['start'] and evidence['end'] == summary['end']


def reject_foreign_bundle(bundle, options):
    """A second private bundle must never adopt this running project's resources."""
    other = bundle.parent/'conflicting-bundle'
    changed = {**options,'flows':'host' if options['flows']=='demo' else 'demo','site':'different'}
    setup.prepare(ROOT, other, changed)
    actions = [lambda:setup.run_compose(ROOT,other,'start')]
    if changed['flows']=='demo':
        actions.append(lambda:flows.demo(ROOT,other))
    else:
        actions.append(lambda:flows.collector_ready(ROOT,other,changed))
    for action in actions:
        try:
            action()
        except ValueError as error:
            assert 'another bundle' in str(error), str(error)
        else:
            raise AssertionError('A foreign bundle was allowed to reuse the test project')


def qualify(mode, evidence_dir):
    project = 'convergence-home-test-' + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix='convergence home ') as parent:
        bundle = Path(parent)/'bundle'
        options = {'site':mode, 'project':project, 'edge_url':'', 'grafana':True,
                   'flows':mode, 'flow_port':port(socket.SOCK_DGRAM)}
        for name in ('api','prometheus','alertmanager','blackbox','receiver','grafana'):
            options[name+'_port'] = port()
        setup.prepare(ROOT, bundle, options)
        before = (bundle/'compose.env').read_bytes()
        assert setup.prepare(ROOT, bundle, options)['status'] == 'preserved'
        assert (bundle/'compose.env').read_bytes() == before
        command = flows.compose(ROOT, bundle, {**options,'repo':str(ROOT)})
        environment = setup.compose_environment(ROOT)
        exporter = None
        server = None
        log = (evidence_dir/(mode+'-exporter.log')).open('w')
        try:
            setup.run_compose(ROOT, bundle, 'validate')
            setup.run_compose(ROOT, bundle, 'start')
            reject_foreign_bundle(bundle, options)
            if mode == 'demo':
                result = flows.demo(ROOT, bundle)
                assert result['passed'], result
                assert any(r['src_addr']=='192.0.2.10' for r in result['evidence']['records'])
            else:
                server = http.server.ThreadingHTTPServer(('127.0.0.1',0), Traffic)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                fixture_port = server.server_address[1]
                flows.collector_ready(ROOT, bundle, options)
                capture = flows.host_command(ROOT, bundle, 'lo', f'tcp port {fixture_port}')['command']
                index = capture.index('run')+1
                capture[index:index] = ['--name',project+'-exporter']
                exporter = subprocess.Popen(capture, env=environment, stdout=log, stderr=log)
                # Compose may still be building/pulling after Popen returns. Start
                # fixture traffic only when softflowd's capture/control loop exists.
                deadline = time.monotonic()+120
                while True:
                    assert exporter.poll() is None, 'Exporter stopped before capture readiness'
                    ready = subprocess.run(['docker','exec',project+'-exporter','softflowctl',
                        '-c','/run/convergence.ctl','statistics'],capture_output=True,text=True,timeout=5)
                    if ready.returncode == 0:
                        break
                    assert time.monotonic()<deadline, 'Exporter control socket did not become ready'
                    time.sleep(1)
                opener = build_opener(ProxyHandler({}))
                for _ in range(35):
                    assert exporter.poll() is None, 'Exporter stopped; inspect private test log'
                    with opener.open(f'http://127.0.0.1:{fixture_port}/',timeout=3) as response:
                        assert response.status == 200
                        response.read()
                    time.sleep(1)
                statistics = subprocess.check_output(['docker','exec',project+'-exporter',
                    'softflowctl','-c','/run/convergence.ctl','statistics'],text=True,timeout=5)
                print('Isolated loopback capture statistics:\n'+statistics,flush=True)
                processed = re.search(r'Packets processed: (\d+)', statistics)
                assert processed and int(processed.group(1)) > 0, 'Exporter must process actual fixture packets'
                result = flows.check(ROOT, bundle)
                assert result['passed'], result
                assert all(r['src_addr']=='127.0.0.1' and r['dst_addr']=='127.0.0.1'
                           for r in result['evidence']['records'])
            asyncio.run(mcp_check(bundle, mode))
            execute(['node', str(ROOT/'ui/netclaw-visual/integration/convergence-home.mjs'), str(bundle)])
            ids = subprocess.check_output(command+['ps','-q'], env=environment, text=True).split()
            stats = subprocess.check_output(['docker','stats','--no-stream','--format','{{json .}}',*ids], text=True)
            volumes = subprocess.check_output(['docker','volume','ls','--filter',
                'label=com.docker.compose.project='+project,'--format','{{.Name}}'],text=True).split()
            flow_volumes = [v for v in volumes if 'convergence-flow-' in v]
            mounts = [arg for index,volume in enumerate(flow_volumes)
                      for arg in ['--mount',f'type=volume,source={volume},target=/data/{index},readonly']]
            disk = subprocess.check_output(['docker','run','--rm','--network','none','--read-only',
                '--cap-drop','ALL',*mounts,'python:3.12-slim-bookworm','python3','-c',
                'import os,json; print(json.dumps({p:sum(os.stat(os.path.join(r,f)).st_size for r,ds,fs in os.walk("/data/"+p) for f in fs) for p in os.listdir("/data")}))'],text=True)
            (evidence_dir/(mode+'-resources.json')).write_text(json.dumps({
                'scope':'Small synthetic fixture snapshot; not capacity or minimum sizing',
                'containers':[json.loads(line) for line in stats.splitlines()],
                'flow_volume_logical_bytes':{volume:json.loads(disk)[str(index)] for index,volume in enumerate(flow_volumes)}},indent=2)+'\n')
            if exporter:
                subprocess.run(['docker','stop','--time','5',project+'-exporter'],stdout=subprocess.DEVNULL,check=False)
                exporter.wait(timeout=15)
                exporter = None
            setup.run_compose(ROOT, bundle, 'stop')
            setup.run_compose(ROOT, bundle, 'start')
            assert flows.check(ROOT, bundle)['passed'], 'Evidence must survive restart'
            print(json.dumps({'mode':mode,'result':'PASS','scope':'exporter, collector, store, real API/MCP/HUD components and restart'}))
        except Exception:
            log.flush()
            # Only synthetic test resources: no operator settings, tokens or captures.
            print((evidence_dir/(mode+'-exporter.log')).read_text()[-12000:],file=sys.stderr)
            for service in ('flow-collector','flow-shipper'):
                subprocess.run(command+['logs','--tail','15',service],env=environment,check=False)
            raise
        finally:
            if exporter:
                subprocess.run(['docker','rm','-f',project+'-exporter'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                exporter.wait(timeout=15)
            if server:
                server.shutdown()
                server.server_close()
            log.close()
            # Only this UUID test project and its disposable volumes are removed.
            subprocess.run(command+['down','--volumes','--remove-orphans'],env=environment,check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('demo','host','both'), default='both')
    parser.add_argument('--evidence-dir', required=True)
    args = parser.parse_args()
    evidence_dir = Path(args.evidence_dir).absolute()
    evidence_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
    for mode in (('demo','host') if args.mode=='both' else (args.mode,)):
        qualify(mode,evidence_dir)

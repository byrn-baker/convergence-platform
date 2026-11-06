#!/usr/bin/env python3
"""Portable Convergence preflight, fresh configuration, and read-only checks.

Standard-library only. Preparation and checks never start services. The explicit
run command operates only the selected Compose project. Existing bundles retain credentials.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import subprocess
import sys
from urllib.parse import urlsplit, urlencode
from urllib.request import build_opener, ProxyHandler, Request


def repo_path(value):
    repo = Path(value).expanduser().resolve()
    for name in ('deploy/convergence/docker-compose.yml', 'scripts/write-env.py'):
        if not (repo/name).is_file():
            raise ValueError('Select the NetClaw checkout with --repo')
    return repo


def env_writer(repo):
    spec = importlib.util.spec_from_file_location('convergence_env_writer', repo/'scripts/write-env.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def private_path(path):
    path = Path(path).expanduser().absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Use a regular deployment directory, without symlinks')
    return path


def output(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5,
                                stdin=subprocess.DEVNULL)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def preflight(repo):
    memory = {}
    meminfo = Path('/proc/meminfo')
    if meminfo.exists():
        raw = dict(re.findall(r'^(MemTotal|MemAvailable):\s+(\d+)', meminfo.read_text(), re.M))
        memory = {k: round(int(v)/1024**2, 2) for k, v in raw.items()}
    listeners = output(['ss', '-H', '-ltn'])
    ports = sorted({int(match[1]) for line in (listeners or '').splitlines()
                    if len(line.split()) > 3 and (match := re.search(r':(\d+)$', line.split()[3]))})
    return {'scope': 'read-only host preflight; no network devices contacted',
            'repo': str(repo), 'os': platform.system(), 'architecture': platform.machine(),
            'cpu_count': os.cpu_count(), 'memory_gib': memory,
            'disk_available_gib': round(shutil.disk_usage(repo).free/1024**3, 2),
            'tools': {name: bool(shutil.which(name)) for name in
                      ('python3', 'node', 'npm', 'docker', 'kubectl', 'systemctl', 'openclaw', 'hermes')},
            'docker_server': output(['docker', 'version', '--format', '{{.Server.Version}}']),
            'compose_version': output(['docker', 'compose', 'version', '--short']),
            'docker_context': output(['docker', 'context', 'show']),
            'listening_tcp_ports': ports if listeners is not None else None,
            'existing_config': {name: (repo/name).exists() for name in
                ('.env', 'deploy/convergence/.env', 'config/convergence.yaml', 'services/alert-receiver/.env')},
            'next': 'Select deployment host, existing-stack reuse, site, ports and telemetry before preparation.'}


def flow_support():
    spec = importlib.util.spec_from_file_location('convergence_flow_support', Path(__file__).with_name('flow_support.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_options(options):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,39}', options['site']):
        raise ValueError('Site must be a short lowercase identifier')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}', options['project']):
        raise ValueError('Invalid Compose project name')
    ports = [options[k] for k in ('api_port', 'prometheus_port', 'alertmanager_port', 'blackbox_port', 'receiver_port')]
    if options.get('grafana'):
        ports.append(options.get('grafana_port', 3300))
    if any(not 1024 <= p <= 65535 for p in ports) or len(set(ports)) != len(ports):
        raise ValueError('Choose distinct unprivileged ports between 1024 and 65535')
    if options.get('flows'):
        flow_support().validate(options)
    if options['edge_url']:
        url = urlsplit(options['edge_url'])
        if (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password
                or url.query or url.fragment or any(c.isspace() for c in options['edge_url'])):
            raise ValueError('Edge URL must be HTTP(S), without credentials, query or fragment')
        try:
            url.port
        except ValueError:
            raise ValueError('Edge URL has an invalid port') from None


def probe_job(name, target, site):
    return {'job_name': name, 'metrics_path': '/probe', 'params': {'module': ['http_2xx']},
            'static_configs': [{'targets': [target], 'labels': {'site': site}}],
            'relabel_configs': [
                {'source_labels': ['__address__'], 'target_label': '__param_target'},
                {'source_labels': ['__param_target'], 'target_label': 'instance'},
                {'target_label': '__address__', 'replacement': 'blackbox:9115'}]}


def prepare(repo, bundle, options):
    validate_options(options)
    bundle = private_path(bundle)
    writer = env_writer(repo)
    # Keep schema-1 core bundles compatible with the new optional dashboard.
    options = dict(options)
    if not options.get('grafana'):
        options.pop('grafana', None); options.pop('grafana_port', None)
    if not options.get('flows'):
        options.pop('flows', None); options.pop('flow_port', None)
    identity = {'repo': str(repo), **options}
    if bundle.exists():
        marker = bundle/'deployment.json'
        if not marker.is_file() or marker.is_symlink():
            raise ValueError('Output exists without a deployment manifest; choose a new directory')
        manifest = json.loads(marker.read_text())
        if manifest.get('schema_version') != 1 or manifest.get('options') != identity:
            raise ValueError('Existing deployment differs; reconcile it explicitly or choose a new directory')
        files = ('compose.env', 'receiver.env', 'hud.env', 'compose.override.json',
                 'prometheus.yml', 'alertmanager.yml', 'receiver-token', 'investigation-policy.yaml')
        if options.get('grafana'):
            files += ('grafana-datasources.yml', 'grafana-dashboards.yml', 'grafana-health.json', 'access.txt')
        if options.get('flows'):
            flow_support().ownership(bundle, manifest)
            files += ('flow-otel.json',)
        if any(not (bundle/file).is_file() or (bundle/file).is_symlink() for file in files):
            raise ValueError('Existing deployment is incomplete; restore missing files without rotating credentials')
        return {'status': 'preserved', 'bundle': str(bundle), 'commands': manifest['commands']}

    # Fresh bundle only: all generated credentials are matched across components.
    api_token, receiver_token = secrets.token_hex(32), secrets.token_hex(32)
    api_url = f"http://127.0.0.1:{options['api_port']}"
    receiver_url = f"http://host.docker.internal:{options['receiver_port']}/webhook"
    site = options['site']
    env = {
        'PGDATABASE': 'convergence', 'PGUSER': 'convergence', 'PGPASSWORD': secrets.token_hex(32),
        'JWT_SECRET': secrets.token_hex(32), 'PUBLIC_URL': api_url,
        'API_KEYS': json.dumps([{'id': 'convergence', 'name': 'Convergence operator',
                                'key': api_token, 'role': 'operator', 'sites': [site]}]),
        'USERS': '[]', 'SITES_CONFIG': json.dumps({site: {'name': site}}),
        'ALERT_RECEIVER_URL': receiver_url, 'ALERT_RECEIVER_TOKEN': receiver_token,
        'CONVERGENCE_API_HOST_PORT': f"127.0.0.1:{options['api_port']}",
        'PROMETHEUS_HOST_PORT': f"127.0.0.1:{options['prometheus_port']}",
        'ALERTMANAGER_HOST_PORT': f"127.0.0.1:{options['alertmanager_port']}",
        'BLACKBOX_HOST_PORT': f"127.0.0.1:{options['blackbox_port']}",
        'PFSENSE_MGMT_URL': options['edge_url'], 'EDGE_MGMT_URL': options['edge_url'],
        'UNIFI_HOST': '', 'UNIFI_MGMT_URL': '', 'SOT_TYPE': 'none',
        'SUZIEQ_DEVICE_USER': '', 'SUZIEQ_DEVICE_PASSWORD': '',
    }
    receiver = {'ALERT_RECEIVER_HOST': '0.0.0.0', 'ALERT_RECEIVER_PORT': str(options['receiver_port']),
                'ALERT_RECEIVER_TOKEN': receiver_token, 'NETWORK_GUARDIAN_URL': api_url,
                'NETWORK_GUARDIAN_TOKEN': api_token, 'INVESTIGATION_POLICY_PATH': str(bundle/'investigation-policy.yaml'),
                'OPENCLAW_GATEWAY_URL': '', 'OPENCLAW_HOOK_TOKEN': '', 'DISCORD_WEBHOOK_URL': '',
                'DISCORD_ALERT_CHANNEL_ID': '', 'NOISY_IOT_HOSTS': '', 'RECONCILE_ENABLED': 'false'}
    hud = {'CONVERGENCE_API_URL': api_url, 'CONVERGENCE_API_TOKEN': api_token,
           'NETWORK_GUARDIAN_URL': api_url, 'NETWORK_GUARDIAN_TOKEN': api_token,
           'PROMETHEUS_URL': f"http://127.0.0.1:{options['prometheus_port']}",
           'ALERTMANAGER_URL': f"http://127.0.0.1:{options['alertmanager_port']}"}
    prometheus = {'global': {'scrape_interval': '15s', 'evaluation_interval': '15s'},
        'alerting': {'alertmanagers': [{'static_configs': [{'targets': ['alertmanager:9093']}]}]},
        'rule_files': [], 'scrape_configs': [
            {'job_name': 'prometheus', 'static_configs': [{'targets': ['localhost:9090'], 'labels': {'site': site}}]},
            probe_job('convergence-api', 'http://convergence-api:3000/healthz', site)]}
    if options['edge_url']:
        prometheus['scrape_configs'].append(probe_job('blackbox_edge', options['edge_url'], site))
    alertmanager = {'route': {'receiver': 'netclaw', 'group_by': ['site', 'alertname'], 'group_wait': '30s'},
        'receivers': [{'name': 'netclaw', 'webhook_configs': [{'url': receiver_url, 'send_resolved': True,
            'http_config': {'authorization': {'type': 'Bearer', 'credentials_file': '/run/secrets/receiver-token'}}}]}]}
    def mount(file, target):
        return {'type': 'bind', 'source': str(bundle/file), 'target': target, 'read_only': True}
    # The legacy base file gives this network a global name. Override it so
    # different projects cannot resolve one another's postgres/prometheus aliases.
    overlay = {'networks': {'convergence': {'name': options['project']+'-convergence'}}, 'services': {
        'convergence-api': {'image': options['project']+'-api:local', 'environment': {
            'PFSENSE_MGMT_URL': options['edge_url'], 'EDGE_MGMT_URL': options['edge_url'],
            'UNIFI_HOST': '', 'UNIFI_MGMT_URL': ''}},
        'prometheus': {'volumes': [mount('prometheus.yml', '/etc/prometheus/prometheus.yml')]},
        'alertmanager': {'volumes': [mount('alertmanager.yml', '/etc/alertmanager/alertmanager.yml'),
                                    mount('receiver-token', '/run/secrets/receiver-token')]}}}
    grafana_files = {}
    if options.get('grafana'):
        password = secrets.token_hex(32)
        overlay['volumes'] = {'convergence-grafana': {}}
        overlay['services']['grafana'] = {
            'image': 'grafana/grafana:12.3.3', 'restart': 'unless-stopped',
            'networks': ['convergence'], 'ports': [f"127.0.0.1:{options['grafana_port']}:3000"],
            'environment': {'GF_SECURITY_ADMIN_USER': 'admin', 'GF_SECURITY_ADMIN_PASSWORD': password,
                'GF_AUTH_ANONYMOUS_ENABLED': 'false', 'GF_USERS_ALLOW_SIGN_UP': 'false'},
            'volumes': ['convergence-grafana:/var/lib/grafana',
                mount('grafana-datasources.yml', '/etc/grafana/provisioning/datasources/default.yml'),
                mount('grafana-dashboards.yml', '/etc/grafana/provisioning/dashboards/default.yml'),
                mount('grafana-health.json', '/var/lib/grafana/provisioned/health.json')]}
        grafana_files = {
            'grafana-datasources.yml': {'apiVersion': 1, 'datasources': [{
                'name': 'Metrics', 'uid': 'convergence-metrics', 'type': 'prometheus', 'access': 'proxy',
                'url': 'http://prometheus:9090', 'isDefault': True, 'editable': False}]},
            'grafana-dashboards.yml': {'apiVersion': 1, 'providers': [{'name': 'Convergence',
                'type': 'file', 'options': {'path': '/var/lib/grafana/provisioned'}}]},
            'grafana-health.json': installation_dashboard(site)}
    flow_files = {}
    flow_identity = secrets.token_hex(16) if options.get('flows') else None
    if options.get('flows'):
        flow_files = flow_support().configure(repo, bundle, options, env, overlay, flow_identity)
    commands = ['python3', str(repo/'workspace/skills/convergence-setup/scripts/setup.py'), '--repo', str(repo),
                'run', '--bundle', str(bundle), '--action']
    manifest = {'schema_version': 1, 'options': identity,
                'commands': {'validate': commands+['validate'], 'start_core': commands+['start']},
                'scope': 'core services; telemetry, HUD, receiver and investigator require their own verification'}
    if flow_identity:
        manifest['flow_identity'] = flow_identity
    bundle.mkdir(parents=True, mode=0o700)
    writer.write_private(bundle/'.gitignore', '*\n')
    for file, values in [('compose.env', env), ('receiver.env', receiver), ('hud.env', hud)]:
        writer.write_private(bundle/file, ''.join(k+'='+writer.quote(v)+'\n' for k, v in values.items()))
    for file, value in [('prometheus.yml', prometheus), ('alertmanager.yml', alertmanager),
                        ('compose.override.json', overlay),
                        ('investigation-policy.yaml', {'version': 1, 'default_tier': 'T0', 'allow_t1': [], 'allow_t2': []})]:
        writer.write_private(bundle/file, json.dumps(value, indent=2)+'\n')
    for file, value in {**grafana_files, **flow_files}.items():
        writer.write_private(bundle/file, json.dumps(value, indent=2)+'\n')
        (bundle/file).chmod(0o644)
    if options.get('grafana'):
        writer.write_private(bundle/'access.txt', f"Grafana: http://127.0.0.1:{options['grafana_port']}\nUsername: admin\nPassword: {password}\n")
    writer.write_private(bundle/'receiver-token', receiver_token)
    # Containers run unprivileged; the parent directory remains owner-private.
    for file in ('prometheus.yml', 'alertmanager.yml', 'receiver-token'):
        (bundle/file).chmod(0o644)
    writer.write_private(bundle/'deployment.json', json.dumps(manifest, indent=2)+'\n')
    return {'status': 'prepared', 'bundle': str(bundle), 'commands': manifest['commands']}



def installation_dashboard(site):
    panels = [{'id': 1, 'type': 'text', 'title': 'What this dashboard proves',
        'gridPos': {'x': 0, 'y': 0, 'w': 24, 'h': 4}, 'options': {'mode': 'markdown',
        'content': 'Core services only. Network telemetry, IDS capture and investigations require separate verification. Missing feeds are unknown.'}}]
    for i, (title, expr) in enumerate([('Core scrape targets', 'up'), ('API reachability', 'probe_success{job="convergence-api"}')], 2):
        expr = expr[:-1]+',site="'+site+'"}' if '{' in expr else expr+'{site="'+site+'"}'
        panels.append({'id': i, 'type': 'timeseries', 'title': title+' (1 = up)',
            'gridPos': {'x': (i-2)*12, 'y': 4, 'w': 12, 'h': 8},
            'datasource': {'type': 'prometheus', 'uid': 'convergence-metrics'},
            'targets': [{'refId': 'A', 'expr': expr, 'legendFormat': '{{job}} / {{instance}}'}]})
    return {'uid': 'convergence-installation', 'title': 'Convergence installation health',
            'schemaVersion': 39, 'version': 1, 'refresh': '15s',
            'time': {'from': 'now-30m', 'to': 'now'}, 'panels': panels}


def compose_environment(repo):
    # Compose shell interpolation wins over --env-file. Strip only inputs used by
    # the base file and Compose overrides; preserve daemon context and OS tools.
    variables = set(re.findall(r'\$\{([A-Za-z_][A-Za-z0-9_]*)',
                              (repo/'deploy/convergence/docker-compose.yml').read_text()))
    variables.discard('HOME')
    return {k: v for k, v in os.environ.items() if k not in variables and not k.startswith('COMPOSE_')}


def require_local_docker():
    context = os.environ.get('DOCKER_CONTEXT') or output(['docker','context','show'])
    endpoint = (None if os.environ.get('DOCKER_CONTEXT') else os.environ.get('DOCKER_HOST')) or output(
        ['docker','context','inspect',context or 'default','--format','{{.Endpoints.docker.Host}}'])
    if not endpoint or not endpoint.startswith(('unix://','npipe://')):
        raise ValueError('Use a local Docker Engine/Desktop context; remote daemons cannot mount this local bundle')


def run_compose(repo, bundle, action):
    bundle = private_path(bundle)
    manifest = json.loads((bundle/'deployment.json').read_text())
    options = manifest['options']; validate_options(options)
    if manifest.get('schema_version')!=1 or options['repo']!=str(repo):
        raise ValueError('Bundle belongs to another checkout or unsupported format')
    require_local_docker()
    if options.get('flows'):
        flow_support().require_linux(output)
        flow_support().guard_project(bundle, manifest, compose_environment(repo), check_ports=action=='start')
    base = ['docker','compose','-p',options['project'],'--env-file',str(bundle/'compose.env'),
            '-f',str(repo/'deploy/convergence/docker-compose.yml'),'-f',str(bundle/'compose.override.json')]
    actions = {'validate':['config','--quiet'], 'start':['up','-d','--build','--wait','--wait-timeout','120',
        'postgres','prometheus','alertmanager','blackbox','convergence-api']+(['grafana'] if options.get('grafana') else [])
        +(['flow-archive','flow-collector','flow-shipper'] if options.get('flows') else []),
        'stop':['stop'], 'status':['ps']}
    result = subprocess.run(base+actions[action], env=compose_environment(repo), stdin=subprocess.DEVNULL)
    if result.returncode:
        raise ValueError('Compose failed; review the error above and docs/convergence/troubleshooting.md')
    return {'status':'complete', 'action':action, 'project':options['project']}


def get_json(url, token=None):
    headers = {'Authorization': 'Bearer '+token} if token else {}
    with build_opener(ProxyHandler({})).open(Request(url, headers=headers), timeout=8) as response:
        return json.load(response)


def check(repo, bundle, include_receiver=False):
    bundle = private_path(bundle)
    manifest = json.loads((bundle/'deployment.json').read_text())
    if manifest.get('schema_version') != 1:
        raise ValueError('Unsupported deployment manifest')
    options = manifest['options']; validate_options(options)
    env = env_writer(repo).values((bundle/'hud.env').read_text())
    api_url = env['CONVERGENCE_API_URL']; token = env['CONVERGENCE_API_TOKEN']
    results = []
    def probe(name, url, predicate, auth=None):
        try:
            passed = bool(predicate(get_json(url, auth)))
            results.append({'check': name, 'status': 'pass' if passed else 'fail'})
        except Exception as exc:
            # Responses/exception messages may include private data or credentials.
            results.append({'check': name, 'status': 'fail', 'reason': type(exc).__name__})
    probe('Site-scoped diary access', api_url+'/api/events?'+urlencode({'site': options['site'], 'limit': 1}),
          lambda d: not d.get('unavailable') and isinstance(d.get('events'), list), token)
    probe('API and diary database', api_url+'/healthz', lambda d: d['features']['diary']=='available')
    def core_targets_up(data):
        targets = data['data']['activeTargets']
        return data['status']=='success' and all(
            any(t['health']=='up' and t.get('labels', {}).get('job')==job
                and t['labels'].get('site')==options['site'] for t in targets)
            for job in ('prometheus', 'convergence-api'))
    probe('Prometheus core scrape targets', env['PROMETHEUS_URL']+'/api/v1/targets', core_targets_up)
    probe('API blackbox probe', env['PROMETHEUS_URL']+'/api/v1/query?'+urlencode({
        'query': 'probe_success{job="convergence-api",site="'+options['site']+'"}'}),
        lambda d: d['status']=='success' and any(float(r['value'][1])==1 for r in d['data']['result']))
    probe('Alertmanager API', env['ALERTMANAGER_URL']+'/api/v2/status', lambda d: bool(d.get('versionInfo')))
    if options.get('grafana'):
        probe('Grafana database', f"http://127.0.0.1:{options['grafana_port']}/api/health",
              lambda d: d.get('database')=='ok')
    if include_receiver:
        url = f"http://127.0.0.1:{options['receiver_port']}"
        probe('Receiver health', url+'/health', lambda d: d.get('status')=='ok')
    return {'scope': 'service connectivity only; no synthetic alert sent or configuration changed',
            'checks': results, 'passed': all(r['status']=='pass' for r in results),
            'remaining': ['Verify selected network telemetry and coverage',
                          'Verify HUD access and model authentication',
                          'Verify receiver/case canary and authorized Guardian investigation separately']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='.', help='NetClaw checkout (defaults to current directory)')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('preflight')
    prep = commands.add_parser('prepare', help='Create a new private Docker core bundle; never starts services')
    prep.add_argument('--output', required=True)
    prep.add_argument('--site', default='home')
    prep.add_argument('--project', default='netclaw-convergence-home')
    prep.add_argument('--edge-url', default='')
    for name, port in [('api', 3080), ('prometheus', 9090), ('alertmanager', 9093), ('blackbox', 9115), ('receiver', 8099)]:
        prep.add_argument('--'+name+'-port', type=int, default=port)
    prep.add_argument('--flows', choices=('demo','host'), help='Opt-in Linux flow demo or explicit host-interface collection')
    prep.add_argument('--flow-port', type=int, default=2055, help='Loopback UDP receiver port for optional flows')
    prep.add_argument('--grafana', action='store_true', help='Add a private installation health dashboard')
    prep.add_argument('--grafana-port', type=int, default=3300)
    execute = commands.add_parser('run', help='Operate the selected bundle with isolated Compose settings')
    execute.add_argument('--bundle', required=True)
    execute.add_argument('--action', choices=('validate','start','stop','status'), required=True)
    verify = commands.add_parser('check', help='Read-only checks; never renders, builds or starts services')
    verify.add_argument('--bundle', required=True)
    verify.add_argument('--receiver', action='store_true')
    args = parser.parse_args()
    try:
        repo = repo_path(args.repo)
        if args.command=='preflight': result = preflight(repo)
        elif args.command=='prepare':
            options = {k: v for k, v in vars(args).items() if k not in ('repo', 'command', 'output')}
            result = prepare(repo, args.output, options)
        elif args.command=='run': result = run_compose(repo, args.bundle, args.action)
        else: result = check(repo, args.bundle, args.receiver)
        print(json.dumps(result, indent=2))
        return 1 if result.get('passed') is False else 0
    except (OSError, ValueError, KeyError) as exc:
        detail = str(exc) if type(exc) is ValueError else type(exc).__name__
        print('Convergence setup could not complete: '+detail, file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())

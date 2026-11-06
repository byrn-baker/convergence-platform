"""Private, opt-in Linux flow collection configuration; no runtime side effects."""
import json
import hashlib
import re
import subprocess
import socket
import platform

COVERAGE = {
    'demo': 'Synthetic capture uses documentation-range addresses; no interface is captured.',
    'host': 'Source: the explicitly selected interface, exported through the local loopback receiver.',
}


def validate(options):
    if options['flows'] not in COVERAGE:
        raise ValueError('Choose demo or host flow mode')
    if not isinstance(options.get('flow_port'), int) or not 1024 <= options['flow_port'] <= 65535:
        raise ValueError('Choose a flow UDP port between 1024 and 65535')
    used = [options[k] for k in ('api_port','prometheus_port','alertmanager_port','blackbox_port','receiver_port')]
    if options.get('grafana'): used.append(options['grafana_port'])
    if options['flow_port'] in used:
        raise ValueError('Flow UDP/metrics port must differ from other service ports')


def require_linux(output):
    info = output(['docker','info','--format','{{.OSType}}|{{.OperatingSystem}}']) or ''
    if platform.system() != 'Linux' or not info.startswith('linux|') or 'desktop' in info.lower():
        raise ValueError('Flow collection currently requires local Linux Docker Engine; Docker Desktop and WSL2 are not qualified')


def configure(repo, bundle, options, env, overlay, identity):
    mode, site = options['flows'], options['site']
    port = options['flow_port']
    env['SITES_CONFIG'] = json.dumps({site: {'name': site, 'flows': {
        'exporters': ['127.0.0.1'], 'coverage': {'mode': mode, 'description': COVERAGE[mode]}}}})
    env['FLOW_VICTORIALOGS_URL'] = 'http://flow-archive:9428'
    assets = str(repo/'deploy/convergence/flows')
    base = {'restart': 'unless-stopped', 'cap_drop': ['ALL'],
            'security_opt': ['no-new-privileges:true'],
            'logging': {'driver':'local','options':{'max-size':'5m','max-file':'2'}}}
    services = overlay['services']
    services['flow-archive'] = {**base, 'image':'victoriametrics/victoria-logs:v1.52.0',
        'networks':['convergence'], 'command':['-storageDataPath=/storage','-retentionPeriod=1d'],
        'volumes':['convergence-flow-archive:/storage'], 'mem_limit':'512m'}
    services['flow-collector'] = {**base,
        'build':{'context':assets,'dockerfile':'Dockerfile.collector'},
        'image':options['project']+'-flow-collector:local', 'network_mode':'host',
        'read_only':True, 'environment':{'FLOW_PORT':str(port)},
        'volumes':['convergence-flow-spool:/flows'], 'mem_limit':'128m',
        'healthcheck':{'test':['CMD','python3','-c',
            'import os,urllib.request; urllib.request.urlopen("http://127.0.0.1:"+os.environ["FLOW_PORT"]+"/metrics",timeout=1).close()'],
            'interval':'2s','timeout':'2s','retries':15}}
    services['flow-shipper'] = {**base, 'image':'otel/opentelemetry-collector-contrib:0.104.0',
        'user':'0:0', 'read_only':True, 'networks':['convergence'],
        'command':['--config=/etc/otel/flow.json'],
        'volumes':[{'type':'bind','source':str(bundle/'flow-otel.json'),'target':'/etc/otel/flow.json','read_only':True},
            'convergence-flow-spool:/flows:ro','convergence-flow-state:/state'],
        'mem_limit':'256m', 'depends_on':['flow-archive','flow-collector']}
    services['flow-exporter'] = {'build':{'context':assets,'dockerfile':'Dockerfile.exporter'},
        'image':options['project']+'-flow-exporter:local', 'profiles':['manual-flow-export'],
        'network_mode':'host', 'cap_drop':['ALL'], 'security_opt':['no-new-privileges:true'],
        'read_only':True, 'tmpfs':['/tmp:rw,noexec,nosuid,size=8m','/run:rw,noexec,nosuid,size=1m'],
        'environment':{'FLOW_PORT':str(port)}}
    overlay.setdefault('volumes', {}).update({name:{} for name in (
        'convergence-flow-archive','convergence-flow-spool','convergence-flow-state')})
    labels = ownership(bundle, {'options':options,'flow_identity':identity})
    for name in ('postgres','prometheus','alertmanager','blackbox','convergence-api'):
        services.setdefault(name, {})
    for service in services.values(): service['labels'] = dict(labels)
    for name in ('convergence-pgdata','convergence-promdata','convergence-amdata'):
        overlay['volumes'].setdefault(name, {})
    for volume in overlay['volumes'].values(): volume['labels'] = dict(labels)
    overlay['networks']['convergence']['labels'] = dict(labels)
    config = {
        'extensions': {'file_storage': {'directory':'/state'}},
        'receivers': {'filelog/flows': {
            'include':['/flows/*.jsonl'], 'start_at':'beginning', 'storage':'file_storage',
            'max_log_size':'64KiB', 'retry_on_failure':{'enabled':True,'max_elapsed_time':'0s'},
            'operators':[
                {'type':'json_parser','parse_from':'body','parse_to':'attributes','on_error':'drop'},
                {'type':'filter','expr':'attributes.sampler_address != "127.0.0.1"'},
                {'type':'filter','expr':'attributes.src_addr == nil or attributes.dst_addr == nil or attributes.bytes == nil'}],
            'resource':{'job':'netflow','site':site,'service.name':'convergence-local-flows'}}},
        'processors':{'memory_limiter':{'check_interval':'1s','limit_mib':180,'spike_limit_mib':32},
                      'batch':{'timeout':'1s','send_batch_size':128,'send_batch_max_size':256}},
        'exporters':{'otlphttp/flows':{
            'logs_endpoint':'http://flow-archive:9428/insert/opentelemetry/v1/logs',
            'headers':{'VL-Stream-Fields':'job,site,sampler_address'},'timeout':'10s',
            'retry_on_failure':{'enabled':True,'max_elapsed_time':'0s'},
            'sending_queue':{'enabled':True,'storage':'file_storage','queue_size':128,'num_consumers':1}}},
        'service':{'extensions':['file_storage'],'pipelines':{'logs':{
            'receivers':['filelog/flows'],'processors':['memory_limiter','batch'],'exporters':['otlphttp/flows']}}}}
    return {'flow-otel.json':config}


LABEL_PREFIX = 'io.netclaw.convergence.flow.'


def ownership(bundle, manifest):
    identity = manifest.get('flow_identity', '')
    if not isinstance(identity,str) or not re.fullmatch(r'[a-f0-9]{32}',identity):
        raise ValueError('Flow bundle has no persisted ownership identity; create a new bundle and project')
    options = manifest['options']
    return {LABEL_PREFIX+'id':identity, LABEL_PREFIX+'mode':options['flows'],
        LABEL_PREFIX+'site':options['site'], LABEL_PREFIX+'port':str(options['flow_port']),
        LABEL_PREFIX+'bundle':hashlib.sha256(str(bundle).encode()).hexdigest()}


def docker_read(arguments, environment):
    result = subprocess.run(['docker',*arguments],env=environment,capture_output=True,text=True,
        timeout=10,stdin=subprocess.DEVNULL)
    if result.returncode:
        raise ValueError('Cannot inspect Docker project ownership; no changes made')
    return result.stdout.strip()


def inspect_labels(kind, identity, environment):
    expression = '{{json .Config.Labels}}' if kind=='container' else '{{json .Labels}}'
    try:
        labels = json.loads(docker_read([kind,'inspect','--format',expression,identity],environment))
    except (ValueError,TypeError):
        raise ValueError('Cannot inspect Docker resource ownership; no changes made') from None
    return labels if isinstance(labels,dict) else {}


def assert_owned(kind, identity, expected, environment):
    actual = inspect_labels(kind,identity,environment)
    if any(actual.get(key)!=value for key,value in expected.items()):
        raise ValueError('Compose project contains resources from another bundle or mode; choose a fresh project name. No changes made')


def guard_project(bundle, manifest, environment, check_ports=False):
    """Refuse adoption of running/stopped containers, retained volumes or networks."""
    expected = ownership(bundle,manifest)
    project = manifest['options']['project']
    # Compose project names may be prefixes of unrelated project names. Only
    # exact generated names are fallback collisions when Compose labels are absent.
    overlay = json.loads((bundle/'compose.override.json').read_text())
    services = '|'.join(re.escape(name) for name in overlay['services'])
    volumes = '|'.join(re.escape(name) for name in overlay['volumes'])
    declarations = [
        ('container',['ps','--all'],r'^/'+re.escape(project)+r'[-_]('+services+r')[-_]1$', '{{.ID}}'),
        ('volume',['volume','ls'],r'^'+re.escape(project)+r'_('+volumes+r')$', '{{.Name}}'),
        ('network',['network','ls'],r'^'+project+r'-convergence$', '{{.ID}}'),
    ]
    for kind, command, name_filter, formatter in declarations:
        found = set()
        for query in ('label=com.docker.compose.project='+project,'name='+name_filter):
            found.update(docker_read([*command,'--filter',query,'--format',formatter],environment).splitlines())
        for identity in found:
            if identity: assert_owned(kind,identity,expected,environment)
    # Host networking makes a port global even when Compose projects differ.
    listeners = []
    for service in ('flow-collector','flow-exporter'):
        active = docker_read(['ps','--filter','label=com.docker.compose.service='+service,
            '--filter','label='+LABEL_PREFIX+'port='+str(manifest['options']['flow_port']),
            '--format','{{.ID}}'],environment).splitlines()
        for identity in active:
            if identity: assert_owned('container',identity,expected,environment)
        if service=='flow-collector': listeners = active
    if check_ports and not listeners:
        sockets = []
        try:
            for kind in (socket.SOCK_STREAM,socket.SOCK_DGRAM):
                connection = socket.socket(socket.AF_INET,kind);sockets.append(connection)
                # Match the collector HTTP server's restart behavior: TIME_WAIT
                # is not a live listener. Never enable address reuse for UDP.
                if kind==socket.SOCK_STREAM:
                    connection.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                connection.bind(('127.0.0.1',manifest['options']['flow_port']))
        except OSError:
            raise ValueError('Flow TCP/UDP port is already occupied by another listener; choose an unused --flow-port. No changes made') from None
        finally:
            for connection in sockets: connection.close()
    return expected

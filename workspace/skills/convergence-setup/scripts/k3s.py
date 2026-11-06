#!/usr/bin/env python3
"""Prepare and install an isolated Convergence Helm release in an explicit context.

Python standard library only. Preparation is offline. Installation is a separate
command; it never rotates an existing Secret or changes the current context.
"""
import argparse
import base64
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys

from setup import env_writer, private_path, repo_path


def validate(options):
    for key, length in [('namespace', 63), ('release', 53), ('storage_class', 253)]:
        value = options[key]
        pattern = r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?' if key=='storage_class' else r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?'
        if len(value)>length or not re.fullmatch(pattern, value):
            raise ValueError('Invalid '+key+'; use a lowercase Kubernetes name')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,39}', options['site']):
        raise ValueError('Invalid site identifier')
    if not options['context'] or options['context'].startswith('-'):
        raise ValueError('An explicit Kubernetes context is required')
    image = options['image']
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._/:@-]*', image):
        raise ValueError('Invalid API image')
    if ':' not in image.rsplit('/', 1)[-1] or image.endswith(':latest'):
        raise ValueError('Choose a versioned API image tag or digest, not latest')


def image_values(image):
    if '@' in image:
        repository, digest = image.split('@', 1)
        # Existing chart formats repository:tag; a tag+digest is valid OCI syntax.
        if ':' in repository.rsplit('/', 1)[-1]:
            repository, tag = repository.rsplit(':', 1)
        else:
            tag = 'pinned'
        return {'repository': repository, 'tag': tag+'@'+digest}
    repository, tag = image.rsplit(':', 1)
    return {'repository': repository, 'tag': tag}


def prepare(repo, bundle, options):
    validate(options)
    bundle = private_path(bundle)
    identity = {'repo':str(repo), **options}
    required = ('secret.json', 'values.json', 'hud.env', 'deployment.json')
    if bundle.exists():
        if any(not (bundle/f).is_file() or (bundle/f).is_symlink() for f in required):
            raise ValueError('Existing bundle is incomplete; choose a new directory or restore it')
        manifest = json.loads((bundle/'deployment.json').read_text())
        if manifest.get('schema_version')!=1 or manifest.get('kind')!='k3s' or manifest.get('options')!=identity:
            raise ValueError('Existing deployment differs; do not rotate its credentials')
        return {'status':'preserved', 'bundle':str(bundle)}
    release, site = options['release'], options['site']
    token = secrets.token_hex(32)
    secret = {'apiVersion':'v1', 'kind':'Secret', 'type':'Opaque',
        'metadata':{'name':release+'-credentials', 'namespace':options['namespace']},
        'stringData':{'PGDATABASE':'convergence', 'PGUSER':'convergence',
            'PGPASSWORD':secrets.token_hex(32), 'JWT_SECRET':secrets.token_hex(32),
            'GRAFANA_ADMIN_PASSWORD':secrets.token_hex(32),
            'API_KEYS':json.dumps([{'id':'convergence', 'name':'Convergence operator',
                                  'key':token, 'role':'operator', 'sites':[site]}]),
            'USERS':'[]'}}
    storage = options['storage_class']
    values = {'existingSecret':release+'-credentials',
        'siteConfig':{'home':None, site:{'name':site}},
        'api':{'image':image_values(options['image'])},
        'postgres':{'storage':{'className':storage, 'size':'2Gi'}},
        'loki':{'storage':{'className':storage, 'size':'2Gi'}, 'retention':'72h'},
        'grafana':{'enabled':options['grafana'], 'storage':{'className':storage, 'size':'1Gi'}},
        'victoria':{'storage':{'className':storage, 'queueSize':'1Gi'},
            'metrics':{'server':{'retentionPeriod':'7d', 'persistentVolume':{'storageClassName':storage,'size':'2Gi'}}},
            'logs':{'server':{'retentionPeriod':'7d', 'persistentVolume':{'storageClassName':storage,'size':'2Gi'}}},
            'agent':{'extraScrapeConfigs':[{
                'job_name':'convergence-api', 'metrics_path':'/probe', 'params':{'module':['http_2xx']},
                'static_configs':[{'targets':['http://'+release+'-api:3000/healthz'], 'labels':{'site':site}}],
                'relabel_configs':[{'source_labels':['__address__'],'target_label':'__param_target'},
                    {'source_labels':['__param_target'],'target_label':'instance'},
                    {'target_label':'__address__','replacement':release+'-blackbox:9115'}]}]}}}
    writer = env_writer(repo)
    bundle.mkdir(parents=True, mode=0o700)
    writer.write_private(bundle/'.gitignore', '*\n')
    for name, data in [('secret.json',secret), ('values.json',values)]:
        writer.write_private(bundle/name,json.dumps(data,indent=2)+'\n')
    writer.write_private(bundle/'hud.env', ''.join(k+'='+writer.quote(v)+'\n' for k,v in {
        'CONVERGENCE_API_URL':'http://127.0.0.1:3080', 'CONVERGENCE_API_TOKEN':token,
        'PROMETHEUS_URL':'http://127.0.0.1:8428','ALERTMANAGER_URL':'http://127.0.0.1:9093'}.items()))
    writer.write_private(bundle/'deployment.json',json.dumps({'schema_version':1,'kind':'k3s','options':identity},indent=2)+'\n')
    return {'status':'prepared','bundle':str(bundle),
            'next':'Build/deliver the API image, review values.json, then run install --bundle with this helper.'}


def run(command, *, sensitive=False):
    result = subprocess.run(command, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if result.returncode:
        detail = ' (Secret output withheld)' if sensitive else ': '+result.stderr.strip()[-1500:]
        raise ValueError('Command failed: '+command[0]+detail)
    return result.stdout


def kubectl(options):
    return ['kubectl','--context',options['context'],'--namespace',options['namespace']]


def require_matching_secret(desired, actual):
    expected = {k:base64.b64encode(v.encode()).decode() for k,v in desired['stringData'].items()}
    if any(actual.get('data',{}).get(k)!=v for k,v in expected.items()):
        raise ValueError('Cluster credentials differ from this bundle; restore the matching bundle, do not overwrite the Secret')


def preflight(context):
    prefix = ['kubectl','--context',context]
    nodes = json.loads(run(prefix+['get','nodes','-o','json']))
    storage = json.loads(run(prefix+['get','storageclasses','-o','json']))
    return {'context':context, 'nodes':[{'name':n['metadata']['name'],
        'architecture':n['status']['nodeInfo']['architecture'],
        'kubelet':n['status']['nodeInfo']['kubeletVersion'],
        'allocatable':n['status'].get('allocatable',{}),
        'ready':any(c['type']=='Ready' and c['status']=='True' for c in n['status']['conditions'])}
        for n in nodes['items']],
        'storage_classes':[s['metadata']['name'] for s in storage['items']],
        'helm':run(['helm','version','--short']).strip(),
        'scope':'read-only; image availability, capacity, backups and access still require review'}


def load(bundle, repo):
    bundle = private_path(bundle)
    for name in ('deployment.json','values.json','secret.json'):
        if not (bundle/name).is_file() or (bundle/name).is_symlink():
            raise ValueError('Incomplete bundle or symlink: '+name)
    manifest = json.loads((bundle/'deployment.json').read_text())
    if manifest.get('kind')!='k3s' or manifest.get('schema_version')!=1:
        raise ValueError('Not a supported K3s bundle')
    options = manifest['options']; validate(options)
    if options['repo']!=str(repo):
        raise ValueError('Bundle belongs to another checkout; use its recorded --repo')
    return bundle, options


def install(repo, bundle):
    bundle, options = load(bundle, repo)
    desired = json.loads((bundle/'secret.json').read_text())
    values = json.loads((bundle/'values.json').read_text())
    if values.get('existingSecret')!=options['release']+'-credentials':
        raise ValueError('Helm Secret target differs from the deployment manifest')
    if desired['metadata']!={'name':options['release']+'-credentials', 'namespace':options['namespace']}:
        raise ValueError('Secret target differs from the deployment manifest')
    k = kubectl(options)
    # Fail on inaccessible/mistyped context before creating anything.
    info = preflight(options['context'])
    if options['storage_class'] not in info['storage_classes']:
        raise ValueError('Selected storage class is absent; choose an available provisioner')
    if not info['nodes'] or not all(n['ready'] for n in info['nodes']):
        raise ValueError('Cluster nodes are not all Ready')
    ns = run(k+['get','namespace',options['namespace'],'--ignore-not-found','-o','name'])
    if not ns.strip():
        run(k+['create','namespace',options['namespace']])
    existing = run(k+['get','secret',desired['metadata']['name'],'--ignore-not-found','-o','json'], sensitive=True)
    if existing.strip():
        require_matching_secret(desired,json.loads(existing))
    else:
        # An existing release/PVC with a missing Secret must not receive new passwords.
        resources = json.loads(run(k+['get','statefulsets,pvc','-o','json']))['items']
        if any(options['release'] in r['metadata']['name'] for r in resources):
            raise ValueError('Existing release data has no matching Secret; restore credentials first')
        run(k+['create','-f',str(bundle/'secret.json')], sensitive=True)
    run(['helm','upgrade','--install',options['release'],str(repo/'deploy/helm/convergence'),
         '--kube-context',options['context'],'--namespace',options['namespace'],
         '--values',str(bundle/'values.json'),'--wait','--timeout','10m'])
    return {'status':'installed','context':options['context'],'namespace':options['namespace'],
            'release':options['release'], 'next':'Run the port-forward and acceptance checks in docs/convergence/k3s.md'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',default='.')
    sub=parser.add_subparsers(dest='command',required=True)
    pre=sub.add_parser('preflight');pre.add_argument('--context',required=True)
    prep=sub.add_parser('prepare')
    for key in ('output','context','namespace','release','site','storage-class','image'):
        prep.add_argument('--'+key,required=True)
    prep.add_argument('--no-grafana',action='store_false',dest='grafana',default=True)
    apply=sub.add_parser('install');apply.add_argument('--bundle',required=True)
    args=parser.parse_args()
    try:
        repo=repo_path(args.repo)
        if args.command=='preflight': result=preflight(args.context)
        elif args.command=='prepare':
            options={k:v for k,v in vars(args).items() if k not in ('repo','command','output')}
            result=prepare(repo,args.output,options)
        else: result=install(repo,args.bundle)
        print(json.dumps(result,indent=2));return 0
    except (OSError, ValueError, KeyError) as exc:
        print('Convergence K3s setup: '+(str(exc) if type(exc) is ValueError else type(exc).__name__),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())

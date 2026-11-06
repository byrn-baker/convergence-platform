#!/usr/bin/env python3
"""Run an explicit synthetic flow demo, inspect evidence, or print host capture commands."""
import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from setup import compose_environment, env_writer, flow_support, get_json, private_path, repo_path, require_local_docker, validate_options


def load(repo, bundle):
    bundle = private_path(bundle)
    manifest = json.loads((bundle/'deployment.json').read_text())
    options = manifest['options']
    validate_options(options)
    if manifest.get('schema_version') != 1 or options.get('repo') != str(repo) or options.get('flows') not in ('demo','host'):
        raise ValueError('Select a flow-enabled bundle from this checkout')
    flow_support().ownership(bundle,manifest)
    return bundle, options


def compose(repo, bundle, options):
    return ['docker','compose','-p',options['project'],'--env-file',str(bundle/'compose.env'),
        '-f',str(repo/'deploy/convergence/docker-compose.yml'),'-f',str(bundle/'compose.override.json')]


def check(repo, bundle, wait_seconds=90):
    bundle, options = load(repo, bundle)
    if not 0 <= wait_seconds <= 180: raise ValueError('Wait must be between 0 and 180 seconds')
    credentials = env_writer(repo).values((bundle/'hud.env').read_text())
    url, token, site = credentials['CONVERGENCE_API_URL'], credentials['CONVERGENCE_API_TOKEN'], options['site']
    deadline = time.monotonic()+wait_seconds
    while True:
        try:
            summary = get_json(url+'/api/flows?'+urlencode({'site':site,'range':'15m'}), token)
        except HTTPError as error:
            if error.code not in (502,503,504) or time.monotonic() >= deadline: raise
            time.sleep(1);continue
        except (URLError,TimeoutError):
            if time.monotonic() >= deadline: raise
            time.sleep(1);continue
        if summary.get('status')=='active' or time.monotonic() >= deadline: break
        time.sleep(1)
    if summary.get('status') != 'active':
        return {'passed':False,'status':summary.get('status','unknown'),'coverage':summary.get('coverage'),
            'reason':'No retained flow evidence yet. A healthy core does not prove interface or network coverage.'}
    rows = summary.get('sources',[])
    if not rows: raise ValueError('Active flow response contains no source observations')
    query={'site':site,'address':rows[0]['src_addr'],'start':summary['start'],'end':summary['end'],'exporter':'127.0.0.1'}
    evidence = get_json(url+'/api/flows/records?'+urlencode(query),token)
    checks = {'same_window':evidence.get('start')==summary['start'] and evidence.get('end')==summary['end'],
        'retained_records':bool(evidence.get('records')),
        'coverage':summary.get('coverage',{}).get('mode')==options['flows'],
        'exporter_scope':summary.get('exporters')==['127.0.0.1'] and all(r.get('sampler_address')=='127.0.0.1' for r in evidence.get('records',[]))}
    for name, path, auth in [('authentication','/api/flows?site='+site,None),
            ('site_isolation','/api/flows?site=unassigned-flow-site',token)]:
        try: get_json(url+path,auth); checks[name]=False
        except HTTPError as error: checks[name]=error.code in (401,403)
    return {'passed':all(checks.values()),'checks':checks,'coverage':summary.get('coverage'),
        'summary':summary,'evidence':evidence,
        'scope':'Retained flow API evidence; browser, MCP and whole-network visibility require separate verification.'}


def collector_ready(repo, bundle, options):
    environment = compose_environment(repo)
    manifest = json.loads((bundle/'deployment.json').read_text())
    expected = flow_support().guard_project(bundle,manifest,environment)
    result = subprocess.run(compose(repo,bundle,options)+['ps','-q','flow-collector'],
        env=environment,capture_output=True,text=True,timeout=10,stdin=subprocess.DEVNULL)
    identity = result.stdout.strip()
    if result.returncode or not re.fullmatch(r'[a-f0-9]{12,64}',identity):
        raise ValueError('Start this bundle before sending demonstration flows')
    flow_support().assert_owned('container',identity,expected,environment)
    for _ in range(45):
        result = subprocess.run(['docker','inspect','--format','{{if .State.Health}}{{.State.Health.Status}}{{end}}',identity],
            env=environment,capture_output=True,text=True,timeout=5,stdin=subprocess.DEVNULL)
        state = result.stdout.strip()
        if result.returncode == 0 and state == 'healthy': return
        if state != 'starting': break
        time.sleep(1)
    raise ValueError('Selected flow collector is not healthy; inspect its status before sending demo data')


def demo(repo, bundle):
    bundle, options = load(repo,bundle)
    if options['flows'] != 'demo': raise ValueError('Synthetic demo is permitted only in a separate demo-mode bundle')
    from setup import output
    require_local_docker()
    flow_support().require_linux(output)
    collector_ready(repo,bundle,options)
    result = subprocess.run(compose(repo,bundle,options)+['run','--rm','--no-deps','--build','flow-exporter'],
        env=compose_environment(repo),stdin=subprocess.DEVNULL,stdout=sys.stderr)
    if result.returncode: raise ValueError('Synthetic exporter failed; inspect its container output')
    return check(repo,bundle)


def host_command(repo, bundle, interface, capture_filter=''):
    bundle, options = load(repo,bundle)
    if options['flows'] != 'host': raise ValueError('Host capture requires a separate host-mode bundle')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}',interface) or interface in ('any','all'):
        raise ValueError('Select one explicit Linux interface name')
    if len(capture_filter)>512 or any(c in capture_filter for c in '\n\r\0'):
        raise ValueError('Capture filter must be a single BPF expression, at most 512 characters')
    port=options['flow_port']
    expression=f'not (udp and port {port})'
    if capture_filter: expression += ' and ('+capture_filter+')'
    command=compose(repo,bundle,options)+['run','--rm','--no-deps','--build','--cap-add','NET_RAW',
        '--entrypoint','softflowd','flow-exporter','-d','-N','-i',interface,'-v','5',
        '-n','127.0.0.1:'+str(port),'-p','/run/convergence.pid','-c','/run/convergence.ctl',
        '-t','maxlife=15','-t','expint=1','-t','tcp.fin=1','-t','udp=5','-t','general=5',expression]
    entry = ['python3',str(Path(__file__).resolve()),'--repo',str(repo),'capture','--bundle',str(bundle),'--interface',interface]
    if capture_filter: entry += ['--capture-filter',capture_filter]
    return {'command':command,'shell_command':shlex.join(entry),'interface':interface,'capture_filter':expression,
        'scope':'Explicit Linux interface capture; foreground process, stop with Ctrl-C. No routing or firewall changes. Not whole-network visibility.',
        'note':'The capture helper removes conflicting Compose settings and uses the selected local Linux Docker Engine.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',default='.')
    commands=parser.add_subparsers(dest='command',required=True)
    for name in ('demo','check','host-command','capture'):
        item=commands.add_parser(name);item.add_argument('--bundle',required=True)
        if name=='check':item.add_argument('--wait-seconds',type=int,default=90)
        if name in ('host-command','capture'):
            item.add_argument('--interface',required=True);item.add_argument('--capture-filter',default='')
    args=parser.parse_args()
    try:
        repo=repo_path(args.repo)
        if args.command=='demo':result=demo(repo,args.bundle)
        elif args.command=='check':result=check(repo,args.bundle,args.wait_seconds)
        else:
            result=host_command(repo,args.bundle,args.interface,args.capture_filter)
            if args.command=='capture':
                from setup import output
                require_local_docker()
                flow_support().require_linux(output)
                collector_ready(repo,private_path(args.bundle),load(repo,args.bundle)[1])
                return subprocess.run(result['command'],env=compose_environment(repo)).returncode
        print(json.dumps(result,indent=2));return 1 if result.get('passed') is False else 0
    except (OSError,ValueError,KeyError) as exc:
        print('Flow setup failed: '+(str(exc) if type(exc) is ValueError else type(exc).__name__),file=sys.stderr);return 2


if __name__=='__main__':sys.exit(main())

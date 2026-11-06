#!/usr/bin/env python3
"""Check a prepared Docker/K3s installation; --canary writes marked test records.

K3s checks open temporary loopback port-forwards and close them on exit. No device
configuration, packet capture, notification, or model investigation is triggered.
"""
import argparse
import base64
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import build_opener, ProxyHandler, Request
import uuid

from setup import env_writer, private_path, repo_path
import k3s


def request(url, *, token=None, basic=None, data=None, method=None):
    headers = {}
    if token: headers['Authorization']='Bearer '+token
    if basic: headers['Authorization']='Basic '+base64.b64encode(basic.encode()).decode()
    if data is not None:
        headers['Content-Type']='application/json'
        data=json.dumps(data).encode()
    with build_opener(ProxyHandler({})).open(Request(url, headers=headers, data=data, method=method), timeout=10) as response:
        body=response.read().decode()
        try: return json.loads(body)
        except json.JSONDecodeError: return body


@contextmanager
def endpoints(repo, bundle, manifest):
    options=manifest['options']
    if manifest.get('kind')!='k3s':
        env=env_writer(repo).values((bundle/'hud.env').read_text())
        urls={'api':env['CONVERGENCE_API_URL'], 'metrics':env['PROMETHEUS_URL'], 'alertmanager':env['ALERTMANAGER_URL']}
        password=None
        if options.get('grafana'):
            urls['grafana']=f"http://127.0.0.1:{options['grafana_port']}"
            overlay=json.loads((bundle/'compose.override.json').read_text())
            password=overlay['services']['grafana']['environment']['GF_SECURITY_ADMIN_PASSWORD']
        yield urls, env['CONVERGENCE_API_TOKEN'], password
        return
    k3s.load(bundle, repo)
    secret=json.loads((bundle/'secret.json').read_text())['stringData']
    processes=[]; urls={}
    services={'api':3000,'metrics':8428,'alertmanager':9093,'logs':9428,'collector':4318,'loki':3100}
    if options['grafana']: services['grafana']=3000
    try:
        for service, remote in services.items():
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
            process=subprocess.Popen(k3s.kubectl(options)+['port-forward','--address','127.0.0.1',
                'service/'+options['release']+'-'+service, str(port)+':'+str(remote)],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL)
            processes.append(process)
            for _ in range(100):
                if process.poll() is not None: raise ValueError('Port-forward failed for '+service+'; check context, service and RBAC')
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=0.1): break
                except OSError: time.sleep(0.1)
            else: raise ValueError('Port-forward timed out for '+service)
            urls[service]='http://127.0.0.1:'+str(port)
        yield urls,json.loads(secret['API_KEYS'])[0]['key'],secret['GRAFANA_ADMIN_PASSWORD']
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill();process.wait()


def verify(repo,bundle,canary=False,wait_seconds=60):
    bundle=private_path(bundle)
    manifest=json.loads((bundle/'deployment.json').read_text())
    site=manifest['options']['site']; checks=[]
    if not 0 <= wait_seconds <= 300:
        raise ValueError('wait-seconds must be between 0 and 300')
    deadline=time.monotonic()+wait_seconds
    def check(name,fn,retry=True):
        while True:
            try:
                if not fn(): raise ValueError('Unexpected response')
                checks.append({'check':name,'status':'pass'});return
            except Exception as exc:
                if retry and time.monotonic()<deadline:
                    time.sleep(0.5);continue
                checks.append({'check':name,'status':'fail','reason':type(exc).__name__});return
    with endpoints(repo,bundle,manifest) as (urls,token,password):
        def api(path,**kwargs): return request(urls['api']+path,token=token,**kwargs)
        check('Selected site',lambda:site in [s['id'] for s in api('/api/sites')['sites']])
        # /healthz reports cached DB state. A real query reopens the circuit
        # after PostgreSQL recovers; polling liveness alone cannot do that.
        def cases_ready():
            data=api('/api/events?'+urlencode({'site':site}))
            return not data.get('unavailable') and isinstance(data.get('events'),list)
        check('Scoped cases',cases_ready)
        check('API and database',lambda:request(urls['api']+'/healthz')['features']['diary']=='available')
        def denied(path,authenticated):
            try: request(urls['api']+path,token=token if authenticated else None)
            except HTTPError as exc: return exc.code in (401,403)
            return False
        check('Unauthenticated cases rejected',lambda:denied('/api/events?site='+site,False))
        check('Other site rejected',lambda:denied('/api/events?site=unassigned-verification-site',True))
        query='probe_success{job="convergence-api",site="'+site+'"}'
        path='/api/v1/query?'+urlencode({'query':query})
        check('API probe ingested',lambda:any(float(x['value'][1])==1 for x in request(urls['metrics']+path)['data']['result']))
        check('Alertmanager',lambda:bool(request(urls['alertmanager']+'/api/v2/status')['versionInfo']))
        if 'grafana' in urls:
            def grafana(path): return request(urls['grafana']+path,basic='admin:'+password)
            check('Grafana database',lambda:request(urls['grafana']+'/api/health')['database']=='ok')
            check('Grafana provisioned dashboard',lambda:grafana('/api/dashboards/uid/convergence-installation')['dashboard']['title']=='Convergence installation health')
            check('Grafana datasource query',lambda:any(float(x['value'][1])==1 for x in grafana('/api/datasources/proxy/uid/convergence-metrics'+path)['data']['result']))
        if 'loki' in urls:
            check('Loki ready',lambda:str(request(urls['loki']+'/ready')).strip()=='ready')
        record=bundle/'verification-case.json'
        if record.exists():
            saved=json.loads(record.read_text())
            check('Previous canary persisted',lambda:api('/api/events/'+str(saved['id'])+'?site='+site)['incident_key']==saved['incident_key'])
        if canary:
            def case_canary():
                incident='setup-canary:'+str(uuid.uuid4())
                created=api('/api/events?site='+site,data={'message':'Installation verification — synthetic; no sensor event',
                    'source':'setup-canary','category':'system','severity':'info','status':'logged','incident_key':incident})
                saved=api('/api/events/'+str(created['id'])+'?site='+site)
                api('/api/events/'+str(created['id'])+'?site='+site,method='PATCH',data={'status':'archived'})
                if saved['incident_key']!=incident: return False
                env_writer(repo).write_private(record,json.dumps({'id':created['id'],'incident_key':incident})+'\n')
                return True
            check('Case create/read/archive',case_canary,retry=False)
            if 'collector' in urls:
                def logs_canary():
                    marker='convergence-setup-canary-'+str(uuid.uuid4())
                    request(urls['collector']+'/v1/logs',data={'resourceLogs':[{'resource':{'attributes':[
                        {'key':'service.name','value':{'stringValue':'convergence-setup-canary'}},
                        {'key':'site','value':{'stringValue':site}}]},'scopeLogs':[{'logRecords':[
                        {'timeUnixNano':str(time.time_ns()),'body':{'stringValue':marker}}]}]}]})
                    for _ in range(20):
                        found=request(urls['logs']+'/select/logsql/query?'+urlencode({'query':'_time:5m "'+marker+'"'}))
                        if marker in json.dumps(found): return True
                        time.sleep(1)
                    return False
                check('OTLP collector to VictoriaLogs',logs_canary,retry=False)
    return {'passed':all(c['status']=='pass' for c in checks),'checks':checks,
            'scope':'core installation; synthetic writes enabled' if canary else 'core installation; read-only HTTP checks',
            'unverified':['network telemetry','IDS capture and coverage','HUD/browser integration','receiver and model investigations']}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',default='.')
    p.add_argument('--bundle',required=True);p.add_argument('--canary',action='store_true')
    p.add_argument('--wait-seconds',type=int,default=60,help='Shared readiness retry budget for read-only checks (default: 60)')
    args=p.parse_args()
    try:
        result=verify(repo_path(args.repo),args.bundle,args.canary,args.wait_seconds)
        print(json.dumps(result,indent=2));return 0 if result['passed'] else 1
    except (ValueError,OSError,KeyError) as exc:
        print('Verification failed: '+(str(exc) if type(exc) is ValueError else type(exc).__name__),file=sys.stderr);return 2


if __name__=='__main__': sys.exit(main())

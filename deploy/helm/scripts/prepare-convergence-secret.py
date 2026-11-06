#!/usr/bin/env python3
"""Create independent Convergence credentials; never print or save tokens to values."""
import argparse,base64,json,secrets,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--namespace',required=True);p.add_argument('--name',default='convergence-secrets');p.add_argument('--copy-host-ids',action='store_true');p.add_argument('--site',action='append');args=p.parse_args()
def kubectl(*items,**kw):return subprocess.run(['kubectl',*items],check=True,**kw)
found=subprocess.run(['kubectl','get','secret',args.name,'-n',args.namespace,'--ignore-not-found','-o','name'],check=True,capture_output=True,text=True).stdout
if found.strip():print('Existing application Secret retained');raise SystemExit(0)
values={'PGUSER':'convergence','PGDATABASE':'convergence','PGPASSWORD':secrets.token_urlsafe(48),'JWT_SECRET':secrets.token_urlsafe(48),
        'API_KEYS':json.dumps([{'id':'helm-operator','name':'Convergence isolated operator','key':secrets.token_urlsafe(48),'role':'operator','sites':args.site or ['home']}]),'USERS':'[]', 'ids-ca.crt':''}
if args.copy_host_ids:
 root=Path(__file__).resolve().parents[3]/'deploy/convergence/secrets/ids'
 env=dict(line.split('=',1) for line in (root/'api.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
 for key in ('IDS_STATUS_URL','IDS_STATUS_TOKEN'):values[key]=env[key]
 values['ids-ca.crt']=(root/'ca.crt').read_text()
manifest={'apiVersion':'v1','kind':'Secret','metadata':{'name':args.name,'namespace':args.namespace},'type':'Opaque','data':{k:base64.b64encode(v.encode()).decode() for k,v in values.items()}}
kubectl('apply','-f','-',input=json.dumps(manifest),text=True,stdout=subprocess.DEVNULL)
print('Independent application Secret created; optional IDS access is read-only')

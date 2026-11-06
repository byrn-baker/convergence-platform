#!/usr/bin/env python3
"""Refresh Helm copies of canonical Convergence assets; --check detects drift."""
import argparse
from pathlib import Path
import yaml
root=Path(__file__).resolve().parents[3]
pairs={
 'deploy/convergence/speedtest/run.py':'deploy/helm/convergence/files/speedtest.py',
 'deploy/convergence/adapters/unifi/exporter.py':'deploy/helm/convergence/files/unifi-exporter.py',
 'deploy/convergence/loki/rules/fake/convergence-security.yml':'deploy/helm/convergence/files/loki-security.yml',
}
for source in (root/'deploy/convergence/grafana/provisioning/dashboards/json').glob('convergence-*.json'):
 pairs[str(source.relative_to(root))]='deploy/helm/convergence/files/dashboards/'+source.name
parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
for source,target in pairs.items():
 a,b=root/source,root/target
 if args.check:
  if not b.exists() or a.read_bytes()!=b.read_bytes():raise SystemExit('Helm asset drift: '+target)
 else:
  b.parent.mkdir(parents=True,exist_ok=True);b.write_bytes(a.read_bytes())
# The home preview reuses the parser but has no second SNMP poller and routes
# all writes to its own release. Keep that adaptation reproducible as it evolves.
profile_path=root/'deploy/helm/profiles/convergence-home.yaml'
if profile_path.exists():
 profile=yaml.safe_load(profile_path.read_text())
 collector=yaml.safe_load((root/'deploy/convergence/otel/otel-config.yaml').read_text())
 collector['receivers']={k:v for k,v in collector['receivers'].items() if not k.startswith('snmp/')}
 collector['receivers']['otlp']={'protocols':{'grpc':{'endpoint':'0.0.0.0:4317'},'http':{'endpoint':'0.0.0.0:4318'}}}
 collector['processors']={k:v for k,v in collector['processors'].items() if not k.startswith('resource/homeswitch')}
 collector['exporters'].pop('prometheusremotewrite/prometheus',None)
 for exporter in collector['exporters'].values():
  for key,value in list(exporter.items()):
   if isinstance(value,str):exporter[key]=value.replace('http://loki:3100','http://{{ .Release.Name }}-loki:3100').replace('http://victorialogs:9428','http://{{ .Release.Name }}-logs:9428').replace('http://victoriametrics:8428','http://{{ .Release.Name }}-metrics:8428')
 collector['extensions']['file_storage/otel']['directory']='/data'
 collector['service']['pipelines']={k:v for k,v in collector['service']['pipelines'].items() if not k.startswith('metrics/homeswitch')}
 collector['service']['pipelines']['logs/otlp']={'receivers':['otlp'],'processors':['memory_limiter','batch'],'exporters':['loki','otlphttp/victorialogs']}
 collector['service']['pipelines']['metrics/otlp']={'receivers':['otlp'],'processors':['memory_limiter','batch'],'exporters':['prometheusremotewrite/victoriametrics']}
 rules={p.name:yaml.safe_load(p.read_text()) for p in (root/'deploy/convergence/prometheus/alerts').glob('*.rules.yml') if p.name!='suzieq.rules.yml'}
 if args.check:
  if profile['victoria']['collector']['config']!=collector or profile['victoria']['alerting']['ruleFiles']!=rules:raise SystemExit('Helm collector/rule profile drift')
 else:
  profile['victoria']['collector']['config']=collector;profile['victoria']['alerting']['ruleFiles']=rules
  profile_path.write_text('# Parallel validation: fresh stores, read-only metric federation, quiet alerts.\n# Docker remains authoritative; no historical cases or logs have been migrated.\n'+yaml.safe_dump(profile,sort_keys=False))
print('Helm assets match canonical sources' if args.check else 'Updated Helm assets')

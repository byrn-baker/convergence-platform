import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
import yaml

ROOT=Path(__file__).resolve().parents[3]
HELM=os.environ.get('HELM_BIN',str(Path.home()/'.local/bin/helm'))
CHART=ROOT/'deploy/helm'

def render(chart,release='example',namespace='example',*args):
    p=subprocess.run([HELM,'template',release,str(CHART/chart),'-n',namespace,*args],capture_output=True,text=True,check=True)
    return [d for d in yaml.safe_load_all(p.stdout) if d]

def test_generic_stacks_do_not_collide_or_deploy_another_grafana():
    a=render('victoria-stack','alpha','project-a','--set','sharedGrafana.enabled=true')
    b=render('victoria-stack','beta','project-b','--set','sharedGrafana.enabled=true')
    def identities(objects,ns):return {(d['kind'],d['metadata'].get('namespace',ns),d['metadata']['name']) for d in objects}
    assert not identities(a,'project-a') & identities(b,'project-b')
    for objects in (a,b):
        assert not any(d['kind'] in ('ClusterRole','ClusterRoleBinding','Secret') for d in objects)
        assert not any(d['kind'] in ('Deployment','StatefulSet') and any('grafana/grafana' in c['image'] for c in d['spec']['template']['spec']['containers']) for d in objects)
        stores=[d for d in objects if d['kind']=='StatefulSet']
        assert len(stores)==5
        for store in stores:
            for volume in store['spec']['volumeClaimTemplates']:
                assert volume['spec']['storageClassName']=='longhorn'
        config=next(d for d in objects if d['kind']=='ConfigMap' and d['metadata']['name'].endswith('victoria-config'))
        assert yaml.safe_load(config['data']['alertmanager.yml'])['receivers']==[{'name':'quiet'}]
        bundle=next(d for d in objects if d['kind']=='ConfigMap' and d['metadata']['name'].endswith('grafana-bundle'))
        sources=json.loads(bundle['data']['datasources.json'])
        assert all(s['isDefault'] is False for s in sources)
        assert len({s['uid'] for s in sources})==2


def test_chart_rejects_missing_application_credentials():
    p=subprocess.run([HELM,'template','test',str(CHART/'convergence'),'--set','api.image.tag=test'],capture_output=True,text=True)
    assert p.returncode and 'existingSecret' in p.stderr


def registrar():
    spec=importlib.util.spec_from_file_location('registrar',CHART/'victoria-stack/files/register-grafana.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def test_shared_grafana_merge_preserves_defaults_and_is_idempotent():
    r=registrar();original={'apiVersion':1,'datasources':[{'uid':'prometheus','name':'Prometheus','isDefault':True,'url':'http://existing'}]}
    sources=[{'uid':'project-metrics','name':'My project / Metrics','isDefault':False,'url':'http://project'}]
    a=r.merge_sources(original,sources);b=r.merge_sources(a,sources)
    assert a==b and len(b['datasources'])==2
    assert b['datasources'][0]==original['datasources'][0]
    assert len(original['datasources'])==1
    with pytest.raises(ValueError):r.merge_sources(original,[{**sources[0],'name':'Prometheus'}])


def test_dashboard_copies_use_project_uids_without_mutating_existing_boards():
    r=registrar();raw={'uid':'convergence-ids','title':'IDS','panels':[{'datasource':{'type':'prometheus','uid':'victoriametrics-longterm'}}]}
    board=r.project_dashboard(raw,'abcd','namespace/release',{'victoriametrics-longterm':'project-metrics'})
    assert board['uid']=='abcd-convergence-ids' and board['panels'][0]['datasource']['uid']=='project-metrics'
    assert raw['uid']=='convergence-ids' and raw['panels'][0]['datasource']['uid']=='victoriametrics-longterm'


def test_canonical_assets_have_not_drifted():
    subprocess.run([sys.executable,str(CHART/'scripts/sync-assets.py'),'--check'],check=True,capture_output=True)


def test_complete_registrar_flow_is_idempotent_and_preserves_shared_grafana(monkeypatch):
    import copy
    r=registrar()
    objects=render('victoria-stack','alpha','project-a','--set','sharedGrafana.enabled=true')
    bundle=next(d for d in objects if d['kind']=='ConfigMap' and d['metadata']['name']=='alpha-grafana-bundle')
    uid=json.loads(bundle['data']['datasources.json'])[0]['uid'].removesuffix('-metrics')
    original={'uid':'prometheus','name':'Prometheus','url':'http://original','isDefault':True}
    class Fake:
        def __init__(self):
            self.maps={'grafana-datasources':{'metadata':{'resourceVersion':'1'},'data':{'datasources.yml':yaml.safe_dump({'apiVersion':1,'datasources':[original]})}},
                       'grafana-dashboards':{'metadata':{'resourceVersion':'1'},'data':{'original.json':'{"uid":"original"}'}}}
            self.restarts=0
        def request(self,path,method='GET',data=None):
            if 'labelSelector=' in path:return {'items':[bundle]}
            if '/deployments/' in path:
                self.restarts+=1;return {}
            name=path.rsplit('/',1)[-1]
            if method=='GET':return copy.deepcopy(self.maps[name])
            assert data['metadata']['resourceVersion']==self.maps[name]['metadata']['resourceVersion']
            self.maps[name]['data']=data['data'];self.maps[name]['metadata']['resourceVersion']=str(int(data['metadata']['resourceVersion'])+1)
            return copy.deepcopy(self.maps[name])
    fake=Fake();monkeypatch.setattr(r,'Kubernetes',lambda:fake)
    for key,value in {'RELEASE_NAMESPACE':'project-a','RELEASE_NAME':'alpha','STACK_UID':uid,'GRAFANA_NAMESPACE':'observability','DATASOURCE_CONFIGMAP':'grafana-datasources','DATASOURCE_KEY':'datasources.yml','DASHBOARD_CONFIGMAP':'grafana-dashboards','GRAFANA_DEPLOYMENT':'grafana'}.items():monkeypatch.setenv(key,value)
    r.main();r.main()
    assert fake.restarts==1
    sources=yaml.safe_load(fake.maps['grafana-datasources']['data']['datasources.yml'])['datasources']
    assert len(sources)==3 and sources[0]==original
    assert fake.maps['grafana-dashboards']['data']['original.json']=='{"uid":"original"}'


def test_offline_grafana_merge_preserves_existing_projects_and_is_idempotent():
    spec=importlib.util.spec_from_file_location('gitops_merge',CHART/'scripts/merge-grafana-gitops.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    objects=render('victoria-stack','alpha','project-a','--set','sharedGrafana.enabled=true','--set','sharedGrafana.registrationMode=external')
    existing={'uid':'existing','name':'Existing','isDefault':True,'url':'http://existing'}
    sources={'data':{'datasources.yml':yaml.safe_dump({'apiVersion':1,'datasources':[existing]})}}
    boards={'data':{'existing.json':'{"uid":"existing"}'}}
    module.merge(objects,'project-a','alpha',sources,boards)
    before=json.dumps([sources,boards],sort_keys=True)
    module.merge(objects,'project-a','alpha',sources,boards)
    assert json.dumps([sources,boards],sort_keys=True)==before
    assert yaml.safe_load(sources['data']['datasources.yml'])['datasources'][0]==existing
    assert boards['data']['existing.json']=='{"uid":"existing"}'
    with pytest.raises(ValueError):module.merge(objects,'wrong-namespace','alpha',sources,boards)


def test_portable_fresh_bundle_renders_optional_grafana_and_local_path(tmp_path):
    import sys
    scripts=ROOT/'setup'
    sys.path.insert(0,str(scripts))
    spec=importlib.util.spec_from_file_location('fresh_k3s',scripts/'k3s.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    bundle=tmp_path/'bundle'
    module.prepare(ROOT,bundle,{'context':'test','namespace':'office-obs','release':'office',
        'site':'office','storage_class':'local-path','image':'api:test','grafana':True})
    objects=render('convergence','office','office-obs','-f',str(bundle/'values.json'))
    assert not any(d['kind']=='Secret' for d in objects)
    config=next(d for d in objects if d['kind']=='ConfigMap' and d['metadata']['name']=='office-application-config')
    assert json.loads(config['data']['SITES_CONFIG'])=={'office':{'name':'office'}}
    grafana=next(d for d in objects if d['kind']=='StatefulSet' and d['metadata']['name']=='office-grafana')
    container=grafana['spec']['template']['spec']['containers'][0]
    env={e['name']:e for e in container['env']}
    assert env['GF_AUTH_ANONYMOUS_ENABLED']['value']=='false'
    assert env['GF_SECURITY_ADMIN_PASSWORD']['valueFrom']['secretKeyRef']['name']=='office-credentials'
    for obj in objects:
        if obj['kind']=='StatefulSet':
            assert obj['spec'].get('persistentVolumeClaimRetentionPolicy',{}).get('whenDeleted','Retain')=='Retain'
            assert all(v['spec']['storageClassName']=='local-path' for v in obj['spec']['volumeClaimTemplates'])
    config=next(d for d in objects if d['kind']=='ConfigMap' and d['metadata']['name']=='office-grafana')
    assert json.loads(config['data']['health.json'])['uid']=='convergence-installation'
    assert all(d['spec'].get('type','ClusterIP')=='ClusterIP' for d in objects if d['kind']=='Service')


def test_every_volume_claim_template_names_its_kind():
    """The API server adds apiVersion/kind; a template without them stays OutOfSync in Argo CD."""
    objects=render('convergence','convergence','convergence','--set','existingSecret=x','--set','api.image.tag=t',
                   '--set','grafana.enabled=true')
    claims=[c for o in objects if o and o.get('kind')=='StatefulSet' for c in o['spec'].get('volumeClaimTemplates',[])]
    assert claims and all(c.get('apiVersion')=='v1' and c.get('kind')=='PersistentVolumeClaim' for c in claims)

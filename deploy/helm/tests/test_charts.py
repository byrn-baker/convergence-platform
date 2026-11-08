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


def test_device_telemetry_values_render_syslog_and_snmp_for_this_site(tmp_path):
    out=tmp_path/'device.json'
    subprocess.run([sys.executable,str(CHART/'scripts/render-device-telemetry.py'),'--site','lab',
        '--device','edge-fw01=192.0.2.1','--snmp','access-sw01=192.0.2.2','--snmp-secret','lab-snmp',
        '--collector-node','node-a','--output',str(out)],check=True)
    objects=render('convergence','qual','convergence','--set','existingSecret=x','--set','api.image.tag=t','-f',str(out))
    config=[o for o in objects if o['kind']=='ConfigMap' and 'collector.yml' in o.get('data',{})][0]
    collector=yaml.safe_load(config['data']['collector.yml'])
    assert {'syslog/devices','syslog/devices_tcp','snmp/access-sw01','otlp'}<=set(collector['receivers'])
    assert collector['exporters']['loki']['endpoint']=='http://qual-loki:3100/loki/api/v1/push'
    assert {'action':'upsert','key':'site','value':'lab'} in collector['processors']['resource/devices']['attributes']
    statements=' '.join(collector['processors']['transform/device_identity']['log_statements'][0]['statements'])
    assert '"edge-fw01") where attributes["net.peer.ip"] == "192.0.2.1"' in statements and 'access-sw' in statements
    assert '192.168.' not in config['data']['collector.yml']
    assert collector['receivers']['snmp/access-sw01']['community']=='${env:SNMP_COMMUNITY}'
    service=[o for o in objects if o['kind']=='Service' and o['metadata']['name']=='qual-collector'][0]
    ports={(p['name'],p.get('protocol','TCP'),p.get('nodePort')) for p in service['spec']['ports']}
    assert {('syslog-udp','UDP',31514),('syslog-tcp','TCP',31515)}<=ports
    assert service['spec']['externalTrafficPolicy']=='Local'
    workload=[o for o in objects if o['kind'] in ('StatefulSet','Deployment') and o['metadata']['name']=='qual-collector'][0]
    assert workload['spec']['template']['spec']['containers'][0]['envFrom']==[{'secretRef':{'name':'lab-snmp'}}]
    assert workload['spec']['template']['spec']['nodeSelector']=={'kubernetes.io/hostname':'node-a'}
    other=[o for o in objects if o['kind'] in ('StatefulSet','Deployment') and o['metadata']['name']=='qual-agent'][0]
    assert 'nodeSelector' not in other['spec']['template']['spec']


def test_device_telemetry_refuses_snmp_without_a_secret_and_bad_names(tmp_path):
    script=str(CHART/'scripts/render-device-telemetry.py')
    for args in (['--snmp','sw=192.0.2.2'],['--device','Bad_Name=192.0.2.1'],['--device','fw=not-an-ip']):
        result=subprocess.run([sys.executable,script,'--site','lab',*args,'--output',str(tmp_path/'x.json')],capture_output=True)
        assert result.returncode==2


QUOTA=['--set','agentMetrics.quota.enabled=true','--set','agentMetrics.quota.modelRegex=deepseek.*',
       '--set','agentMetrics.quota.weeklyTokenBudget=200000000']


def quota_objects(*args,release='quota'):
    return render('convergence',release,'quota','--set','existingSecret=x','--set','api.image.tag=t',*args)


def quota_rules(objects):
    """Every rule file vmalert loads: the stack's own ConfigMap plus the agent metrics one, if rendered."""
    files={}
    for o in objects:
        if o['kind']=='ConfigMap' and o['metadata']['name'] in ('quota-victoria-rules','quota-agent-metrics-rules'):
            files.update({name:yaml.safe_load(text) for name,text in o['data'].items()})
    return files


def rule_list(rule_file):
    return [r for g in rule_file['groups'] for r in g['rules']]


def test_quota_rules_are_off_by_default_and_vmalert_mounts_them_optionally():
    objects=quota_objects()
    assert set(quota_rules(objects))=={'stack.yml'}
    assert 'netclaw:quota' not in json.dumps(objects)
    vmalert=next(o for o in objects if o['kind']=='Deployment' and o['metadata']['name']=='quota-vmalert')
    pod=vmalert['spec']['template']['spec']
    sources=next(v for v in pod['volumes'] if v['name']=='rules')['projected']['sources']
    assert sources==[{'configMap':{'name':'quota-victoria-rules'}},
                     {'configMap':{'name':'quota-agent-metrics-rules','optional':True}}]
    assert '-configCheckInterval=1m' in pod['containers'][0]['args']


def test_a_plain_victoria_stack_keeps_its_single_rules_configmap():
    objects=render('victoria-stack','plain','plain')
    pod=next(o for o in objects if o['kind']=='Deployment' and o['metadata']['name']=='plain-vmalert')['spec']['template']['spec']
    assert next(v for v in pod['volumes'] if v['name']=='rules')=={'name':'rules','configMap':{'name':'plain-victoria-rules'}}
    assert not any(a.startswith('-configCheckInterval') for a in pod['containers'][0]['args'])


@pytest.mark.parametrize('settings,message',[
    ([],'agentMetrics.quota.modelRegex'),
    (['--set','agentMetrics.quota.modelRegex=deepseek.*'],'agentMetrics.quota.weeklyTokenBudget'),
    (['--set','agentMetrics.quota.modelRegex=deepseek.*','--set','agentMetrics.quota.weeklyTokenBudget=1',
      '--set','agentMetrics.quota.burnCriticalRatio=1.5'],'burnCriticalRatio'),
    (['--set','agentMetrics.quota.modelRegex=deepseek.*','--set','agentMetrics.quota.weeklyTokenBudget=lots'],
     'agentMetrics.quota.weeklyTokenBudget'),
])
def test_enabling_quota_without_its_settings_fails_the_render(settings,message):
    p=subprocess.run([HELM,'template','quota',str(CHART/'convergence'),'--set','existingSecret=x','--set','api.image.tag=t',
                      '--set','agentMetrics.quota.enabled=true',*settings],capture_output=True,text=True)
    assert p.returncode and message in p.stderr


def test_enabled_quota_rules_read_the_exporter_metrics_with_the_operator_settings():
    rules=quota_rules(quota_objects(*QUOTA,'--set','agentMetrics.quota.projectionWarnRatio=0.75'))
    assert set(rules)=={'stack.yml','netclaw-quota.yml'}
    quota=rule_list(rules['netclaw-quota.yml'])
    alerts={r['alert']:r for r in quota if 'alert' in r}
    assert set(alerts)=={'NetclawQuotaProjectionHigh','NetclawQuotaBurn7dCritical'}
    assert all(a['labels']['investigate']=='false' and a['labels']['service']=='netclaw' for a in alerts.values())
    records={r['record']:r['expr'] for r in quota if 'record' in r}
    import re
    used={name for r in alerts.values() for name in re.findall(r'netclaw:[a-z0-9_]+',r['expr'])}
    assert used<=set(records)
    assert records['netclaw:quota_tokens_7d']=='sum(increase({__name__=~"netclaw_model_(input|output)_tokens_total", model=~"deepseek.*"}[7d]))'
    assert records['netclaw:quota_weekly_token_budget']=='vector(200000000)'
    assert '0.75 * netclaw:quota_weekly_token_budget' in alerts['NetclawQuotaProjectionHigh']['expr']
    assert '75%' in alerts['NetclawQuotaProjectionHigh']['annotations']['summary']
    assert all('on()' in a['expr'] for a in alerts.values())


def test_quota_model_regex_is_quoted_for_promql(tmp_path):
    values=tmp_path/'regex.json'
    values.write_text(json.dumps({'agentMetrics':{'quota':{'enabled':True,'modelRegex':'a"b\\.c','weeklyTokenBudget':5}}}))
    rules=quota_rules(quota_objects('-f',str(values)))
    expr={r.get('record'):r['expr'] for r in rule_list(rules['netclaw-quota.yml'])}['netclaw:quota_tokens_1d']
    assert 'model=~"a\\"b\\\\.c"' in expr


def test_quota_regex_braces_are_literal_in_annotations_but_not_in_promql(tmp_path):
    values=tmp_path/'braces.json'
    values.write_text(json.dumps({'agentMetrics':{'quota':{'enabled':True,'modelRegex':'deepseek.*|z{{x}}','weeklyTokenBudget':5}}}))
    rules=rule_list(quota_rules(quota_objects('-f',str(values)))['netclaw-quota.yml'])
    assert 'model=~"deepseek.*|z{{x}}"' in next(r['expr'] for r in rules if r.get('record')=='netclaw:quota_tokens_1d')
    for alert in (r for r in rules if 'alert' in r):
        assert '(deepseek.*|z{{ "{{" }}x{{ "}}" }})' in alert['annotations']['description']


@pytest.mark.parametrize('value,ok',[('0.75',True),('1',True),('lots',False),('0',False)])
def test_quota_ratios_given_with_set_string(value,ok):
    p=subprocess.run([HELM,'template','quota',str(CHART/'convergence'),'--set','existingSecret=x','--set','api.image.tag=t',
                      *QUOTA,'--set-string','agentMetrics.quota.projectionWarnRatio='+value,
                      '--set-string','agentMetrics.quota.weeklyTokenBudget=200000000'],capture_output=True,text=True)
    if ok:
        rules=quota_rules([d for d in yaml.safe_load_all(p.stdout) if d])
        alert=next(r for r in rule_list(rules['netclaw-quota.yml']) if r.get('alert')=='NetclawQuotaProjectionHigh')
        assert f'({float(value):g} * netclaw:quota_weekly_token_budget)' in alert['expr']
    else:
        assert p.returncode and 'between 0 and 1' in p.stderr


def test_k3s_bundle_with_a_receiver_mounts_and_enables_quota_rules(tmp_path):
    import sys
    sys.path.insert(0,str(ROOT/'setup'))
    spec=importlib.util.spec_from_file_location('quota_k3s',ROOT/'setup/k3s.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    bundle=tmp_path/'bundle'
    module.prepare(ROOT,bundle,{'context':'test','namespace':'quota','release':'quota','site':'lab',
        'storage_class':'local-path','image':'api:test','grafana':False,'receiver_url':'http://192.0.2.10:8099'})
    objects=quota_objects('-f',str(bundle/'values.json'))
    assert set(quota_rules(objects))=={'stack.yml'}
    vmalert=next(o for o in objects if o['kind']=='Deployment' and o['metadata']['name']=='quota-vmalert')
    assert 'quota-agent-metrics-rules' in json.dumps(vmalert)
    assert 'netclaw-quota.yml' in quota_rules(quota_objects('-f',str(bundle/'values.json'),*QUOTA))


@pytest.mark.skipif(not os.environ.get('PROMTOOL_BIN'),reason='set PROMTOOL_BIN to evaluate the rules with promtool')
def test_quota_alerts_fire_on_metered_burn_only(tmp_path):
    # Braces in the regex also check that the annotations expand to the literal regex.
    values=tmp_path/'quota.json'
    values.write_text(json.dumps({'agentMetrics':{'quota':{'enabled':True,'modelRegex':'deepseek.*|z{{x}}','weeklyTokenBudget':200000000}}}))
    rules=quota_rules(quota_objects('-f',str(values)))
    (tmp_path/'netclaw-quota.yml').write_text(yaml.safe_dump(rules['netclaw-quota.yml']))
    alerts={r['alert']:r for r in rule_list(rules['netclaw-quota.yml']) if 'alert' in r}
    def expected(name):
        shown={k:v.replace('{{ "{{" }}','{{').replace('{{ "}}" }}','}}') for k,v in alerts[name]['annotations'].items()}
        assert '(deepseek.*|z{{x}})' in shown['description']
        return {'exp_labels':alerts[name]['labels'],'exp_annotations':shown}
    # 500k tokens per 10 minutes: 72M a day (504M projected a week), 300M over 100 hours; budget 200M.
    metered='netclaw_model_input_tokens_total{model="deepseek-v3.1:671b",provider="ollama",agent="main"}'
    local='netclaw_model_output_tokens_total{model="qwen3:32b",provider="ollama",agent="main"}'
    tests={'evaluation_interval':'1m','rule_files':['netclaw-quota.yml'],'tests':[
        {'interval':'10m','input_series':[{'series':metered,'values':'0+500000x600'},{'series':local,'values':'0+9000000x600'}],
         'alert_rule_test':[
            # 3M an hour: the 1-day increase passes 160M/7 after ~7.6 hours, then 30 minutes pending.
            {'alertname':'NetclawQuotaProjectionHigh','eval_time':'2h','exp_alerts':[]},
            {'alertname':'NetclawQuotaProjectionHigh','eval_time':'9h','exp_alerts':[expected('NetclawQuotaProjectionHigh')]},
            {'alertname':'NetclawQuotaBurn7dCritical','eval_time':'24h','exp_alerts':[]},
            {'alertname':'NetclawQuotaBurn7dCritical','eval_time':'100h','exp_alerts':[expected('NetclawQuotaBurn7dCritical')]}]},
        {'interval':'10m','input_series':[{'series':local,'values':'0+9000000x600'}],
         'alert_rule_test':[{'alertname':n,'eval_time':'100h','exp_alerts':[]} for n in alerts]}]}
    (tmp_path/'tests.yml').write_text(yaml.safe_dump(tests))
    result=subprocess.run([*os.environ['PROMTOOL_BIN'].split(),'test','rules','tests.yml'],cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr

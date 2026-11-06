"""Fresh setup is portable, preserves secrets, and checks without mutations."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT/'setup'
spec = importlib.util.spec_from_file_location('convergence_setup', SETUP/'setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path/'different account'/'netclaw checkout'
    (root/'scripts').mkdir(parents=True)
    (root/'deploy/convergence').mkdir(parents=True)
    shutil.copy2(ROOT/'scripts/write-env.py', root/'scripts/write-env.py')
    shutil.copy2(ROOT/'deploy/convergence/docker-compose.yml', root/'deploy/convergence/docker-compose.yml')
    return root


def options(**changes):
    return dict(site='cottage', project='convergence-cottage-test', edge_url='', api_port=23080,
                prometheus_port=29090, alertmanager_port=29093, blackbox_port=29115,
                receiver_port=28099, **changes)


def test_fresh_bundle_has_matched_unique_credentials_and_only_selected_targets(repo):
    bundle = repo/'deployment with spaces'
    # A user's private file must neither execute nor contaminate generated config.
    marker = repo/'must-not-exist'
    (repo/'.env').write_text(f'ALERT_RECEIVER_TOKEN=existing-private-token\nEVIL=$(touch "{marker}")\n')
    result = setup.prepare(repo, bundle, options())
    assert result['status']=='prepared' and not marker.exists()
    writer = setup.env_writer(repo)
    stack, receiver, hud = [writer.values((bundle/file).read_text()) for file in ('compose.env', 'receiver.env', 'hud.env')]
    key = json.loads(stack['API_KEYS'])[0]
    assert key['key']==receiver['NETWORK_GUARDIAN_TOKEN']==hud['CONVERGENCE_API_TOKEN']
    assert key['sites']==['cottage'] and key['role']=='operator'
    assert stack['ALERT_RECEIVER_TOKEN']==receiver['ALERT_RECEIVER_TOKEN']==(bundle/'receiver-token').read_text()
    assert len({stack['PGPASSWORD'], stack['JWT_SECRET'], key['key'], stack['ALERT_RECEIVER_TOKEN']})==4
    assert len(key['key'])==64
    serialized = '\n'.join(p.read_text() for p in bundle.iterdir())
    for forbidden in ('192.168.', 'byrnbaker', 'HomeSwitch', '/home/ubuntu', 'existing-private-token'):
        assert forbidden not in serialized
    public = json.dumps(result)
    for secret in (key['key'], stack['ALERT_RECEIVER_TOKEN'], stack['PGPASSWORD'], stack['JWT_SECRET']):
        assert secret not in public
    assert bundle.stat().st_mode & 0o777 == 0o700
    assert (bundle/'compose.env').stat().st_mode & 0o777 == 0o600
    assert (bundle/'receiver-token').stat().st_mode & 0o777 == 0o644
    assert (bundle/'.gitignore').read_text()=='*\n'
    assert json.loads((bundle/'investigation-policy.yaml').read_text())['allow_t2']==[]
    jobs = json.loads((bundle/'prometheus.yml').read_text())['scrape_configs']
    assert {j['job_name'] for j in jobs}=={'prometheus','convergence-api'}
    assert all(j['static_configs'][0]['labels']['site']=='cottage' for j in jobs)
    overlay=json.loads((bundle/'compose.override.json').read_text())
    assert overlay['services']['convergence-api']['environment']['PFSENSE_MGMT_URL']==''
    assert overlay['networks']['convergence']['name']=='convergence-cottage-test-convergence'


def test_rerun_preserves_credentials_and_operator_edits(repo):
    bundle = repo/'bundle'
    setup.prepare(repo, bundle, options())
    edited = bundle/'receiver.env'; edited.write_text(edited.read_text()+'CUSTOM=preserved\n')
    before = {p.name:p.read_bytes() for p in bundle.iterdir()}
    assert setup.prepare(repo, bundle, options())['status']=='preserved'
    assert {p.name:p.read_bytes() for p in bundle.iterdir()}==before
    with pytest.raises(ValueError, match='differs'):
        setup.prepare(repo, bundle, {**options(), 'api_port': 23081})
    assert {p.name:p.read_bytes() for p in bundle.iterdir()}==before


def test_unrelated_directory_and_symlinks_are_not_overwritten(repo, tmp_path):
    output = tmp_path/'operator-files';output.mkdir();(output/'keep').write_text('original')
    with pytest.raises(ValueError, match='without a deployment manifest'):
        setup.prepare(repo,output,options())
    linked=tmp_path/'link';linked.symlink_to(output,target_is_directory=True)
    with pytest.raises(ValueError, match='symlinks'):setup.prepare(repo,linked/'nested',options())
    assert (output/'keep').read_text()=='original' and not (output/'nested').exists()


def test_incomplete_bundle_does_not_report_success_or_replace_credentials(repo):
    bundle=repo/'bundle';setup.prepare(repo,bundle,options())
    original=(bundle/'compose.env').read_bytes()
    (bundle/'receiver-token').unlink()
    with pytest.raises(ValueError,match='incomplete'):
        setup.prepare(repo,bundle,options())
    assert (bundle/'compose.env').read_bytes()==original


@pytest.mark.parametrize('change', [
    {'site':'../../bad'}, {'project':'bad;command'}, {'api_port':80}, {'api_port':29090},
    {'edge_url':'https://user:private-key@example.test'}, {'edge_url':'file:///etc/passwd'},
    {'edge_url':'https://example.test/?token=secret'},
    {'edge_url':'https://example.test:invalid'},
])
def test_bad_options_do_not_create_files(repo, change):
    with pytest.raises(ValueError):setup.prepare(repo,repo/'bundle',{**options(),**change})
    assert not (repo/'bundle').exists()


def test_explicit_edge_is_the_only_device_target(repo):
    bundle=repo/'bundle';setup.prepare(repo,bundle,{**options(),'edge_url':'https://192.0.2.1:4443'})
    config=json.loads((bundle/'prometheus.yml').read_text())
    edge=[j for j in config['scrape_configs'] if j['job_name']=='blackbox_edge']
    assert edge[0]['static_configs'][0]['targets']==['https://192.0.2.1:4443']


def test_checker_is_read_only_and_requires_database_and_scoped_access(repo, monkeypatch):
    bundle=repo/'bundle';setup.prepare(repo,bundle,options())
    before={p.name:p.read_bytes() for p in bundle.iterdir()}
    requests=[]
    def get(url, token=None):
        requests.append((url,token))
        if '/healthz' in url:return {'features':{'diary':'available'}}
        if '/api/events' in url:return {'events':[]}
        if '/targets' in url:return {'status':'success','data':{'activeTargets':[
            {'health':'up','labels':{'job':job,'site':'cottage'}} for job in ('prometheus','convergence-api')]}}
        if '/query?' in url:return {'status':'success','data':{'result':[{'value':[0,'1']}]}}
        if '/api/v2/status' in url:return {'versionInfo':{'version':'test'}}
        if url.endswith('/health'):return {'status':'ok'}
        pytest.fail('Unexpected endpoint')
    monkeypatch.setattr(setup,'get_json',get)
    monkeypatch.setattr(setup.subprocess,'run',lambda *_a,**_k:pytest.fail('check must not execute commands'))
    result=setup.check(repo,bundle,True)
    assert result['passed'] and len(result['checks'])==6
    assert any('site=cottage' in url and token for url,token in requests)
    assert {p.name:p.read_bytes() for p in bundle.iterdir()}==before
    def unavailable(url, token=None):
        if '/healthz' in url:return {'features':{'diary':'unavailable'}}
        return get(url,token)
    monkeypatch.setattr(setup,'get_json',unavailable)
    assert not setup.check(repo,bundle)['passed']
    def broken_probe(url, token=None):
        if '/query?' in url:return {'status':'success','data':{'result':[{'value':[0,'0']}]}}
        return get(url,token)
    monkeypatch.setattr(setup,'get_json',broken_probe)
    assert not setup.check(repo,bundle)['passed']
    def other_site(url, token=None):
        if '/targets' in url:return {'status':'success','data':{'activeTargets':[
            {'health':'up','labels':{'job':job,'site':'another-home'}} for job in ('prometheus','convergence-api')]}}
        return get(url,token)
    monkeypatch.setattr(setup,'get_json',other_site)
    assert not setup.check(repo,bundle)['passed']
    monkeypatch.setattr(setup,'get_json',lambda *_:(_ for _ in ()).throw(RuntimeError('sensitive response')))
    result=setup.check(repo,bundle)
    assert not result['passed'] and 'sensitive response' not in json.dumps(result)


def test_preflight_reports_ports_without_reading_secrets_or_starting_services(repo, monkeypatch):
    calls=[]
    def run(args):
        calls.append(args)
        return 'LISTEN 0 511 0.0.0.0:3000 0.0.0.0:*\n' if args[0]=='ss' else None
    monkeypatch.setattr(setup,'output',run)
    result=setup.preflight(repo)
    assert result['listening_tcp_ports']==[3000]
    assert result['docker_server'] is None
    assert calls==[['ss','-H','-ltn'],['docker','version','--format','{{.Server.Version}}'],
                   ['docker','compose','version','--short'],['docker','context','show']]


def test_optional_dashboard_has_private_credentials_and_site_scoped_queries(repo):
    bundle=repo/'dashboard bundle'
    result=setup.prepare(repo,bundle,options(grafana=True,grafana_port=23300))
    overlay=json.loads((bundle/'compose.override.json').read_text())
    grafana=overlay['services']['grafana']
    assert grafana['ports']==['127.0.0.1:23300:3000']
    assert grafana['environment']['GF_AUTH_ANONYMOUS_ENABLED']=='false'
    assert grafana['environment']['GF_SECURITY_ADMIN_PASSWORD'] not in json.dumps(result)
    dashboard=json.loads((bundle/'grafana-health.json').read_text())
    assert 'cottage' in json.dumps(dashboard)
    assert 'Grafana' in (bundle/'access.txt').read_text()
    assert setup.prepare(repo,bundle,options(grafana=True,grafana_port=23300))['status']=='preserved'


def test_compose_environment_drops_conflicting_interpolation_and_profiles(repo,monkeypatch):
    monkeypatch.setenv('PGPASSWORD','wrong')
    monkeypatch.setenv('API_KEYS','wrong')
    monkeypatch.setenv('COMPOSE_PROFILES','full,unifi')
    monkeypatch.setenv('DOCKER_CONTEXT','desktop-linux')
    env=setup.compose_environment(repo)
    assert 'PGPASSWORD' not in env and 'API_KEYS' not in env and 'COMPOSE_PROFILES' not in env
    assert env['DOCKER_CONTEXT']=='desktop-linux' and 'HOME' in env

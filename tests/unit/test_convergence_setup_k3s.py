"""K3s bundles pin a target and preserve credentials before any cluster mutation."""
import importlib.util
import json
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT/'workspace/skills/convergence-setup/scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('convergence_k3s', SCRIPTS/'k3s.py')
k3s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(k3s)


def options(**changes):
    return dict(context='lab-context', namespace='observability-trial', release='trial',
                site='cottage', storage_class='local-path', image='convergence-api:test-123',
                grafana=True, **changes)


def test_fresh_and_repeat_preserve_secrets_and_explicit_target(tmp_path):
    bundle = tmp_path/'private bundle'
    result = k3s.prepare(ROOT, bundle, options())
    secret = json.loads((bundle/'secret.json').read_text())
    values = json.loads((bundle/'values.json').read_text())
    assert secret['metadata']['namespace']=='observability-trial'
    assert json.loads(secret['stringData']['API_KEYS'])[0]['sites']==['cottage']
    assert values['siteConfig']=={'home': None, 'cottage': {'name':'cottage'}}
    assert values['postgres']['storage']['className']=='local-path'
    assert values['victoria']['metrics']['server']['persistentVolume']['storageClassName']=='local-path'
    for key in ('PGPASSWORD', 'JWT_SECRET', 'GRAFANA_ADMIN_PASSWORD'):
        assert secret['stringData'][key] not in json.dumps(result)+json.dumps(values)
    assert (bundle/'secret.json').stat().st_mode & 0o777 == 0o600
    assert bundle.stat().st_mode & 0o777 == 0o700
    before = {p.name:p.read_bytes() for p in bundle.iterdir()}
    assert k3s.prepare(ROOT, bundle, options())['status']=='preserved'
    assert before=={p.name:p.read_bytes() for p in bundle.iterdir()}
    with pytest.raises(ValueError, match='differs'):
        k3s.prepare(ROOT, bundle, {**options(), 'context':'wrong-cluster'})


@pytest.mark.parametrize('change', [{'namespace':'Bad Namespace'}, {'release':'x'*54},
    {'site':'../bad'}, {'storage_class':''}, {'image':'api:latest'}, {'image':'api'}, {'context':''}])
def test_bad_options_do_not_create_files(tmp_path, change):
    with pytest.raises(ValueError):
        k3s.prepare(ROOT, tmp_path/'bundle', {**options(), **change})
    assert not (tmp_path/'bundle').exists()


def test_incomplete_bundle_and_foreign_secret_are_rejected(tmp_path):
    bundle = tmp_path/'bundle'
    k3s.prepare(ROOT, bundle, options())
    desired = json.loads((bundle/'secret.json').read_text())
    with pytest.raises(ValueError, match='credentials'):
        k3s.require_matching_secret(desired, {'data':{'PGPASSWORD':'d3Jvbmc='}})
    (bundle/'secret.json').unlink()
    with pytest.raises(ValueError, match='incomplete'):
        k3s.prepare(ROOT, bundle, options())


def test_install_never_overwrites_foreign_credentials(tmp_path, monkeypatch):
    bundle=tmp_path/'bundle';k3s.prepare(ROOT,bundle,options())
    monkeypatch.setattr(k3s,'preflight',lambda _:{'storage_classes':['local-path'],'nodes':[{'ready':True}]})
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        if 'namespace' in command:return 'namespace/observability-trial'
        if 'secret' in command:return json.dumps({'data':{'PGPASSWORD':'d3Jvbmc='}})
        pytest.fail('Must stop before mutation')
    monkeypatch.setattr(k3s,'run',run)
    with pytest.raises(ValueError,match='credentials'):k3s.install(ROOT,bundle)
    assert all(c[:5]==['kubectl','--context','lab-context','--namespace','observability-trial'] for c in calls)
    assert not any('create' in c or 'upgrade' in c for c in calls)

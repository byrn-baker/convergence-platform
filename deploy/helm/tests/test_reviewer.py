import os
from pathlib import Path
import subprocess

import pytest
import yaml

HELM = os.environ.get('HELM_BIN', str(Path.home() / '.local/bin/helm'))
CHART = Path(__file__).resolve().parents[1] / 'convergence'
REVIEW = {
    'enabled': True,
    'apiImage': 'registry.example.test/convergence-api:v1',
    'hudImage': 'registry.example.test/convergence-hud@sha256:' + 'b' * 64,
    'publicOrigin': 'https://cases.example.test:8443',
    'databaseSecret': 'review-db',
    'serviceTlsSecret': 'review-service',
    'installationTlsSecret': 'review-client',
    'browserTlsSecret': 'review-browser',
}


def render(tmp_path, reviewer, release='case-demo', namespace='ops-example', check=True):
    values = {
        'existingSecret': 'ordinary-app', 'api': {'image': {'tag': 'existing'}},
        'reviewer': reviewer, 'imagePullSecrets': [{'name': 'pull-images'}],
        'nodeSelector': {'kubernetes.io/os': 'linux'},
    }
    config = tmp_path / 'values.yaml'
    config.write_text(yaml.safe_dump(values))
    result = subprocess.run(
        [HELM, 'template', release, str(CHART), '--namespace', namespace, '--values', str(config)],
        capture_output=True, text=True, check=check,
    )
    return [item for item in yaml.safe_load_all(result.stdout) if item] if check else result


def test_reviewer_disabled_does_not_add_resources(tmp_path):
    # Incomplete enrollments do not affect a stock rendering.
    objects = render(tmp_path, {'enabled': False})
    assert not any('-review-' in obj['metadata']['name'] for obj in objects)
    assert all(obj['kind'] != 'Ingress' for obj in objects)


@pytest.mark.parametrize(('release', 'namespace'), [('alpha', 'team-east'), ('beta', 'team-west')])
def test_reviewer_only_mounts_each_workloads_credentials(tmp_path, release, namespace):
    docs = render(tmp_path, REVIEW, release, namespace)
    review = {(obj['kind'], obj['metadata']['name']): obj for obj in docs if '-review-' in obj['metadata']['name']}
    api, hud = release + '-review-api', release + '-review-hud'
    assert set(review) == {('Service', api), ('Service', hud), ('Deployment', api), ('Deployment', hud), ('NetworkPolicy', api)}
    for name, port in [(api, 3443), (hud, 3444)]:
        service = review['Service', name]['spec']
        deployment = review['Deployment', name]['spec']
        assert service['type'] == 'ClusterIP'
        assert service['ports'] == [{'name': 'https', 'port': port, 'targetPort': 'https'}]
        assert service['selector'] == deployment['selector']['matchLabels'] == deployment['template']['metadata']['labels']
        pod = deployment['template']['spec']
        assert pod['automountServiceAccountToken'] is False
        assert pod['securityContext']['runAsNonRoot'] and pod['securityContext']['seccompProfile']['type'] == 'RuntimeDefault'
        assert pod['imagePullSecrets'] == [{'name': 'pull-images'}]
        assert pod['nodeSelector'] == {'kubernetes.io/os': 'linux'}
        assert not pod.get('initContainers') and len(pod['containers']) == 1
        c = pod['containers'][0]
        assert c['ports'] == [{'name': 'https', 'containerPort': port}]
        assert c['securityContext'] == {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}
        assert all(m['readOnly'] is True for m in c['volumeMounts'])
        assert not c.get('envFrom')
        env = {v['name']: v for v in c['env']}
        volumes = {v['secret']['secretName']: {i['key'] for i in v['secret']['items']} for v in pod['volumes']}
        mounted = {m['name']: m['mountPath'] for m in c['volumeMounts']}
        for v in pod['volumes']:
            assert v['secret']['defaultMode'] == 0o440
            for item in v['secret']['items']:
                assert mounted[v['name']] + '/' + item['path'] in [e.get('value') for e in env.values()]
        if name == api:
            assert c['image'] == REVIEW['apiImage']
            assert c['command'] == ['node', 'reviewer-service.js']
            keys = {k: e['valueFrom']['secretKeyRef'] for k, e in env.items() if 'valueFrom' in e}
            assert keys == {k: {'name': 'review-db', 'key': k} for k in ['PGHOST','PGPORT','PGUSER','PGDATABASE','PGPASSWORD']}
            assert volumes == {'review-service': {'tls.crt','tls.key','ca.crt','enrolled.json'}}
            assert 'reviewer_grants' in c['readinessProbe']['exec']['command'][-1]
        else:
            assert deployment['replicas'] == 1 and deployment['strategy'] == {'type': 'Recreate'}
            assert c['image'] == REVIEW['hudImage']
            assert c['command'] == ['node', 'reviewer/start.mjs']
            assert env['REVIEWER_HUD_ORIGIN']['value'] == REVIEW['publicOrigin']
            assert env['REVIEWER_SERVICE_ORIGIN']['value'] == f'https://{api}.{namespace}.svc:3443'
            assert volumes == {'review-browser': {'tls.crt','tls.key'}, 'review-client': {'tls.crt','tls.key','ca.crt'}}
            assert not any('valueFrom' in e for e in env.values())
            assert c['readinessProbe']['httpGet'] == {'scheme':'HTTPS','path':'/','port':'https',
                'httpHeaders':[{'name':'Host','value':'cases.example.test:8443'}]}
    policy = review['NetworkPolicy', api]['spec']
    assert policy['policyTypes'] == ['Ingress']
    assert policy['podSelector']['matchLabels'] == review['Service', api]['spec']['selector']
    assert policy['ingress'] == [{
        'from':[{'podSelector':{'matchLabels':review['Service', hud]['spec']['selector']}}],
        'ports':[{'protocol':'TCP','port':3443}],
    }]


@pytest.mark.parametrize('invalid', [
    {'publicOrigin': 'http://case.example'}, {'publicOrigin': 'https://case.example/login'},
    {'publicOrigin': 'https://case.example:84430'}, {'publicOrigin': 'https://case.example:443'},
    {'publicOrigin': 'https://127.1'}, {'publicOrigin': 'https://0x7f.1'},
    {'publicOrigin': 'https://999.999.999.999'}, {'publicOrigin': 'https://' + 'a' * 64 + '.test'},
    {'publicOrigin': 'https://' + '.'.join(['a' * 63] * 4)},
    {'databaseSecret': ''}, {'serviceTlsSecret': 'Not_valid'}, {'browserTlsSecret': 'review-client'},
    {'serviceTlsSecret': 'ordinary-app'}, {'apiImage': 'no-tag'}, {'hudImage': 'registry/image:latest'},
])
def test_enabled_reviewer_rejects_invalid_configuration(tmp_path, invalid):
    result = render(tmp_path, {**REVIEW, **invalid}, check=False)
    assert result.returncode and ('reviewer' in result.stderr.lower() or 'Reviewer' in result.stderr)


RELAY = {'enabled': True, 'databaseSecret': 'relay-db', 'deliverySecret': 'relay-delivery',
         'receiverOrigin': 'https://192.0.2.4:8446'}


def test_investigation_acceptance_and_delivery_can_be_stopped_separately(tmp_path):
    for accept in (False, True):
        docs = render(tmp_path, {**REVIEW, 'investigationEnabled':accept, 'relay':RELAY})
        api = next(d for d in docs if d['kind']=='Deployment' and d['metadata']['name'].endswith('-review-api'))
        env = {e['name']:e for e in api['spec']['template']['spec']['containers'][0]['env']}
        assert env['REVIEWER_INVESTIGATION_ENABLED']['value'] == ('1' if accept else '0')
        relay = next(d for d in docs if d['kind']=='Deployment' and d['metadata']['name'].endswith('-review-relay'))
        pod = relay['spec']['template']['spec']
        assert pod['automountServiceAccountToken'] is False
        assert pod['securityContext']['runAsNonRoot'] is True
        assert pod['terminationGracePeriodSeconds'] == 30
        assert relay['spec']['strategy'] == {'type':'Recreate'}
        c = pod['containers'][0]
        assert c['command'] == ['node','reviewer-relay.js'] and c['image'] == REVIEW['apiImage']
        assert c['securityContext']['readOnlyRootFilesystem'] is True
        env = {e['name']:e for e in c['env']}
        assert env['REVIEWER_DELIVERY_ENABLED']['value']=='1'
        assert env['REVIEWER_RECEIVER_ORIGIN']['value']==RELAY['receiverOrigin']
        assert {e['valueFrom']['secretKeyRef']['name'] for e in c['env'] if 'valueFrom' in e}=={'relay-db'}
        assert pod['volumes']==[{'name':'delivery','secret':{'secretName':'relay-delivery','defaultMode':0o440,
                 'items':[{'key':'token','path':'token'},{'key':'ca.crt','path':'ca.crt'}]}}]
        assert c['volumeMounts']==[{'name':'delivery','mountPath':'/run/reviewer/delivery','readOnly':True}]
        policy = next(d for d in docs if d['kind']=='NetworkPolicy' and d['metadata']['name'].endswith('-review-relay'))
        assert policy['spec']['policyTypes']==['Ingress'] and policy['spec']['ingress']==[]
        assert not any(d['kind']=='Service' and d['metadata']['name'].endswith('-review-relay') for d in docs)


@pytest.mark.parametrize('invalid', [
    {'databaseSecret':''}, {'deliverySecret':''}, {'databaseSecret':'review-db'},
    {'deliverySecret':'relay-db'}, {'deliverySecret':'ordinary-app'},
    {'receiverOrigin':'http://192.0.2.4'}, {'receiverOrigin':'https://receiver.test/'},
    {'receiverOrigin':'https://user:password@receiver.test'}, {'receiverOrigin':'https://receiver.test:99999'},
    {'receiverOrigin':'https://receiver.test:443'}, {'receiverOrigin':'https://127.1'},
    {'receiverOrigin':'https://999.2.3.4'}, {'receiverOrigin':'https://receiver..test'},
])
def test_relay_rejects_invalid_identity_or_transport(tmp_path, invalid):
    assert render(tmp_path, {**REVIEW,'relay':{**RELAY,**invalid}}, check=False).returncode


def test_relay_and_acceptance_require_reviewer_component(tmp_path):
    for reviewer in ({'enabled':False,'relay':RELAY}, {'enabled':False,'investigationEnabled':True}):
        assert render(tmp_path, reviewer, check=False).returncode


def test_acceptance_can_be_enabled_without_starting_delivery(tmp_path):
    docs = render(tmp_path, {**REVIEW,'investigationEnabled':True})
    assert not any(d['metadata']['name'].endswith('-review-relay') for d in docs)

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
    {'databaseSecret': ''}, {'serviceTlsSecret': 'Not_valid'}, {'browserTlsSecret': 'review-client'},
    {'serviceTlsSecret': 'ordinary-app'}, {'apiImage': 'no-tag'}, {'hudImage': 'registry/image:latest'},
])
def test_enabled_reviewer_rejects_invalid_configuration(tmp_path, invalid):
    result = render(tmp_path, {**REVIEW, **invalid}, check=False)
    assert result.returncode and ('reviewer' in result.stderr.lower() or 'Reviewer' in result.stderr)

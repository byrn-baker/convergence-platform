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


def test_telemetry_is_opt_in_and_mounts_only_read_source_credentials(tmp_path):
    docs = render(tmp_path, {**REVIEW, 'telemetryEnabled': True, 'telemetryIdsSecret': 'ids-reader'})
    deployment = next(d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'].endswith('-review-api'))
    pod = deployment['spec']['template']['spec']
    container = pod['containers'][0]
    env = {e['name']: e for e in container['env']}
    assert env['REVIEWER_TELEMETRY_ENABLED']['value'] == '1'
    assert env['SITES_CONFIG']['valueFrom']['configMapKeyRef']['key'] == 'SITES_CONFIG'
    assert env['VICTORIAMETRICS_URL']['value'] == 'http://case-demo-metrics:8428'
    assert env['IDS_STATUS_TOKEN']['valueFrom']['secretKeyRef'] == {'name': 'ids-reader', 'key': 'IDS_STATUS_TOKEN'}
    assert not container.get('envFrom')
    assert not {'API_TOKEN', 'AUTH_TOKEN', 'ALERT_RECEIVER_TOKEN'} & env.keys()
    volume = next(v for v in pod['volumes'] if v['name'] == 'telemetry-ids')
    assert volume['secret']['items'] == [{'key': 'ids-ca.crt', 'path': 'ca.crt'}]
    assert next(m for m in container['volumeMounts'] if m['name'] == 'telemetry-ids')['readOnly']
    off = render(tmp_path, REVIEW)
    api = next(d for d in off if d['kind'] == 'Deployment' and d['metadata']['name'].endswith('-review-api'))
    env = {e['name']: e for e in api['spec']['template']['spec']['containers'][0]['env']}
    assert env['REVIEWER_TELEMETRY_ENABLED']['value'] == '0'
    assert 'IDS_STATUS_TOKEN' not in env and 'VICTORIAMETRICS_URL' not in env


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


SSO = {'issuer': 'https://sso.example.test/realms/home', 'clientId': 'convergence-cases',
       'clientSecret': 'review-oidc', 'requiredGroup': 'convergence-users'}


def workloads(docs):
    found = {}
    for doc in docs:
        if doc['kind'] == 'Deployment' and '-review-' in doc['metadata']['name']:
            pod = doc['spec']['template']['spec']
            env = {item['name']: item.get('value') for item in pod['containers'][0]['env']}
            found[doc['metadata']['name'].rsplit('-', 1)[1]] = (env, pod)
    return found


def test_password_sign_in_is_the_default_without_an_identity_provider(tmp_path):
    for env, pod in workloads(render(tmp_path, REVIEW)).values():
        assert env['REVIEWER_PASSWORD_SIGN_IN'] == '1'
        assert not any(name.startswith('REVIEWER_OIDC_') for name in env)
        assert all(volume['name'] != 'oidc-client' for volume in pod['volumes'])


def test_identity_provider_sign_in_gives_the_client_secret_to_the_hud_only(tmp_path):
    found = workloads(render(tmp_path, {**REVIEW, 'oidc': SSO, 'passwordSignIn': {'enabled': False}}))
    api, pod = found['api']
    assert api['REVIEWER_OIDC_ISSUER'] == SSO['issuer']
    assert api['REVIEWER_OIDC_CLIENT_ID'] == 'convergence-cases'
    assert api['REVIEWER_OIDC_REQUIRED_GROUP'] == 'convergence-users'
    assert api['REVIEWER_PASSWORD_SIGN_IN'] == '0'
    assert 'REVIEWER_OIDC_CLIENT_SECRET_FILE' not in api
    assert all(volume['secret']['secretName'] != 'review-oidc' for volume in pod['volumes'])
    hud, pod = found['hud']
    assert hud['REVIEWER_OIDC_ISSUER'] == SSO['issuer']
    assert hud['REVIEWER_OIDC_CLIENT_SECRET_FILE'] == '/run/reviewer/oidc/client-secret'
    assert hud['REVIEWER_PASSWORD_SIGN_IN'] == '0'
    assert 'REVIEWER_OIDC_REQUIRED_GROUP' not in hud
    volume = next(volume for volume in pod['volumes'] if volume['name'] == 'oidc-client')
    assert volume['secret'] == {'secretName': 'review-oidc', 'defaultMode': 0o440,
                                'items': [{'key': 'client-secret', 'path': 'client-secret'}]}
    mount = next(m for m in pod['containers'][0]['volumeMounts'] if m['name'] == 'oidc-client')
    assert mount == {'name': 'oidc-client', 'mountPath': '/run/reviewer/oidc', 'readOnly': True}


def test_break_glass_password_sign_in_names_its_addresses(tmp_path):
    values = {**REVIEW, 'oidc': SSO, 'passwordSignIn': {'enabled': True, 'addresses': ['192.0.2.10', '192.0.2.11']}}
    found = workloads(render(tmp_path, values))
    assert found['hud'][0]['REVIEWER_PASSWORD_SIGN_IN_ADDRESSES'] == '192.0.2.10,192.0.2.11'
    assert found['hud'][0]['REVIEWER_PASSWORD_SIGN_IN'] == found['api'][0]['REVIEWER_PASSWORD_SIGN_IN'] == '1'


@pytest.mark.parametrize('invalid', [
    {'oidc': {**SSO, 'clientSecret': ''}},
    {'oidc': {**SSO, 'issuer': ''}},
    {'oidc': {**SSO, 'issuer': 'http://sso.example.test/realms/home'}},
    {'oidc': {**SSO, 'issuer': SSO['issuer'] + '/'}},
    {'oidc': {**SSO, 'clientSecret': 'review-browser'}},
    {'oidc': SSO},
    {'oidc': SSO, 'passwordSignIn': {'enabled': True, 'addresses': []}},
    {'oidc': SSO, 'passwordSignIn': {'enabled': True, 'addresses': ['workstation']}},
    {'passwordSignIn': {'enabled': False}},
])
def test_sign_in_settings_are_rejected_when_incomplete_or_unsafe(tmp_path, invalid):
    assert render(tmp_path, {**REVIEW, **invalid}, check=False).returncode != 0

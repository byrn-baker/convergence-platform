"""Private-CA leaf renewal: profile reproduction, compare-and-swap publish, pinned rotation."""
import base64
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'setup'))
spec = importlib.util.spec_from_file_location('tls_renewal', ROOT/'setup/tls_renewal.py')
renewal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renewal)
cli_spec = importlib.util.spec_from_file_location('reviewer_tls', ROOT/'setup/reviewer_tls.py')
cli = importlib.util.module_from_spec(cli_spec)
cli_spec.loader.exec_module(cli)

DAY = 86400
NOW = 1_900_000_000  # fixed clock; certificates below are minted relative to real time


def ssl(*args):
    subprocess.run(['openssl', *map(str, args)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def pki(tmp_path):
    def ca(name):
        ssl('req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes', '-days', '3650',
            '-subj', f'/CN={name}', '-keyout', tmp_path/f'{name}.key', '-out', tmp_path/f'{name}.crt')
        return str(tmp_path/f'{name}.crt'), str(tmp_path/f'{name}.key')

    def leaf(issuer, name, ext, days):
        (tmp_path/f'{name}.ext').write_text(ext)
        ssl('req', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes', '-subj', f'/CN={name}',
            '-keyout', tmp_path/f'{name}.key', '-out', tmp_path/f'{name}.csr')
        ssl('x509', '-req', '-in', tmp_path/f'{name}.csr', '-CA', issuer[0], '-CAkey', issuer[1], '-set_serial', '7',
            '-days', str(days), '-extfile', tmp_path/f'{name}.ext', '-out', tmp_path/f'{name}.crt')
        return (tmp_path/f'{name}.crt').read_bytes(), (tmp_path/f'{name}.key').read_bytes()
    return ca, leaf


SERVER = ('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n'
          'extendedKeyUsage=serverAuth\nsubjectAltName=IP:192.0.2.10\n')
CLIENT = 'extendedKeyUsage=clientAuth\nkeyUsage=digitalSignature\nbasicConstraints=critical,CA:FALSE\n'
API = ('subjectAltName=DNS:api.example.svc,DNS:api.example.svc.cluster.local\nextendedKeyUsage=serverAuth\n'
       'keyUsage=digitalSignature\nbasicConstraints=critical,CA:FALSE\n')


class FakeKube:
    def __init__(self):
        self.secrets, self.patches, self.pushed, self.fail_next = {}, [], [], None

    def secret(self, namespace, name):
        return dict(self.secrets[(namespace, name)])

    def patch_secret(self, namespace, name, operations):
        if self.fail_next:
            self.fail_next, error = None, self.fail_next
            raise error
        data = self.secrets[(namespace, name)]
        for op in operations:
            key = op['path'].split('/', 2)[2]
            value = base64.b64decode(op['value'])
            if op['op'] == 'test' and data.get(key) != value:
                raise renewal.RenewalError('kubectl patch failed')
        for op in operations:
            if op['op'] == 'replace':
                data[op['path'].split('/', 2)[2]] = base64.b64decode(op['value'])
        self.patches.append((name, [op['op'] for op in operations]))

    def post_raw(self, path, body):
        self.pushed.append((path, body))


def real_now():
    import time
    return time.time()


def write_config(tmp_path, leaves, **extra):
    path = tmp_path/'tls-renewal.json'
    path.write_text(json.dumps({'state_dir': str(tmp_path/'state'), 'leaves': leaves, **extra}))
    return path


def test_profile_round_trips_each_deployed_leaf_shape(pki):
    ca, leaf = pki
    root = ca('root')
    for ext in (SERVER, CLIENT, API):
        cert, _ = leaf(root, 'shape', ext, 30)
        prof = renewal.profile(cert)
        new_cert, new_key = renewal.issue(prof, root, 365)
        renewal.verify(new_cert, new_key, root[0], prof, real_now(), 30*DAY)
        assert renewal.profile(new_cert) == prof
    assert renewal.profile(leaf(root, 'ip', SERVER, 30)[0])['extensions'] == {
        'basicConstraints': 'critical,CA:FALSE', 'keyUsage': 'critical,digitalSignature',
        'extendedKeyUsage': 'serverAuth', 'subjectAltName': 'IP:192.0.2.10'}
    with pytest.raises(renewal.RenewalError, match='end-entity'):
        renewal.profile(Path(root[0]).read_bytes())


def test_verify_rejects_a_foreign_key_wrong_ca_and_short_validity(pki):
    ca, leaf = pki
    root, other = ca('root'), ca('other')
    cert, key = leaf(root, 'leaf', SERVER, 30)
    prof = renewal.profile(cert)
    new_cert, new_key = renewal.issue(prof, root, 365)
    with pytest.raises(renewal.RenewalError, match='does not match'):
        renewal.verify(new_cert, key, root[0], prof, real_now(), 30*DAY)
    with pytest.raises(renewal.RenewalError, match='chain'):
        renewal.verify(new_cert, new_key, other[0], prof, real_now(), 30*DAY)
    with pytest.raises(renewal.RenewalError, match='outlast'):
        renewal.verify(new_cert, new_key, root[0], prof, real_now(), 400*DAY)


def test_file_leaf_renews_only_inside_the_window_and_archives_the_old_pair(tmp_path, pki):
    ca, leaf = pki
    root = ca('receiver-ca')
    live = tmp_path/'live'
    live.mkdir()
    cert, key = leaf(root, 'receiver', SERVER, 60)
    (live/'receiver.crt').write_bytes(cert)
    (live/'receiver.key').write_bytes(key)
    config = write_config(tmp_path, [{'name': 'receiver', 'ca': {'cert': root[0], 'key': root[1]},
                                      'files': {'cert': str(live/'receiver.crt'), 'key': str(live/'receiver.key')}}])
    settings, leaves, state_dir, _ = renewal.load_config(config, kube=FakeKube())
    assert renewal.Renewal(settings, leaves, state_dir).run()[0]['action'] == 'none'
    assert 'error' not in renewal.Renewal(settings, leaves, state_dir).plan()[0]
    assert (live/'receiver.crt').read_bytes() == cert

    settings['renew_before_days'] = 90
    rows = renewal.Renewal(settings, leaves, state_dir).run()
    assert rows[0]['action'] == 'renew' and 'error' not in rows[0]
    new_cert, new_key = (live/'receiver.crt').read_bytes(), (live/'receiver.key').read_bytes()
    assert new_cert != cert and renewal.profile(new_cert) == renewal.profile(cert)
    renewal.verify(new_cert, new_key, root[0], renewal.profile(cert), real_now(), 90*DAY)
    assert (live/'receiver.key').stat().st_mode & 0o777 == 0o600
    archived = list((state_dir/'archive'/'receiver').iterdir())
    assert len(archived) == 1 and (archived[0]/'tls.crt').read_bytes() == cert
    assert (archived[0]/'tls.key').stat().st_mode & 0o777 == 0o600


def test_wrong_ca_or_a_concurrent_change_publishes_nothing(tmp_path, pki):
    ca, leaf = pki
    root, other = ca('api-ca'), ca('unrelated')
    kube = FakeKube()
    cert, key = leaf(root, 'api', API, 10)
    kube.secrets[('ns', 'svc')] = {'tls.crt': cert, 'tls.key': key, 'ca.crt': b'bundle'}
    config = write_config(tmp_path, [{'name': 'api', 'ca': {'cert': other[0], 'key': other[1]},
                                      'secret': {'namespace': 'ns', 'name': 'svc'}}])
    settings, leaves, state_dir, _ = renewal.load_config(config, kube=kube)
    row = renewal.Renewal(settings, leaves, state_dir).run()[0]
    assert 'not issued by the configured CA' in row['error'] and kube.patches == []
    assert 'not issued by the configured CA' in renewal.Renewal(settings, leaves, state_dir).plan()[0]['error']

    config = write_config(tmp_path, [{'name': 'api', 'ca': {'cert': root[0], 'key': root[1]},
                                      'secret': {'namespace': 'ns', 'name': 'svc'}}])
    settings, leaves, state_dir, _ = renewal.load_config(config, kube=kube)
    original_read, reads = leaves[0].target.read, []

    def raced():
        # Another writer lands between the renewal's own read and its publish.
        result = original_read()
        reads.append(1)
        if len(reads) == 2:
            kube.secrets[('ns', 'svc')]['tls.crt'] = b'someone else renewed it'
        return result
    leaves[0].target.read = raced
    row = renewal.Renewal(settings, leaves, state_dir).run()[0]
    assert row['error'] == 'kubectl patch failed'
    assert kube.secrets[('ns', 'svc')]['tls.crt'] == b'someone else renewed it'
    assert kube.secrets[('ns', 'svc')]['ca.crt'] == b'bundle'


def test_pinned_installation_rotates_in_settled_resumable_steps(tmp_path, pki):
    ca, leaf = pki
    root = ca('installation-ca')
    kube = FakeKube()
    cert, key = leaf(root, 'installation', CLIENT, 10)
    old_pin = renewal.fingerprint(cert)
    kube.secrets[('ns', 'install')] = {'tls.crt': cert, 'tls.key': key, 'ca.crt': b'api-ca'}
    enrolled = json.dumps([{'installation': 'home-id', 'fingerprint256': old_pin}]).encode()
    kube.secrets[('ns', 'service')] = {'enrolled.json': enrolled, 'tls.crt': b'service', 'tls.key': b'k'}
    record = tmp_path/'record'
    record.mkdir()
    config = write_config(tmp_path, [{
        'name': 'installation', 'ca': {'cert': root[0], 'key': root[1]},
        'secret': {'namespace': 'ns', 'name': 'install'},
        'record': {'cert': str(record/'installation.crt'), 'key': str(record/'installation.key')},
        'enrollment': {'namespace': 'ns', 'name': 'service'}}], settle_seconds=3600)
    settings, leaves, state_dir, _ = renewal.load_config(config, kube=kube)
    pins = lambda: [r['fingerprint256'] for r in json.loads(kube.secrets[('ns', 'service')]['enrolled.json'])]
    at = lambda offset: renewal.Renewal(settings, leaves, state_dir, now=real_now() + offset).run()[0]

    # Step 1 fails at the enrollment write after the step was recorded; the rerun resumes it.
    kube.fail_next = renewal.RenewalError('kubectl patch failed')
    assert at(0)['error'] == 'kubectl patch failed'
    new_pin = json.loads((state_dir/'state.json').read_text())['installation']['new_pin']
    assert pins() == [old_pin]
    assert at(60)['action'] == 'enroll'
    assert pins() == [old_pin, new_pin], 'one new pin, beside the old one'
    assert kube.secrets[('ns', 'install')]['tls.crt'] == cert, 'the identity is unchanged until the API has the pin'
    assert json.loads(kube.secrets[('ns', 'service')]['enrolled.json'])[1]['installation'] == 'home-id'

    assert at(600)['action'] == 'wait'
    assert at(3700)['action'] == 'publish'
    published = kube.secrets[('ns', 'install')]['tls.crt']
    assert renewal.fingerprint(published) == new_pin and kube.secrets[('ns', 'install')]['ca.crt'] == b'api-ca'
    assert (record/'installation.crt').read_bytes() == published
    assert pins() == [old_pin, new_pin]

    assert at(4000)['action'] == 'wait'
    assert at(7400)['action'] == 'unpin'
    assert pins() == [new_pin]
    assert json.loads((state_dir/'state.json').read_text()) == {}
    assert not any((state_dir/'pending'/'installation').iterdir())
    assert at(7500)['action'] == 'none'


def test_metrics_report_expiry_and_keep_last_success_on_failure():
    ok = renewal.metrics([{'leaf': 'a', 'not_after': 2_000_000_000.0}], NOW).decode()
    assert 'convergence_tls_cert_not_after_seconds{leaf="a"} 2000000000' in ok
    assert f'convergence_tls_renewal_last_success_seconds {NOW}' in ok
    failed = renewal.metrics([{'leaf': 'a', 'error': 'x'}], NOW).decode()
    assert 'convergence_tls_renewal_failed_leaves 1' in failed and 'last_success' not in failed


def test_cli_plan_changes_nothing_and_renew_pushes_metrics(tmp_path, pki, capsys, monkeypatch):
    ca, leaf = pki
    root = ca('receiver-ca')
    cert, key = leaf(root, 'receiver', SERVER, 10)
    (tmp_path/'r.crt').write_bytes(cert)
    (tmp_path/'r.key').write_bytes(key)
    config = write_config(tmp_path, [{'name': 'receiver', 'ca': {'cert': root[0], 'key': root[1]},
                                      'files': {'cert': str(tmp_path/'r.crt'), 'key': str(tmp_path/'r.key')}}],
                          pushgateway={'namespace': 'observability', 'service': 'pushgateway:9091'})
    kube = FakeKube()
    real_load = renewal.load_config
    monkeypatch.setattr(cli.tls_renewal, 'load_config', lambda path: real_load(path, kube=kube))
    assert cli.main(['plan', '--config', str(config)]) == 0
    assert json.loads(capsys.readouterr().out)['leaves'][0]['action'] == 'renew'
    assert (tmp_path/'r.crt').read_bytes() == cert and kube.pushed == []
    assert cli.main(['renew', '--config', str(config)]) == 0
    assert json.loads(capsys.readouterr().out)['leaves'][0]['action'] == 'renew'
    path, body = kube.pushed[0]
    assert path == '/api/v1/namespaces/observability/services/pushgateway:9091/proxy/metrics/job/convergence-tls-renewal'
    assert b'BEGIN' not in body and b'convergence_tls_cert_not_after_seconds{leaf="receiver"}' in body


def test_timer_unit_runs_renew_from_this_checkout_without_secrets(tmp_path):
    written = cli.units(tmp_path/'tls-renewal.json')
    service = written['convergence-reviewer-tls.service']
    assert f'{ROOT/"setup/reviewer_tls.py"} renew --config {tmp_path/"tls-renewal.json"}' in service
    assert 'UMask=0077' in service and 'Type=oneshot' in service
    assert 'OnCalendar=*-*-* 00/6:17:00' in written['convergence-reviewer-tls.timer']
    assert 'Persistent=true' in written['convergence-reviewer-tls.timer']

"""Proxmox provisioning only ever touches the VMs its spec names and tags."""
import importlib.util
import json
from pathlib import Path
import sys
import urllib.parse

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec_ = importlib.util.spec_from_file_location('cluster_proxmox', ROOT/'deploy/cluster/proxmox.py')
px = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(px)
sys.path.insert(0, str(ROOT/'workspace/skills/convergence-setup/scripts'))
import k3s  # noqa: E402


def cluster_spec(tmp_path, **changes):
    key = tmp_path/'id.pub'
    key.write_text('ssh-ed25519 AAAATEST operator@example\n')
    spec = {'node': 'pve1', 'template': 8000, 'storage': 'local-lvm', 'bridge': 'vmbr0', 'vlan': None,
            'vmid_range': [7000, 7009], 'name_prefix': 'cvg-test-', 'ssh_public_key': str(key),
            'gateway': '192.0.2.1', 'cidr': 24, 'nameservers': ['192.0.2.1'], 'vip': '192.0.2.50',
            'nodes': [{'name': f'cvg-test-s{i}', 'vmid': 7000+i, 'role': 'server', 'cores': 2,
                       'memory_mb': 4096, 'disk_gb': 30, 'ip': f'192.0.2.{10+i}'} for i in range(3)]}
    spec.update(changes)
    path = tmp_path/'spec.json'
    path.write_text(json.dumps(spec))
    return path


class FakeProxmox:
    def __init__(self, vms=()):
        self.vms = {int(v['vmid']): dict(v) for v in vms}
        self.vms.setdefault(8000, {'vmid': 8000, 'name': 'ubuntu-cloud', 'node': 'pve1', 'template': 1})
        self.configs = {8000: {'scsi0': 'local-lvm:base-8000-disk-0,size=3G', 'ide2': 'local-lvm:cloudinit,media=cdrom'}}
        self.calls = []

    def call(self, method, path, data=None, timeout=60):
        self.calls.append((method, path, data))
        if path == '/cluster/resources?type=vm':
            return list(self.vms.values())
        if path == '/nodes/pve1/storage':
            return [{'storage': 'local-lvm', 'content': 'images,rootdir'}]
        if method == 'POST' and path.endswith('/clone'):
            new = data['newid']
            self.vms[new] = {'vmid': new, 'name': data['name'], 'node': 'pve1', 'status': 'stopped'}
            self.configs[new] = dict(self.configs[8000])
            return 'UPID:clone'
        if method == 'POST' and path.endswith('/config'):
            vmid = int(path.split('/')[4])
            self.vms[vmid]['tags'] = data['tags']
            self.configs[vmid].update(data)
            return None
        if method == 'GET' and path.endswith('/config'):
            return self.configs[int(path.split('/')[4])]
        if path.endswith('/status/current'):
            return {'status': self.vms[int(path.split('/')[4])].get('status', 'stopped')}
        if path.endswith('/status/start'):
            self.vms[int(path.split('/')[4])]['status'] = 'running'
            return 'UPID:start'
        if path.endswith('/status/stop'):
            self.vms[int(path.split('/')[4])]['status'] = 'stopped'
            return 'UPID:stop'
        if method == 'DELETE':
            del self.vms[int(path.split('/')[4].split('?')[0])]
            return 'UPID:delete'
        if method == 'PUT' and path.endswith('/resize'):
            return None
        raise AssertionError((method, path))

    def wait_task(self, node, upid, timeout=900):
        assert upid.startswith('UPID:')


def changes(api):
    return [(m, p) for m, p, _ in api.calls if m != 'GET']


def test_spec_rejects_vmids_or_names_outside_the_declared_scope(tmp_path):
    good = px.load_spec(cluster_spec(tmp_path))
    assert [n['vmid'] for n in good['nodes']] == [7000, 7001, 7002]
    for bad in ({'vmid_range': [7001, 7009]}, {'name_prefix': 'other-'}):
        with pytest.raises(px.SpecError):
            px.load_spec(cluster_spec(tmp_path, **bad))
    two = json.loads(cluster_spec(tmp_path).read_text())
    two['nodes'] = two['nodes'][:2]
    (tmp_path/'two.json').write_text(json.dumps(two))
    with pytest.raises(px.SpecError, match='quorum'):
        px.load_spec(tmp_path/'two.json')


def test_plan_refuses_a_vmid_used_by_an_unmanaged_vm(tmp_path):
    spec = px.load_spec(cluster_spec(tmp_path))
    api = FakeProxmox([{'vmid': 7001, 'name': 'someone-else', 'node': 'pve1'}])
    with pytest.raises(px.SpecError, match='does not manage'):
        px.plan(api, spec)
    assert changes(api) == []


def test_apply_clones_tags_configures_cloud_init_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(px, 'wait_ssh', lambda address, timeout=600: None)
    spec = px.load_spec(cluster_spec(tmp_path))
    api = FakeProxmox()
    result = px.apply(api, spec)
    assert result['addresses'] == {'cvg-test-s0': '192.0.2.10', 'cvg-test-s1': '192.0.2.11', 'cvg-test-s2': '192.0.2.12'}
    config = api.configs[7000]
    assert px.MANAGED_TAG in config['tags'].split(';')
    assert config['ipconfig0'] == 'ip=192.0.2.10/24,gw=192.0.2.1'
    assert urllib.parse.unquote(config['sshkeys']).startswith('ssh-ed25519 AAAATEST')
    resize = [d for m, p, d in api.calls if p.endswith('/resize')]
    assert resize[0] == {'disk': 'scsi0', 'size': '30G'}
    api.calls.clear()
    again = px.apply(api, spec)
    assert {a['action'] for a in again['actions']} == {'exists'}
    assert not any(p.endswith('/clone') for _, p in changes(api))


def test_destroy_only_removes_tagged_vms_of_this_spec(tmp_path, monkeypatch):
    monkeypatch.setattr(px, 'wait_ssh', lambda address, timeout=600: None)
    spec = px.load_spec(cluster_spec(tmp_path))
    api = FakeProxmox()
    px.apply(api, spec)
    api.vms[7001]['tags'] = ''          # e.g. someone re-tagged it by hand
    api.calls.clear()
    with pytest.raises(px.SpecError, match='Refusing'):
        px.destroy(api, spec)
    assert changes(api) == []
    api.vms[7001]['tags'] = px.MANAGED_TAG
    assert px.destroy(api, spec) == [7000, 7001, 7002]
    assert set(api.vms) == {8000}


def test_inventory_and_private_cluster_secrets(tmp_path):
    spec = px.load_spec(cluster_spec(tmp_path))
    values = px.cluster_vars(spec, tmp_path/'state')
    secret_file = Path(values['secrets_file'])
    assert oct(secret_file.stat().st_mode & 0o777) == '0o600'
    assert px.cluster_vars(spec, tmp_path/'state')['secrets_file'] == values['secrets_file']
    first = json.loads(secret_file.read_text())
    assert json.loads(secret_file.read_text()) == first
    assert 'k3s_token' not in values
    inv = px.inventory(spec, {}, values)
    assert list(inv['all']['children']['k3s_init_node']['hosts']) == ['cvg-test-s0']
    assert inv['all']['vars']['virtual_ip'] == '192.0.2.50'


def test_credentials_strip_scheme_and_never_need_shell(tmp_path, monkeypatch):
    for key in px.KEYS:
        monkeypatch.delenv(key, raising=False)
    env = tmp_path/'.env'
    env.write_text('PROXMOX_HOST="https://pve.example.test/"\nPROXMOX_USER=api@pve\n'
                   'PROXMOX_TOKEN_NAME=t\nPROXMOX_TOKEN_VALUE=$(touch /tmp/never)\n')
    creds = px.credentials(env)
    assert creds['host'] == 'pve.example.test' and creds['port'] == '8006'
    assert creds['token_value'] == '$(touch /tmp/never)'


def test_argocd_application_keeps_the_secret_out_and_validates_the_source(tmp_path):
    options = {'release': 'convergence', 'namespace': 'convergence'}
    values = {'existingSecret': 'convergence-credentials', 'api': {'image': {'repository': 'x', 'tag': 'y'}}}
    app = k3s.application(options, values, 'git@github.com:example/convergence.git', 'deploy/helm/convergence', 'v1.0.0')
    assert app['spec']['source']['helm']['valuesObject'] == values
    assert 'PGPASSWORD' not in json.dumps(app)
    for url, path in (('file:///etc', 'deploy'), ('git@github.com:x/y.git', '../secret')):
        with pytest.raises(ValueError):
            k3s.application(options, values, url, path, 'main')
    key = tmp_path/'deploy-key'
    key.write_text('-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n')
    secret = k3s.repository_secret('git@github.com:example/convergence.git', key)
    assert secret['metadata']['labels'] == {'argocd.argoproj.io/secret-type': 'repository'}

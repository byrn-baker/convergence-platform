"""Prove original capture/output settings cannot leak into the new instance."""
import copy
import importlib.util
from pathlib import Path

import pytest
import yaml


spec = importlib.util.spec_from_file_location('mirror_config', Path(__file__).with_name('build-config.py'))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_original_rules_and_home_net_survive_but_capture_and_retention_do_not():
    original = {'vars': {'address-groups': {'HOME_NET': '[10.0.0.0/8]'}},
        'af-packet': [{'interface': 'eth0', 'cluster-id': 99,
            'bpf-filter': 'ip proto 47 and src host 192.0.2.2'}],
        'bpf-filter': 'ip proto 47', 'pcap': [{'interface': 'eth0'}],
        'rule-files': ['suricata.rules', '/etc/suricata/local.rules'],
        'stream': {'memcap': '64 MiB', 'reassembly': {'depth': '1 MiB'}},
        'detect': {'profile': 'medium'}, 'runmode': 'autofp',
        'unix-command': {'enabled': True, 'filename': '/var/run/suricata/suricata-command.socket'},
        'outputs': [{'pcap-log': {'enabled': True}}, {'file-store': {'enabled': True}},
            {'eve-log': {'types': [{'alert': {'payload': True, 'packet': True}}]}}]}
    unchanged = copy.deepcopy(original)
    config = builder.build(yaml.safe_dump(original), 'ens19')
    assert original == unchanged
    assert config['vars'] == original['vars']
    assert config['rule-files'] == original['rule-files']
    assert config['stream'] == original['stream']
    assert config['af-packet'][0]['interface'] == 'ens19'
    assert config['af-packet'][0]['cluster-id'] != 99
    assert 'bpf-filter' not in config['af-packet'][0]
    assert 'bpf-filter' not in config and 'pcap' not in config
    assert config['vlan']['use-for-tracking'] is False
    assert config['livedev']['use-for-tracking'] is False
    assert config['detect']['profile'] == 'low'
    assert config['detect']['sgh-mpm-caching'] is False
    assert config['runmode'] == 'workers'
    assert config['af-packet'][0]['threads'] == 1
    assert config['af-packet'][0]['tpacket-v3'] is True
    assert config['unix-command'] == {'enabled': False}
    assert len(config['outputs']) == 1
    eve = config['outputs'][0]['eve-log']
    assert eve['types'][0] == {'alert': {'payload': False, 'packet': False}}
    assert set(x if isinstance(x, str) else next(iter(x)) for x in eve['types']) == {
        'alert', 'stats', 'flow', 'dns', 'tls'}


@pytest.mark.parametrize('interface', ['eth0', 'lo', '../eth1', '', 'eth1;echo'])
def test_reject_management_or_invalid_interface(interface):
    with pytest.raises(ValueError):
        builder.build('{}', interface)

"""Optional home flows preserve private core setup and explicit capture boundaries."""
import importlib.util
import json
from pathlib import Path
import struct
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]
SCRIPTS=ROOT/'workspace/skills/convergence-setup/scripts'
sys.path.insert(0,str(SCRIPTS))
import setup
import flows


def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def options(**changes):
    return dict(site='demo',project='flow-fixture',edge_url='',api_port=23080,
        prometheus_port=29090,alertmanager_port=29093,blackbox_port=29115,
        receiver_port=28099,flows='demo',flow_port=12055,**changes)


def test_demo_profile_private_source_and_persistent_bounded_collection(tmp_path):
    bundle=tmp_path/'flow bundle';setup.prepare(ROOT,bundle,options())
    manifest=json.loads((bundle/'deployment.json').read_text())
    env=setup.env_writer(ROOT).values((bundle/'compose.env').read_text())
    source=json.loads(env['SITES_CONFIG'])['demo']['flows']
    assert source['exporters']==['127.0.0.1'] and source['coverage']['mode']=='demo'
    assert 'Synthetic' in source['coverage']['description']
    overlay=json.loads((bundle/'compose.override.json').read_text())
    collector=overlay['services']['flow-collector']
    assert collector['network_mode']=='host' and collector['cap_drop']==['ALL']
    assert 'ports' not in collector and not any('docker.sock' in str(v) for v in collector['volumes'])
    archive=overlay['services']['flow-archive']
    assert '-retentionPeriod=1d' in archive['command'] and 'ports' not in archive
    assert overlay['services']['flow-exporter']['profiles']==['manual-flow-export']
    assert overlay['services']['flow-exporter']['cap_drop']==['ALL']
    config=json.loads((bundle/'flow-otel.json').read_text())
    assert config['receivers']['filelog/flows']['storage']=='file_storage'
    assert config['exporters']['otlphttp/flows']['sending_queue']['queue_size']==128
    assert config['receivers']['filelog/flows']['resource']['site']=='demo'
    assert manifest['options']['flows']=='demo'
    before={p.name:p.read_bytes() for p in bundle.iterdir()}
    assert setup.prepare(ROOT,bundle,options())['status']=='preserved'
    assert before=={p.name:p.read_bytes() for p in bundle.iterdir()}
    with pytest.raises(ValueError,match='differs'):setup.prepare(ROOT,bundle,{**options(),'flows':'host'})
    with pytest.raises(ValueError,match='differs'):setup.prepare(ROOT,bundle,{**options(),'site':'live'})


def test_incomplete_flow_bundle_is_not_regenerated(tmp_path):
    bundle=tmp_path/'bundle';setup.prepare(ROOT,bundle,options())
    before=(bundle/'compose.env').read_bytes();(bundle/'flow-otel.json').unlink()
    with pytest.raises(ValueError,match='incomplete'):setup.prepare(ROOT,bundle,options())
    assert (bundle/'compose.env').read_bytes()==before


def test_core_bundle_stays_compatible_without_flow_options(tmp_path):
    bundle=tmp_path/'bundle';old=options();old.pop('flows');old.pop('flow_port')
    setup.prepare(ROOT,bundle,old)
    assert setup.prepare(ROOT,bundle,{**old,'flows':None,'flow_port':2055})['status']=='preserved'
    assert 'flow-archive' not in json.loads((bundle/'compose.override.json').read_text())['services']


@pytest.mark.parametrize('update',[{'flow_port':80},{'flow_port':65536},{'flows':'network'}])
def test_flow_inputs_rejected_before_files(tmp_path,update):
    bundle=tmp_path/'bundle'
    with pytest.raises(ValueError):setup.prepare(ROOT,bundle,{**options(),**update})
    assert not bundle.exists()


def test_live_capture_and_synthetic_injection_are_separate_explicit_actions(tmp_path,monkeypatch):
    host=tmp_path/'host';demo=tmp_path/'demo'
    setup.prepare(ROOT,host,{**options(),'flows':'host'})
    setup.prepare(ROOT,demo,options())
    monkeypatch.setattr(flows.subprocess,'run',lambda *a,**k:pytest.fail('must not start capture or demo'))
    with pytest.raises(ValueError,match='separate demo'):flows.demo(ROOT,host)
    with pytest.raises(ValueError,match='separate host'):flows.host_command(ROOT,demo,'eth0')
    command=flows.host_command(ROOT,host,'lo','tcp port 12345')
    assert command['capture_filter']=='not (udp and port 12055) and (tcp port 12345)'
    assert command['command'][-1]==command['capture_filter']
    assert command['command'][command['command'].index('-i')+1]=='lo'
    assert 'NET_RAW' in command['command'] and 'NET_ADMIN' not in command['command']
    assert ' capture ' in command['shell_command']
    for invalid in ('any','all','eth0;command','../lo'):
        with pytest.raises(ValueError):flows.host_command(ROOT,host,invalid)
    with pytest.raises(ValueError):flows.host_command(ROOT,host,'lo','tcp\ncommand')


def test_linux_desktop_and_remote_qualification_not_assumed(monkeypatch):
    support=setup.flow_support()
    monkeypatch.setattr(support.platform,'system',lambda:'Linux')
    support.require_linux(lambda _: 'linux|Ubuntu 24.04')
    with pytest.raises(ValueError):support.require_linux(lambda _: 'linux|Docker Desktop')
    monkeypatch.setattr(support.platform,'system',lambda:'Darwin')
    with pytest.raises(ValueError):support.require_linux(lambda _: 'linux|Ubuntu 24.04')


def test_rotating_spool_preserves_recent_whole_records_with_fixed_bound(tmp_path):
    collector=load_module('flow_collector_fixture',ROOT/'deploy/convergence/flows/collector.py')
    spool=collector.Spool(tmp_path,maximum=40,count=3)
    records=[json.dumps({'n':n}).encode()+b'\n' for n in range(35)]
    for record in records:spool.write(record)
    spool.close()
    paths=list(tmp_path.glob('*.jsonl'))
    assert len(paths)==3 and sum(p.stat().st_size for p in paths)<=120
    retained=[json.loads(line)['n'] for p in paths for line in p.read_bytes().splitlines()]
    assert max(retained)==34 and min(retained)>0 and len(retained)==len(set(retained))


def test_demo_capture_contains_only_documentation_addresses_and_no_interface_calls(tmp_path):
    demo=load_module('flow_demo_fixture',ROOT/'deploy/convergence/flows/demo.py')
    file=tmp_path/'demo.pcap';demo.capture(file)
    data=file.read_bytes();assert struct.unpack('<I',data[:4])[0]==0xa1b2c3d4
    packets=[];offset=24
    while offset<len(data):
        _,_,length,original=struct.unpack('<IIII',data[offset:offset+16]);offset+=16
        packet=data[offset:offset+length];offset+=length
        assert length==original
        packets.append(packet)
        assert packet[26:30] in (bytes([192,0,2,10]),bytes([198,51,100,20]))
        assert packet[30:34] in (bytes([192,0,2,10]),bytes([198,51,100,20]))
        assert b'CONVERGENCE SYNTHETIC DEMO' in packet
    assert len(packets)==15


def test_flow_port_cannot_conflict_with_metrics_or_api(tmp_path):
    with pytest.raises(ValueError,match='differ'):setup.prepare(ROOT,tmp_path/'bundle',{**options(),'flow_port':23080})


def test_remote_docker_rejected_before_demo_or_capture(monkeypatch):
    monkeypatch.delenv('DOCKER_CONTEXT',raising=False)
    monkeypatch.setenv('DOCKER_HOST','tcp://untrusted.example:2375')
    monkeypatch.setattr(setup,'output',lambda _:'default')
    with pytest.raises(ValueError,match='local Docker'):setup.require_local_docker()


def test_demo_refuses_collector_without_healthy_readiness(tmp_path,monkeypatch):
    from types import SimpleNamespace
    bundle=tmp_path/'bundle';setup.prepare(ROOT,bundle,options())
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0,stdout='a'*64 if 'ps' in command else 'unhealthy')
    support=setup.flow_support()
    monkeypatch.setattr(support,'guard_project',lambda *_:{})
    monkeypatch.setattr(support,'assert_owned',lambda *_:None)
    monkeypatch.setattr(flows,'flow_support',lambda:support)
    monkeypatch.setattr(flows.subprocess,'run',run)
    with pytest.raises(ValueError,match='not healthy'):flows.collector_ready(ROOT,bundle,options())
    assert len(calls)==2 and all('flow-exporter' not in call for call in calls)


def test_flow_check_retries_backend_warmup_but_not_auth_errors(tmp_path,monkeypatch):
    from urllib.error import HTTPError
    bundle=tmp_path/'bundle';setup.prepare(ROOT,bundle,options())
    calls=[]
    def request(url,token=None):
        calls.append(url)
        if len(calls)==1:raise HTTPError(url,502,'warmup',{},None)
        return {'status':'empty'}
    monkeypatch.setattr(flows,'get_json',request)
    monkeypatch.setattr(flows.time,'sleep',lambda _:None)
    ticks=iter([0,0,5]);monkeypatch.setattr(flows.time,'monotonic',lambda:next(ticks))
    assert not flows.check(ROOT,bundle,wait_seconds=1)['passed']
    assert len(calls)==2
    monkeypatch.setattr(flows.time,'monotonic',lambda:0)
    def unauthorized(url,token=None):raise HTTPError(url,403,'denied',{},None)
    monkeypatch.setattr(flows,'get_json',unauthorized)
    with pytest.raises(HTTPError):flows.check(ROOT,bundle,wait_seconds=1)



def test_flow_identity_is_unique_persisted_and_labels_every_owned_resource(tmp_path):
    first=tmp_path/'first';second=tmp_path/'second'
    setup.prepare(ROOT,first,options());setup.prepare(ROOT,second,options())
    left=json.loads((first/'deployment.json').read_text())
    right=json.loads((second/'deployment.json').read_text())
    assert left['flow_identity']!=right['flow_identity']
    labels=setup.flow_support().ownership(first,left)
    overlay=json.loads((first/'compose.override.json').read_text())
    for group in ('services','volumes','networks'):
        assert all(resource['labels']==labels for resource in overlay[group].values())
    setup.prepare(ROOT,first,options())
    assert json.loads((first/'deployment.json').read_text())['flow_identity']==left['flow_identity']


@pytest.mark.parametrize('kind',['container','volume','network'])
@pytest.mark.parametrize('foreign',['other_bundle','host_mode','other_site','legacy_unlabeled'])
def test_foreign_retained_resources_refused_without_mutation(tmp_path,monkeypatch,kind,foreign):
    bundle=tmp_path/'new';setup.prepare(ROOT,bundle,options())
    manifest=json.loads((bundle/'deployment.json').read_text())
    support=setup.flow_support();labels=support.ownership(bundle,manifest)
    bad=dict(labels)
    if foreign=='other_bundle':bad[support.LABEL_PREFIX+'id']='f'*32
    if foreign=='host_mode':bad[support.LABEL_PREFIX+'mode']='host'
    if foreign=='other_site':bad[support.LABEL_PREFIX+'site']='another'
    if foreign=='legacy_unlabeled':bad={}
    calls=[]
    def read(arguments,environment):
        calls.append(arguments)
        if 'inspect' in arguments:return json.dumps(bad)
        if kind=='container' and arguments[0]=='ps':return 'containerfixture'
        if kind=='volume' and arguments[:2]==['volume','ls']:return 'flow-fixture_convergence-flow-archive'
        if kind=='network' and arguments[:2]==['network','ls']:return 'networkfixture'
        return ''
    monkeypatch.setattr(support,'docker_read',read)
    with pytest.raises(ValueError,match='another bundle or mode'):
        support.guard_project(bundle,manifest,{})
    assert all(command[0] in ('ps','container','volume','network') for command in calls)
    assert all('--all' in command for command in calls if command[0]=='ps' and not any('service=flow-' in item for item in command))


def test_same_project_collision_blocks_start_and_demo_before_export_or_compose(tmp_path,monkeypatch):
    first=tmp_path/'host';second=tmp_path/'demo'
    setup.prepare(ROOT,first,{**options(),'flows':'host'})
    setup.prepare(ROOT,second,options())
    support=setup.flow_support()
    first_manifest=json.loads((first/'deployment.json').read_text())
    labels=support.ownership(first,first_manifest)
    def read(arguments,environment):
        if 'inspect' in arguments:return json.dumps(labels)
        return 'old-stopped-container' if arguments[0]=='ps' else ''
    monkeypatch.setattr(support,'docker_read',read)
    monkeypatch.setattr(support,'require_linux',lambda _:None)
    monkeypatch.setattr(setup,'flow_support',lambda:support)
    monkeypatch.setattr(flows,'flow_support',lambda:support)
    monkeypatch.setattr(setup,'require_local_docker',lambda:None)
    monkeypatch.setattr(flows,'require_local_docker',lambda:None)
    monkeypatch.setattr(setup.subprocess,'run',lambda *a,**k:pytest.fail('No Compose mutation or export before ownership passes'))
    with pytest.raises(ValueError,match='another bundle or mode'):setup.run_compose(ROOT,second,'start')
    with pytest.raises(ValueError,match='another bundle or mode'):flows.demo(ROOT,second)


def test_matching_retained_resources_can_be_restarted(tmp_path,monkeypatch):
    bundle=tmp_path/'bundle';setup.prepare(ROOT,bundle,options())
    manifest=json.loads((bundle/'deployment.json').read_text());support=setup.flow_support()
    expected=support.ownership(bundle,manifest)
    monkeypatch.setattr(support,'docker_read',lambda args,env:json.dumps(expected) if 'inspect' in args else 'owned-resource')
    assert support.guard_project(bundle,manifest,{})==expected



def test_other_project_running_on_same_flow_port_is_refused(tmp_path,monkeypatch):
    bundle=tmp_path/'bundle';setup.prepare(ROOT,bundle,options())
    manifest=json.loads((bundle/'deployment.json').read_text());support=setup.flow_support()
    foreign=dict(support.ownership(bundle,manifest));foreign[support.LABEL_PREFIX+'id']='e'*32
    def read(arguments,environment):
        if any('service=flow-collector' in value for value in arguments):return 'foreign-collector'
        if 'inspect' in arguments:return json.dumps(foreign)
        return ''
    monkeypatch.setattr(support,'docker_read',read)
    with pytest.raises(ValueError,match='another bundle or mode'):support.guard_project(bundle,manifest,{})


@pytest.mark.parametrize('kind',['tcp','udp'])
def test_new_project_refuses_unlabeled_existing_host_listener(tmp_path,monkeypatch,kind):
    import socket
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM if kind=='tcp' else socket.SOCK_DGRAM) as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        bundle=tmp_path/'bundle';setup.prepare(ROOT,bundle,{**options(),'flow_port':port})
        manifest=json.loads((bundle/'deployment.json').read_text());support=setup.flow_support()
        monkeypatch.setattr(support,'docker_read',lambda *_:'')
        with pytest.raises(ValueError,match='already occupied'):
            support.guard_project(bundle,manifest,{},check_ports=True)



@pytest.mark.parametrize('kind',['container','volume'])
def test_project_prefix_does_not_claim_an_unrelated_project(tmp_path,monkeypatch,kind):
    import re
    bundle=tmp_path/'bundle';setup.prepare(ROOT,bundle,{**options(),'project':'convergence'})
    manifest=json.loads((bundle/'deployment.json').read_text());support=setup.flow_support()
    # Docker name-filter matching is emulated against a similarly prefixed project.
    foreign = '/convergence-demo-postgres-1' if kind=='container' else 'convergence-demo_convergence-flow-archive'
    def read(arguments,environment):
        assert 'inspect' not in arguments, 'Unrelated project must not be claimed'
        for argument in arguments:
            if argument.startswith('name=') and re.search(argument[5:],foreign):
                return 'foreign-prefix-resource'
        return ''
    monkeypatch.setattr(support,'docker_read',read)
    assert support.guard_project(bundle,manifest,{})==support.ownership(bundle,manifest)


def test_running_exporter_keeps_port_owned_after_its_collector_stops(tmp_path,monkeypatch):
    bundle=tmp_path/'demo';setup.prepare(ROOT,bundle,options())
    manifest=json.loads((bundle/'deployment.json').read_text());support=setup.flow_support()
    foreign=dict(support.ownership(bundle,manifest));foreign[support.LABEL_PREFIX+'mode']='host'
    foreign[support.LABEL_PREFIX+'id']='c'*32
    def read(arguments,environment):
        if any('service=flow-exporter' in value for value in arguments):return 'still-running-host-exporter'
        if 'inspect' in arguments:return json.dumps(foreign)
        return ''
    monkeypatch.setattr(support,'docker_read',read)
    with pytest.raises(ValueError,match='another bundle or mode'):
        support.guard_project(bundle,manifest,{})


def test_restart_allows_tcp_time_wait_without_an_active_listener(tmp_path,monkeypatch):
    import socket
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        listener.bind(('127.0.0.1',0));listener.listen(1)
        port=listener.getsockname()[1]
        with socket.create_connection(('127.0.0.1',port),timeout=2) as client:
            accepted,_=listener.accept()
            with accepted:
                accepted.settimeout(2)
                accepted.shutdown(socket.SHUT_WR)
                assert client.recv(1)==b''
                client.shutdown(socket.SHUT_WR)
                assert accepted.recv(1)==b''
    # The server endpoint is now TIME_WAIT. A plain bind reproduces the old guard.
    with socket.socket() as plain:
        with pytest.raises(OSError):plain.bind(('127.0.0.1',port))
    bundle=tmp_path/'bundle';setup.prepare(ROOT,bundle,{**options(),'flow_port':port})
    manifest=json.loads((bundle/'deployment.json').read_text());support=setup.flow_support()
    monkeypatch.setattr(support,'docker_read',lambda *_:'')
    assert support.guard_project(bundle,manifest,{},check_ports=True)


def test_active_tcp_listener_remains_rejected_with_reuseaddr(tmp_path,monkeypatch):
    import socket
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        listener.bind(('127.0.0.1',0));listener.listen(1)
        port=listener.getsockname()[1]
        bundle=tmp_path/'bundle';setup.prepare(ROOT,bundle,{**options(),'flow_port':port})
        manifest=json.loads((bundle/'deployment.json').read_text());support=setup.flow_support()
        monkeypatch.setattr(support,'docker_read',lambda *_:'')
        with pytest.raises(ValueError,match='already occupied'):
            support.guard_project(bundle,manifest,{},check_ports=True)

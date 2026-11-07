#!/usr/bin/env python3
"""Render Helm values that turn on device syslog and SNMP for a Convergence release.

The syslog parsers and processors come from the canonical Docker collector config
(deploy/convergence/otel/otel-config.yaml), so both paths parse devices the same way.
Only the inventory, site and destinations differ:

  python3 deploy/helm/scripts/render-device-telemetry.py --site lab \\
      --device edge-fw01=192.0.2.1 --device access-sw01=192.0.2.2 \\
      --snmp access-sw01=192.0.2.2 --output device-telemetry.json

Pass the result to Helm after the release values. Syslog is exposed as NodePorts: UDP
31514 and TCP 31515 by default. The Service uses externalTrafficPolicy Local, because a
cluster-routed NodePort replaces the sender's address and device naming depends on it:
point devices at the node that runs the collector (pin it with --collector-node; with
local-path storage choose that node before the first install). SNMP v2c reads the
community from SNMP_COMMUNITY in the Secret named by --snmp-secret, which the operator
creates; it is never rendered.
"""
import argparse
import copy
import ipaddress
import json
from pathlib import Path
import re
import sys

import yaml

ROOT = Path(__file__).resolve().parents[3]
CANONICAL = ROOT/'deploy/convergence/otel/otel-config.yaml'
NAME = re.compile(r'[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?')


def pairs(values, flag):
    result = {}
    for value in values:
        name, _, address = value.partition('=')
        if not NAME.fullmatch(name):
            raise ValueError(f'{flag} {value}: name must be a lowercase DNS label')
        ipaddress.ip_address(address)
        if name in result:
            raise ValueError(f'{flag} {name} is repeated')
        result[name] = address
    return result


def render(site, devices, snmp, node_port=31514, snmp_secret=None, collector_node=None, release='{{ .Release.Name }}'):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,39}', site):
        raise ValueError('Invalid site')
    if snmp and not snmp_secret:
        raise ValueError('SNMP needs --snmp-secret (a Secret holding SNMP_COMMUNITY)')
    canon = yaml.safe_load(CANONICAL.read_text())
    names = {**devices, **snmp}
    receivers = {'otlp': {'protocols': {'grpc': {'endpoint': '0.0.0.0:4317'}, 'http': {'endpoint': '0.0.0.0:4318'}}}}
    for key in ('syslog/devices', 'syslog/devices_tcp'):
        receivers[key] = copy.deepcopy(canon['receivers'][key])
    keep = ('transform/receive_time', 'transform/clean_body', 'groupbyattrs/device', 'batch', 'memory_limiter')
    processors = {key: copy.deepcopy(canon['processors'][key]) for key in keep}
    identity = copy.deepcopy(canon['processors']['transform/device_identity'])
    statements = identity['log_statements'][0]['statements']
    # Replace the canonical example inventory with this site's; keep the fallbacks.
    fallbacks = [s for s in statements if 'net.peer.ip"] ==' not in s]
    mapped = [f'set(attributes["device_name"], "{name}") where attributes["net.peer.ip"] == "{ip}"'
              for name, ip in sorted(names.items())]
    identity['log_statements'][0]['statements'] = mapped + fallbacks
    processors['transform/device_identity'] = identity
    processors['resource/devices'] = {'attributes': [
        {'action': 'upsert', 'key': 'site', 'value': site},
        {'action': 'upsert', 'key': 'service.name', 'value': 'device-syslog'},
        {'action': 'insert', 'key': 'loki.resource.labels', 'value': 'device_name,site,service.name'}]}
    queue = {'enabled': True, 'storage': 'file_storage'}
    exporters = {
        'loki': {'endpoint': f'http://{release}-loki:3100/loki/api/v1/push', 'sending_queue': queue,
                 'retry_on_failure': {'enabled': True}, 'timeout': '30s'},
        'otlphttp/logs': {'logs_endpoint': f'http://{release}-logs:9428/insert/opentelemetry/v1/logs',
                          'sending_queue': queue, 'retry_on_failure': {'enabled': True}},
        'prometheusremotewrite': {'endpoint': f'http://{release}-metrics:8428/api/v1/write',
                                  'resource_to_telemetry_conversion': {'enabled': True},
                                  'retry_on_failure': {'enabled': True}},
    }
    pipelines = {
        'logs': {'receivers': ['otlp'], 'processors': ['memory_limiter', 'batch'], 'exporters': ['otlphttp/logs']},
        'metrics': {'receivers': ['otlp'], 'processors': ['memory_limiter', 'batch'], 'exporters': ['prometheusremotewrite']},
        'logs/devices': {'receivers': ['syslog/devices', 'syslog/devices_tcp'],
                         'processors': ['memory_limiter', 'transform/device_identity', 'transform/receive_time',
                                        'transform/clean_body', 'groupbyattrs/device', 'resource/devices', 'batch'],
                         'exporters': ['loki', 'otlphttp/logs']},
    }
    template = canon['receivers']['snmp/access-sw01']
    for name, ip in sorted(snmp.items()):
        receiver = copy.deepcopy(template)
        receiver['endpoint'] = f'udp://{ip}:161'
        receivers[f'snmp/{name}'] = receiver
        processors[f'resource/{name}'] = {'attributes': [
            {'action': 'upsert', 'key': 'service.name', 'value': 'device_snmp'},
            {'action': 'upsert', 'key': 'service.instance.id', 'value': ip},
            {'action': 'upsert', 'key': 'device_name', 'value': name},
            {'action': 'upsert', 'key': 'site', 'value': site}]}
        pipelines[f'metrics/{name}'] = {'receivers': [f'snmp/{name}'],
                                        'processors': ['memory_limiter', f'resource/{name}', 'batch'],
                                        'exporters': ['prometheusremotewrite']}
    config = {'receivers': receivers, 'processors': processors, 'exporters': exporters,
              'extensions': {'file_storage': {'directory': '/data'}, 'health_check': {'endpoint': '0.0.0.0:13133'}},
              'service': {'extensions': ['file_storage', 'health_check'], 'pipelines': pipelines}}
    # Distinct Service ports: Kubernetes merges ports by number, dropping a same-numbered TCP twin.
    collector = {'config': config,
                 'service': {'type': 'NodePort', 'nodePort': None, 'grpcNodePort': None, 'externalTrafficPolicy': 'Local'},
                 'extraServicePorts': [
                     {'name': 'syslog-udp', 'port': 1514, 'targetPort': 1514, 'protocol': 'UDP', 'nodePort': node_port},
                     {'name': 'syslog-tcp', 'port': 1515, 'targetPort': 1514, 'protocol': 'TCP', 'nodePort': node_port+1}]}
    if collector_node:
        collector['nodeSelector'] = {'kubernetes.io/hostname': collector_node}
    if snmp:
        collector['envFrom'] = [{'secretRef': {'name': snmp_secret}}]
    return {'victoria': {'collector': collector}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--site', required=True)
    parser.add_argument('--device', action='append', default=[], help='name=ip of a syslog sender')
    parser.add_argument('--snmp', action='append', default=[], help='name=ip to poll with SNMP v2c')
    parser.add_argument('--syslog-node-port', type=int, default=31514)
    parser.add_argument('--snmp-secret', help='Secret with SNMP_COMMUNITY (created by the operator)')
    parser.add_argument('--collector-node', help='Kubernetes node name that runs the collector; devices send to its address')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        values = render(args.site, pairs(args.device, '--device'), pairs(args.snmp, '--snmp'),
                        args.syslog_node_port, args.snmp_secret, args.collector_node)
    except (ValueError, KeyError) as exc:
        print(f'render-device-telemetry: {exc}', file=sys.stderr)
        return 2
    Path(args.output).write_text(json.dumps(values, indent=2)+'\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Render an optional K3s goflow2 log shipper; never changes the cluster itself."""
import argparse
import ipaddress
import json
import re
from urllib.parse import urlsplit


def manifest(namespace, deployment, container, site, exporters, endpoint):
    for name in [namespace, deployment, container]:
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', name):
            raise ValueError('Use Kubernetes DNS-label names')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', site):
        raise ValueError('Invalid site identifier')
    exporters = [str(ipaddress.ip_address(ip)) for ip in exporters]
    if not exporters:
        raise ValueError('At least one exporter is required')
    url = urlsplit(endpoint)
    if url.scheme not in ['http', 'https'] or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Use an HTTP(S) VictoriaLogs base URL without credentials or query')
    name = 'netclaw-flow-logs'
    # Container metadata and exact exporter filters constrain collection. No
    # other pod logs or Kubernetes API credentials are sent to the archive.
    config = {
        'extensions': {'file_storage': {'directory': '/var/lib/netclaw-flow-logs'}, 'health_check': {'endpoint': '0.0.0.0:13133'}},
        'receivers': {'filelog/flows': {
            'include': [f'/var/log/pods/{namespace}_{deployment}-*/{container}/*.log'],
            'start_at': 'beginning', 'include_file_path': True, 'storage': 'file_storage',
            'max_log_size': '64KiB', 'retry_on_failure': {'enabled': True, 'max_elapsed_time': '0s'},
            'operators': [
                {'type': 'container', 'max_log_size': 65536},
                {'type': 'filter', 'expr': 'attributes["log.iostream"] == "stderr"'},
                {'type': 'json_parser', 'parse_from': 'body', 'parse_to': 'attributes', 'on_error': 'drop'},
                {'type': 'filter', 'expr': 'not (attributes.sampler_address in ' + json.dumps(exporters) + ')'},
                {'type': 'filter', 'expr': 'attributes.src_addr == nil or attributes.dst_addr == nil or attributes.bytes == nil'},
            ],
            'resource': {'job': 'netflow', 'site': site, 'service.name': 'netclaw-flow-logs'},
        }},
        'processors': {
            'memory_limiter': {'check_interval': '1s', 'limit_mib': 180, 'spike_limit_mib': 32},
            'batch': {'timeout': '5s', 'send_batch_size': 256, 'send_batch_max_size': 512},
        },
        'exporters': {'otlphttp/flows': {
            'logs_endpoint': endpoint.rstrip('/') + '/insert/opentelemetry/v1/logs',
            'headers': {'VL-Stream-Fields': 'job,site,sampler_address'},
            'timeout': '15s', 'retry_on_failure': {'enabled': True, 'max_elapsed_time': '0s'},
            'sending_queue': {'enabled': True, 'storage': 'file_storage', 'queue_size': 1000, 'num_consumers': 2},
        }},
        'service': {'extensions': ['file_storage', 'health_check'], 'pipelines': {'logs': {
            'receivers': ['filelog/flows'], 'processors': ['memory_limiter', 'batch'], 'exporters': ['otlphttp/flows'],
        }}},
    }
    labels = {'app.kubernetes.io/name': name}
    return {'apiVersion': 'v1', 'kind': 'List', 'items': [
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': name, 'namespace': namespace},
         'data': {'collector.yaml': json.dumps(config, indent=2)}},
        {'apiVersion': 'apps/v1', 'kind': 'DaemonSet', 'metadata': {'name': name, 'namespace': namespace},
         'spec': {'selector': {'matchLabels': labels}, 'template': {'metadata': {'labels': labels}, 'spec': {
             'automountServiceAccountToken': False,
             'containers': [{
                 'name': 'collector', 'image': 'otel/opentelemetry-collector-contrib:0.104.0',
                 'args': ['--config=/etc/otel/collector.yaml'],
                 'securityContext': {'runAsUser': 0, 'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                     'capabilities': {'drop': ['ALL']}},
                 'resources': {'requests': {'cpu': '50m', 'memory': '96Mi'}, 'limits': {'cpu': '500m', 'memory': '256Mi'}},
                 'readinessProbe': {'httpGet': {'path': '/', 'port': 13133}, 'initialDelaySeconds': 5},
                 'livenessProbe': {'httpGet': {'path': '/', 'port': 13133}, 'initialDelaySeconds': 20},
                 'volumeMounts': [
                     {'name': 'config', 'mountPath': '/etc/otel', 'readOnly': True},
                     {'name': 'logs', 'mountPath': '/var/log/pods', 'readOnly': True},
                     {'name': 'state', 'mountPath': '/var/lib/netclaw-flow-logs'},
                 ],
             }],
             'volumes': [
                 {'name': 'config', 'configMap': {'name': name}},
                 {'name': 'logs', 'hostPath': {'path': '/var/log/pods', 'type': 'Directory'}},
                 {'name': 'state', 'hostPath': {'path': '/var/lib/netclaw-flow-logs', 'type': 'DirectoryOrCreate'}},
             ],
         }}}},
    ]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', default='observability')
    parser.add_argument('--deployment', default='goflow2')
    parser.add_argument('--container', default='goflow2')
    parser.add_argument('--site', required=True)
    parser.add_argument('--exporter', action='append', required=True)
    parser.add_argument('--victorialogs', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(manifest(args.namespace, args.deployment, args.container, args.site, args.exporter, args.victorialogs), indent=2))
    except ValueError as error:
        parser.error(str(error))

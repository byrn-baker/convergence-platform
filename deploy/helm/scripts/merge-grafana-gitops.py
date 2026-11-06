#!/usr/bin/env python3
"""Merge a rendered project bundle into shared Grafana manifests, without cluster writes."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import yaml

MODULE = Path(__file__).resolve().parents[1] / 'victoria-stack/files/register-grafana.py'
spec = importlib.util.spec_from_file_location('registrar', MODULE)
registrar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registrar)


class Dumper(yaml.SafeDumper):
    pass


Dumper.add_representer(str, lambda dumper, value: dumper.represent_scalar(
    'tag:yaml.org,2002:str', value, style='|' if '\n' in value else None))


def merge(objects, namespace, release, sources_manifest, boards_manifest):
    sources, boards = [], {}
    for obj in objects:
        if not obj or obj.get('kind') != 'ConfigMap':
            continue
        if obj['metadata'].get('labels', {}).get('netclaw.io/grafana-bundle') != release:
            continue
        data = obj.get('data', {})
        sources.extend(json.loads(data.get('datasources.json', '[]')))
        boards.update({key: json.loads(raw) for key, raw in data.items()
                       if key.endswith('.json') and key != 'datasources.json'})
    if not sources:
        raise ValueError('No project data sources in rendered input')
    prefix = sources[0]['uid'].rsplit('-', 1)[0]
    project = namespace + '/' + release
    if any(not source['uid'].startswith(prefix + '-') or
           not source['name'].startswith(project + ' / ') for source in sources):
        raise ValueError('Bundle identities do not match the namespace/release')
    aliases = {source['jsonData']['netclawAlias']: source['uid'] for source in sources}
    aliases.update({'victoriametrics-longterm': aliases['prometheus'],
                    'victorialogs-longterm': aliases['victorialogs']})
    projected = {key: registrar.project_dashboard(board, prefix, project, aliases)
                 for key, board in boards.items()}
    if len({board['uid'] for board in projected.values()}) != len(projected):
        raise ValueError('Project dashboard UID collision')
    data = sources_manifest.setdefault('data', {})
    data['datasources.yml'] = yaml.safe_dump(registrar.merge_sources(
        yaml.safe_load(data.get('datasources.yml', 'apiVersion: 1\ndatasources: []')),
        sources), sort_keys=False)
    for key, board in projected.items():
        boards_manifest.setdefault('data', {})['project-' + prefix + '-' + key] = json.dumps(board, separators=(',', ':'))
    for manifest in (sources_manifest, boards_manifest):
        if sum(len(k.encode()) + len(v.encode()) for k, v in manifest['data'].items()) > 950000:
            raise ValueError('Shared Grafana ConfigMap needs sharding')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('rendered', 'namespace', 'release', 'datasources', 'dashboards', 'deployment'):
        parser.add_argument('--' + arg, required=True)
    args = parser.parse_args()
    paths = [Path(args.datasources), Path(args.dashboards), Path(args.deployment)]
    manifests = [yaml.safe_load(path.read_text()) for path in paths]
    before = json.dumps(manifests, sort_keys=True)
    merge(list(yaml.safe_load_all(Path(args.rendered).read_text())), args.namespace,
          args.release, manifests[0], manifests[1])
    if json.dumps(manifests, sort_keys=True) == before:
        print('Shared Grafana configuration already matches this project bundle')
        return
    digest = hashlib.sha256(json.dumps([m['data'] for m in manifests[:2]], sort_keys=True).encode()).hexdigest()
    manifests[2]['spec']['template'].setdefault('metadata', {}).setdefault('annotations', {})['netclaw.io/provisioning-sha256'] = digest
    for path, manifest in zip(paths, manifests):
        path.write_text(yaml.dump(manifest, Dumper=Dumper, sort_keys=False, width=120))
    print('Updated local Grafana manifests. Review and commit them to the shared observability repository.')


if __name__ == '__main__':
    main()

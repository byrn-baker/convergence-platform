"""Render and evaluate operator-owned IDS scrape expectations; no live endpoints."""
import copy
import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest
import yaml

HELM = os.environ.get('HELM_BIN', str(Path.home() / '.local/bin/helm'))
CHART = Path(__file__).resolve().parents[1] / 'convergence'
TARGETS = [dict(id='primary', site='home', job='netclaw-ids', instance='ids-one:8443'),
           dict(id='secondary', site='home', job='netclaw-ids', instance='ids-two:8443')]


def render(tmp_path, targets, override=None, check=True):
    values = {'existingSecret': 'fixture', 'api': {'image': {'tag': 'fixture'}},
              'monitoring': {'ids': {'expectedScrapes': targets}}}
    values.update(override or {})
    path = tmp_path / 'values.json'
    path.write_text(json.dumps(values))
    result = subprocess.run([HELM, 'template', 'fixture', str(CHART), '-n', 'lab', '-f', str(path)],
                            capture_output=True, text=True)
    if not check:
        return result
    assert result.returncode == 0, result.stderr
    return [value for value in yaml.safe_load_all(result.stdout) if value]


def monitoring_rules(objects):
    return next((yaml.safe_load(obj['data']['ids-scrapes.yml']) for obj in objects
                 if obj['kind'] == 'ConfigMap' and obj['metadata']['name'] == 'fixture-ids-monitoring-rules'), None)


@pytest.mark.parametrize('enabled', [False, True])
def test_empty_inventory_has_no_missing_alert_even_with_ids_enabled(tmp_path, enabled):
    objects = render(tmp_path, [], {'ids': {'enabled': enabled, 'image': {'tag': 'fixture'},
                                         'existingSecret': 'fixture', 'existingClaim': 'fixture'}})
    assert monitoring_rules(objects) is None
    assert 'IDSMetricsScrapeMissing' not in json.dumps(objects)


def test_explicit_external_inventory_renders_exact_scoped_rules_and_mount(tmp_path):
    objects = render(tmp_path, TARGETS)  # IDS workload remains disabled.
    rules = monitoring_rules(objects)['groups'][0]['rules']
    assert len(rules) == 2
    for rule, target in zip(rules, TARGETS):
        assert rule['expr'] == f'absent_over_time(up{{job="netclaw-ids",instance="{target["instance"]}"}}[5m]) == 1'
        assert rule['labels'] == {'inventory_id': target['id'], 'site': 'home', 'service': 'ids',
                                  'release': 'fixture', 'namespace': 'lab', 'investigate': 'false', 'severity': 'warning'}
    deployment = next(obj for obj in objects if obj['kind'] == 'Deployment' and obj['metadata']['name'] == 'fixture-vmalert')
    pod = deployment['spec']['template']['spec']
    sources = next(v for v in pod['volumes'] if v['name'] == 'rules')['projected']['sources']
    assert {'configMap': {'name': 'fixture-ids-monitoring-rules', 'optional': True}} in sources
    assert '-configCheckInterval=1m' in pod['containers'][0]['args']


@pytest.mark.parametrize('change', [
    lambda t: t.append(dict(t[0])),
    lambda t: t[1].update(id=t[0]['id']),
    lambda t: t[1].update(instance=t[0]['instance']),
    lambda t: t[0].update(site='absent'),
    lambda t: t[0].update(id='bad identity'),
    lambda t: t[0].update(job=''),
    lambda t: t[0].update(instance='line\nbreak'),
    lambda t: t[0].update(extra='unexpected'),
    lambda t: t[0].pop('job'),
    lambda t: t.extend(dict(t[0], id=f'extra-{n}', instance=f'node-{n}') for n in range(64)),
])
def test_invalid_or_ambiguous_inventory_refuses_render(tmp_path, change):
    targets = copy.deepcopy(TARGETS)
    change(targets)
    result = render(tmp_path, targets, check=False)
    assert result.returncode != 0
    assert 'expectedScrapes' in result.stderr


@pytest.mark.parametrize('alerting', [{'enabled': False}, {'extraRuleConfigMaps': []}])
def test_enabled_inventory_cannot_silently_omit_its_evaluator_or_rules(tmp_path, alerting):
    result = render(tmp_path, TARGETS, {'victoria': {'alerting': alerting}}, check=False)
    assert result.returncode != 0 and 'expectedScrapes' in result.stderr


def test_selector_values_are_literals_not_promql_or_template_injection(tmp_path):
    target = dict(TARGETS[0], job='a"b\\c{{x}}', instance='node"or(vector(1))')
    rule = monitoring_rules(render(tmp_path, [target]))['groups'][0]['rules'][0]
    assert rule['expr'] == 'absent_over_time(up{job=' + json.dumps(target['job']) + ',instance=' + json.dumps(target['instance']) + '}[5m]) == 1'
    if os.environ.get('PROMTOOL_BIN'):
        path = tmp_path / 'escaped.yml'
        path.write_text(yaml.safe_dump({'groups': [{'name': 'escaped', 'rules': [rule]}]}))
        result = subprocess.run([*shlex.split(os.environ['PROMTOOL_BIN']), 'check', 'rules', str(path)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not os.environ.get('PROMTOOL_BIN'), reason='set PROMTOOL_BIN for actual PromQL evaluation')
def test_promql_absence_is_per_expected_target_and_recovers(tmp_path):
    rules = monitoring_rules(render(tmp_path, TARGETS))
    (tmp_path / 'ids-scrapes.yml').write_text(yaml.safe_dump(rules))
    rule = rules['groups'][0]['rules'][1]
    expected = {'exp_labels': {**rule['labels'], 'job': 'netclaw-ids', 'instance': 'ids-two:8443'},
                'exp_annotations': rule['annotations']}
    first = {'series': 'up{job="netclaw-ids",instance="ids-one:8443"}', 'values': '1+0x20'}
    def check(at, missing=False):
        return {'eval_time': at, 'alertname': 'IDSMetricsScrapeMissing', 'exp_alerts': [expected] if missing else []}
    cases = [
        {'interval': '1m', 'input_series': [first, {'series': 'up{job="netclaw-ids",instance="ids-two:8443"}', 'values': '1+0x20'}],
         'alert_rule_test': [check('10m')]},
        {'interval': '1m', 'input_series': [first, {'series': 'up{job="unrelated",instance="ids-two:8443"}', 'values': '1+0x20'}],
         'alert_rule_test': [check('1m'), check('3m', True)]},
        {'interval': '1m', 'input_series': [first, {'series': 'up{job="netclaw-ids",instance="ids-two:8443"}',
                                                  'values': '1 stale _ _ _ _ _ _ _ 1+0x11'}],
         'alert_rule_test': [check('4m'), check('8m', True), check('10m')]},
        {'interval': '1m', 'input_series': [first, {'series': 'up{job="netclaw-ids",instance="ids-two:8443"}', 'values': '0+0x20'}],
         'alert_rule_test': [check('10m')]},  # Present-but-down belongs to the existing up==0 rule.
    ]
    fixture = {'evaluation_interval': '1m', 'rule_files': ['ids-scrapes.yml'], 'tests': cases}
    (tmp_path / 'tests.yml').write_text(yaml.safe_dump(fixture))
    command = shlex.split(os.environ['PROMTOOL_BIN'])
    result = subprocess.run([*command, 'test', 'rules', 'tests.yml'], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    # Evaluate the actual default rules with no IDS inventory and no input series.
    default = render(tmp_path, [])
    stack = next(obj for obj in default if obj['kind'] == 'ConfigMap' and obj['metadata']['name'] == 'fixture-victoria-rules')
    (tmp_path / 'ids-scrapes.yml').write_text(stack['data']['stack.yml'])
    fixture['tests'] = [{'interval': '1m', 'input_series': [], 'alert_rule_test': [check('10m')]}]
    (tmp_path / 'tests.yml').write_text(yaml.safe_dump(fixture))
    result = subprocess.run([*command, 'test', 'rules', 'tests.yml'], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_opted_in_host_enrollment_rules_are_the_canonical_asset(tmp_path):
    objects = render(tmp_path, TARGETS)
    config = next(obj for obj in objects if obj['kind'] == 'ConfigMap'
                  and obj['metadata']['name'] == 'fixture-ids-monitoring-rules')
    canonical = CHART.parents[2] / 'deploy/convergence/prometheus/alerts/host-enrollment.rules.yml'
    assert yaml.safe_load(config['data']['host-enrollment.rules.yml']) == yaml.safe_load(canonical.read_text())
    names = [r['alert'] for g in yaml.safe_load(config['data']['host-enrollment.rules.yml'])['groups'] for r in g['rules']]
    assert names == ['IDSHostEnrollmentExpiring', 'IDSHostEnrollmentExpired']
    assert 'IDSIPContextUnavailable' not in config['data']['host-enrollment.rules.yml']


@pytest.mark.skipif(not os.environ.get('PROMTOOL_BIN'), reason='set PROMTOOL_BIN for actual PromQL evaluation')
def test_host_enrollment_rules_cover_validity_and_revocation_boundaries():
    root = CHART.parents[2] / 'deploy/convergence/ids'
    result = subprocess.run([*shlex.split(os.environ['PROMTOOL_BIN']), 'test', 'rules',
                             'tests-host-enrollment-alerts.yml'], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr

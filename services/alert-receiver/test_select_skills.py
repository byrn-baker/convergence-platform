"""The skill selector scopes catalog skills only; skills from elsewhere are never removed."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('select_skills', Path(__file__).parent/'select_skills.py')
selector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selector)


def skill(root, name, description='x'):
    (root/name).mkdir(parents=True)
    (root/name/'SKILL.md').write_text(f'---\nname: {name}\ndescription: {description}\n---\nbody\n')


def test_scoping_keeps_skills_outside_the_catalog(tmp_path):
    catalog, target = tmp_path/'catalog', tmp_path/'runtime'
    for name in ('bgp', 'ospf', 'wifi'):
        skill(catalog, name)
        skill(target, name)
    skill(target, 'alert-triage')          # installed by Convergence setup, not in the catalog
    skill(target, 'home-network-context')  # the operator's own skill
    (target/'notes.txt').write_text('left alone')
    selected = [s for s in selector.load_catalog(catalog) if s['id'] == 'bgp']
    plan = selector.materialize(selected, target, True, catalog)
    assert plan['remove'] == ['ospf', 'wifi']
    assert plan['outside_catalog'] == ['alert-triage', 'home-network-context']
    assert sorted(p.name for p in target.iterdir()) == ['alert-triage', 'bgp', 'home-network-context', 'notes.txt']
    assert plan['final_count'] == 3


def test_restore_all_returns_catalog_skills_and_keeps_the_rest(tmp_path):
    catalog, target = tmp_path/'catalog', tmp_path/'runtime'
    for name in ('bgp', 'ospf'):
        skill(catalog, name)
    skill(target, 'bgp')
    skill(target, 'alert-triage')
    plan = selector.materialize(selector.load_catalog(catalog), target, True, catalog)
    assert plan['add'] == ['ospf'] and plan['remove'] == []
    assert sorted(p.name for p in target.iterdir()) == ['alert-triage', 'bgp', 'ospf']


def test_dry_run_changes_nothing(tmp_path):
    catalog, target = tmp_path/'catalog', tmp_path/'runtime'
    skill(catalog, 'bgp')
    skill(target, 'ospf-old')
    skill(catalog, 'ospf-old')
    plan = selector.materialize([], target, False, catalog)
    assert plan['remove'] == ['ospf-old'] and (target/'ospf-old').exists()

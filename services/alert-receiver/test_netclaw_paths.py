"""The receiver and its skill selector ship with Convergence; NetClaw's skill catalog and RAG come from NETCLAW_DIR."""
import importlib.util
from pathlib import Path


def load(monkeypatch, **env):
    for key in ('NETCLAW_DIR', 'ALERT_RECEIVER_INVENTORY', 'SKILL_SELECTOR_SCRIPT', 'SKILL_CATALOG_DIR'):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location('receiver_paths', Path(__file__).parent/'server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_netclaw_paths_follow_netclaw_dir(monkeypatch, tmp_path):
    module = load(monkeypatch, NETCLAW_DIR=str(tmp_path/'netclaw'), ALERT_RECEIVER_INVENTORY=str(tmp_path/'inv.yaml'))
    assert module.REPO_ROOT == tmp_path/'netclaw'
    assert module.SKILL_SELECTOR_SCRIPT == str(Path(__file__).resolve().parent/'select_skills.py')
    assert Path(module.SKILL_SELECTOR_SCRIPT).is_file()
    assert module.SKILL_CATALOG_DIR == str(tmp_path/'netclaw/workspace/skills')
    assert module.INVENTORY_FILE == tmp_path/'inv.yaml'


def test_defaults_keep_the_in_tree_layout(monkeypatch):
    module = load(monkeypatch)
    assert module.REPO_ROOT == Path(__file__).resolve().parents[2]
    assert module.INVENTORY_FILE == Path(__file__).parent/'inventory.yaml'

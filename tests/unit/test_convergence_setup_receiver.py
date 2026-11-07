"""The receiver unit runs this checkout with the bundle's environment, for any user and path."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('convergence_receiver', ROOT/'setup/receiver.py')
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)


def test_unit_uses_checkout_bundle_and_private_state():
    text = receiver.unit('convergence-alert-receiver', Path('/b/receiver.env'), Path('/s/venv'), Path('/s'),
                         Path('/n'), Path('/n/.env'))
    assert f'WorkingDirectory={ROOT}/services/alert-receiver' in text
    assert 'ExecStart=/s/venv/bin/python server.py' in text
    assert 'EnvironmentFile=/b/receiver.env' in text
    assert 'Environment=ALERT_JOBS_PATH=/s/jobs.sqlite' in text
    assert 'Environment=NETCLAW_DIR=/n' in text and 'Environment=CONVERGENCE_JEV_ENV_FILE=/n/.env' in text
    assert 'WantedBy=default.target' in text and 'User=' not in text


def test_unit_rejects_paths_systemd_would_reinterpret():
    for bad in ('/b/my env', '/b/%h', '/b/$X'):
        with pytest.raises(ValueError):
            receiver.unit('r', Path(bad), Path('/v'), Path('/s'))


def test_install_requires_a_private_complete_env(tmp_path):
    env = tmp_path/'receiver.env'
    env.write_text('ALERT_RECEIVER_TOKEN=x\n')
    env.chmod(0o644)
    args = receiver.argparse.Namespace(env_file=str(env), netclaw_dir=None, jev_env_file=None)
    with pytest.raises(ValueError, match='private'):
        receiver.install(args)
    env.chmod(0o600)
    with pytest.raises(ValueError, match='NETWORK_GUARDIAN_TOKEN'):
        receiver.install(args)

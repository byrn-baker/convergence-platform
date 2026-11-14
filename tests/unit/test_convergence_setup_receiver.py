"""The receiver unit runs this checkout with the bundle's environment, for any user and path."""
import importlib.util
import sys
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
    assert 'RAG_PYTHON' not in text
    assert 'WantedBy=default.target' in text and 'User=' not in text


def test_unit_records_rag_python():
    text = receiver.unit('convergence-alert-receiver', Path('/b/receiver.env'), Path('/s/venv'), Path('/s'),
                         Path('/n'), Path('/n/.env'), Path('/opt/rag/bin/python3'))
    assert 'Environment=RAG_PYTHON=/opt/rag/bin/python3' in text
    assert 'Environment=NETCLAW_DIR=/n' in text


def test_rag_python_keeps_the_symlink_path(tmp_path):
    link = tmp_path / 'rag-runtime' / 'bin' / 'python3'
    link.parent.mkdir(parents=True)
    link.symlink_to(sys.executable)
    recorded = receiver._rag_python(str(link), Path('/n'))
    assert recorded == link.absolute()
    assert recorded.is_symlink()
    text = receiver.unit('convergence-alert-receiver', Path('/b/receiver.env'), Path('/s/venv'), Path('/s'),
                         Path('/n'), None, recorded)
    assert f'Environment=RAG_PYTHON={recorded}' in text
    assert str(link.resolve()) not in text


def test_unit_rejects_rag_python_paths_systemd_would_reinterpret():
    for bad in ('/opt/my python', '/opt/%h/python', '/opt/$X/python'):
        with pytest.raises(ValueError):
            receiver.unit('r', Path('/b'), Path('/v'), Path('/s'), Path('/n'), None, Path(bad))


def test_rag_python_must_be_an_executable_and_needs_netclaw(tmp_path):
    env = tmp_path/'receiver.env'
    env.write_text('ALERT_RECEIVER_TOKEN=x\nNETWORK_GUARDIAN_URL=http://cases\nNETWORK_GUARDIAN_TOKEN=y\n')
    env.chmod(0o600)
    netclaw = tmp_path/'netclaw'
    (netclaw/'mcp-servers/jev-mcp').mkdir(parents=True)
    (netclaw/'mcp-servers/jev-mcp/core.py').write_text('# jev\n')
    script = tmp_path/'rag-python'
    script.write_text('#!/bin/sh\n')
    args = receiver.argparse.Namespace(
        name='convergence-alert-receiver', env_file=str(env), venv=None, python='python3',
        netclaw_dir=str(netclaw), jev_env_file=None, rag_python=str(script), start=False)
    with pytest.raises(ValueError, match='executable'):
        receiver.install(args)
    script.chmod(0o755)
    args.netclaw_dir = None
    with pytest.raises(ValueError, match='netclaw-dir'):
        receiver.install(args)


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


def private_environment(tmp_path, extra=''):
    env = tmp_path/'receiver.env'
    env.write_text('ALERT_RECEIVER_TOKEN=ordinary-fixture\nNETWORK_GUARDIAN_URL=http://cases\nNETWORK_GUARDIAN_TOKEN=fixture\n' + extra)
    env.chmod(0o600)
    return env


def test_preflight_is_read_only_and_ignores_invoking_shell_settings(tmp_path, monkeypatch):
    env = private_environment(tmp_path)
    monkeypatch.setenv('REVIEWER_HTTPS_PORT', 'bad')
    before = set(tmp_path.iterdir())
    result = receiver.preflight(receiver.argparse.Namespace(env_file=str(env)))
    assert result == {'status':'valid', 'env_file':str(env), 'https_configured':False}
    assert set(tmp_path.iterdir()) == before


def test_partial_tls_rejected_before_installer_mutations(tmp_path, monkeypatch):
    env = private_environment(tmp_path, 'REVIEWER_HTTPS_PORT=9443\n')
    monkeypatch.setattr(receiver.subprocess, 'run', lambda *a, **k: pytest.fail('Must not run commands on invalid TLS'))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path/'state'))
    args = receiver.argparse.Namespace(env_file=str(env))
    with pytest.raises(ValueError):
        receiver.install(args)
    assert not (tmp_path/'state').exists()


def test_preflight_reads_literal_values_without_expansion_or_execution(tmp_path):
    marker = tmp_path/'unexpected'
    env = private_environment(tmp_path, f'UNRELATED="$(touch {marker})"\n')
    assert receiver.preflight(receiver.argparse.Namespace(env_file=str(env)))['status'] == 'valid'
    assert not marker.exists()


def test_invalid_env_does_not_echo_credentials(tmp_path):
    secret = 'fixture-value-must-not-appear'
    env = private_environment(tmp_path, "UNRELATED='" + secret + '\n')
    with pytest.raises(ValueError) as failure:
        receiver.preflight(receiver.argparse.Namespace(env_file=str(env)))
    assert secret not in str(failure.value)


def test_tls_preflight_validates_real_pair_without_changing_environment(tmp_path):
    import subprocess
    cert = tmp_path/'receiver cert.pem'
    key = tmp_path/'receiver key.pem'
    other = tmp_path/'other key.pem'
    subprocess.run(['openssl','req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256',
                    '-nodes','-days','1','-subj','/CN=localhost','-keyout',str(key),'-out',str(cert)],
                   check=True, capture_output=True)
    subprocess.run(['openssl','genpkey','-algorithm','EC','-pkeyopt','ec_paramgen_curve:P-256','-out',str(other)],
                   check=True, capture_output=True)
    extra = ('REVIEWER_HTTPS_HOST=127.0.0.1\nREVIEWER_HTTPS_PORT=9443\n'
             f'REVIEWER_HTTPS_CERT_FILE="{cert}"\nREVIEWER_HTTPS_KEY_FILE="{key}"\n'
             'REVIEWER_DELIVERY_TOKEN=' + 'x'*40 + '\n')
    env = private_environment(tmp_path, extra)
    initial = env.read_bytes()
    assert receiver.preflight(receiver.argparse.Namespace(env_file=str(env)))['https_configured']
    assert env.read_bytes() == initial
    env.write_text(initial.decode().replace(str(key), str(other)))
    with pytest.raises((ValueError, OSError)):
        receiver.preflight(receiver.argparse.Namespace(env_file=str(env)))

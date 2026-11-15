"""Opt-in service-boundary proof; no live RAG, model, case API or production queue.

Run with CONVERGENCE_SYSTEMD_RAG_TEST=1 and an available systemd user manager.
Raw SIGTERM comparison always runs. Each fixture owns a new private directory,
loopback port, synthetic RAG module, SQLite queue and (optionally) transient unit.
"""
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

import pytest


SERVICE = Path(__file__).resolve().parents[2] / 'services' / 'alert-receiver'
RUNNER = Path(__file__).with_name('rag_restart_fixture.py')
BUS_ENV = {**os.environ, 'XDG_RUNTIME_DIR': '/run/user/1000',
           'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/1000/bus'}
FAKE_RAG = '''
import json, os, signal, time
from pathlib import Path
root = Path(os.environ['RAG_FIXTURE_DIR'])
generation = int(os.environ['RAG_FIXTURE_GENERATION'])

def run(op):
    pid = os.getpid()
    if generation == 1 and os.environ.get('RAG_FIXTURE_IGNORE_TERM') == '1':
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if op == 'snapshot':
        with (root / 'snapshot-writes.jsonl').open('a') as f:
            f.write(json.dumps({'generation': generation, 'pid': pid, 'phase': 'partial'}) + '\\n')
    with (root / 'rag.jsonl').open('a') as f:
        f.write(json.dumps({'op': op, 'pid': pid, 'generation': generation,
                           'time': time.time(), 'cgroup': Path('/proc/self/cgroup').read_text()}) + '\\n')
    if generation == 1:
        while True:
            (root / ('heartbeat-' + str(pid))).write_text(str(time.time()))
            time.sleep(.02)
    if op == 'snapshot':
        with (root / 'snapshot-writes.jsonl').open('a') as f:
            f.write(json.dumps({'generation': generation, 'pid': pid, 'phase': 'complete'}) + '\\n')
    return {'success': True, 'data': {'results': [], 'snapshot_id': 'synthetic-snapshot', 'chunk_count': 1}}

def _do_search(**kwargs): return run('search')
def _do_snapshot(**kwargs): return run('snapshot')
'''


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.02)
    raise AssertionError('Isolated fixture did not reach expected state')


def alive(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1][0] != 'Z'
    except FileNotFoundError:
        return False


def prepare(root):
    rag = root / 'netclaw' / 'mcp-servers' / 'rag-mcp'
    rag.mkdir(parents=True)
    (rag / 'rag_mcp_server.py').write_text(FAKE_RAG)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    env = {'NETCLAW_DIR': str(root / 'netclaw'), 'RAG_PYTHON': sys.executable,
           'RAG_FIXTURE_DIR': str(root), 'RAG_TIMEOUT_SECONDS': '60', 'RAG_PRIOR_SEARCH': 'true',
           'ALERT_JOBS_PATH': str(root / 'jobs.sqlite'), 'ALERT_RECEIVER_HOST': '127.0.0.1',
           'ALERT_RECEIVER_PORT': str(port), 'ALERT_RECEIVER_TOKEN': 'isolated-ordinary-token',
           'REVIEWER_DELIVERY_TOKEN': 'isolated-dedicated-token', 'NETWORK_GUARDIAN_URL': '',
           'NETWORK_GUARDIAN_TOKEN': '', 'OPENCLAW_GATEWAY_URL': '', 'OPENCLAW_HOOK_TOKEN': '',
           'REVIEWER_HTTPS_HOST': '', 'REVIEWER_HTTPS_PORT': '',
           'REVIEWER_HTTPS_CERT_FILE': '', 'REVIEWER_HTTPS_KEY_FILE': '',
           'LOG_LEVEL': 'ERROR'}
    (root / 'environment.json').write_text(json.dumps(env))
    return [sys.executable, str(RUNNER), str(root), str(SERVICE)]


def cleanup_children(root):
    for child in records(root / 'rag.jsonl'):
        if alive(child['pid']):
            os.killpg(child['pid'], signal.SIGKILL)


@pytest.mark.parametrize('mode', ['search', 'snapshot'])
def test_raw_python_sigterm_does_not_stop_active_rag_child(tmp_path, mode):
    command = prepare(tmp_path) + [mode]
    with (tmp_path / 'receiver.log').open('w') as output:
        process = subprocess.Popen(command, cwd=tmp_path, stdout=output, stderr=output)
        try:
            child = wait_until(lambda: records(tmp_path / 'rag.jsonl'))[0]
            heartbeat = tmp_path / ('heartbeat-' + str(child['pid']))
            wait_until(lambda: heartbeat.exists() and heartbeat.read_text())
            process.send_signal(signal.SIGTERM)
            wait_until(lambda: any(r['event'] == 'lifespan-stop' for r in records(tmp_path / 'receiver.jsonl')))
            before = heartbeat.read_text()
            wait_until(lambda: heartbeat.read_text() != before)
            assert alive(child['pid']) and process.poll() is None
            cleanup_children(tmp_path)
            assert process.wait(timeout=5) == 0
        finally:
            cleanup_children(tmp_path)
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


@pytest.mark.skipif(os.environ.get('CONVERGENCE_SYSTEMD_RAG_TEST') != '1',
                    reason='Explicit opt-in required for disposable systemd user unit')
@pytest.mark.parametrize('mode,ignore_term', [('search', False), ('snapshot', False), ('snapshot', True)])
def test_systemd_restart_contains_rag_and_preserves_receipts(tmp_path, mode, ignore_term):
    command = prepare(tmp_path) + [mode]
    if ignore_term:
        env_path = tmp_path / 'environment.json'
        environment = json.loads(env_path.read_text())
        environment.update(RAG_FIXTURE_IGNORE_TERM='1', RAG_TIMEOUT_SECONDS='180')
        env_path.write_text(json.dumps(environment))
    unit = 'convergence-rag-proof-' + uuid4().hex
    def control(*args, check=True):
        return subprocess.run(['systemctl', '--user', *args], env=BUS_ENV,
                              capture_output=True, text=True, check=check, timeout=100)
    subprocess.run(['systemd-run', '--user', '--unit=' + unit, '--collect',
                    '--property=KillMode=control-group', '--property=KillSignal=SIGTERM',
                    '--property=SendSIGKILL=yes', '--property=TimeoutStopSec=90',
                    '--property=Restart=no', '--property=WorkingDirectory=' + str(tmp_path),
                    '--property=StandardOutput=append:' + str(tmp_path / 'receiver.log'),
                    '--property=StandardError=append:' + str(tmp_path / 'receiver.log'), *command],
                   env=BUS_ENV, capture_output=True, text=True, check=True, timeout=10)
    try:
        child = wait_until(lambda: records(tmp_path / 'rag.jsonl'))[0]
        assert unit in child['cgroup']  # setsid() did not escape service ownership.
        heartbeat = tmp_path / ('heartbeat-' + str(child['pid']))
        wait_until(lambda: heartbeat.exists() and heartbeat.read_text())
        started = time.monotonic()
        control('restart', unit)
        elapsed = time.monotonic() - started
        assert not alive(child['pid'])
        second = wait_until(lambda: [r for r in records(tmp_path / 'rag.jsonl') if r['generation'] == 2])[0]
        assert second['pid'] != child['pid']
        boot = [r for r in records(tmp_path / 'receiver.jsonl') if r['event'] == 'boot' and r['generation'] == 2][0]
        assert boot['old_children'] == []
        if ignore_term:
            assert 89 <= elapsed <= 97  # Exercise the actual supported 90-second SIGKILL boundary.
        last_write = heartbeat.read_text()
        receipts = json.loads((tmp_path / 'receipts.json').read_text())
        def checkpoint_recovered():
            with sqlite3.connect(tmp_path / 'jobs.sqlite') as db:
                row = db.execute('SELECT state FROM reviewer_command_receipts WHERE delivery_id=?',
                                 (receipts['checkpointed']['delivery_id'],)).fetchone()
                return row and row[0] == 'unknown'
        wait_until(checkpoint_recovered)
        with sqlite3.connect(tmp_path / 'jobs.sqlite') as db:
            assert db.execute('SELECT state FROM reviewer_command_receipts WHERE delivery_id=?',
                              (receipts['accepted']['delivery_id'],)).fetchone()[0] == 'enqueued'
            assert db.execute('SELECT state FROM jobs WHERE id=?',
                              (receipts['accepted']['job_id'],)).fetchone()[0] == 'queued'
            assert db.execute('SELECT dispatched FROM jobs WHERE id=?',
                              (receipts['checkpointed']['job_id'],)).fetchone()[0] is not None
        assert heartbeat.read_text() == last_write
        assert not [r for r in records(tmp_path / 'receiver.jsonl') if r['event'] == 'hook']
        if mode == 'snapshot':
            wait_until(lambda: any(r['event'] == 'case-link' for r in records(tmp_path / 'receiver.jsonl')))
            writes = records(tmp_path / 'snapshot-writes.jsonl')
            assert [(r['generation'], r['phase']) for r in writes] == [(1, 'partial'), (2, 'partial'), (2, 'complete')]
        print(f'{mode} (ignore SIGTERM={ignore_term}): matching-policy restart {elapsed:.3f}s; '
              'old RAG child dead before replacement boot; checkpoint unknown; no hook')
    finally:
        control('stop', unit, check=False)
        control('reset-failed', unit, check=False)
        cleanup_children(tmp_path)

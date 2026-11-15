"""Reviewer HTTPS certificate renewal applies in place (specs/1016-tls-renewal-reload)."""
import asyncio
import hashlib
import os
import shutil
import socket
import ssl
import subprocess
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import receiver_runtime as runtime
from fastapi import FastAPI
from tls_reload import TLSReloader


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def pair(root, name):
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
                    '-keyout', str(root/(name+'.key')), '-out', str(root/(name+'.pem')), '-days', '1',
                    '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return root/(name+'.pem'), root/(name+'.key')


def publish(source, target):
    """The renewal tool's write: a complete temporary file renamed over the live one."""
    temporary = target.with_name('.'+target.name+'.new')
    shutil.copyfile(source, temporary)
    os.replace(temporary, target)


def served_fingerprint(port):
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection(('127.0.0.1', port), timeout=2) as raw, context.wrap_socket(raw) as tls:
        return hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()


def fingerprint(pem):
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(Path(pem).read_text())).hexdigest()


def test_reloader_keeps_current_material_and_logs_each_failure_once(tmp_path):
    files = [tmp_path/'tls.crt', tmp_path/'tls.key']
    for path in files:
        path.write_text('v1')
    applied, logs, outcomes = [], [], iter(['ok', 'bad', 'bad', 'ok'])

    def build():
        if next(outcomes) == 'bad':
            raise ValueError('certificate/key could not be loaded or do not match')
        return files[0].read_text()
    reloader = TLSReloader(files, build, applied.append, log=logs.append)
    reloader.check()
    reloader.check()
    assert applied == ['v1'] and reloader.reloads == 0
    for content in ('v2', 'v3'):
        files[0].write_text(content)
        os.utime(files[0], ns=(1, len(content) + hash(content) % 1000))
        reloader.check()
    assert applied == ['v1'] and reloader.error.endswith('do not match')
    files[1].unlink()
    reloader.check()
    files[1].write_text('v1')
    reloader.check()
    assert applied == ['v1', 'v3'] and reloader.reloads == 1 and reloader.error is None
    assert logs == ['Reviewer HTTPS TLS reload rejected: certificate/key could not be loaded or do not match',
                    'Reviewer HTTPS TLS files are unreadable']


def test_renewed_reviewer_certificate_is_served_without_restarting(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'TLS_RELOAD_SECONDS', .05)
    first_cert, first_key = pair(tmp_path, 'first')
    next_cert, next_key = pair(tmp_path, 'next')
    live = tmp_path/'live'
    live.mkdir()
    cert, key = live/'receiver.crt', live/'receiver.key'
    publish(first_cert, cert)
    publish(first_key, key)
    http_port, tls_port = free_port(), free_port()
    config = replace(runtime.load_settings({
        'ALERT_RECEIVER_HOST': '127.0.0.1', 'ALERT_RECEIVER_PORT': str(http_port),
        'REVIEWER_HTTPS_HOST': '127.0.0.1', 'REVIEWER_HTTPS_PORT': str(tls_port),
        'REVIEWER_HTTPS_CERT_FILE': str(cert), 'REVIEWER_HTTPS_KEY_FILE': str(key),
        'REVIEWER_DELIVERY_TOKEN': 'reviewer-fixture', 'ALERT_RECEIVER_TOKEN': 'legacy-fixture', 'LOG_LEVEL': 'ERROR',
    }), shutdown_seconds=.3)
    app = FastAPI()

    @app.get('/health')
    async def health():
        return {'ok': True}

    async def serves(expected):
        for _ in range(100):
            try:
                if await asyncio.to_thread(served_fingerprint, tls_port) == expected:
                    return True
            except OSError:
                pass  # the listener is still starting
            await asyncio.sleep(.02)
        return False

    async def run():
        task = asyncio.create_task(runtime.serve(app, config, handle_signals=False))
        try:
            assert await serves(fingerprint(first_cert))
            # Half-written renewal (new certificate, old key): rejected, old pair keeps serving.
            publish(next_cert, cert)
            await asyncio.sleep(.3)
            assert await asyncio.to_thread(served_fingerprint, tls_port) == fingerprint(first_cert)
            publish(next_key, key)
            assert await serves(fingerprint(next_cert))
            # Garbage is rejected; the renewed pair keeps serving and HTTPS stays healthy.
            (live/'junk').write_text('not a certificate')
            publish(live/'junk', cert)
            await asyncio.sleep(.3)
            assert await asyncio.to_thread(served_fingerprint, tls_port) == fingerprint(next_cert)
            async with httpx.AsyncClient(verify=False, trust_env=False, timeout=2) as client:
                assert (await client.get(f'https://127.0.0.1:{tls_port}/health')).status_code in (200, 404)
            assert not task.done(), 'the receiver kept running throughout'
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
    asyncio.run(run())

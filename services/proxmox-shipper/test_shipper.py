import importlib.util
import contextlib
import io
import json
from pathlib import Path
import tempfile
import sys
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('shipper', Path(__file__).with_name('shipper.py'))
shipper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shipper)


class ShipperTests(unittest.TestCase):
    def test_redirect_never_forwards_bearer(self):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append((self.path, self.headers.get('Authorization')))
                self.send_response(307)
                self.send_header('Location', '/untrusted-destination')
                self.end_headers()
            def do_GET(self):
                seen.append((self.path, self.headers.get('Authorization')))
                self.send_response(200)
                self.end_headers()
            def log_message(self, *_):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            client = urllib.request.build_opener(shipper.NoRedirect())
            config = {'url': f'http://127.0.0.1:{server.server_port}/v1/hypervisor-events'}
            with self.assertRaises(urllib.error.HTTPError) as caught:
                shipper.publish(client, config, 'test-bearer', b'{}')
            self.assertEqual(caught.exception.code, 307)
            self.assertEqual(seen, [('/v1/hypervisor-events', 'Bearer test-bearer')])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_untrusted_stale_future_and_duplicate_snapshots_rejected(self):
        config = {'node': 'node1', 'ssh_host': 'root@192.0.2.1', 'known_hosts_file': '/pins', 'identity_file': '/identity'}
        valid = {'node': 'node1', 'schema_version': 1, 'observed_at': time.time()}
        cases = [dict(valid, node='node2'), dict(valid, observed_at=time.time() - 121),
                 dict(valid, observed_at=time.time() + 31), dict(valid, observed_at=True)]
        for snapshot in cases:
            with self.subTest(snapshot=snapshot), patch.object(shipper.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(snapshot).encode())):
                with self.assertRaises(ValueError):
                    shipper.read_snapshot(config)
        with patch.object(shipper.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=b'{"node":"node1","node":"node2"}')):
            with self.assertRaises(ValueError):
                shipper.read_snapshot(config)

    def test_invalid_transport_configuration_rejected_before_ssh(self):
        for url in ['http://receiver/v1/hypervisor-events', 'https://token@receiver/v1/hypervisor-events',
                    'https://receiver/v1/eve', 'https://receiver/v1/hypervisor-events?token=secret']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                shipper.validate_config({'url': url, 'node': 'node1'})

    def test_failure_status_is_atomic_private_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'status.json'
            shipper.status_file(path, {'failures': 1, 'last_error_type': 'TimeoutError'})
            self.assertEqual(json.loads(path.read_text())['failures'], 1)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse(path.with_name('status.json.tmp').exists())


class RelayTests(unittest.TestCase):
    def snapshot(self, **changes):
        return json.dumps({'schema_version': 1, 'node': 'node1', 'observed_at': time.time(),
                           'collector_started_at': time.time()-10, 'sequence': 1, **changes}).encode()

    def test_relay_command_is_fixed_pinned_and_token_free(self):
        config = {'node': 'node1', 'relay_host': 'ubuntu@192.168.3.252',
                  'known_hosts_file': '/pins', 'identity_file': '/key'}
        raw = self.snapshot()
        with patch.object(shipper, 'bounded_exchange', return_value=b'{"status":"accepted"}') as exchange:
            shipper.relay_publish(config, raw)
        args = exchange.call_args.args[0]
        self.assertEqual(exchange.call_args.args[1], raw)
        self.assertEqual(exchange.call_args.kwargs, {'timeout': 20})
        self.assertEqual(args[-2:], ['ubuntu@192.168.3.252', shipper.RELAY_COMMAND])
        for flag in ('StrictHostKeyChecking=yes', 'BatchMode=yes', 'IdentitiesOnly=yes',
                     'ForwardAgent=no', 'ClearAllForwardings=yes', 'GlobalKnownHostsFile=/dev/null',
                     'UpdateHostKeys=no', 'UserKnownHostsFile=/pins'):
            self.assertIn(flag, args)
        self.assertEqual(args[1:3], ['-F', '/dev/null'])
        self.assertNotIn('token', ' '.join(args))

    def test_relay_backend_never_reads_credentials_or_tls_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key, pins = root/'key', root/'pins'
            key.touch()
            pins.touch()
            config = {'node': 'node1', 'ssh_host': 'root@192.0.2.1',
                      'relay_host': 'ubuntu@192.168.3.252', 'identity_file': str(key),
                      'known_hosts_file': str(pins), 'token_file': '/must-not-read', 'ca_file': '/must-not-read'}
            config_path = root/'config.json'
            config_path.write_text(json.dumps(config))
            raw = self.snapshot()
            with patch.object(shipper, 'read_token', side_effect=AssertionError('credential accessed')) as token, \
                    patch.object(shipper, 'opener', side_effect=AssertionError('TLS context created')) as tls, \
                    patch.object(shipper, 'read_snapshot', return_value=(raw, json.loads(raw))), \
                    patch.object(shipper, 'relay_publish') as publish:
                self.assertEqual(shipper.run(SimpleNamespace(config=str(config_path), once=True, publish_stdin=False)), 0)
            token.assert_not_called()
            tls.assert_not_called()
            publish.assert_called_once_with(config, raw)

    def test_publisher_accepts_minimal_local_config_and_local_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token, ca = root/'token', root/'ca'
            token.write_text('secret-'*6)
            ca.touch()
            config = {'node': 'node1', 'url': 'https://receiver/v1/hypervisor-events',
                      'ca_file': str(ca), 'token_file': str(token)}
            self.assertEqual(shipper.validate_config(config, publish_stdin=True), config)
            output = io.StringIO()
            raw = self.snapshot()
            with patch.dict(shipper.os.environ, {'CREDENTIALS_DIRECTORY': '/must-not-read'}), \
                    patch.object(shipper, 'opener', return_value='client'), \
                    patch.object(shipper, 'publish') as publish, contextlib.redirect_stdout(output):
                self.assertEqual(shipper.publish_stdin(config, io.BytesIO(raw)), 0)
            publish.assert_called_once_with('client', config, 'secret-'*6, raw)
            self.assertEqual(json.loads(output.getvalue()), {'status': 'accepted'})
            self.assertNotIn('secret', output.getvalue())

    def test_publisher_rejects_bad_stdin_before_token_or_network(self):
        invalid = [b'x'*(shipper.MAX_BYTES+1), b'{', b'[]', b'{"node":"node1","node":"node1"}',
                   self.snapshot(node='other'), self.snapshot(observed_at=time.time()-121),
                   self.snapshot(observed_at=time.time()+31), self.snapshot(schema_version=True),
                   self.snapshot().replace(b'"sequence": 1', b'"sequence": 1e999')]
        with patch.object(shipper, 'read_token', side_effect=AssertionError('token read')) as token:
            for raw in invalid:
                with self.subTest(raw_length=len(raw)), self.assertRaises((ValueError, json.JSONDecodeError)):
                    shipper.publish_stdin({'node': 'node1'}, io.BytesIO(raw))
        token.assert_not_called()

    def test_destination_validation_rejects_options_and_shell_syntax(self):
        for destination in ('root@192.0.2.1', 'ubuntu@-option', 'ubuntu@host;id',
                            'ubuntu@host$(id)', 'ubuntu@host/path', 'ubuntu@host\n',
                            'ubuntu@host:22', 'ubuntu@host..invalid', 'ubuntu@', 'ubuntu@fe80::1%$(id)'):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                shipper.validate_host(destination, 'ubuntu')
        for destination in ('ubuntu@192.168.3.252', 'ubuntu@source.example.com', 'ubuntu@2001:db8::1'):
            shipper.validate_host(destination, 'ubuntu')

    def test_invalid_ack_never_counts_as_success(self):
        config = {'node': 'node1', 'relay_host': 'ubuntu@192.168.3.252', 'known_hosts_file': '/pins', 'identity_file': '/key'}
        for ack in (b'{}', b'[]', b'{"status":"accepted","extra":"untrusted"}',
                    b'{"status":"rejected","status":"accepted"}'):
            with self.subTest(ack=ack), patch.object(shipper, 'bounded_exchange', return_value=ack):
                with self.assertRaises((ValueError, RuntimeError)):
                    shipper.relay_publish(config, self.snapshot())

    def test_subprocess_exchange_bounds_ack_and_wall_time(self):
        command = [sys.executable, '-c', "import sys, json; sys.stdin.buffer.read(); print(json.dumps(dict(status='accepted')))"]
        self.assertEqual(json.loads(shipper.bounded_exchange(command, b'x'*shipper.MAX_BYTES)), {'status': 'accepted'})
        with self.assertRaises(ValueError):
            shipper.bounded_exchange([sys.executable, '-c', 'import sys; sys.stdout.write("x"*8192)'], b'')
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            shipper.bounded_exchange([sys.executable, '-c', 'import time; time.sleep(10)'], b'input', timeout=.1)
        self.assertLess(time.monotonic()-started, 2)

    def test_cli_failures_redact_startup_publish_and_argument_details(self):
        secret = 'PRIVATE_TOKEN_OR_URL'
        for arguments, runner in ((['--config', 'config'], True),
                                  (['--config', 'config', '--publish-stdin'], True),
                                  (['--config', 'config', '--unknown', secret], False)):
            output, errors = io.StringIO(), io.StringIO()
            with patch.object(sys, 'argv', ['shipper.py', *arguments]), \
                    patch.object(shipper, 'run', side_effect=ValueError(secret)) as run, \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(shipper.main(), 1)
            self.assertEqual(run.called, runner)
            self.assertEqual(output.getvalue(), '')
            self.assertEqual(json.loads(errors.getvalue()), {'event': 'publication_failed', 'error_type': 'ValueError'})
            self.assertNotIn(secret, errors.getvalue())


if __name__ == '__main__':
    unittest.main()

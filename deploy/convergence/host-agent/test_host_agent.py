"""Synthetic agent tests: no remote endpoint, customer host or provider access."""
import importlib.util
import json
import os
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import Mock, patch

MODULE = Path(__file__).with_name('agent.py')
spec = importlib.util.spec_from_file_location('convergence_host_agent', MODULE)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.token = self.root/'token'
        self.token.write_text('synthetic-test-credential\n')
        self.token.chmod(0o600)
        self.spools = []

    def tearDown(self):
        for spool in self.spools:
            spool.close()
        self.tmp.cleanup()

    def spool(self, name='spool', limit=128*1024):
        spool = agent.Spool(self.root/name, limit)
        self.spools.append(spool)
        return spool

    def config(self):
        return {'endpoint': 'https://telemetry.example.invalid:8445/v1/host-events',
                'token_file': str(self.token), 'spool_dir': str(self.root/'spool')}

    def transport(self, status=200, ack=None):
        response = Mock()
        response.status = status
        response.read.return_value = agent.encode(ack if ack is not None else {
            'status': 'accepted', 'received': 1, 'inserted': 1})
        connection = Mock()
        connection.getresponse.return_value = response
        factory = Mock(return_value=connection)
        return agent.Transport(self.config(), factory), connection, response, factory

    def test_heartbeat_contains_only_own_queue_health(self):
        spool = self.spool()
        self.assertTrue(spool.queue_heartbeat(now=1700000000))
        names, payload = spool.batch()
        self.assertEqual(set(payload), {'schema_version', 'events'})
        event = payload['events'][0]
        self.assertEqual(set(event), {'event_id', 'timestamp', 'event_type', 'data'})
        self.assertEqual(event['event_type'], 'heartbeat')
        self.assertEqual(set(event['data']), {'collection_ok', 'queue_events', 'queue_bytes', 'dropped_events'})
        self.assertEqual(event['data']['queue_events'], 0)
        self.assertEqual(event['timestamp'], '2023-11-14T22:13:20+00:00')
        self.assertEqual(len(names), 1)
        self.assertEqual((spool.path/names[0]).stat().st_mode & 0o777, 0o600)

    def test_outage_retains_exact_batch_and_id_across_restart(self):
        spool = self.spool()
        spool.queue_heartbeat()
        before = spool.batch()
        transport = Mock()
        transport.send.side_effect = agent.DeliveryError('fixture outage')
        with self.assertRaises(agent.DeliveryError):
            agent.deliver_once(spool, transport)
        spool.close()
        recovered = self.spool()
        self.assertEqual(recovered.batch(), before)
        transport.send.side_effect = None
        self.assertTrue(agent.deliver_once(recovered, transport))
        self.assertEqual(recovered.batch()[1]['events'], [])

    def test_full_queue_drops_newest_reports_durable_loss_and_bounds_files(self):
        spool = self.spool()
        first = None
        for _ in range(100):
            accepted = spool.queue_heartbeat()
            if first is None:
                first = spool.batch()[1]['events'][0]
            self.assertLessEqual(spool.usage(), spool.limit)
            if not accepted:
                break
        else:
            self.fail('fixture did not fill queue')
        before = spool.batch()
        self.assertEqual(before[1]['events'][0], first)
        drops = spool.state['dropped_events']
        self.assertEqual(drops, 1)
        spool.close()
        recovered = self.spool()
        self.assertEqual(recovered.state['dropped_events'], drops)
        self.assertEqual(recovered.batch(), before)
        recovered.acknowledge(before[0])
        recovered.queue_heartbeat()
        latest = recovered.batch()[1]['events'][-1]
        self.assertEqual(latest['data']['dropped_events'], drops)

    def test_crash_temp_is_removed_but_committed_event_preserved(self):
        spool = self.spool()
        spool.queue_heartbeat()
        before = spool.batch()
        temporary = spool.path/(before[0][0]+'.tmp')
        temporary.write_bytes(b'incomplete')
        spool.close()
        recovered = self.spool()
        self.assertFalse(temporary.exists())
        self.assertEqual(recovered.batch(), before)

    def test_multiple_processes_cannot_own_same_spool(self):
        spool = self.spool()
        with self.assertRaises(BlockingIOError):
            agent.Spool(spool.path, 128*1024)
        self.assertTrue(spool.queue_heartbeat())

    def test_spool_symlinks_and_world_readable_token_are_rejected(self):
        target = self.root/'target'
        target.mkdir(mode=0o700)
        link = self.root/'spool'
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            agent.Spool(link)
        link.unlink()
        spool = self.spool()
        spool.queue_heartbeat()
        name = spool.batch()[0][0]
        (spool.path/name).unlink()
        (spool.path/name).symlink_to(self.token)
        with self.assertRaises(OSError):
            spool.batch()
        self.token.chmod(0o644)
        transport, connection, _, _ = self.transport()
        with self.assertRaises(ValueError):
            transport.send({'schema_version': 1, 'events': [agent.heartbeat(0, 0, 0)]})
        connection.request.assert_not_called()

    def test_corrupted_spool_cannot_upload_private_fields(self):
        spool = self.spool()
        spool.queue_heartbeat()
        name, payload = spool.batch()
        event = payload['events'][0]
        event['data']['command_line'] = 'private fixture'
        (spool.path/name[0]).write_bytes(agent.encode(event))
        transport = Mock()
        with self.assertRaises(ValueError):
            agent.deliver_once(spool, transport)
        transport.send.assert_not_called()
        self.assertTrue((spool.path/name[0]).exists())

    def test_invalid_endpoint_and_config_keys_rejected(self):
        for endpoint in ['http://host/v1/host-events', 'https://user:pass@host/v1/host-events',
                         'https://host/elsewhere', 'https://host/v1/host-events?token=x',
                         'https://host/v1/host-events#x', 'https://host/\nv1/host-events',
                         'https://host:0/v1/host-events', 'https://host:99999/v1/host-events']:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                agent.endpoint_parts(endpoint)
        path = self.root/'config.json'
        path.write_text(json.dumps(self.config()))
        cfg = agent.load_config(path)
        self.assertEqual(cfg['interval_seconds'], 30)
        self.assertEqual(cfg['spool_bytes'], 33554432)
        for extra in [{'tenant_id': 'spoof'}, {'interval_seconds': True}, {'spool_bytes': 10**12}]:
            path.write_text(json.dumps({**self.config(), **extra}))
            with self.assertRaises(ValueError):
                agent.load_config(path)

    def test_verified_tls_no_proxy_and_expected_destination(self):
        transport, connection, response, factory = self.transport()
        payload = {'schema_version': 1, 'events': [agent.heartbeat(0, 0, 0)]}
        transport.send(payload)
        self.assertTrue(transport.context.check_hostname)
        self.assertEqual(transport.context.verify_mode, ssl.CERT_REQUIRED)
        factory.assert_called_once_with('telemetry.example.invalid', 8445, timeout=15, context=transport.context)
        request = connection.request.call_args
        self.assertEqual(request.args, ('POST', '/v1/host-events'))
        self.assertEqual(json.loads(request.kwargs['body']), payload)
        self.assertEqual(request.kwargs['headers']['Authorization'], 'Bearer synthetic-test-credential')
        response.read.assert_called_once_with(agent.MAX_RESPONSE+1)
        connection.close.assert_called_once()

    def test_redirect_is_not_followed_and_batch_retained(self):
        spool = self.spool()
        spool.queue_heartbeat()
        before = spool.batch()
        transport, connection, response, factory = self.transport(status=302)
        response.getheader.return_value = 'https://another.example.invalid/'
        with self.assertRaises(agent.DeliveryError) as failure:
            agent.deliver_once(spool, transport)
        self.assertFalse(failure.exception.retryable)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(connection.request.call_count, 1)
        response.read.assert_not_called()
        self.assertEqual(spool.batch(), before)

    def test_ambiguous_ack_retries_without_discard(self):
        spool = self.spool()
        spool.queue_heartbeat()
        before = spool.batch()
        for ack in [{'status': 'accepted', 'received': 0, 'inserted': 0},
                    {'status': 'accepted', 'received': 1, 'inserted': 2},
                    {'status': 'accepted', 'received': True, 'inserted': 0},
                    {'status': 'accepted', 'received': 1, 'inserted': True},
                    ['accepted']]:
            transport, _, _, _ = self.transport(ack=ack)
            with self.subTest(ack=ack), self.assertRaises(agent.DeliveryError):
                agent.deliver_once(spool, transport)
            self.assertEqual(spool.batch(), before)
        transport, _, _, _ = self.transport(ack={'status': 'accepted', 'received': 1, 'inserted': 0})
        self.assertTrue(agent.deliver_once(spool, transport))
        self.assertEqual(spool.batch()[1]['events'], [])

    def test_oversized_response_and_certificate_failure_retain_records(self):
        spool = self.spool()
        spool.queue_heartbeat()
        before = spool.batch()
        transport, connection, response, _ = self.transport()
        response.read.return_value = b' '*(agent.MAX_RESPONSE+1)
        with self.assertRaises(agent.DeliveryError):
            agent.deliver_once(spool, transport)
        self.assertEqual(spool.batch(), before)
        connection.request.side_effect = ssl.SSLCertVerificationError('untrusted fixture certificate')
        with self.assertRaises(agent.DeliveryError) as failure:
            agent.deliver_once(spool, transport)
        self.assertTrue(failure.exception.retryable)
        self.assertNotIn('untrusted fixture', str(failure.exception))
        self.assertEqual(spool.batch(), before)

    def test_retry_status_classification(self):
        payload = {'schema_version': 1, 'events': [agent.heartbeat(0, 0, 0)]}
        for status, retryable in [(400, False), (401, False), (403, False), (409, False),
                                  (413, True), (429, True), (503, True)]:
            transport, _, _, _ = self.transport(status=status)
            with self.subTest(status=status), self.assertRaises(agent.DeliveryError) as failure:
                transport.send(payload)
            self.assertEqual(failure.exception.retryable, retryable)

    def test_maximum_batch_size_and_oldest_first(self):
        spool = self.spool(limit=1024*1024)
        for _ in range(70):
            self.assertTrue(spool.queue_heartbeat())
        names, payload = spool.batch()
        self.assertEqual(len(payload['events']), 64)
        self.assertTrue(names[0].startswith('00000000000000000000-'))
        self.assertLessEqual(len(agent.encode(payload)), agent.MAX_BODY)
        spool.acknowledge(names)
        self.assertEqual(len(spool.batch()[1]['events']), 6)

    def test_corrupted_counter_state_is_rejected_without_discard(self):
        spool = self.spool()
        spool.queue_heartbeat()
        names, _ = spool.batch()
        spool.state['dropped_events'] = agent.MAX_COUNTER + 1
        state_path = spool.path/'state.json'
        state_path.write_bytes(agent.encode(spool.state))
        spool.close()
        with self.assertRaises(ValueError):
            agent.Spool(spool.path, 128*1024)
        self.assertTrue((spool.path/names[0]).exists())

    def test_corrupted_wire_counter_or_timestamp_never_uploads(self):
        spool = self.spool()
        spool.queue_heartbeat()
        names, payload = spool.batch()
        original = payload['events'][0]
        for value in [None, 10**100, '999999-01-01T00:00:00Z', '2026-01-01T00:00:00']:
            changed = {**original, 'timestamp': value}
            (spool.path/names[0]).write_bytes(agent.encode(changed))
            transport = Mock()
            with self.assertRaises(ValueError):
                agent.deliver_once(spool, transport)
            transport.send.assert_not_called()
        changed = {**original, 'data': {**original['data'], 'queue_bytes': agent.MAX_COUNTER+1}}
        (spool.path/names[0]).write_bytes(agent.encode(changed))
        with self.assertRaises(ValueError):
            spool.batch()
        self.assertTrue((spool.path/names[0]).exists())

    def test_main_sanitizes_overflow_failure(self):
        with patch('sys.argv', ['agent.py', '--config', '/synthetic/config']), \
                patch.object(agent, 'load_config', side_effect=OverflowError('private fixture')):
            with self.assertLogs(agent.LOG, 'ERROR') as logs:
                self.assertEqual(agent.main(), 2)
        self.assertIn('OverflowError', logs.output[0])
        self.assertNotIn('private fixture', logs.output[0])

    def test_acknowledge_cannot_delete_outside_spool(self):
        spool = self.spool()
        with self.assertRaises(ValueError):
            spool.acknowledge(['../token'])
        self.assertTrue(self.token.exists())


if __name__ == '__main__':
    unittest.main()

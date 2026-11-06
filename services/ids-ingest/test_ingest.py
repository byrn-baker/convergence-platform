import hashlib
import importlib.util
import json
import socket
import struct
import subprocess
from pathlib import Path
import tempfile
import time
import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient
import httpx
from server import create_app
from store import Store, case_payload
from telemetry import render_metrics


def event(kind='alert', now=None, **extra):
    value = {'timestamp': datetime.fromtimestamp(now or time.time(), timezone.utc).isoformat(),
        'event_type': kind, 'vlan': [104], 'src_ip': '192.168.104.10', 'dest_ip': '198.51.100.20'}
    if kind == 'alert':
        value['alert'] = {'signature_id': 12345, 'signature': 'Test signature', 'severity': 1}
    value.update(extra)
    return value


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name)/'eve.sqlite')
        self.store = Store(self.path, capacity=10)
        self.sensor = {'id': 'sensor-home', 'site': 'home', 'vlans': [104, 301],
            'token_sha256': hashlib.sha256(b'sensor-secret').hexdigest(), 'auto_investigate': False}
        self.hb = {'capture_running': True, 'rules_count': 50000, 'rules_checked_at': time.time(),
                   'disk_free_bytes': 10*1024**3}
        self.config = {'sensors': [self.sensor], 'status_token_sha256': hashlib.sha256(b'status-secret').hexdigest(),
            'state_dir': self.temp.name}
        self.client = TestClient(create_app(self.config, self.store))

    def tearDown(self):
        self.client.close()
        self.store.db.close()
        self.temp.cleanup()

    def test_authentication_binds_site_and_status_is_separate(self):
        payload = {'events': [event()], 'heartbeat': self.hb, 'site': 'other', 'sensor': 'spoofed'}
        self.assertEqual(self.client.post('/v1/eve', json=payload).status_code, 401)
        r = self.client.post('/v1/eve', json=payload, headers={'Authorization': 'Bearer sensor-secret'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.store.pending('log')[0]['site'], 'home')
        self.assertEqual(self.client.get('/v1/status?site=home', headers={'Authorization': 'Bearer sensor-secret'}).status_code, 401)
        r = self.client.get('/v1/status?site=other', headers={'Authorization': 'Bearer status-secret'})
        self.assertEqual(r.json()['sensors'], [])
        self.assertEqual(r.json()['alertsLast24h'], None)

    def test_replay_survives_restart_and_delivery_checkpoints_are_independent(self):
        record = event()
        self.assertEqual(self.store.accept(self.sensor, [record, record], self.hb), 1)
        row = self.store.pending('log')[0]
        self.store.acknowledge('log', [row['id']])
        self.store.db.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.accept(self.sensor, [record], self.hb), 0)
        self.assertEqual(self.store.pending('log'), [])
        self.assertEqual(len(self.store.pending('case')), 1)
        self.store.prune(now=time.time()+90*86400)
        self.assertEqual(len(self.store.pending('case')), 1, 'Undelivered cases must never expire')

    def test_pruning_is_bounded_resumes_and_keeps_undelivered_evidence(self):
        old = time.time()-40*86400
        self.store.accept(self.sensor, [event(now=old, src_port=n) for n in range(6)], self.hb, now=old)
        rows = self.store.pending('log')
        for target in ('log', 'case'):
            self.store.acknowledge(target, [r['id'] for r in rows[1:]])
        self.assertTrue(self.store.prune(limit=2))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM eve').fetchone()[0], 5)
        self.store.db.close()
        self.store = Store(self.path)
        self.assertTrue(self.store.prune(limit=2))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM eve').fetchone()[0], 3)
        self.assertTrue(self.store.prune(limit=2))
        self.assertFalse(self.store.prune(limit=2))
        self.assertEqual([r['id'] for r in self.store.pending('log')], [rows[0]['id']])
        for target in ('log', 'case'):
            self.store.acknowledge(target, [rows[0]['id']])
        self.assertFalse(self.store.prune(limit=2))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM eve').fetchone()[0], 0)

    def test_full_queue_rolls_back_entire_batch(self):
        self.store.capacity = 1
        with self.assertRaises(OverflowError):
            self.store.accept(self.sensor, [event(), event(src_ip='192.168.104.11')], self.hb)
        self.assertEqual(self.store.pending('log'), [])
        self.assertEqual(self.store.status([self.sensor], 'home')['sensors'][0]['status'], 'unknown')

    def test_bad_batch_cannot_partially_persist_or_rewrite_identity(self):
        invalid = event(src_ip='an instruction instead of an IP')
        r = self.client.post('/v1/eve', json={'events': [event(), invalid], 'heartbeat': self.hb},
                             headers={'Authorization': 'Bearer sensor-secret'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.store.pending('log'), [])

    def test_recent_heartbeat_does_not_hide_stale_capture_or_missing_vlan(self):
        now = time.time()
        stats = event('stats', now, stats={'capture': {'kernel_packets': 500, 'kernel_drops': 0}})
        self.store.accept(self.sensor, [stats, event('flow', now)], self.hb, now=now)
        status = self.store.status([self.sensor], 'home', now=now)
        self.assertEqual(status['status'], 'active')
        self.assertEqual([v['status'] for v in status['sensors'][0]['coverage']], ['observed', 'unverified'])
        self.store.accept(self.sensor, [], self.hb, now=now+180)
        self.assertEqual(self.store.status([self.sensor], 'home', now=now+180)['sensors'][0]['status'], 'degraded')
        self.assertEqual(self.store.status([self.sensor], 'home', now=now+600)['sensors'][0]['status'], 'stale')

    def test_case_identity_correlates_repeated_signature_but_separates_sites_and_windows(self):
        now = 1700000101
        a = case_payload(self.sensor, event(now=now))['alerts'][0]
        b = case_payload(self.sensor, event(now=now+1, src_port=9999))['alerts'][0]
        self.assertEqual(a['fingerprint'], b['fingerprint'])
        self.assertEqual(a['labels']['investigate'], 'false')
        other = case_payload({**self.sensor, 'site': 'other'}, event(now=now))['alerts'][0]
        self.assertNotEqual(a['fingerprint'], other['fingerprint'])
        other_vlan = case_payload(self.sensor, event(now=now, vlan=[301]))['alerts'][0]
        self.assertNotEqual(a['fingerprint'], other_vlan['fingerprint'])
        canary = case_payload({**self.sensor, 'auto_investigate': True}, event(alert={'signature_id':9000001}))['alerts'][0]
        self.assertEqual(canary['labels']['investigate'], 'false')
        self.assertEqual(canary['labels']['severity'], 'info')

    def test_case_capture_annotation_preserves_tags_independently_of_description(self):
        for tags in ([100, 100], [300, 301], [0, 13], []):
            record = event(vlan=tags, alert={'signature_id': 12345, 'signature': 'x'*15000})
            self.store.accept(self.sensor, [record], self.hb)
            case = json.loads(self.store.pending('case')[-1]['case_payload'])['alerts'][0]
            self.assertEqual(json.loads(case['annotations']['capture_context']),
                             {'version': 1, 'vlan_tags': tags})
            self.assertEqual(case['labels']['sensor_id'], self.sensor['id'])
            self.assertEqual(record['vlan'], tags)
            with self.assertRaises(ValueError):
                json.loads(case['annotations']['description'].split('. ', 1)[1])
        record = event(src_ip='192.168.13.2', dest_ip='10.42.4.57')
        del record['vlan']
        case = case_payload(self.sensor, record)['alerts'][0]
        self.assertEqual(json.loads(case['annotations']['capture_context'])['vlan_tags'], [])

    def test_malformed_mirror_cannot_report_healthy_capture(self):
        now = time.time()
        stats = event('stats', now, stats={'capture': {'kernel_packets': 1000, 'kernel_drops': 0},
                                         'decoder': {'too_many_layers': 500}})
        self.store.accept(self.sensor, [stats], self.hb, now=now)
        state = self.store.status([self.sensor], 'home', now=now)['sensors'][0]
        self.assertEqual(state['status'], 'degraded')
        self.assertIn('decoder', state['reason'])

    def test_shipper_waits_for_complete_line(self):
        script = Path(__file__).resolve().parents[2]/'deploy/convergence/ids/shipper.py'
        spec = importlib.util.spec_from_file_location('ids_shipper', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        path = Path(self.temp.name)/'eve.json'
        first = json.dumps(event()).encode()+b'\n'
        second = json.dumps(event('flow')).encode()
        path.write_bytes(first+second)
        records, offset = module.read_batch(path, 0)
        self.assertEqual(len(records), 1)
        self.assertEqual(offset, len(first))
        path.write_bytes(first+second+b'\n')
        records, end = module.read_batch(path, offset)
        self.assertEqual(len(records), 1)
        self.assertEqual(end, path.stat().st_size)

    def test_one_failed_mirror_is_not_hidden_by_other_sources(self):
        now = time.time()
        self.sensor['capture_sources'] = [
            {'id': 'switch-a', 'sessionId': 110}, {'id': 'switch-b', 'sessionId': 112}]
        hb = {**self.hb, 'capture_probe': {'checkedAt': now, 'sources': {
            'switch-a': {'lastSeen': now, 'packets': 99, 'vlans': {'13': now}},
            'unconfigured': {'lastSeen': now, 'packets': 99, 'vlans': {}}}}}
        stats = event('stats', now, stats={'capture': {'kernel_packets': 500, 'kernel_drops': 0}})
        self.store.accept(self.sensor, [stats], hb, now=now)
        state = self.store.status([self.sensor], 'home', now=now)['sensors'][0]
        self.assertEqual(state['status'], 'degraded')
        self.assertEqual([x['status'] for x in state['captureSources']], ['active', 'unverified'])
        self.assertEqual(state['captureSources'][0]['observedVlans'], [13])
        hb['capture_probe']['sources']['switch-b'] = {'lastSeen': now, 'packets': 10, 'vlans': {}}
        self.store.accept(self.sensor, [], hb, now=now)
        self.assertEqual(self.store.status([self.sensor], 'home', now=now)['status'], 'active')
        state = self.store.status([self.sensor], 'home', now=now+61)['sensors'][0]
        self.assertEqual(state['status'], 'degraded')
        self.assertEqual(state['captureSources'][0]['status'], 'stale')

    def test_invalid_probe_cannot_partially_accept_a_batch(self):
        for probe in ({'checkedAt': time.time()+1000}, {'sources': []},
                      {'sources': {'a': {'lastSeen': 0, 'packets': -1}}},
                      {'sources': {'a': {'lastSeen': 0, 'packets': 1, 'vlans': {'4095': 0}}}}):
            response = self.client.post('/v1/eve', json={'events': [event()],
                'heartbeat': {**self.hb, 'capture_probe': probe}}, headers={'Authorization': 'Bearer sensor-secret'})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.store.pending('log'), [])

    def test_tls_metadata_is_site_scoped_and_does_not_claim_decryption(self):
        self.store.accept(self.sensor, [event('tls', tls={'ja4': 'fingerprint', 'sni': 'test.invalid'})], self.hb)
        status = self.store.status([self.sensor], 'home')['encryptedTraffic']
        self.assertEqual((status['tlsObservations'], status['fingerprinted'], status['serverNamesObserved']), (1, 1, 1))
        self.assertFalse(status['payloadDecryption'])
        self.assertEqual(self.store.status([self.sensor], 'other')['encryptedTraffic']['tlsObservations'], 0)

    def test_probe_decodes_headers_and_rejects_truncation_and_other_gre(self):
        script = Path(__file__).resolve().parents[2]/'deploy/convergence/ids/capture-probe.py'
        spec = importlib.util.spec_from_file_location('ids_probe', script)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        ip = struct.pack('!BBHHHBBH4s4s', 0x45, 0, 60, 0, 0, 64, 47, 0,
                         socket.inet_aton('192.0.2.2'), socket.inet_aton('192.0.2.200'))
        gre = struct.pack('!HHI', 0x1000, 0x88be, 1)
        erspan = struct.pack('!HHI', 0x1000 | 13, 114, 0)
        frame = b'\0'*12+struct.pack('!H', 0x0800)+ip+gre+erspan
        self.assertEqual(module.decode_header(frame), ('192.0.2.2', '192.0.2.200', 114, 13))
        tagged = frame[:12]+struct.pack('!HH', 0x8100, 3)+frame[12:]
        self.assertEqual(module.decode_header(tagged), module.decode_header(frame))
        for length in range(len(frame)):
            self.assertIsNone(module.decode_header(frame[:length]))
        self.assertIsNone(module.decode_header(frame.replace(b'\x88\xbe', b'\x08\x00')))

    def test_renderer_allows_only_verified_ipv4_origins_and_decodes_flannel(self):
        import yaml
        root = Path(self.temp.name)
        base, target = root/'base.yaml', root/'out.yaml'
        base.write_text('vars:\n  address-groups: {}\n')
        script = Path(__file__).resolve().parents[2]/'deploy/convergence/ids/render-sensor.py'
        import sys
        command = [sys.executable, str(script), '--base', str(base), '--output', str(target),
                   '--sensor-ip', '192.0.2.200', '--home-net', '10.42.0.0/16', '--switch-ip']
        subprocess.run(command+['192.0.2.2,192.0.2.3'], check=True, capture_output=True)
        data = yaml.safe_load(target.read_text())
        self.assertEqual(data['af-packet'][0]['bpf-filter'], 'ip proto 47 and (src host 192.0.2.2 or src host 192.0.2.3) and dst host 192.0.2.200')
        self.assertEqual(data['vars']['port-groups']['VXLAN_PORTS'], '[4789,8472]')
        self.assertNotEqual(subprocess.run(command+['192.0.2.2 or ip'], capture_output=True).returncode, 0)

    def test_case_outage_does_not_block_raw_log_delivery(self):
        self.store.accept(self.sensor, [event()], self.hb)
        calls = []
        def upstream(request):
            calls.append(request.url.host)
            return httpx.Response(204 if request.url.host == 'logs' else 503)
        config = {**self.config, 'loki_url': 'http://logs', 'receiver_url': 'http://cases/webhook',
                  'receiver_token': 'test'}
        factory = lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(upstream), **kw)
        with TestClient(create_app(config, self.store, factory)):
            deadline = time.monotonic()+3
            while self.store.pending('log') and time.monotonic()<deadline:
                time.sleep(0.02)
        self.assertEqual(self.store.pending('log'), [])
        self.assertEqual(len(self.store.pending('case')), 1)
        self.assertIn('logs', calls)
        self.assertIn('cases', calls)

    def test_case_delivery_reserves_capacity_for_new_and_old_observations(self):
        self.store.capacity = 1000
        now = time.time()
        self.store.accept(self.sensor, [event(now=now-300, src_port=n) for n in range(200)], self.hb, now=now-300)
        self.store.accept(self.sensor, [event(src_port=9999)], self.hb, now=now)
        rows = self.store.pending('case', limit=128)
        ports = [json.loads(row['payload'])['src_port'] for row in rows]
        self.assertIn(9999, ports, 'Fresh cases must not wait behind the whole outage backlog')
        self.assertIn(0, ports, 'Historical cases must keep making progress')
        self.assertEqual(len(set(row['id'] for row in rows)), 128)

    def test_case_batch_retries_same_evidence_until_complete_acceptance(self):
        records = [event(src_port=n) for n in range(3)]
        self.store.accept(self.sensor, records, self.hb)
        bodies = []
        config = {**self.config, 'loki_url': 'http://logs', 'receiver_url': 'http://cases/webhook',
                  'receiver_token': 'test'}
        def upstream(request):
            if request.url.host != 'cases':
                return httpx.Response(204)
            body = json.loads(request.content)
            bodies.append(body)
            # A partial batch response must not acknowledge any inbox row.
            if len(bodies) == 1:
                return httpx.Response(200, json={'status': 'accepted', 'alerts_received': 1})
            self.assertEqual(len(self.store.pending('case')), 3)
            return httpx.Response(200, json={'status': 'accepted', 'alerts_received': 3})
        factory = lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(upstream), **kw)
        with TestClient(create_app(config, self.store, factory)):
            deadline = time.monotonic()+3
            while self.store.pending('case') and time.monotonic()<deadline:
                time.sleep(0.02)
        self.assertEqual(self.store.pending('case'), [])
        self.assertEqual(len(bodies), 2)
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(len(bodies[0]['alerts']), 3)

    def test_archive_backfill_is_bounded_restartable_and_protected_from_pruning(self):
        records = [event(now=time.time()-40*86400, src_port=n) for n in range(6)]
        self.store.accept(self.sensor, records, self.hb, now=time.time()-40*86400)
        ids = [r['id'] for r in self.store.pending('log')]
        for target in ('log', 'case'):
            self.store.acknowledge(target, ids)
        self.store.enable_archive()
        self.store.schedule_backfill(limit=2)
        self.assertEqual(len(self.store.pending('archive')), 2)
        self.store.prune()
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM eve').fetchone()[0], 6)
        first = self.store.pending('archive')
        self.store.acknowledge('archive', [r['id'] for r in first])
        self.store.prune()
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM eve').fetchone()[0], 4)
        self.store.db.close()
        self.store = Store(self.path, capacity=10)
        self.store.enable_archive()
        self.store.schedule_backfill(limit=2)
        self.assertFalse(set(r['id'] for r in first) & set(r['id'] for r in self.store.pending('archive')))
        # Historical replay occupies at most half the live queue.
        self.store.schedule_backfill(limit=100)
        self.store.accept(self.sensor, [event()], self.hb)
        self.assertLessEqual(len(self.store.pending('archive')), 6)

    def test_metrics_are_authenticated_persistent_deduplicated_and_scope_bound(self):
        record = event(site='spoofed', sensor='spoofed')
        self.store.enable_archive()
        self.store.accept(self.sensor, [record, record], self.hb)
        self.assertEqual(self.client.get('/metrics').status_code, 401)
        self.assertEqual(self.client.get('/metrics', headers={'Authorization':'Bearer sensor-secret'}).status_code, 401)
        response = self.client.get('/metrics', headers={'Authorization':'Bearer status-secret'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('netclaw_ids_alerts_ingested_total{priority="1",sensor="sensor-home",site="home"} 1.0', response.text)
        self.assertNotIn('spoofed', response.text)
        self.store.db.close()
        self.store = Store(self.path)
        self.store.enable_archive()
        self.store.accept(self.sensor, [record], self.hb)
        text = render_metrics(self.store, [self.sensor])
        self.assertIn('netclaw_ids_alerts_ingested_total{priority="1",sensor="sensor-home",site="home"} 1.0', text)

    def test_metric_counters_follow_engine_resets_and_ignore_old_stats(self):
        now = time.time()
        def stats(t, packets, errors):
            return event('stats', t, stats={'capture': {'kernel_packets': packets}, 'decoder': {'unknown_ethertype': errors}})
        self.store.accept(self.sensor, [stats(now-20, 1000, 10), stats(now-10, 5, 1)], self.hb)
        self.store.accept(self.sensor, [stats(now-15, 2000, 50)], self.hb)
        text = render_metrics(self.store, [self.sensor])
        self.assertIn('netclaw_ids_capture_packets_total{sensor="sensor-home",site="home"} 5.0', text)
        self.assertIn('netclaw_ids_decoder_events_total{reason="unknown_ethertype",sensor="sensor-home",site="home"} 1.0', text)

    def test_archive_failure_keeps_evidence_without_blocking_other_destinations(self):
        record = event(site='spoofed', sensor='spoofed', _time='invalid')
        config = {**self.config, 'loki_url': 'http://logs', 'receiver_url': 'http://cases/webhook',
                  'receiver_token': 'test', 'victorialogs_url': 'http://archive'}
        failing, bodies = [True], []
        def upstream(request):
            if request.url.host == 'archive':
                bodies.append(request.content)
                return httpx.Response(200, json={'errors': failing[0], 'items': [{'create': {'status': 201}}]})
            if request.url.host == 'cases':
                return httpx.Response(200, json={'status': 'accepted',
                    'alerts_received': len(json.loads(request.content)['alerts'])})
            return httpx.Response(204)
        factory = lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(upstream), **kw)
        app = create_app(config, self.store, factory)
        self.store.accept(self.sensor, [record], self.hb)
        with TestClient(app):
            deadline = time.monotonic()+3
            while (self.store.pending('log') or self.store.pending('case') or not bodies) and time.monotonic()<deadline:
                time.sleep(0.02)
            self.assertEqual(self.store.pending('log'), [])
            self.assertEqual(self.store.pending('case'), [])
            self.assertEqual(len(self.store.pending('archive')), 1)
            self.store.prune(now=time.time()+90*86400)
            self.assertEqual(len(self.store.pending('archive')), 1)
            failing[0] = False
            deadline = time.monotonic()+3
            while self.store.pending('archive') and time.monotonic()<deadline:
                time.sleep(0.02)
        self.assertEqual(self.store.pending('archive'), [])
        self.assertEqual(bodies[0], bodies[-1], 'Retries retain the same evidence ID and timestamp')
        envelope = json.loads(bodies[0].splitlines()[1])
        self.assertEqual((envelope['site'], envelope['sensor']), ('home', 'sensor-home'))
        self.assertEqual(json.loads(envelope['eve']), record)
        self.assertEqual(envelope['_time'], record['timestamp'])

    def test_archive_backpressure_is_atomic_and_metrics_do_not_count_rejected_events(self):
        self.store.enable_archive()
        self.store.capacity = 1
        self.store.accept(self.sensor, [event()], self.hb)
        row = self.store.pending('archive')[0]
        for target in ('log', 'case'):
            self.store.acknowledge(target, [row['id']])
        with self.assertRaises(OverflowError):
            self.store.accept(self.sensor, [event(src_port=99)], self.hb)
        self.assertEqual(self.store.db.execute('SELECT sum(count) FROM event_counts').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()

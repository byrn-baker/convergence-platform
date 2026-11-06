import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ip_context import IPContext
from store import Store, canonical, case_payload
from telemetry import archive_record
from test_ingest import event


class Reader:
    def __init__(self, country='US', epoch=None):
        self.country, self.epoch = country, epoch or int(time.time())
        self.lookups, self.closed = [], False

    def metadata(self):
        return SimpleNamespace(database_type='ipinfo bundle_location_lite.mmdb', build_epoch=self.epoch)

    def get(self, ip):
        self.lookups.append(ip)
        return {'country_code': self.country, 'country': 'Test country', 'asn': 'AS13335',
                'as_name': 'Test network', 'as_domain': 'example.com'}

    def close(self):
        self.closed = True


class IPContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'country.mmdb'
        self.path.write_bytes(b'fixture')
        self.reader = Reader()
        self.context = IPContext({'database': str(self.path), 'internal_networks': ['10.42.0.0/16', '9.9.9.0/24']},
                                 reader_factory=lambda _: self.reader)

    def tearDown(self):
        self.context.close()
        self.temp.cleanup()

    def test_only_public_unicast_is_looked_up_and_owned_public_prefix_is_internal(self):
        for address in ['10.42.0.1', '192.168.3.2', '127.0.0.1', '169.254.1.2', '224.0.0.1',
                        '100.64.0.1', '192.0.2.5', 'fc00::1', 'fe80::1', 'ff02::1', '::1', '0.0.0.0']:
            self.assertNotEqual(self.context.lookup(address)['scope'], 'external')
        self.assertEqual(self.context.lookup('9.9.9.9')['scope'], 'internal')
        self.assertEqual(self.reader.lookups, [])
        self.assertEqual(self.context.lookup('1.1.1.1')['asn'], 'AS13335')
        self.context.lookup('::ffff:1.1.1.1')
        self.assertEqual(self.reader.lookups, ['1.1.1.1'], 'Mapped IPv4 reuses the same cached lookup')
        self.assertEqual(self.context.lookup('2001:4860:4860::8888')['scope'], 'external')

    def test_missing_or_bad_database_does_not_block_evidence_and_stale_is_visible(self):
        self.path.unlink()
        status = self.context.enrich(event(dest_ip='1.1.1.1'))
        self.assertEqual(status['status'], 'unavailable')
        self.assertEqual(status['destination']['status'], 'unavailable')
        self.path.write_bytes(b'fixture-again')
        self.context.checked = 0
        self.reader.epoch = int(time.time()-8*86400)
        self.assertEqual(self.context.enrich(event(dest_ip='1.1.1.1'))['status'], 'stale')

    def test_atomic_reload_invalidates_cache_and_bad_replacement_preserves_old_reader(self):
        self.assertEqual(self.context.lookup('1.1.1.1')['country_code'], 'US')
        new = Reader(country='CA')
        self.context.reader_factory = lambda _: new
        replacement = self.path.with_suffix('.next')
        replacement.write_bytes(b'new fixture'); replacement.replace(self.path)
        self.context.checked = 0
        self.assertEqual(self.context.lookup('1.1.1.1')['country_code'], 'CA')
        self.assertTrue(self.reader.closed)
        def fail(_): raise ValueError('invalid database')
        self.context.reader_factory = fail
        replacement.write_bytes(b'broken'); replacement.replace(self.path)
        self.context.checked = 0
        self.assertEqual(self.context.lookup('1.1.1.1')['country_code'], 'CA')
        self.assertEqual(self.context.status()['status'], 'degraded')
        self.assertFalse(new.closed)

    def test_context_is_frozen_for_retries_and_original_evidence_identity_is_unchanged(self):
        sensor = {'id': 'one', 'site': 'home', 'vlans': [104]}
        original = event(dest_ip='1.1.1.1')
        store = Store(str(Path(self.temp.name)/'eve.sqlite'), enricher=self.context)
        try:
            store.accept(sensor, [original], {})
            row = store.pending('log')[0]
            self.assertEqual(row['payload'], canonical(original))
            self.assertEqual(row['id'], hashlib.sha256(('one\0'+canonical(original)).encode()).hexdigest())
            context = row['ip_context']
            self.reader.country = 'CA'; self.context.cache.clear()
            self.assertEqual(store.accept(sensor, [original], {}), 0)
            self.assertEqual(store.pending('log')[0]['ip_context'], context)
            case = json.loads(row['case_payload'])['alerts'][0]
            self.assertEqual(case['fingerprint'], case_payload(sensor, original)['alerts'][0]['fingerprint'])
            self.assertEqual(case['annotations']['ip_context'], context)
            archived = archive_record(row)
            self.assertEqual(archived['eve'], canonical(original))
            self.assertEqual(archived['ip_context']['destination']['asn'], 'AS13335')
        finally:
            store.db.close()

    def test_invalid_download_preserves_last_good_database(self):
        script = Path(__file__).resolve().parents[2]/'deploy/convergence/ids/update-ip-context.py'
        spec = importlib.util.spec_from_file_location('update_ip_context', script)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def raise_for_status(self): pass
            def iter_bytes(self, _): yield b'not a database'
        with patch.object(module.httpx, 'stream', return_value=Response()):
            with self.assertRaises(Exception):
                module.update(self.path, 'not-a-real-token')
        self.assertEqual(self.path.read_bytes(), b'fixture')
        self.assertEqual(list(self.path.parent.glob('.ipinfo-*')), [])


if __name__ == '__main__':
    unittest.main()

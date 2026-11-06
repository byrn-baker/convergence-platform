"""Local-only public-IP context. Database metadata never changes IDS verdicts."""
from collections import OrderedDict
from copy import deepcopy
import ipaddress
from pathlib import Path
import time

import maxminddb


class IPContext:
    def __init__(self, config=None, reader_factory=maxminddb.open_database):
        config = config or {}
        self.path = Path(config['database']) if config.get('database') else None
        self.internal = [ipaddress.ip_network(n) for n in config.get('internal_networks', [])]
        self.reader_factory = reader_factory
        self.reader = None
        self.identity = None
        self.build_epoch = 0
        self.checked = 0
        self.error = None
        self.cache = OrderedDict()

    def refresh(self):
        if not self.path or (self.checked and time.monotonic()-self.checked < 60):
            return
        self.checked = time.monotonic()
        candidate = None
        try:
            stat = self.path.stat()
            identity = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            if identity == self.identity:
                self.error = None
                return
            candidate = self.reader_factory(str(self.path))
            metadata = candidate.metadata()
            if 'ipinfo' not in metadata.database_type.lower() or not 0 < metadata.build_epoch <= time.time()+86400:
                raise ValueError('Unsupported database')
            old = self.reader
            self.reader, self.identity, self.build_epoch = candidate, identity, metadata.build_epoch
            self.error = None
            self.cache.clear()
            if old:
                old.close()
        except Exception as exc:
            if candidate and candidate is not self.reader:
                candidate.close()
            # Keep a previous validated reader available, but report the reload failure.
            self.error = type(exc).__name__

    def status(self):
        self.refresh()
        stale = bool(self.reader and time.time()-self.build_epoch > 7*86400)
        return {'provider': 'IPinfo Lite', 'mode': 'local_database',
                'status': 'not_configured' if not self.path else 'unavailable' if not self.reader else
                          'degraded' if self.error else 'stale' if stale else 'active',
                'databaseBuiltAt': self.build_epoch or None, 'reloadError': self.error,
                'attributionUrl': 'https://ipinfo.io', 'reputationIncluded': False}

    def lookup(self, value):
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        result = {'ip': str(value), 'scope': 'non_public', 'status': 'not_applicable'}
        if any(address in network for network in self.internal):
            return {**result, 'scope': 'internal'}
        if not address.is_global or address.is_multicast or address.is_reserved:
            return result
        self.refresh()
        result.update(scope='external', status='unavailable')
        if not self.reader:
            return result
        key = str(address)
        if key in self.cache:
            found = self.cache.pop(key)
            self.cache[key] = found
        else:
            try:
                raw = self.reader.get(key)
                found = {k: str(raw[k])[:240] for k in ('country_code', 'country', 'continent_code',
                          'continent', 'asn', 'as_name', 'as_domain') if raw and raw.get(k)}
            except Exception as exc:
                self.error = type(exc).__name__
                return result
            self.cache[key] = found
            if len(self.cache) > 20000:
                self.cache.popitem(last=False)
        return {**result, **found, 'status': 'enriched' if found else 'not_found'}

    def enrich(self, event):
        if not self.path:
            return {}
        endpoints = {name: self.lookup(event[field]) for name, field in
                     (('source', 'src_ip'), ('destination', 'dest_ip')) if field in event}
        if not endpoints:
            return {}
        return {**self.status(), 'enrichedAt': time.time(), **deepcopy(endpoints)}

    def close(self):
        if self.reader:
            self.reader.close()
            self.reader = None

#!/usr/bin/env python3
"""Synthetic IDS arrival/backlog qualification; no network or production configuration.

Run inside the documented 1 CPU / 512 MiB container. The parent launches two
separate service processes against a fresh private state directory. Downstream
protocols are explicit in-process test doubles with a durable receipt ledger.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import logging
import math
import os
from pathlib import Path
import resource
import sqlite3
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'services/ids-ingest'))
import httpx
from server import create_app
from store import Store

BASELINE = {'observed_at': '2026-11-15', 'last_5m_events_per_second': 132.28,
    'max_5m_rate_over_24h': 145.67, 'mix_1h_rates': {'flow': 106.078,
    'dns': 11.983, 'alert': 10.696, 'tls': 2.85, 'stats': 0.0667},
    'secondary_sensor_fraction': 0.154/(131.520+0.154)}
LIMITS = ['ASGI transport: no TLS, socket, ingress or remote client-network qualification',
    'Controlled sinks: no real Loki, VictoriaLogs or case receiver performance claim',
    'Short synthetic history: no production-volume SQLite or physical storage contention claim',
    'No capture engine or packet replay: capture loss remains unqualified',
    'Assumed batch size and event byte size; measured rate is only a five-minute average peak',
    'Graceful process restart, not power loss or filesystem crash recovery',
    'Logical sink identity is checked; production delivery remains at least once']


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')
    path.chmod(0o600)


def stats(values):
    values = sorted(values)
    if not values:
        return {'samples': 0}
    return {'samples': len(values), 'p50_ms': values[len(values)//2]*1000,
        'p95_ms': values[min(len(values)-1, math.ceil(len(values)*.95)-1)]*1000,
        'p99_ms': values[min(len(values)-1, math.ceil(len(values)*.99)-1)]*1000,
        'max_ms': values[-1]*1000}


def event_id(sensor, event):
    return digest(sensor+'\0'+canonical(event))


def case_key(sensor, event):
    stamp = datetime.fromisoformat(event['timestamp']).timestamp()
    return digest(canonical(['load-lab', sensor, event['alert']['signature_id'],
        event['src_ip'], event['dest_ip'], event['vlan'], int(stamp//300)*300]))


def make_event(index, started, byte_size, force_alert=False):
    rates = BASELINE['mix_1h_rates']
    point = ((index*7919) % 100000)/100000*sum(rates.values())
    kind = 'stats'
    for candidate, weight in rates.items():
        point -= weight
        if point < 0:
            kind = candidate
            break
    if force_alert:
        kind = 'alert'
    event = {'timestamp': datetime.fromtimestamp(started, timezone.utc).isoformat(),
        'event_type': kind, 'src_ip': '192.0.2.'+str(index % 250+1),
        'dest_ip': '198.51.100.10', 'vlan': [100], 'flow_id': index,
        'proto': 'TCP', 'src_port': 10000+index % 50000, 'dest_port': 443,
        'synthetic_fixture': 'ids-load'}
    if kind == 'alert':
        event['alert'] = {'signature_id': 100000+index, 'severity': 3, 'signature': 'Synthetic load observation'}
    elif kind == 'stats':
        event['stats'] = {'capture': {'kernel_packets': index+1, 'kernel_drops': 0}}
    elif kind == 'dns':
        event['dns'] = {'rrname': 'load.invalid', 'rrtype': 'A'}
    elif kind == 'tls':
        event['tls'] = {'sni': 'load.invalid', 'version': 'TLS 1.3'}
    event['fixture_padding'] = 'x'*max(0, byte_size-len(canonical(event))-21)
    return event


def manifest(args):
    started = time.time()
    total = round(args.rate*args.duration)
    batches, current = [], None
    for index in range(total):
        sensor = 'load-secondary' if index % 855 == 854 else 'load-primary'
        if current is None or current['sensor'] != sensor or len(current['events']) >= args.batch_size:
            current = {'sensor': sensor, 'events': [], 'scheduled': index/args.rate, 'group': 'arrivals'}
            batches.append(current)
        current['events'].append(make_event(index, started, args.event_bytes))
    for index in range(total, total+128):
        batches.append({'sensor': 'load-primary', 'events': [make_event(index, started, args.event_bytes)], 'group': 'burst'})
    batches.append({'sensor': 'load-primary', 'events': [make_event(index, started, args.event_bytes, True)
        for index in range(total+128, total+148)], 'group': 'restart-tail'})
    return {'schema': 1, 'synthetic_fixture': 'ids-load', 'rate': args.rate, 'duration': args.duration, 'batch_size': args.batch_size,
        'event_bytes_assumed': args.event_bytes, 'storage_delay_ms': args.storage_delay_ms,
        'storage_stall_seconds': args.storage_stall_seconds, 'queue_capacity': args.queue_capacity,
        'health_limit_seconds': args.health_limit, 'arrival_lag_limit_seconds': args.arrival_lag_limit,
        'started': started, 'batches': batches, 'baseline': BASELINE}


def expectations(plan):
    events, cases = {}, set()
    for batch in plan['batches']:
        for event in batch['events']:
            key = event_id(batch['sensor'], event)
            check(key not in events, 'Fixture has duplicate event identities')
            events[key] = digest(canonical(event))
            if event['event_type'] == 'alert':
                cases.add(case_key(batch['sensor'], event))
    return events, cases


class Sinks:
    """Durable logical receipt ledger; every network attempt is counted separately."""
    def __init__(self, path, plan, recover):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS receipts(target TEXT,key TEXT,payload TEXT,attempts INTEGER,PRIMARY KEY(target,key))')
        self.db.commit()
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.plan, self.recover = plan, recover
        self.force_outage = False
        self.failures = Counter()
        self.protocol_error = None
        self.first_success_after_outage = {}
        self.ambiguous = set() if recover else {'log', 'archive', 'case'}
        self.expected_events, self.expected_cases = expectations(plan)

    def commit(self, target, rows):
        with self.lock, self.db:
            for key, payload in rows:
                old = self.db.execute('SELECT payload FROM receipts WHERE target=? AND key=?', (target, key)).fetchone()
                check(old is None or old[0] == payload, 'Replay changed sink payload')
                self.db.execute('INSERT INTO receipts VALUES(?,?,?,1) ON CONFLICT(target,key) DO UPDATE SET attempts=attempts+1',
                                (target, key, payload))

    async def request(self, request):
        try:
            return await self._request(request)
        except AssertionError as error:
            self.protocol_error = str(error)
            raise

    async def _request(self, request):
        check(request.url.host == 'sink.invalid', 'Harness attempted an unexpected destination')
        target = {'/loki/api/v1/push': 'log', '/insert/elasticsearch/_bulk': 'archive', '/cases': 'case'}.get(request.url.path)
        check(target is not None, 'Unexpected downstream protocol path')
        elapsed = time.monotonic()-self.started
        if self.force_outage or (not self.recover and self.plan['duration']*.2 <= elapsed < self.plan['duration']*.5):
            self.failures[target] += 1
            return httpx.Response(503)
        rows = []
        if target == 'log':
            for stream in json.loads(request.content)['streams']:
                check(stream['stream']['site'] == 'load-lab', 'Log site attribution changed')
                for _, encoded in stream['values']:
                    value = json.loads(encoded)
                    key = event_id(stream['stream']['sensor'], value)
                    payload = digest(canonical(value))
                    check(self.expected_events.get(key) == payload, 'Unexpected or altered log event')
                    rows.append((key, payload))
            response = httpx.Response(204)
        elif target == 'archive':
            lines = request.content.decode().splitlines()
            check(len(lines) % 2 == 0, 'Incomplete archive bulk envelope')
            for line in lines[1::2]:
                value = json.loads(line)
                check(value['site'] == 'load-lab', 'Archive site attribution changed')
                payload = digest(canonical(json.loads(value['eve'])))
                check(self.expected_events.get(value['event_id']) == payload, 'Unexpected or altered archive event')
                rows.append((value['event_id'], payload))
            response = httpx.Response(200, json={'errors': False, 'items': [{'create': {'status': 201}} for _ in rows]})
        else:
            check(request.headers.get('authorization') == 'Bearer fixture-receiver', 'Receiver authentication changed')
            value = json.loads(request.content)
            for alert in value['alerts']:
                check(alert['fingerprint'] in self.expected_cases, 'Unexpected case identity')
                check(alert['labels']['site'] == 'load-lab' and alert['labels']['investigate'] == 'false', 'Unsafe case scope')
                rows.append((alert['fingerprint'], digest(canonical(alert))))
            response = httpx.Response(200, json={'status': 'accepted', 'alerts_received': len(rows)})
        await asyncio.to_thread(self.commit, target, rows)
        if target in self.ambiguous:
            self.ambiguous.remove(target)
            self.failures['ambiguous_'+target] += 1
            return httpx.Response(503)  # Accepted externally, acknowledgement lost.
        if not self.recover and elapsed >= self.plan['duration']*.5:
            self.first_success_after_outage.setdefault(target, time.monotonic()-self.started-self.plan['duration']*.5)
        return response

    def client(self, **kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.request), **kwargs)


def snapshot(store):
    with store.lock:
        rows = list(store.db.execute('SELECT id,payload,log_done,case_done,archive_done FROM eve ORDER BY id'))
        return {'rows': len(rows), 'identity_payload_flags_sha256': digest(canonical([tuple(r) for r in rows])),
            'ids': [row['id'] for row in rows], 'pending_log': sum(not row['log_done'] for row in rows),
            'pending_case': sum(not row['case_done'] for row in rows),
            'pending_archive': sum(not row['archive_done'] for row in rows),
            'integrity': store.db.execute('PRAGMA integrity_check').fetchone()[0]}


def queue_state(store):
    with store.lock:
        row = store.db.execute('SELECT count(*),coalesce(sum(log_done=0),0),coalesce(sum(case_done=0),0),coalesce(sum(archive_done=0),0) FROM eve').fetchone()
        return dict(zip(('rows', 'pending_log', 'pending_case', 'pending_archive'), row))


async def wait_drained(app, store, sinks, deadline=90):
    started = time.monotonic()
    while time.monotonic()-started < deadline:
        check(sinks.protocol_error is None, 'Sink oracle rejected delivery: '+str(sinks.protocol_error))
        state = await app.state.storage.run(queue_state, store)
        if not any(state['pending_'+target] for target in ('log', 'case', 'archive')):
            return time.monotonic()-started
        await asyncio.sleep(.1)
    raise AssertionError('Durable backlog did not drain within 90 seconds')


async def service_phase(output, plan, recover):
    store = Store(str(output/'eve.sqlite'), capacity=plan['queue_capacity'])
    store.enable_archive()
    if recover:
        check(snapshot(store) == json.loads((output/'checkpoint.json').read_text()), 'Restart changed retained evidence/checkpoints')
    sensors = [{'id': name, 'site': 'load-lab', 'vlans': [100], 'auto_investigate': False,
        'token_sha256': digest('fixture-'+name)} for name in ('load-primary', 'load-secondary')]
    sinks = Sinks(str(output/'sinks.sqlite'), plan, recover)
    config = {'state_dir': str(output), 'sensors': sensors, 'status_token_sha256': digest('fixture-status'),
        'loki_url': 'http://sink.invalid', 'victorialogs_url': 'http://sink.invalid',
        'receiver_url': 'http://sink.invalid/cases', 'receiver_token': 'fixture-receiver'}
    app = create_app(config, store, client_factory=sinks.client, owns_store=True)
    started = time.monotonic()
    stalled = False
    stall_window = {'started': None, 'finished': None}
    original_run = app.state.storage.run
    async def delayed(operation, *args, **kwargs):
        def execute():
            nonlocal stalled
            if not recover and not stalled and time.monotonic()-started >= plan['duration']*.3:
                stalled = True
                stall_window['started'] = time.monotonic()-started
                time.sleep(plan['storage_stall_seconds'])
                stall_window['finished'] = time.monotonic()-started
            if not recover:
                time.sleep(plan['storage_delay_ms']/1000)
            return operation(*args, **kwargs)
        return await original_run(execute)
    app.state.storage.run = delayed
    health, health_effective, latency, scheduling, admission = [], [], [], [], []
    metrics_samples, health_during_stall = [], []
    responses, inserted, queue_samples = Counter(), Counter(), []
    observations = {'client_queue_peak': 0, 'sample_failures': [], 'worker_pending_peak': 0, 'metrics_responses': {}, 'metrics_latency': []}
    stop = asyncio.Event()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://ids.invalid') as client:
            async def send(batch, replay=False):
                deadline = time.monotonic()+120
                while True:
                    check(sinks.protocol_error is None, 'Sink oracle rejected delivery: '+str(sinks.protocol_error))
                    before = time.monotonic()
                    result = await client.post('/v1/eve', json={'events': batch['events'],
                        'heartbeat': {'capture_running': True, 'rules_count': 0, 'rules_checked_at': 0}},
                        headers={'Authorization': 'Bearer fixture-'+batch['sensor']})
                    latency.append(time.monotonic()-before)
                    responses[str(result.status_code)] += 1
                    if result.status_code == 503:
                        check(time.monotonic() < deadline, 'Retry deadline exceeded; retained batch is unresolved')
                        await asyncio.sleep(.05)
                        continue
                    check(result.status_code == 200, 'Unexpected ingestion response '+str(result.status_code))
                    reply = result.json()
                    check(reply['received'] == len(batch['events']), 'Partial ingestion ACK')
                    check(reply['inserted'] == (0 if replay else len(batch['events'])), 'Insertion/replay count mismatch')
                    inserted['replay' if replay else 'new'] += reply['inserted']
                    if '_offered_at' in batch:
                        admission.append(time.monotonic()-batch['_offered_at'])
                    return

            async def health_probe():
                due = time.monotonic()
                while not stop.is_set():
                    await asyncio.sleep(max(0, due-time.monotonic()))
                    before = time.monotonic()
                    response = await client.get('/healthz')
                    ended = time.monotonic()
                    check(response.status_code == 200, 'Health endpoint failed')
                    health.append(ended-before)
                    health_effective.append(ended-due)
                    stall_start, stall_end = stall_window['started'], stall_window['finished']
                    if stall_start is not None and before-started >= stall_start and (stall_end is None or before-started <= stall_end):
                        health_during_stall.append(ended-due)
                    due = ended+.05  # No catch-up burst after a delayed scheduler.

            async def observe_storage():
                while not stop.is_set():
                    try:
                        state = await app.state.storage.run(queue_state, store)
                        queue_samples.append({k: v for k, v in state.items() if k.startswith('pending_')})
                    except Exception as error:
                        from async_storage import StorageBusy
                        if not isinstance(error, StorageBusy):
                            raise
                        observations['sample_failures'].append('storage_busy')
                    with app.state.storage._lock:
                        observations['worker_pending_peak'] = max(observations['worker_pending_peak'], len(app.state.storage._pending))
                    await asyncio.sleep(.5)

            async def metrics_probe():
                # Never await storage observation before starting this timer:
                # doing so conceals the very queue/disk wait being measured.
                due = time.monotonic()
                while not stop.is_set():
                    await asyncio.sleep(max(0, due-time.monotonic()))
                    before = time.monotonic()
                    response = await client.get('/metrics', headers={'Authorization': 'Bearer fixture-status'})
                    ended = time.monotonic()
                    check(response.status_code in (200, 503), 'Unexpected metrics response')
                    key = str(response.status_code)
                    observations['metrics_responses'][key] = observations['metrics_responses'].get(key, 0)+1
                    observations['metrics_latency'].append(ended-due)
                    metrics_samples.append({'scheduled_seconds': due-started,
                        'request_started_seconds': before-started, 'completed_seconds': ended-started,
                        'status': response.status_code, 'request_ms': (ended-before)*1000,
                        'including_scheduler_ms': (ended-due)*1000})
                    due = ended+.1  # One independently scheduled probe; no catch-up burst.

            monitors = [asyncio.create_task(health_probe()), asyncio.create_task(observe_storage()),
                        asyncio.create_task(metrics_probe())]
            try:
                if recover:
                    for batch in plan['batches']:
                        await send(batch, replay=True)
                    drain_seconds = await wait_drained(app, store, sinks)
                else:
                    queue = asyncio.Queue(maxsize=256)
                    async def producer():
                        origin = time.monotonic()
                        for batch in (b for b in plan['batches'] if b['group'] == 'arrivals'):
                            due = origin+batch['scheduled']
                            await asyncio.sleep(max(0, due-time.monotonic()))
                            scheduling.append(max(0, time.monotonic()-due))
                            batch['_offered_at'] = time.monotonic()
                            queue.put_nowait(batch)
                            observations['client_queue_peak'] = max(observations['client_queue_peak'], queue.qsize())
                        observations['offered_arrivals'] = sum(len(b['events']) for b in plan['batches'] if b['group'] == 'arrivals')
                        observations['arrival_schedule_span_seconds'] = time.monotonic()-origin
                        observations['offered_event_mix'] = dict(Counter(event['event_type'] for b in plan['batches'] if b['group']=='arrivals' for event in b['events']))
                        for _ in range(16):
                            await queue.put(None)
                    async def sender():
                        while (batch := await queue.get()) is not None:
                            await send(batch)
                    async with asyncio.TaskGroup() as arrivals:
                        arrivals.create_task(producer())
                        for _ in range(16):
                            arrivals.create_task(sender())
                    observations['arrival_finish_seconds'] = time.monotonic()-started
                    drain_seconds = await wait_drained(app, store, sinks)
                    observations['backlog_drained_after_outage_upper_bound_seconds'] = time.monotonic()-started-plan['duration']*.5
                    check(stalled and stall_window['finished'] is not None, 'Configured storage stall was not exercised')
                    overlapping = [sample for sample in metrics_samples if sample['status'] == 200
                        and sample['request_started_seconds'] < stall_window['finished']
                        and sample['completed_seconds'] > stall_window['started']]
                    check(overlapping, 'No independently timed metrics request observed the storage stall')
                    slowest = max(overlapping, key=lambda sample: sample['including_scheduler_ms'])
                    minimum_delay = max(.01, plan['storage_stall_seconds']-.25)
                    check(slowest['including_scheduler_ms'] >= minimum_delay*1000,
                        'Metrics timing concealed the deliberately injected storage delay')
                    check(health_during_stall and max(health_during_stall) <= plan['health_limit_seconds'],
                        'Health did not remain responsive during the observed metrics stall')
                    observations['metrics_stall_observation'] = {'storage_window_seconds': stall_window,
                        'minimum_expected_delay_ms': minimum_delay*1000, 'slowest_overlapping_probe': slowest,
                        'health_during_stall': stats(health_during_stall)}
                    check(max(scheduling) <= plan['arrival_lag_limit_seconds'], 'Generator undershot offered workload')
                    check(all(sinks.failures[t] for t in ('log', 'case', 'archive')), 'Downstream outage was not exercised')
                    # Hold the real storage worker, then offer 128 simultaneous
                    # unique requests. Retain 503 batches and retry them intact.
                    entered, release = threading.Event(), threading.Event()
                    def hold():
                        entered.set()
                        check(release.wait(10), 'Burst release was not signalled')
                    blocker = asyncio.create_task(original_run(hold))
                    check(await asyncio.to_thread(entered.wait, 3), 'Storage burst gate did not start')
                    burst = [b for b in plan['batches'] if b['group'] == 'burst']
                    async def first_attempt(batch):
                        before = time.monotonic()
                        response = await client.post('/v1/eve', json={'events': batch['events'],
                            'heartbeat': {'capture_running': True, 'rules_count': 0, 'rules_checked_at': 0}},
                            headers={'Authorization': 'Bearer fixture-'+batch['sensor']})
                        latency.append(time.monotonic()-before)
                        return response
                    tasks = [asyncio.create_task(first_attempt(batch)) for batch in burst]
                    try:
                        await asyncio.sleep(.25)
                        rejected = sum(t.done() and t.result().status_code == 503 for t in tasks)
                        check(rejected > 0, 'Admission bound was not reached')
                        with app.state.storage._lock:
                            pending = len(app.state.storage._pending)
                        check(pending <= 64, 'Storage admission exceeded its documented bound')
                        observations['burst_rejected_while_held'] = rejected
                        observations['worker_pending_peak'] = max(observations['worker_pending_peak'], pending)
                    finally:
                        release.set()
                    await blocker
                    for batch, response in zip(burst, await asyncio.gather(*tasks)):
                        if response.status_code == 503:
                            responses['503'] += 1
                            await send(batch)
                        else:
                            check(response.status_code == 200 and response.json()['inserted'] == 1, 'Unexpected burst ACK')
                            inserted['new'] += 1
                            responses['200'] += 1
                    await wait_drained(app, store, sinks)
                    # Deliberately leave a known unacknowledged tail for the
                    # next OS process; no delivery can acknowledge this outage.
                    sinks.force_outage = True
                    await send(plan['batches'][-1])
                    checkpoint = await app.state.storage.run(snapshot, store)
                    check(checkpoint['pending_log'] == 20 and checkpoint['pending_archive'] == 20 and checkpoint['pending_case'] == 20,
                          'Restart tail was not durably pending at every sink')
                    save(output/'checkpoint.json', checkpoint)
                final = await app.state.storage.run(snapshot, store)
                expected, expected_cases = expectations(plan)
                check(final['integrity'] == 'ok' and set(final['ids']) == set(expected), 'Lost or extra durable events')
                if recover:
                    with sinks.lock:
                        received = {target: {row[0] for row in sinks.db.execute('SELECT key FROM receipts WHERE target=?', (target,))}
                                    for target in ('log', 'archive', 'case')}
                        check(sinks.db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Sink ledger integrity failed')
                        attempts = dict(sinks.db.execute('SELECT target,sum(attempts) FROM receipts GROUP BY target'))
                    check(received['log'] == set(expected) and received['archive'] == set(expected), 'Sink event reconciliation failed')
                    check(received['case'] == expected_cases, 'Case reconciliation failed')
                    observations['sink_unique'] = {k: len(v) for k, v in received.items()}
                    observations['sink_attempts'] = attempts
                check(health_effective and max(health_effective) <= plan['health_limit_seconds'], 'Health/scheduler latency exceeded target')
                observations['metrics_latency'] = stats(observations['metrics_latency'])
                observations['metrics_samples'] = metrics_samples
                observations.update(health=stats(health), health_with_scheduler_lag=stats(health_effective),
                    ingestion=stats(latency), end_to_end_admission=stats(admission), arrival_scheduling_lag=stats(scheduling), responses=dict(responses),
                    inserted=dict(inserted), retained_events=final['rows'], drain_wait_seconds=drain_seconds,
                    pending_peaks={target: max((r['pending_'+target] for r in queue_samples), default=0)
                                   for target in ('log', 'archive', 'case')}, sink_failures=dict(sinks.failures), first_success_after_outage_seconds=sinks.first_success_after_outage)
            finally:
                stop.set()
                for task in monitors:
                    task.cancel()
                results = await asyncio.gather(*monitors, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        raise result
    sinks.db.close()
    return observations


def retention_probe(output):
    # Only a disposable copy is advanced in time; original evidence stays intact.
    with sqlite3.connect(output/'eve.sqlite') as source, sqlite3.connect(output/'retention.sqlite') as target:
        source.backup(target)
    store = Store(str(output/'retention.sqlite'))
    store.enable_archive()
    before = snapshot(store)
    rounds = 0
    while True:
        more = store.prune(now=time.time()+40*86400, limit=512)
        rounds += 1
        check(rounds < 10000, 'Retention traversal did not finish')
        if not more:
            break
    after = snapshot(store)
    store.db.close()
    check(after['rows'] == 20 and all(after['pending_'+t] == 20 for t in ('log', 'case', 'archive')),
          'Retention removed undelivered evidence or kept expired acknowledged evidence')
    return {'copy_only': True, 'future_days': 40, 'bounded_scan_rows': 512,
            'scan_rounds': rounds, 'before_rows': before['rows'], 'retained_pending_rows': after['rows']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--rate', type=int, choices=(150, 300), default=150)
    parser.add_argument('--duration', type=int, default=30)
    parser.add_argument('--batch-size', type=int, default=20)
    parser.add_argument('--event-bytes', type=int, default=1024)
    parser.add_argument('--storage-delay-ms', type=float, default=2)
    parser.add_argument('--storage-stall-seconds', type=float, default=2)
    parser.add_argument('--queue-capacity', type=int, default=1024)
    parser.add_argument('--health-limit', type=float, default=.75)
    parser.add_argument('--arrival-lag-limit', type=float, default=.25)
    parser.add_argument('--phase', choices=('exercise', 'recover'), help=argparse.SUPPRESS)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    output = args.output.resolve()
    if args.phase:
        plan = json.loads((output/'manifest.json').read_text())
        check(plan.get('synthetic_fixture') == 'ids-load', 'Synthetic fixture marker missing')
        result = asyncio.run(service_phase(output, plan, args.phase == 'recover'))
        if args.phase == 'exercise':
            result['retention'] = retention_probe(output)
        usage = resource.getrusage(resource.RUSAGE_SELF)
        result.update(status='PASS', pid=os.getpid(), peak_rss_bytes=usage.ru_maxrss*1024,
                      cpu_seconds=usage.ru_utime+usage.ru_stime)
        save(output/(args.phase+'.json'), result)
        print(json.dumps({'phase': args.phase, 'status': 'PASS', 'events': result['retained_events']}), flush=True)
        return
    check(20 <= args.duration <= 300 and 1 <= args.batch_size <= 256 and 512 <= args.event_bytes <= 16384,
          'Workload bounds exceeded')
    check(all(math.isfinite(v) and v > 0 for v in (args.storage_delay_ms, args.storage_stall_seconds,
        args.health_limit, args.arrival_lag_limit)), 'Finite positive delay/latency bounds required')
    check(not output.exists(), 'Choose a new output directory; existing evidence is never overwritten')
    output.mkdir(parents=True, mode=0o700)
    save(output/'manifest.json', manifest(args))
    results = []
    for phase in ('exercise', 'recover'):
        with (output/(phase+'.log')).open('w') as log:
            process = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--output', str(output), '--phase', phase],
                stdout=log, stderr=subprocess.STDOUT, timeout=600)
        check(process.returncode == 0, phase+' failed; inspect retained private log')
        results.append(json.loads((output/(phase+'.json')).read_text()))
    check(results[0]['pid'] != results[1]['pid'], 'Restart did not use a new process')
    sources = {str(p.relative_to(ROOT)): digest(p.read_text()) for p in sorted((ROOT/'services/ids-ingest').glob('*.py'))}
    summary = {'status': 'PASS', 'baseline': BASELINE, 'workload': {k: v for k, v in json.loads((output/'manifest.json').read_text()).items()
        if k != 'batches'}, 'phases': dict(zip(('exercise', 'recover'), results)), 'limits': LIMITS,
        'versions': {name: version(name) for name in ('fastapi', 'httpx', 'uvicorn')}, 'event_payload_bytes': {'min': min(len(canonical(e)) for b in json.loads((output/'manifest.json').read_text())['batches'] for e in b['events']),
            'max': max(len(canonical(e)) for b in json.loads((output/'manifest.json').read_text())['batches'] for e in b['events'])},
        'source_sha256': sources, 'harness_sha256': digest(Path(__file__).read_text()),
        'resource_contract': {'cpu_limit': 1, 'memory_limit_bytes': 512*1024**2,
            'enforcement': 'External container command must establish these limits; see README'}}
    save(output/'summary.json', summary)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()

"""Bounded-cardinality metrics and a structured, lossless VictoriaLogs envelope."""
import json
import math
import time
from datetime import datetime, timezone


def archive_record(row):
    event = json.loads(row['payload'])
    if row['kind'].startswith('host_'):
        identity = event['identity']
        return {'job': 'host-events', 'site': row['site'], 'sensor': row['sensor'],
                'event_type': row['kind'], 'event_id': row['id'],
                'tenant_id': identity['tenant_id'], 'allocation_id': identity['allocation_id'],
                'vm_id': identity['vm_id'], 'mapping_version': identity['mapping_version'],
                'evidence_trust': 'guest_reported', 'host_event': row['payload'],
                '_time': datetime.fromtimestamp(row['observed'], timezone.utc).isoformat(),
                '_msg': f"Guest-reported {event['event']['event_type']}"}
    # Keep the complete EVE JSON as a string: stats and arbitrary protocol fields
    # cannot exceed VictoriaLogs' flattened field limit. Index useful dimensions
    # separately; trusted sensor/site always come from the authenticated inbox.
    record = {key: event[key] for key in ('src_ip', 'dest_ip', 'src_port', 'dest_port',
              'proto', 'app_proto', 'flow_id') if key in event and isinstance(event[key], (str, int, float))}
    if 'vlan' in event:
        record['vlan'] = event['vlan']
    record.update(job='ids-eve', site=row['site'], sensor=row['sensor'],
                  event_type=row['kind'], event_id=row['id'], eve=row['payload'],
                  _time=datetime.fromtimestamp(row['observed'], timezone.utc).isoformat(),
                  _msg=f"Suricata {row['kind']}")
    if row['kind'] == 'alert':
        alert = event['alert']
        record.update(signature_id=alert['signature_id'], priority=alert.get('severity', 3),
                      _msg=str(alert.get('signature', 'Suricata alert'))[:240])
    if row.get('ip_context'):
        record['ip_context'] = json.loads(row['ip_context'])
    return record


def archive_body(rows):
    # Bulk API fails explicitly on malformed records, unlike jsonline's silent
    # per-line skip. Retrying after an ambiguous ACK may duplicate records;
    # event_id remains stable for query-time deduplication.
    return ''.join('{"create":{}}\n'+json.dumps(archive_record(row), allow_nan=False)+'\n' for row in rows)


def render_metrics(store, configured, now=None):
    now = time.time() if now is None else now
    lines, declared = [], set()

    def emit(name, value, labels, help_text, kind='gauge'):
        name = 'netclaw_ids_'+name
        if name not in declared:
            lines.extend([f'# HELP {name} {help_text}', f'# TYPE {name} {kind}'])
            declared.add(name)
        def escape(value):
            return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace('"', '\\"')
        tags = ','.join(f'{key}="{escape(value)}"' for key, value in sorted(labels.items()))
        number = float(value or 0)
        lines.append(f'{name}{{{tags}}} {number if math.isfinite(number) else 0}')

    with store.lock:
        for cfg in configured:
            labels = {'site': cfg['site'], 'sensor': cfg['id']}
            row = store.db.execute('SELECT * FROM sensors WHERE id=?', (cfg['id'],)).fetchone()
            hb = json.loads(row['heartbeat']) if row else {}
            emit('sensor_last_seen_timestamp_seconds', row['seen'] if row else 0, labels, 'Last authenticated sensor heartbeat.')
            emit('capture_running', bool(hb.get('capture_running')), labels, 'Capture service running at latest heartbeat; check heartbeat age.')
            emit('stats_timestamp_seconds', row['stats_time'] if row else 0, labels, 'Observation time of latest capture statistics.')
            for field, metric in [('packets', 'capture_packets_total'), ('drops', 'capture_drops_total')]:
                emit(metric, row[field] if row else 0, labels, 'Cumulative capture counter; resets when Suricata restarts.', 'counter')
            counts = json.loads(row['decoder_counts']) if row else {}
            for reason in ('invalid', 'too_many_layers', 'unknown_ethertype'):
                emit('decoder_events_total', counts.get(reason, 0), {**labels, 'reason': reason},
                     'Decoder event counters; categories can overlap and reset with Suricata.', 'counter')
            for field, metric, description in [
                ('rules_count', 'rules_loaded', 'Loaded detection rules.'),
                ('rules_checked_at', 'rules_checked_timestamp_seconds', 'Last detection rule update check.'),
                ('disk_free_bytes', 'sensor_disk_free_bytes', 'Available sensor spool disk space.')]:
                emit(metric, hb.get(field, 0), labels, description)
            probe = hb.get('capture_probe', {})
            emit('probe_timestamp_seconds', probe.get('checkedAt', 0), labels, 'Time of the last capture-source probe.')
            for source in cfg.get('capture_sources', []):
                observation = probe.get('sources', {}).get(source['id'], {})
                last_seen = observation.get('lastSeen', 0)
                source_labels = {**labels, 'source': source['id']}
                emit('source_last_seen_timestamp_seconds', last_seen, source_labels, 'Last matching ERSPAN header from configured mirror source.')
                active = now-probe.get('checkedAt', 0) < 60 and now-last_seen < 300 and now-(row['seen'] if row else 0) < 120
                emit('source_active', active, source_labels, 'Fresh heartbeat, probe and matching ERSPAN source header.')
            coverage = dict(store.db.execute('SELECT vlan,observed FROM coverage WHERE sensor=?', (cfg['id'],)))
            for vlan in cfg['vlans']:
                emit('vlan_last_seen_timestamp_seconds', coverage.get(vlan, 0), {**labels, 'vlan': vlan},
                     'Last decoded EVE observation for a configured VLAN; silence is not proof of failure.')
            for target in ('log', 'case', 'archive') if store.archive_enabled else ('log', 'case'):
                column = {'log': 'log_done', 'case': 'case_done', 'archive': 'archive_done'}[target]
                pending = store.db.execute(f'SELECT count(*),min(received_ns) FROM eve WHERE {column}=0 AND sensor=?', (cfg['id'],)).fetchone()
                target_labels = {**labels, 'destination': {'log': 'loki', 'case': 'cases', 'archive': 'victorialogs'}[target]}
                emit('delivery_pending_events', pending[0], target_labels, 'Durably queued events awaiting delivery.')
                emit('delivery_oldest_age_seconds', max(0, now-pending[1]/1e9) if pending[1] else 0, target_labels,
                     'Age of oldest undelivered event, including scheduled historical replay.')
            counters = list(store.db.execute('SELECT kind,priority,count FROM event_counts WHERE site=? AND sensor=?', (cfg['site'], cfg['id'])))
            for kind in ('alert', 'flow', 'dns', 'tls', 'stats'):
                emit('events_ingested_total', sum(r['count'] for r in counters if r['kind']==kind), {**labels, 'event_type': kind},
                     'Unique events accepted since telemetry installation; persistent across ingest restarts.', 'counter')
            for priority in ('1', '2', '3', 'other'):
                emit('alerts_ingested_total', sum(r['count'] for r in counters if r['kind']=='alert' and r['priority']==priority),
                     {**labels, 'priority': priority}, 'Unique alerts by original Suricata priority since telemetry installation.', 'counter')
        meta = dict(store.db.execute('SELECT key,value FROM metadata'))
        for target in ('log', 'case', 'archive') if store.archive_enabled else ('log', 'case'):
            emit('delivery_last_success_timestamp_seconds', meta.get('delivery_success_'+target, 0),
                 {'destination': {'log': 'loki', 'case': 'cases', 'archive': 'victorialogs'}[target]},
                 'Last successful durable batch delivery for this destination, across sensors.')
        emit('archive_backfill_active', store.archive_enabled and meta.get('archive_backfill_cursor', 0) < meta.get('archive_backfill_end', 0),
             {}, 'Retained history still awaiting scheduling; also check archive delivery queue.')
        if store.enricher:
            status = store.enricher.status()
            if status['status'] != 'not_configured':
                emit('ip_context_ready', status['status'] == 'active', {}, 'Public-IP database available, current and reload healthy.')
                emit('ip_context_database_timestamp_seconds', status['databaseBuiltAt'], {}, 'Build time of local public-IP database.')
    return '\n'.join(lines)+'\n'

"""Case-bound JEV reviews. Only an allowlisted abstraction may leave the host."""
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import sys
from dotenv import dotenv_values

# JEV is NetClaw's; the receiver runs beside a NetClaw checkout (NETCLAW_DIR).
ROOT = Path(os.environ.get('NETCLAW_DIR') or Path(__file__).resolve().parents[2])
spec = importlib.util.spec_from_file_location('convergence_jev_core', ROOT/'mcp-servers/jev-mcp/core.py')
core = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = core
spec.loader.exec_module(core)


def case_task(site, event_id):
    return 'convergence:' + hashlib.sha256((site+'\0'+event_id).encode()).hexdigest()[:40]


def configuration(site, event_id):
    path = Path(os.getenv('CONVERGENCE_JEV_ENV_FILE', str(ROOT/'.env')))
    values = {k: v for k, v in dotenv_values(path, interpolate=False).items()
              if v is not None and (k.startswith(('JEV_', 'CONVERGENCE_JEV_')) or k == 'TYPESAFE_API_KEY')}
    values.update({k: v for k, v in os.environ.items()
                   if k.startswith(('JEV_', 'CONVERGENCE_JEV_')) or k == 'TYPESAFE_API_KEY'})
    if values.get('CONVERGENCE_JEV_ENABLED', '').lower() != 'true':
        raise ValueError('JEV case reviews are not enabled')
    # A caller cannot select a new budget identity or override provider settings.
    values['JEV_TASK_ID'] = case_task(site, event_id)
    config = core.Config.from_env(values)
    if not config.enabled or (config.endpoint == core.HOSTED and not config.api_key):
        raise ValueError('JEV is not configured and enabled')
    return config


def abstraction(event):
    """Never send IPs, VLAN IDs, signatures, notes, names, payloads or timestamps."""
    evidence = event.get('source_evidence') or {}
    if event.get('alert_name') != 'SuricataIntrusion' or not isinstance(evidence, dict):
        raise ValueError('JEV case review currently supports Suricata intrusion cases')
    annotations = evidence.get('annotations') or {}
    description = annotations.get('description') if isinstance(annotations, dict) else None
    prefix = 'Passive IDS observation; not proof of compromise. '
    try:
        raw = json.loads(description[len(prefix):]) if isinstance(description, str) and description.startswith(prefix) else None
    except ValueError:
        raw = None
    if not isinstance(raw, dict) or not isinstance(raw.get('alert'), dict):
        raise ValueError('Complete original IDS evidence is required for JEV review')
    alert = raw['alert']
    signature = str(alert.get('signature', '')).upper()
    family = 'other'
    for prefixes, label in (
        (('SURICATA STREAM', 'SURICATA TCP', 'SURICATA UDP', 'SURICATA ICMP', 'SURICATA TLS', 'SURICATA APPLAYER'), 'protocol_anomaly'),
        (('SURICATA ',), 'decoder_event'), (('ET SCAN ', 'ET DROP '), 'scan_or_reputation'),
        (('ET MALWARE ', 'ET TROJAN ', 'ET CNC '), 'malware_indicator'),
        (('ET EXPLOIT ', 'ET WEB_SERVER ', 'ET WEB_CLIENT '), 'exploit_indicator'),
        (('ET POLICY ',), 'policy_indicator'),
    ):
        if signature.startswith(prefixes):
            family = label
            break
    def scope(value):
        if not isinstance(value, str): return 'unknown'
        try:
            ip = ipaddress.ip_address(value)
            if ip.is_global: return 'public'
            if ip.is_private and not (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified): return 'private'
            return 'non_public_special'
        except (ValueError, TypeError):
            return 'unknown'
    priority = alert.get('severity')
    proto = raw.get('proto')
    app = raw.get('app_proto')
    capture = event.get('capture_context') or {}
    if not isinstance(capture, dict): capture = {}
    return {
        'evidence_scope': 'one IDS alert, without corroborating flow, packet or endpoint evidence',
        'rule_family': family,
        'rule_priority': priority if type(priority) is int and 1 <= priority <= 3 else 'unknown',
        'transport': proto if proto in ('TCP', 'UDP', 'ICMP', 'IPv6-ICMP') else 'unknown',
        'application': app if app in ('http', 'http2', 'tls', 'dns', 'ssh', 'smb', 'smtp', 'rdp', 'quic', 'dhcp', 'ntp', 'pgsql') else 'unknown',
        'source_scope': scope(raw.get('src_ip')),
        'destination_scope': scope(raw.get('dest_ip')),
        'vlan_metadata': capture.get('status') if capture.get('status') in ('recorded', 'not_recorded') else 'unavailable',
        'limits': ['A signature match is not proof of compromise.',
                   'Missing observations are not proof of safety.',
                   'Capture VLAN metadata does not establish endpoint VLAN membership.'],
    }


def questions(state):
    context = f"For this {state['rule_family']} alert with priority {state['rule_priority']} and {state['source_scope']}-to-{state['destination_scope']} traffic"
    options = {'correlate_flows': 'Correlate the alert with flow timing, volume and related alerts.',
               'check_endpoint': 'Inspect relevant endpoint process and connection evidence.',
               'insufficient_context': 'Obtain the complete local alert and capture context before choosing a diagnostic.'}
    if state['rule_family'] in ('protocol_anomaly', 'decoder_event'):
        options['check_capture'] = 'Check capture duplication, offload artifacts and decoder or stream behavior.'
    if state['source_scope'] == 'public' or state['destination_scope'] == 'public':
        options['correlate_dns_tls'] = 'Correlate DNS and TLS metadata with expected application activity.'
    if state['vlan_metadata'] != 'recorded':
        options['check_capture_scope'] = 'Establish the observation point and tunnel context locally.'
    return {
        'next_check': {'type': 'choice', 'instructions': context + ', which read-only evidence collection step would be most useful next? Do not recommend blocking or suppressing alerts.', 'criteria': options},
        'evidence_strength': {'type': 'score', 'instructions': context + ', how strongly does the supplied abstract evidence support malicious activity? Assess evidence strength, not network health or incident probability.',
                              'criteria': ['Insufficient evidence to distinguish benign from malicious activity', 'Weak indication requiring corroboration', 'Multiple corroborated malicious behaviors', 'Direct independently verified compromise evidence']},
    }


def public_result(result, state, job_id):
    allowed = ('status', 'assessment_id', 'model', 'created_at', 'questions', 'answers',
               'cost_usd', 'charge_status', 'budgets', 'audit', 'message', 'request_digest')
    return {**{k: result[k] for k in allowed if k in result}, 'version': 1,
            'advisory_only': True, 'basis': 'abstract_ids_alert', 'input_summary': state, 'job_id': job_id}


async def run_job(store, job, publish):
    """One provider attempt; retries recover the ledger/result and only redeliver."""
    body = json.loads(job['payload'])
    if job.get('result'):
        result = json.loads(job['result'])
    else:
        state = body['input_summary']
        try:
            config = configuration(body['site'], body['event_id'])
            if job['dispatched']:
                with core.Ledger(config.data_dir).connect() as db:
                    row = db.execute('SELECT record FROM calls WHERE task=? AND digest=? ORDER BY rowid DESC LIMIT 1',
                                     (config.task_id, body.get('request_digest', ''))).fetchone()
                result = json.loads(row[0]) if row else {}
                if result.get('status') not in ('ok', 'timeout', 'unavailable', 'incompatible_provider', 'usage_bound_exceeded'):
                    result = {'status': 'unavailable', 'message': 'Review was interrupted; no duplicate provider call was sent.'}
            else:
                args = dict(state=state, questions=questions(state), purpose='incident_triage',
                            evidence_metadata=[{'source': 'allowlisted abstraction of one stored Suricata case'}],
                            data_classification='sanitized', config=config)
                preview = await core.evaluate(**args, prepare_only=True)
                if preview.get('status') != 'prepared' or preview.get('approval_required'):
                    result = {'status': 'unavailable', 'message': 'Review input did not pass the disclosure boundary.'}
                else:
                    await publish(body, public_result({'status': 'queued'}, state, job['id']))
                    # Commit dispatch intent and exact digest before the outbound call.
                    body['request_digest'] = preview['request_digest']
                    store.checkpoint_review(job['id'], body)
                    result = await core.evaluate(**args)
        except (ValueError, core.Refused):
            result = {'status': 'disabled', 'message': 'JEV case reviews are not configured and enabled.'}
        result = public_result(result, state, job['id'])
        store.save_result(job['id'], result)
    await publish(body, result)

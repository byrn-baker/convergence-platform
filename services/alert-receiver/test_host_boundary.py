"""Synthetic host cases retain attribution and cannot opt into a model turn."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock
import uuid

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import server as receiver
import investigation_policy

spec = importlib.util.spec_from_file_location('case_host_events', Path(__file__).parents[1]/'ids-ingest/host_events.py')
host_events = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host_events)


@pytest.mark.parametrize('site', ['lab', 'Lab.One', 'House', 'tenant:site'])
def test_guest_case_logged_as_security_without_model_or_notification(monkeypatch, site):
    now = time.time()
    stamp = lambda offset: datetime.fromtimestamp(now+offset, timezone.utc).isoformat()
    enrollment = {'id': 'agent-one', 'site': site, 'tenant_id': 'tenant-one',
                  'allocation_id': 'allocation-one', 'vm_id': 'vm-one', 'mapping_version': 'version-one',
                  'valid_from': stamp(-3600), 'valid_until': stamp(3600),
                  'protected_destinations': ['192.0.2.0/24']}
    event = {'event_type': 'connection', 'event_id': str(uuid.uuid4()), 'timestamp': stamp(0),
             'data': {'scope': 'outer_vm', 'destination_ip': '192.0.2.7', 'destination_port': 22,
                      'protocol': 'tcp', 'outcome': 'blocked', 'attempts': 5}}
    envelope = {'identity': host_events.binding(enrollment), 'event': event, 'evidence_trust': 'guest_reported'}
    payload = receiver.AlertmanagerPayload.model_validate(host_events.case_payload(enrollment, event, envelope))
    alert = payload.alerts[0]
    # Even a permissive policy must obey the explicit do-not-investigate label.
    monkeypatch.setattr(investigation_policy, 'load_policy', lambda: {
        'default_tier': 'T2', 'allow_t2': [{'alertname': 'HostBoundaryObservation'}]})
    assert investigation_policy.resolve_tier(alert).tier == 'T0'
    monkeypatch.setattr(receiver, 'NETWORK_GUARDIAN_URL', 'https://cases.invalid')
    monkeypatch.setattr(receiver, 'NETWORK_GUARDIAN_TOKEN', 'synthetic-token')
    lookup = AsyncMock(side_effect=AssertionError('must not look up a guest-supplied device'))
    notify = AsyncMock(side_effect=AssertionError('must not send notifications'))
    monkeypatch.setattr(receiver, 'lookup_device', lookup)
    monkeypatch.setattr(receiver, 'post_discord', notify)
    requests = []

    def capture(request):
        assert request.url.host == 'cases.invalid'
        assert request.url.path == '/api/events'
        assert request.url.params['site'] == site
        requests.append(json.loads(request.content))
        return httpx.Response(201, json={'id': 'fixture-case'})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(receiver.httpx, 'AsyncClient',
                        lambda **kw: original_client(transport=httpx.MockTransport(capture), **kw))
    asyncio.run(receiver.process_alert(alert))
    assert len(requests) == 1
    case = requests[0]
    assert (case['category'], case['status'], case['severity']) == ('security', 'logged', 'info')
    labels = case['source_evidence']['labels']
    assert labels['site'] == site
    assert labels['tenant_id'] == 'tenant-one' and labels['allocation_id'] == 'allocation-one'
    assert labels['mapping_version'] == 'version-one'
    assert labels['investigate'] == 'false' and labels['outcome'] == 'blocked'
    assert json.loads(case['source_evidence']['annotations']['host_evidence']) == envelope
    lookup.assert_not_awaited()
    notify.assert_not_awaited()


def test_missing_host_site_fails_closed_and_legacy_inventory_aliases_still_work():
    alert = receiver.Alert(status='firing',
                           labels=receiver.AlertLabel(alertname='HostBoundaryObservation', source='host-agent'),
                           annotations=receiver.AlertAnnotation())
    with pytest.raises(ValueError, match='enrollment site'):
        receiver.guardian_site_for_alert(alert, 'House')
    alert.labels.alertname = 'SuricataIntrusion'
    assert receiver.guardian_site_for_alert(alert, 'House') == receiver.GUARDIAN_DEFAULT_SITE

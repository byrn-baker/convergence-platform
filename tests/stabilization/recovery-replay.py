#!/usr/bin/env python3
"""Replay existing admissions in an isolated restored queue, without workers.

Run with network disabled. Does not invoke providers, Gateway, relay or RAG.
Retain the original snapshot separately before opening the writable clone.
"""
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.dont_write_bytecode = True
from recovery_guard import require_disposable_clone

directory = require_disposable_clone(os.environ['RECOVERY_DIRECTORY'],
                                    ('receiver.sqlite', 'jev-ledger.sqlite3'))
sys.path.insert(0, os.environ['RECEIVER_SOURCE'])
from jobs import JobStore

ledger_before = hashlib.sha256((directory / 'jev-ledger.sqlite3').read_bytes()).hexdigest()
store = JobStore(directory / 'receiver.sqlite')
def counts():
    return {name: store.db.execute('SELECT count(*) FROM ' + name).fetchone()[0]
            for name in ['jobs', 'reviewer_command_receipts', 'reviewer_jev_requests',
                         'jev_assessments', 'reviewer_delivery_keys']}
before = counts()
jev = [json.loads(row[0]) for row in store.db.execute('SELECT envelope FROM reviewer_jev_requests')]
investigations = [json.loads(row[0]) for row in store.db.execute('''SELECT j.payload
    FROM reviewer_command_receipts r JOIN jobs j ON j.id=r.job_id''')]
original = {body['delivery_id']: store.assessments.read(body['delivery_id']) for body in jev}
for restart in range(2):
    for body in jev:
        receipt = store.assessments.admit(body['site_id'], body['case_id'], {}, request=body)
        assert receipt == original[body['delivery_id']]
        assert receipt['participation'] == 'reused' and receipt['state'] == 'completed'
    for body in investigations:
        expected = store.reviewer.read(body['delivery_id'])
        assert store.reviewer.admit(body) == expected
    assert counts() == before
    store.db.close()
    store = JobStore(directory / 'receiver.sqlite')
assert counts() == before
store.db.close()
ledger_after = hashlib.sha256((directory / 'jev-ledger.sqlite3').read_bytes()).hexdigest()
assert ledger_before == ledger_after
print(json.dumps({'result': 'pass', 'jev_receipts': len(jev),
                  'investigation_receipts': len(investigations), 'restart_cycles': 2,
                  'added_jobs': 0, 'ledger_unchanged': True, 'workers_started': 0,
                  'provider_calls': 0, 'external_network': 'caller-enforced isolation required',
                  'scope': 'exact replay of retained admissions only; pending delivery not qualified'}))

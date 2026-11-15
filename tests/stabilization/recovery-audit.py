#!/usr/bin/env python3
"""Read-only cross-store audit of private, isolated recovery artifacts.

This intentionally never opens application runtimes or contacts any service.
Passing relational checks does not establish an atomic multi-store snapshot.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'services' / 'alert-receiver'))
from jev_assessments import receipt_result


def database(path):
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def audit(directory, postgres):
    receiver = database(directory / 'receiver.sqlite')
    ledger = database(directory / 'jev-ledger.sqlite3')
    registry = database(directory / 'rag-registry.db')
    chroma = database(directory / 'rag-chroma.sqlite3')
    findings = []
    def require(condition, code):
        if not condition:
            findings.append(code)
    try:
        integrity = {name: db.execute('PRAGMA integrity_check').fetchone()[0]
                     for name, db in [('receiver', receiver), ('ledger', ledger),
                                      ('rag_registry', registry), ('rag_chroma', chroma)]}
        require(all(value == 'ok' for value in integrity.values()), 'sqlite_integrity_failed')
        require(postgres['orphan_history'] == 0, 'postgres_orphan_history')
        for row in postgres['investigations']:
            saved = receiver.execute('SELECT job_id,state FROM reviewer_command_receipts WHERE delivery_id=?',
                                     (row['delivery_id'],)).fetchone()
            require(saved is not None, 'investigation_receiver_receipt_missing')
            if saved:
                require(saved['job_id'] == row['job_id'], 'investigation_job_mismatch')
                require(saved['state'] == row['state'], 'investigation_state_mismatch')
        for row in postgres['jev']:
            saved = receiver.execute('''SELECT r.operation_id,r.actor,r.participation,a.*
                FROM reviewer_jev_requests r JOIN jev_assessments a ON a.id=r.assessment_id
                WHERE r.delivery_id=?''', (row['delivery_id'],)).fetchone()
            require(saved is not None, 'jev_receiver_receipt_missing')
            if not saved:
                continue
            for actual, expected in [('operation_id', 'operation_id'), ('actor', 'actor_id'),
                                     ('participation', 'participation'), ('job_id', 'job_id'),
                                     ('id', 'assessment_id'), ('case_id', 'case_id'), ('site', 'site_id')]:
                require(saved[actual] == row[expected], 'jev_' + actual + '_mismatch')
            result = json.loads(saved['result']) if saved['result'] else None
            require(receipt_result(saved['result']) == row['result'], 'jev_result_mismatch')
            if result and result.get('assessment_id'):
                paid = ledger.execute('SELECT record FROM calls WHERE id=?', (result['assessment_id'],)).fetchone()
                require(paid is not None, 'jev_ledger_result_missing')
                if paid:
                    require(json.loads(paid['record']).get('status') == result.get('status'), 'jev_ledger_status_mismatch')
        for row in postgres['rag_references']:
            require(registry.execute('SELECT 1 FROM documents WHERE id=?', (row['rag_document_id'],)).fetchone()
                    is not None, 'case_rag_document_missing')
        states = dict(registry.execute('SELECT ingest_status,count(*) FROM documents GROUP BY ingest_status'))
        pending_documents = sum(count for status, count in states.items()
                                if status in ('pending', 'parsing', 'chunking', 'embedding'))
        require(pending_documents == 0, 'rag_ingestion_requires_reconciliation')
        return {'relational_audit': 'pass' if not findings else 'needs_reconciliation',
                'findings': sorted(set(findings)), 'finding_count': len(findings),
                'integrity': integrity, 'postgres_counts': postgres['counts'],
                'receiver_job_states': dict(receiver.execute('SELECT state,count(*) FROM jobs GROUP BY state')),
                'jev_ledger_statuses': dict(ledger.execute('SELECT status,count(*) FROM calls GROUP BY status')),
                'rag_document_states': states,
                'rag_embedding_rows': chroma.execute('SELECT count(*) FROM embeddings').fetchone()[0],
                'checked_investigations': len(postgres['investigations']), 'checked_jev': len(postgres['jev']),
                'checked_case_rag_references': len(postgres['rag_references']),
                'global_atomic_snapshot': False,
                'full_recovery': False,
                'remaining_boundaries': ['IDS full history/delivery database', 'RAG cross-index consistency and runtime readback',
                                         'multi-store snapshot cut and interrupted-work reconciliation',
                                         'configuration/PKI activation in isolated runtime']}
    finally:
        for db in [receiver, ledger, registry, chroma]:
            db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--postgres', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.directory, json.loads(args.postgres.read_text()))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    raise SystemExit(0 if result['relational_audit'] == 'pass' else 1)

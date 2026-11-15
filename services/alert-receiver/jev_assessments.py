"""Canonical case assessments and immutable attributed request associations.

Uses the receiver's existing transaction, queue, worker and JEV budget identity.
A request is not an instruction to buy another assessment.
"""
import hashlib
import hmac
import json
import time
import uuid

from reviewer_commands import CommandConflict, validate_identity, reserve_delivery

FIELDS = ('version', 'delivery_id', 'installation_id', 'actor_id', 'actor_name',
          'operation_id', 'site_id', 'case_id', 'command')
RESULT_FIELDS = ('version', 'status', 'advisory_only', 'basis', 'input_summary',
                 'assessment_id', 'model', 'created_at', 'questions', 'answers',
                 'cost_usd', 'charge_status', 'budgets', 'message', 'request_digest', 'job_id')


def receipt_result(encoded):
    if encoded is None:
        return None
    result = json.loads(encoded)
    if not isinstance(result, dict) or result.get('version', 1) != 1:
        return {'version': 1, 'status': 'unavailable', 'advisory_only': True,
                'message': 'Assessment result format is unsupported; the canonical result is retained for review.'}
    result = {key: result[key] for key in RESULT_FIELDS if key in result}
    result.setdefault('version', 1)
    result['advisory_only'] = True
    if len(json.dumps(result).encode()) > 32 * 1024:
        return {'version': 1, 'status': 'unavailable', 'advisory_only': True,
                'message': 'Assessment result exceeds the display limit; the canonical result is retained for review.'}
    return result


def validate_request(body):
    if not isinstance(body, dict) or set(body) != set(FIELDS):
        raise ValueError('Invalid assessment request fields')
    validate_identity(body)
    if body['command'] != 'jev_review':
        raise ValueError('Unsupported assessment command')
    return body


class JevAssessments:
    def __init__(self, jobs):
        self.jobs, self.db = jobs, jobs.db
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            self.db.execute('''CREATE TABLE IF NOT EXISTS jev_assessments (
                id TEXT PRIMARY KEY, site TEXT NOT NULL, case_id TEXT NOT NULL,
                job_id TEXT UNIQUE, result TEXT, created REAL NOT NULL,
                UNIQUE(site, case_id))''')
            self.db.execute('''CREATE TABLE IF NOT EXISTS reviewer_jev_requests (
                delivery_id TEXT PRIMARY KEY, digest TEXT NOT NULL,
                installation TEXT NOT NULL, actor TEXT NOT NULL, operation_id TEXT NOT NULL,
                assessment_id TEXT NOT NULL REFERENCES jev_assessments(id),
                participation TEXT NOT NULL, envelope TEXT NOT NULL, created REAL NOT NULL,
                UNIQUE(installation, actor, operation_id))''')
            self.db.execute('''INSERT OR IGNORE INTO reviewer_delivery_keys
                SELECT delivery_id,'jev_review',installation,actor,operation_id FROM reviewer_jev_requests''')

    def replay(self, request):
        validate_request(request)
        encoded = json.dumps([request[k] for k in FIELDS], ensure_ascii=False, separators=(',', ':'))
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        prior = self.db.execute('''SELECT delivery_id,digest FROM reviewer_jev_requests
            WHERE delivery_id=? OR (installation=? AND actor=? AND operation_id=?)''',
            (request['delivery_id'], request['installation_id'], request['actor_id'], request['operation_id'])).fetchall()
        if not prior:
            return None
        if len(prior) != 1 or prior[0]['delivery_id'] != request['delivery_id'] or not hmac.compare_digest(prior[0]['digest'], digest):
            raise CommandConflict('Assessment request conflicts with accepted inputs')
        return self.read(request['delivery_id'])

    def read(self, delivery_id):
        row = self.db.execute('''SELECT r.delivery_id,r.participation,a.id AS assessment_id,
            a.job_id,a.result,j.state AS job_state,j.dispatched FROM reviewer_jev_requests r
            JOIN jev_assessments a ON a.id=r.assessment_id
            LEFT JOIN jobs j ON j.id=a.job_id WHERE r.delivery_id=?''', (delivery_id,)).fetchone()
        if not row:
            return None
        # A saved provider result survives a failed publication or job cleanup.
        state = ('completed' if row['result'] is not None else
                 'unknown' if row['job_state'] is None else
                 'failed' if row['job_state'] == 'failed' else
                 'dispatched' if row['dispatched'] else 'enqueued')
        return {key: row[key] for key in ('delivery_id', 'assessment_id', 'job_id', 'participation')} | {
            'state': state, 'result': receipt_result(row['result'])}

    def admit(self, site, case_id, input_summary, *, request=None, cached_result=None, retry_failed=False):
        """Atomically start/join/reuse; legacy callers use the same serialization."""
        digest = None
        if request is not None:
            validate_request(request)
            if request['site_id'] != site or request['case_id'] != case_id:
                raise ValueError('Assessment identity mismatch')
            encoded = json.dumps([request[k] for k in FIELDS], ensure_ascii=False, separators=(',', ':'))
            digest = hashlib.sha256(encoded.encode()).hexdigest()
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            if request:
                reserve_delivery(self.db, request)
                prior = self.db.execute('''SELECT delivery_id,digest FROM reviewer_jev_requests
                    WHERE delivery_id=? OR (installation=? AND actor=? AND operation_id=?)''',
                    (request['delivery_id'], request['installation_id'], request['actor_id'], request['operation_id'])).fetchall()
                if prior:
                    if len(prior) != 1 or prior[0]['delivery_id'] != request['delivery_id'] or not hmac.compare_digest(prior[0]['digest'], digest):
                        raise CommandConflict('Assessment request conflicts with accepted inputs')
                    return self.read(request['delivery_id'])
            assessment = self.db.execute('SELECT * FROM jev_assessments WHERE site=? AND case_id=?',
                                         (site, case_id)).fetchone()
            participation = 'joined'
            if assessment is None:
                key = f'jev:{site}:{case_id}'
                # Adopt checkpoints created by the legacy path before migration.
                job = self.db.execute("SELECT * FROM jobs WHERE key=? AND kind='jev_review' ORDER BY created DESC LIMIT 1", (key,)).fetchone()
                result = job['result'] if job else json.dumps(cached_result) if cached_result else None
                if job:
                    jid = job['id']
                elif result is not None:
                    jid = None
                else:
                    self.jobs._check_capacity()
                    jid = self.jobs._insert_job(key, 'jev_review',
                        {'event_id': case_id, 'site': site, 'input_summary': input_summary})
                    participation = 'started'
                aid = str(uuid.uuid4())
                self.db.execute('INSERT INTO jev_assessments VALUES (?,?,?,?,?,?)',
                                (aid, site, case_id, jid, result, time.time()))
                assessment = self.db.execute('SELECT * FROM jev_assessments WHERE id=?', (aid,)).fetchone()
            if assessment['result'] is not None:
                participation = 'reused'
            if retry_failed and assessment['job_id']:
                old = self.db.execute('SELECT state FROM jobs WHERE id=?', (assessment['job_id'],)).fetchone()
                if old and old['state'] == 'failed':
                    self.jobs._check_capacity()
                    # Same checkpoint/result: the worker only resumes/re-publishes.
                    self.db.execute("UPDATE jobs SET state='queued',attempts=0,available=? WHERE id=?",
                                    (time.time(), assessment['job_id']))
            if request:
                self.db.execute('INSERT INTO reviewer_jev_requests VALUES (?,?,?,?,?,?,?,?,?)',
                    (request['delivery_id'], digest, request['installation_id'], request['actor_id'], request['operation_id'],
                     assessment['id'], participation, json.dumps(request), time.time()))
                return self.read(request['delivery_id'])
            return {'job_id': assessment['job_id'], 'assessment_id': assessment['id'],
                    'result': json.loads(assessment['result']) if assessment['result'] else None}

    def save_result(self, jid, encoded):
        """Caller owns JobStore.save_result's transaction; first result is final."""
        previous = self.db.execute('SELECT result FROM jev_assessments WHERE job_id=?', (jid,)).fetchone()
        if previous and previous['result'] is not None and previous['result'] != encoded:
            raise ValueError('Canonical assessment result is immutable')
        self.db.execute('UPDATE jev_assessments SET result=? WHERE job_id=? AND result IS NULL', (encoded, jid))

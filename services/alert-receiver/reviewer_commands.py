"""Durable admission for authenticated, attributed reviewer commands.

The receipt is deliberately independent of the retained job: retrying a delivery
must stay idempotent after the ordinary inbox's terminal-job cleanup.
"""
import asyncio
import hashlib
import hmac
import json
import re
import time

FIELDS = ('version', 'delivery_id', 'installation_id', 'actor_id', 'actor_name',
          'operation_id', 'site_id', 'case_id', 'command', 'feedback')
UUID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$')
SITE = re.compile(r'^[A-Za-z0-9_.-]{1,128}$')
INSTALLATION = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
# Match ECMAScript String.trim, including its exclusion of U+0085.
JS_TRIM = '\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
TERMINAL = frozenset(('completed', 'failed', 'unknown'))


class CommandConflict(ValueError):
    """A delivery ID already represents a different immutable envelope."""


def _text(value, maximum, minimum=1):
    return (isinstance(value, str) and minimum <= len(value) <= maximum and
            not any(c == '\0' or 0xd800 <= ord(c) <= 0xdfff for c in value))


def validate_command(body):
    if not isinstance(body, dict) or set(body) != set(FIELDS):
        raise ValueError('Invalid command fields')
    if type(body['version']) is not int or body['version'] != 1 or body['command'] != 'reinvestigate':
        raise ValueError('Unsupported command')
    if not all(isinstance(body[k], str) and UUID.fullmatch(body[k])
               for k in ('delivery_id', 'operation_id', 'case_id')):
        raise ValueError('Invalid command identity')
    if not isinstance(body['installation_id'], str) or not INSTALLATION.fullmatch(body['installation_id']):
        raise ValueError('Invalid installation')
    if not isinstance(body['site_id'], str) or not SITE.fullmatch(body['site_id']) or body['site_id'] in ('.', '..'):
        raise ValueError('Invalid site')
    if not _text(body['actor_id'], 128) or not _text(body['actor_name'], 256):
        raise ValueError('Invalid reviewer identity')
    if not _text(body['feedback'], 4000, 10) or body['feedback'] != body['feedback'].strip(JS_TRIM):
        raise ValueError('Invalid normalized feedback')
    return body


def command_digest(body):
    validate_command(body)
    canonical = json.dumps([body[k] for k in FIELDS], ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


class ReviewerCommands:
    """Shares JobStore's connection and transactions, not a second queue."""

    def __init__(self, jobs):
        self.jobs = jobs
        self.db = jobs.db
        with self.db:
            self.db.execute('''CREATE TABLE IF NOT EXISTS reviewer_command_receipts (
                delivery_id TEXT PRIMARY KEY, digest TEXT NOT NULL, job_id TEXT UNIQUE NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('enqueued','dispatched','completed','failed','unknown')),
                created REAL NOT NULL)''')

    def read(self, delivery_id):
        row = self.db.execute('SELECT delivery_id,job_id,state FROM reviewer_command_receipts WHERE delivery_id=?',
                              (delivery_id,)).fetchone()
        return dict(row) if row else None

    def admit(self, body):
        digest = command_digest(body)
        # Begin before SELECT: the receipt, shared capacity and job insertion must
        # have one serialization point even with concurrent admission connections.
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            previous = self.db.execute('SELECT * FROM reviewer_command_receipts WHERE delivery_id=?',
                                       (body['delivery_id'],)).fetchone()
            if previous:
                if not hmac.compare_digest(previous['digest'], digest):
                    raise CommandConflict('Delivery ID has different inputs')
                return {key: previous[key] for key in ('delivery_id', 'job_id', 'state')}
            self.jobs._check_capacity()
            jid = self.jobs._insert_job('reviewer:' + body['delivery_id'], 'reviewer', body)
            self.db.execute("INSERT INTO reviewer_command_receipts VALUES (?,?,?,'enqueued',?)",
                            (body['delivery_id'], digest, jid, time.time()))
            return {'delivery_id': body['delivery_id'], 'job_id': jid, 'state': 'enqueued'}

    def checkpoint(self, jid, hourly_budget):
        """Return false if this job is already checkpointed; never re-hook it."""
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            row = self.db.execute("SELECT * FROM jobs WHERE id=? AND kind='reviewer'", (jid,)).fetchone()
            if not row or row['state'] != 'running':
                return False
            if row['dispatched'] is not None:
                return False
            if self.jobs.hourly_dispatches() >= hourly_budget:
                raise OverflowError('Hourly investigation budget exhausted')
            body = json.loads(row['payload'])
            self.db.execute('UPDATE jobs SET dispatched=?,event_id=?,site=? WHERE id=?',
                            (time.time(), body['case_id'], body['site_id'], jid))
            self.db.execute("UPDATE reviewer_command_receipts SET state='dispatched' WHERE job_id=? AND state='enqueued'", (jid,))
            return True

    def finish(self, jid, state, error=None):
        if state not in TERMINAL:
            raise ValueError('Expected a terminal reviewer state')
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            row = self.db.execute('SELECT state FROM reviewer_command_receipts WHERE job_id=?', (jid,)).fetchone()
            if not row or row['state'] in TERMINAL:
                return
            self.db.execute('UPDATE reviewer_command_receipts SET state=? WHERE job_id=?', (state, jid))
            self.db.execute('UPDATE jobs SET state=?,error=? WHERE id=?',
                            ('done' if state == 'completed' else 'failed', error, jid))


def _object(pairs):
    body = {}
    for key, value in pairs:
        if key in body:
            raise ValueError('Duplicate JSON field')
        body[key] = value
    return body


async def command_body(request):
    if not re.fullmatch(r'application/json(?:\s*;\s*charset=utf-8)?', request.headers.get('content-type', ''), re.I):
        raise ValueError('Expected JSON')
    if request.headers.get('content-encoding'):
        raise ValueError('Encoded bodies are unsupported')
    chunks, size = [], 0
    async with asyncio.timeout(10):
        async for part in request.stream():
            size += len(part)
            if size > 24 * 1024:
                raise ValueError('Command body too large')
            chunks.append(part)
    body = json.loads(b''.join(chunks).decode('utf-8'), object_pairs_hook=_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Invalid JSON constant')))
    return validate_command(body)


def authenticate(request, token, ordinary_token):
    """Separate service identity, including authenticated receipt GETs."""
    from fastapi.responses import JSONResponse
    if not token or token == ordinary_token:
        return JSONResponse({'error': 'Reviewer delivery is not configured'}, status_code=503)
    if request.scope.get('scheme') != 'https':
        return JSONResponse({'error': 'HTTPS is required'}, status_code=403)
    if (request.headers.get('origin') is not None or request.headers.get('cookie') is not None or
            any(k.lower() == b'forwarded' or k.lower().startswith(b'x-forwarded-') for k, _ in request.scope['headers'])):
        return JSONResponse({'error': 'Service delivery required'}, status_code=403)
    supplied = request.headers.getlist('authorization')
    if len(supplied) != 1 or not hmac.compare_digest(supplied[0].encode('utf-8'), ('Bearer ' + token).encode('utf-8')):
        return JSONResponse({'error': 'Authentication required'}, status_code=401)
    return None

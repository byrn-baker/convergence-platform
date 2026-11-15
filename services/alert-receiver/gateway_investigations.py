"""One bounded, policy-pinned OpenClaw hook execution for a reviewer command.

Policy helpers use only the standard library so setup can validate exports without
loading the receiver or its credentials. A terminal result means agent execution
settled, not that a particular case update succeeded. Ambiguous delivery is never
retried by this adapter; the receiver owns its durable pre-dispatch checkpoint.
"""
import asyncio
from dataclasses import dataclass, field
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

PLACEHOLDER = '{{alerts[0].annotations.investigation_prompt}}'
POLICY_FIELDS = frozenset(('version', 'agent_id', 'model', 'timeout_seconds', 'message_template'))
UUID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$')
MAX_POLICY_BYTES = 65536
MAX_RESPONSE_BYTES = 16384
MAX_REQUEST_BYTES = 256 * 1024
ADMISSION_ALLOWANCE_SECONDS = 30


def _text(value, maximum):
    return (isinstance(value, str) and 0 < len(value) <= maximum and
            not any(c == '\0' or 0xd800 <= ord(c) <= 0xdfff for c in value))


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate gateway field')
        result[key] = value
    return result


def _json(data):
    return json.loads(data.decode('utf-8'), object_pairs_hook=_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid JSON value')))


def validate_policy(policy):
    """Validate and copy the exact versioned policy; never resolve credentials."""
    if not isinstance(policy, dict) or set(policy) != POLICY_FIELDS:
        raise ValueError('Invalid gateway policy fields')
    if type(policy['version']) is not int or policy['version'] != 1:
        raise ValueError('Unsupported gateway policy version')
    if not isinstance(policy['agent_id'], str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', policy['agent_id']):
        raise ValueError('Invalid gateway policy agent')
    if not _text(policy['model'], 256) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in policy['model']):
        raise ValueError('Invalid gateway policy model')
    if type(policy['timeout_seconds']) is not int or not 1 <= policy['timeout_seconds'] <= 900:
        raise ValueError('Invalid gateway policy timeout')
    template = policy['message_template']
    if not _text(template, 16384) or template.count(PLACEHOLDER) != 1:
        raise ValueError('Invalid gateway policy template')
    remainder = template.replace(PLACEHOLDER, '')
    if '{{' in remainder or '}}' in remainder:
        raise ValueError('Unsupported gateway policy template variable')
    return dict(policy)


def load_policy(path, digest):
    """Read a private regular file and verify its raw bytes before parsing it."""
    if not isinstance(path, (str, Path)) or not Path(path).is_absolute():
        raise ValueError('An absolute gateway policy path is required')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('A pinned gateway policy digest is required')
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                stat.S_IMODE(info.st_mode) not in (0o400, 0o600) or info.st_size > MAX_POLICY_BYTES):
            raise ValueError('Gateway policy must be an owner-private regular file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(MAX_POLICY_BYTES + 1)
        if len(raw) > MAX_POLICY_BYTES or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), digest):
            raise ValueError('Gateway policy digest does not match')
        return validate_policy(_json(raw))
    finally:
        os.close(fd)


@dataclass(frozen=True)
class GatewayRequest:
    url: str
    token: str = field(repr=False)
    body: bytes = field(repr=False)
    timeout_seconds: int


@dataclass(frozen=True)
class GatewayResult:
    state: str
    completion: dict | None = None


def prepare_request(origin, token, policy, delivery_id, prompt):
    """Validate all local inputs before the receiver commits its checkpoint."""
    policy = validate_policy(policy)
    if not isinstance(origin, str):
        raise ValueError('Invalid gateway origin')
    url = urlsplit(origin)
    if (url.scheme not in ('http', 'https') or not url.hostname or url.username is not None or
            url.password is not None or url.path or url.query or url.fragment or
            any(c.isspace() or ord(c) < 32 for c in origin)):
        raise ValueError('An exact gateway origin is required')
    port = url.port  # Invalid ports fail before any dispatch.
    try:
        loopback = ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        loopback = False
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('Invalid gateway port')
    if url.scheme == 'http' and not loopback:
        raise ValueError('Remote gateway calls require HTTPS')
    if not _text(token, 4096) or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError('A valid gateway hook credential is required')
    if not isinstance(delivery_id, str) or not UUID.fullmatch(delivery_id):
        raise ValueError('Invalid gateway delivery identity')
    if not _text(prompt, MAX_REQUEST_BYTES):
        raise ValueError('Invalid gateway investigation prompt')
    body = {
        'message': policy['message_template'].replace(PLACEHOLDER, prompt),
        'name': 'Convergence reviewer ' + delivery_id,
        'agentId': policy['agent_id'], 'model': policy['model'],
        'timeoutSeconds': policy['timeout_seconds'],
        'sessionKey': 'hook:convergence-reviewer:' + delivery_id,
        'sessionMode': 'isolated', 'wakeMode': 'now', 'deliver': False,
        'waitForCompletion': True, 'idempotencyKey': delivery_id,
    }
    raw = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError('Gateway investigation prompt is too large')
    return GatewayRequest(origin + '/hooks/agent', token, raw, policy['timeout_seconds'])


def validate_completion(completion):
    """Bounded internal storage projection; no provider messages or result text."""
    if (not isinstance(completion, dict) or set(completion) != {'run_id', 'status', 'reply_disposition'} or
            not isinstance(completion['run_id'], str) or not UUID.fullmatch(completion['run_id']) or
            completion['status'] not in ('ok', 'error', 'skipped') or
            completion['reply_disposition'] not in ('visible', 'silent', 'empty')):
        raise ValueError('Invalid gateway completion metadata')
    return dict(completion)


def parse_result(raw):
    value = _json(raw)
    if not isinstance(value, dict) or set(value) != {'ok', 'runId', 'completion'} or value['ok'] is not True:
        raise ValueError('Invalid gateway completion response')
    completion = value['completion']
    optional = {'delivered', 'deliveryAttempted', 'deliveryError', 'deliverySuppressionReason'}
    if (not isinstance(completion, dict) or not {'status', 'replyDisposition'} <= set(completion) or
            set(completion) - {'status', 'replyDisposition'} - optional):
        raise ValueError('Invalid gateway completion fields')
    for key in ('delivered', 'deliveryAttempted'):
        if key in completion and type(completion[key]) is not bool:
            raise ValueError('Invalid gateway delivery flag')
    if 'deliveryError' in completion and completion['deliveryError'] != 'delivery-failed':
        raise ValueError('Invalid gateway delivery error')
    if ('deliverySuppressionReason' in completion and
            completion['deliverySuppressionReason'] not in ('empty', 'silent', 'heartbeat', 'channel_transform')):
        raise ValueError('Invalid gateway delivery disposition')
    metadata = validate_completion({'run_id': value['runId'], 'status': completion['status'],
                                    'reply_disposition': completion['replyDisposition']})
    return GatewayResult('completed' if metadata['status'] == 'ok' else 'failed', metadata)


async def dispatch(request, *, client_factory=None):
    """Make exactly one request. Cancellation propagates; other ambiguity is unknown."""
    import httpx
    total = request.timeout_seconds + ADMISSION_ALLOWANCE_SECONDS
    factory = client_factory or httpx.AsyncClient
    try:
        async with asyncio.timeout(total):
            async with factory(timeout=httpx.Timeout(total, connect=10, write=10, pool=10),
                               follow_redirects=False, trust_env=False) as client:
                payload = _json(request.body)
                headers = {'Authorization': 'Bearer ' + request.token, 'Content-Type': 'application/json',
                           'Idempotency-Key': payload['idempotencyKey'], 'Accept-Encoding': 'identity'}
                async with client.stream('POST', request.url, content=request.body, headers=headers) as response:
                    if response.status_code != 200 or response.headers.get('content-encoding', 'identity').lower() != 'identity':
                        return GatewayResult('unknown')
                    if not re.fullmatch(r'application/json(?:\s*;\s*charset=utf-8)?', response.headers.get('content-type', ''), re.I):
                        return GatewayResult('unknown')
                    chunks, size = [], 0
                    async for chunk in response.aiter_raw():
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            return GatewayResult('unknown')
                        chunks.append(chunk)
                    return parse_result(b''.join(chunks))
    except Exception:
        # A receiver checkpoint already exists. Error text could contain private
        # prompts/credentials, so neither expose it nor attempt another POST.
        return GatewayResult('unknown')

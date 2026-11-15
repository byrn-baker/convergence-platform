"""Renew private-CA TLS leaves and publish them where the in-place reloaders pick them up.

Engine for setup/reviewer_tls.py; design in specs/1016-tls-renewal-reload/spec.md.

A leaf is renewed when fewer than `renew_before_days` remain. The new key and certificate
are issued by the leaf's own CA with the profile (subject, SANs, key usages) read from the
current certificate, then verified (chain, key match, identical profile, validity) before
anything is written. Every publish is a compare-and-swap against what was read, so a
concurrent change aborts instead of being overwritten, and the previous material is
archived first for rollback.

A pinned leaf (the reviewer installation client, whose fingerprint the API enrolls) rotates
in three steps, at most one per run and `settle_seconds` apart, so each consumer reloads
before the next step: enroll the new pin beside the old one, publish the new identity, then
remove the old pin. Each step is recorded before it runs and is idempotent, so a run that
stops midway resumes the same step.

Private keys never reach argv, logs or output; they pass through owner-only temporary files.
"""
import base64
import calendar
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

DAY = 86400
EKU = {'TLS Web Server Authentication': 'serverAuth', 'TLS Web Client Authentication': 'clientAuth'}
KU = {'Digital Signature': 'digitalSignature', 'Key Encipherment': 'keyEncipherment'}
RDN = re.compile(r'[A-Za-z]{1,16}=[A-Za-z0-9 ._:@-]{1,64}')
NAME = re.compile(r'[a-z][a-z0-9-]{0,40}')
STEPS = ('enroll', 'publish', 'unpin')


class RenewalError(Exception):
    """A leaf could not be renewed. Messages never include key material."""


def openssl(*args, data=None):
    result = subprocess.run(['openssl', *map(str, args)], input=data, capture_output=True, check=False,
                            stdin=None if data is not None else subprocess.DEVNULL)
    if result.returncode:
        raise RenewalError(f'openssl {args[0]} failed')
    return result.stdout


def fingerprint(cert):
    out = openssl('x509', '-noout', '-fingerprint', '-sha256', data=cert).decode()
    return out.strip().split('=', 1)[1].upper()


def validity(cert):
    out = openssl('x509', '-noout', '-startdate', '-enddate', data=cert).decode().split('\n')
    stamp = {line.split('=', 1)[0]: line.split('=', 1)[1] for line in out if '=' in line}
    return tuple(calendar.timegm(time.strptime(stamp[name], '%b %d %H:%M:%S %Y %Z'))
                 for name in ('notBefore', 'notAfter'))


def profile(cert):
    """Subject and extensions that a renewal must reproduce exactly."""
    subject = openssl('x509', '-noout', '-subject', '-nameopt', 'RFC2253', data=cert).decode().strip().split('=', 1)[1]
    parts = subject.split(',')
    if not all(RDN.fullmatch(part) for part in parts):
        raise RenewalError('certificate subject is not a simple name this tool can reproduce')
    text = openssl('x509', '-noout', '-ext', 'subjectAltName,keyUsage,extendedKeyUsage,basicConstraints',
                   data=cert).decode()
    found, current = {}, None
    for line in text.splitlines():
        if line.startswith('X509v3 '):
            name, _, flags = line[len('X509v3 '):].partition(':')
            current = name.strip()
            found[current] = {'critical': flags.strip() == 'critical', 'value': ''}
        elif current and line.strip():
            found[current]['value'] += line.strip()
    ext = {}
    for name, item in found.items():
        prefix = 'critical,' if item['critical'] else ''
        values = [v.strip() for v in item['value'].split(',') if v.strip()]
        if name == 'Basic Constraints':
            if values != ['CA:FALSE']:
                raise RenewalError('only end-entity certificates (CA:FALSE) are renewed')
            ext['basicConstraints'] = prefix + 'CA:FALSE'
        elif name == 'Key Usage' and values and all(v in KU for v in values):
            ext['keyUsage'] = prefix + ','.join(KU[v] for v in values)
        elif name == 'Extended Key Usage' and values and all(v in EKU for v in values):
            ext['extendedKeyUsage'] = prefix + ','.join(EKU[v] for v in values)
        elif name == 'Subject Alternative Name' and values:
            names = []
            for value in values:
                kind, _, entry = value.partition(':')
                if kind == 'DNS' and re.fullmatch(r'[A-Za-z0-9.-]{1,253}', entry):
                    names.append('DNS:' + entry)
                elif kind == 'IP Address' and re.fullmatch(r'[0-9A-Fa-f:.]{2,45}', entry):
                    names.append('IP:' + entry)
                else:
                    raise RenewalError('certificate has a subject alternative name this tool cannot reproduce')
            ext['subjectAltName'] = prefix + ','.join(names)
        else:
            raise RenewalError(f'certificate extension {name} is not one this tool can reproduce')
    if 'basicConstraints' not in ext:
        raise RenewalError('only end-entity certificates (CA:FALSE) are renewed')
    return {'subject': '/' + '/'.join(reversed(parts)), 'extensions': ext}


def private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def write_private(path, data):
    """Atomic owner-only replace: readers see the old file or the complete new one."""
    path = Path(path)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.')
    try:
        os.fchmod(handle, 0o600)
        os.write(handle, data)
        os.fsync(handle)
    finally:
        os.close(handle)
    try:
        os.replace(temporary, path)
    except BaseException:
        os.unlink(temporary)
        raise


def issue(prof, ca, days):
    """A new P-256 key and a certificate with the given profile, signed by ca=(cert, key)."""
    with tempfile.TemporaryDirectory(prefix='convergence-tls-') as work:
        work = Path(work)
        os.chmod(work, 0o700)
        key = openssl('genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256')
        write_private(work/'tls.key', key)
        lines = [f'{name}={value}' for name, value in sorted(prof['extensions'].items())]
        (work/'ext').write_text('\n'.join(lines + ['subjectKeyIdentifier=hash', 'authorityKeyIdentifier=keyid']) + '\n')
        openssl('req', '-new', '-key', work/'tls.key', '-subj', prof['subject'], '-out', work/'tls.csr')
        openssl('x509', '-req', '-in', work/'tls.csr', '-CA', ca[0], '-CAkey', ca[1], '-sha256',
                '-set_serial', '0x' + secrets.token_hex(16), '-days', str(days), '-extfile', work/'ext',
                '-out', work/'tls.crt')
        return (work/'tls.crt').read_bytes(), key


def chains(cert, ca_cert, check_time=True):
    with tempfile.NamedTemporaryFile() as handle:
        handle.write(cert)
        handle.flush()
        try:
            out = openssl('verify', *([] if check_time else ['-no_check_time']), '-CAfile', ca_cert, handle.name)
        except RenewalError:
            return False
    return out.decode().strip().endswith(': OK')


def verify(cert, key, ca_cert, expected, now, renew_before_seconds):
    """Refuse to publish anything the consumers' reloaders, or clients, would reject."""
    if not chains(cert, ca_cert):
        raise RenewalError('new certificate does not chain to its CA')
    if openssl('pkey', '-pubout', data=key) != openssl('x509', '-pubkey', '-noout', data=cert):
        raise RenewalError('new key does not match the new certificate')
    if profile(cert) != expected:
        raise RenewalError('new certificate profile differs from the current certificate')
    start, end = validity(cert)
    if start > now + 300 or end - now <= renew_before_seconds:
        raise RenewalError('new certificate would not outlast the renewal window')


class Kube:
    """The few kubectl calls this needs. Secret values pass through private files, never argv."""

    def __init__(self, kubectl='kubectl', context=None, kubeconfig=None):
        self.base = ([kubectl] + (['--kubeconfig', str(Path(kubeconfig).expanduser())] if kubeconfig else [])
                     + (['--context', context] if context else []))

    def _run(self, *args, data=None):
        result = subprocess.run([*self.base, *args], input=data, capture_output=True, check=False,
                                stdin=None if data is not None else subprocess.DEVNULL)
        if result.returncode:
            raise RenewalError(f'kubectl {args[0]} {args[-1] if args[0] != "create" else ""} failed'.strip())
        return result.stdout

    def secret(self, namespace, name):
        data = json.loads(self._run('-n', namespace, 'get', 'secret', name, '-o', 'json')).get('data') or {}
        return {key: base64.b64decode(value) for key, value in data.items()}

    def patch_secret(self, namespace, name, operations):
        with tempfile.TemporaryDirectory(prefix='convergence-tls-patch-') as work:
            os.chmod(work, 0o700)
            path = Path(work)/'patch.json'
            write_private(path, json.dumps(operations).encode())
            self._run('-n', namespace, 'patch', 'secret', name, '--type=json', '--patch-file', str(path))

    def post_raw(self, path, body):
        with tempfile.TemporaryDirectory(prefix='convergence-tls-push-') as work:
            file = Path(work)/'body'
            file.write_bytes(body)
            self._run('create', '--raw', path, '-f', str(file))


def b64(data):
    return base64.b64encode(data).decode()


class FileTarget:
    def __init__(self, cert, key):
        self.cert, self.key = Path(cert).expanduser(), Path(key).expanduser()

    def read(self):
        return self.cert.read_bytes(), self.key.read_bytes()

    def publish(self, cert, key, expected):
        if self.cert.read_bytes() != expected:
            raise RenewalError('published certificate changed since it was read')
        # Key first: until the certificate follows, the pair mismatches and reloaders keep the old one.
        write_private(self.key, key)
        write_private(self.cert, cert)


class SecretTarget:
    def __init__(self, kube, namespace, name):
        self.kube, self.namespace, self.name = kube, namespace, name

    def read(self):
        data = self.kube.secret(self.namespace, self.name)
        if 'tls.crt' not in data or 'tls.key' not in data:
            raise RenewalError(f'Secret {self.name} has no tls.crt/tls.key')
        return data['tls.crt'], data['tls.key']

    def publish(self, cert, key, expected):
        self.kube.patch_secret(self.namespace, self.name, [
            {'op': 'test', 'path': '/data/tls.crt', 'value': b64(expected)},
            {'op': 'replace', 'path': '/data/tls.crt', 'value': b64(cert)},
            {'op': 'replace', 'path': '/data/tls.key', 'value': b64(key)}])


class Enrollment:
    """Installation pins the reviewer API trusts, in its service Secret."""

    def __init__(self, kube, namespace, name, key='enrolled.json'):
        self.kube, self.namespace, self.name, self.key = kube, namespace, name, key

    def read(self):
        raw = self.kube.secret(self.namespace, self.name).get(self.key)
        if raw is None:
            raise RenewalError(f'Secret {self.name} has no {self.key}')
        records = json.loads(raw)
        if not isinstance(records, list) or not all(isinstance(r, dict) and r.get('fingerprint256') for r in records):
            raise RenewalError('enrollment file is not a list of pins')
        return raw, records

    def write(self, raw, records):
        path = '/data/' + self.key.replace('~', '~0').replace('/', '~1')
        self.kube.patch_secret(self.namespace, self.name, [
            {'op': 'test', 'path': path, 'value': b64(raw)},
            {'op': 'replace', 'path': path, 'value': b64(json.dumps(records).encode())}])


@dataclass
class Leaf:
    name: str
    ca: tuple
    target: object
    record: tuple = None
    enrollment: Enrollment = None


def load_config(path, kube=None):
    config = json.loads(Path(path).expanduser().read_text())
    kube = kube or Kube(config.get('kubectl', 'kubectl'), config.get('context'), config.get('kubeconfig'))
    settings = {'renew_before_days': 30, 'validity_days': 365, 'settle_seconds': 3600, **config}
    if not 1 <= settings['renew_before_days'] < settings['validity_days'] <= 825:
        raise RenewalError('renew_before_days must be at least 1 and below validity_days (at most 825)')
    leaves = []
    for item in config.get('leaves') or []:
        if not NAME.fullmatch(str(item.get('name', ''))) or any(leaf.name == item['name'] for leaf in leaves):
            raise RenewalError('each leaf needs a unique short lowercase name')
        ca = tuple(str(Path(item['ca'][k]).expanduser()) for k in ('cert', 'key'))
        if 'files' in item:
            target = FileTarget(item['files']['cert'], item['files']['key'])
        elif 'secret' in item:
            target = SecretTarget(kube, item['secret']['namespace'], item['secret']['name'])
        else:
            raise RenewalError(f'leaf {item["name"]}: needs files or secret')
        record = tuple(Path(item['record'][k]).expanduser() for k in ('cert', 'key')) if item.get('record') else None
        pins = item.get('enrollment')
        enrollment = Enrollment(kube, pins['namespace'], pins['name'], pins.get('key', 'enrolled.json')) if pins else None
        leaves.append(Leaf(item['name'], ca, target, record, enrollment))
    if not leaves:
        raise RenewalError('no leaves configured')
    state_dir = Path(settings.get('state_dir', '~/.local/state/convergence/reviewer-tls')).expanduser()
    return settings, leaves, state_dir, kube


class Renewal:
    def __init__(self, settings, leaves, state_dir, now=None):
        self.settings, self.leaves, self.state_dir = settings, leaves, Path(state_dir)
        self.now = time.time() if now is None else now
        self.state_file = self.state_dir/'state.json'
        self.state = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}

    def save(self):
        private_dir(self.state_dir)
        write_private(self.state_file, json.dumps(self.state, indent=2, sort_keys=True).encode())

    def decide(self, leaf, cert):
        """The next action for a leaf, without side effects."""
        step = self.state.get(leaf.name)
        if step and not step['done']:
            return step['step']
        if step:
            following = STEPS[STEPS.index(step['step']) + 1]
            waited = self.now - step['since']
            return following if waited >= self.settings['settle_seconds'] else 'wait'
        remaining = validity(cert)[1] - self.now
        if remaining >= self.settings['renew_before_days'] * DAY:
            return 'none'
        return 'enroll' if leaf.enrollment else 'renew'

    def plan(self):
        rows = []
        for leaf in self.leaves:
            row = {'leaf': leaf.name}
            try:
                cert, _ = leaf.target.read()
                row.update(not_after=validity(cert)[1], action=self.decide(leaf, cert))
                row['days_left'] = round((row['not_after'] - self.now) / DAY, 1)
                # Problems a renewal would hit, found before it is due.
                if not chains(cert, leaf.ca[0], check_time=False):
                    row['error'] = 'the current certificate was not issued by the configured CA'
                profile(cert)
                if leaf.record and leaf.record[0].exists() and leaf.record[0].read_bytes() != cert:
                    row['record_differs'] = True
                if self.state.get(leaf.name):
                    row['rotation'] = {k: self.state[leaf.name][k] for k in ('step', 'done', 'since')}
            except (RenewalError, OSError, ValueError, KeyError) as error:
                row['error'] = str(error) if isinstance(error, RenewalError) else type(error).__name__
            rows.append(row)
        return rows

    def run(self):
        rows = []
        for leaf in self.leaves:
            row = {'leaf': leaf.name}
            try:
                cert, _ = leaf.target.read()
                row['action'] = action = self.decide(leaf, cert)
                if action in ('renew', 'enroll', 'publish', 'unpin'):
                    getattr(self, '_' + action)(leaf)
                    cert, _ = leaf.target.read()
                row['not_after'] = validity(cert)[1]
            except (RenewalError, OSError, ValueError, KeyError) as error:
                row['error'] = str(error) if isinstance(error, RenewalError) else type(error).__name__
            rows.append(row)
        return rows

    # Steps -----------------------------------------------------------------------------

    def _new(self, leaf, current):
        # A configuration naming the wrong CA must not mint a certificate nobody trusts.
        if not chains(current, leaf.ca[0], check_time=False):
            raise RenewalError('the current certificate was not issued by the configured CA')
        prof = profile(current)
        cert, key = issue(prof, leaf.ca, self.settings['validity_days'])
        verify(cert, key, leaf.ca[0], prof, self.now, self.settings['renew_before_days'] * DAY)
        return cert, key

    def _archive(self, leaf, cert, key):
        stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(self.now))
        folder = private_dir(self.state_dir/'archive'/leaf.name/stamp)
        write_private(folder/'tls.crt', cert)
        write_private(folder/'tls.key', key)

    def _record(self, leaf, cert, key):
        if leaf.record:
            write_private(leaf.record[1], key)
            write_private(leaf.record[0], cert)

    def _renew(self, leaf):
        cert, key = leaf.target.read()
        new_cert, new_key = self._new(leaf, cert)
        self._archive(leaf, cert, key)
        leaf.target.publish(new_cert, new_key, cert)
        self._record(leaf, new_cert, new_key)

    def _begin(self, leaf, step, **fields):
        self.state[leaf.name] = {**self.state.get(leaf.name, {}), **fields, 'step': step, 'done': False}
        self.save()

    def _finish(self, leaf):
        self.state[leaf.name].update(done=True, since=self.now)
        self.save()

    def _pending(self, leaf):
        return private_dir(self.state_dir/'pending'/leaf.name)

    def _enroll(self, leaf):
        step = self.state.get(leaf.name)
        if not step:
            cert, _ = leaf.target.read()
            new_cert, new_key = self._new(leaf, cert)
            folder = self._pending(leaf)
            write_private(folder/'tls.key', new_key)
            write_private(folder/'tls.crt', new_cert)
            self._begin(leaf, 'enroll', old_pin=fingerprint(cert), new_pin=fingerprint(new_cert))
            step = self.state[leaf.name]
        raw, records = leaf.enrollment.read()
        pins = {str(r['fingerprint256']).upper(): r for r in records}
        if step['new_pin'] not in pins:
            if step['old_pin'] not in pins:
                raise RenewalError('the current installation certificate is not enrolled')
            records = records + [{**pins[step['old_pin']], 'fingerprint256': step['new_pin']}]
            leaf.enrollment.write(raw, records)
        self._finish(leaf)

    def _publish(self, leaf):
        step = self.state[leaf.name]
        if step['step'] != 'publish':
            self._begin(leaf, 'publish')
        folder = self._pending(leaf)
        new_cert, new_key = (folder/'tls.crt').read_bytes(), (folder/'tls.key').read_bytes()
        if fingerprint(new_cert) != step['new_pin']:
            raise RenewalError('pending certificate does not match the enrolled pin')
        cert, key = leaf.target.read()
        if fingerprint(cert) != step['new_pin']:
            if fingerprint(cert) != step['old_pin']:
                raise RenewalError('published installation certificate changed during rotation')
            start, end = validity(new_cert)
            if not start <= self.now < end:
                raise RenewalError('pending certificate is not currently valid')
            self._archive(leaf, cert, key)
            leaf.target.publish(new_cert, new_key, cert)
        self._record(leaf, new_cert, new_key)
        self._finish(leaf)

    def _unpin(self, leaf):
        step = self.state[leaf.name]
        if step['step'] != 'unpin':
            self._begin(leaf, 'unpin')
        raw, records = leaf.enrollment.read()
        kept = [r for r in records if str(r['fingerprint256']).upper() != step['old_pin']]
        if not any(str(r['fingerprint256']).upper() == step['new_pin'] for r in kept):
            raise RenewalError('refusing to remove the old pin: the new pin is not enrolled')
        if kept != records:
            leaf.enrollment.write(raw, kept)
        for name in ('tls.crt', 'tls.key'):
            (self._pending(leaf)/name).unlink(missing_ok=True)
        del self.state[leaf.name]
        self.save()


def metrics(rows, now):
    """Prometheus text for the Pushgateway: expiry per leaf and run outcome."""
    lines = ['# TYPE convergence_tls_cert_not_after_seconds gauge']
    lines += [f'convergence_tls_cert_not_after_seconds{{leaf="{row["leaf"]}"}} {int(row["not_after"])}'
              for row in rows if 'not_after' in row]
    failed = sum('error' in row for row in rows)
    lines += ['# TYPE convergence_tls_renewal_failed_leaves gauge', f'convergence_tls_renewal_failed_leaves {failed}',
              '# TYPE convergence_tls_renewal_last_run_seconds gauge', f'convergence_tls_renewal_last_run_seconds {int(now)}']
    if not failed:
        lines += ['# TYPE convergence_tls_renewal_last_success_seconds gauge',
                  f'convergence_tls_renewal_last_success_seconds {int(now)}']
    return ('\n'.join(lines) + '\n').encode()

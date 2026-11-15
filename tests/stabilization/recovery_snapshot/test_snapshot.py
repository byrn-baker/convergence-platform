"""Synthetic-only qualification; never discovers production/VM/source resources."""
import copy
import hashlib
import io
import json
import mmap
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from contracts import BLOCK, CHUNK, RATE, Refusal, binding, digest, identity, range_check
from reader import Lease, copy_range, direct_open, metadata, source_check
from direct_io import STATX_DIOALIGN, qualify, statx_alignment, validate_proof
from target import Store, validate_target


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def wait(self, interval):
        self.now += interval
        return False


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.img'
        self.data = b''.join(bytes([65 + n]) * BLOCK for n in range(4))
        self.source.write_bytes(self.data)
        self.source.chmod(0o600)
        self.meta = self.root / 'source.meta'
        self.meta.write_text(json.dumps(dict(Name='volume-head-old.img', Parent='', Removed=False,
                                            UserCreated=True, Created='2026-11-15T00:00:00Z', Labels={})))
        self.meta.chmod(0o600)
        snapshot = str(uuid.uuid4())
        self.config = dict(version=1, snapshot_uid=snapshot, file=identity(self.source.stat()),
                           metadata_file=identity(self.meta.stat()),
                           metadata_sha256=hashlib.sha256(self.meta.read_bytes()).hexdigest(),
                           created='2026-11-15T00:00:00Z', labels={}, identities={'snapshot_uid': snapshot})
        self.attempt = str(uuid.uuid4())
        self.config_path = self.root / 'fingerprint.json'
        self.config_path.write_text(json.dumps(self.config))
        self.config_path.chmod(0o600)

    def tearDown(self):
        self.temp.cleanup()

    def lease(self, offset=0, length=BLOCK, clock=time.monotonic):
        expected = {'binding': binding(self.config, self.attempt, offset, length),
                    'identities': self.config['identities']}
        lease = Lease(expected, clock)
        lease.accept(dict(ok=True, sequence=0, **expected))
        return lease

    def store(self):
        materials = self.root / 'materials'
        materials.mkdir(mode=0o700)
        return Store.create(materials, str(uuid.uuid4()), self.config)

    def receipt(self, attempt, offset, data):
        return dict(version=1, kind='source', status='complete',
                    binding=binding(self.config, attempt, offset, len(data)),
                    initial_stat=self.config['file'], final_stat=self.config['file'],
                    direct_io_opened=True, rate=RATE, bytes_read=len(data), bytes_written=len(data),
                    direct_io_alignment=dict(statx_mask=STATX_DIOALIGN, memory_alignment=512,
                                             offset_alignment=512, buffer_address_mod=0,
                                             offset_mod=0, length_mod=0, block_size=BLOCK),
                    sha256=hashlib.sha256(data).hexdigest())

    def accepted(self, store, offset, data):
        attempt = str(uuid.uuid4())
        store.receive(attempt, offset, len(data), io.BytesIO(data))
        return store.accept(attempt, self.receipt(attempt, offset, data), 0)


class ReaderTests(Fixtures):
    def copy(self, *, offset=0, length=BLOCK, wait=None, readv=os.preadv, sink=None, clock=None):
        clock = clock or Clock()
        lease = self.lease(offset, length, clock)
        out, receipt = sink or io.BytesIO(), {}
        fd = direct_open(self.source)
        try:
            copy_range(fd, self.source, self.config, lambda: metadata(self.meta, self.config),
                       lease, out, offset, length, RATE, 60, receipt, clock, wait or clock.wait, readv)
        finally:
            os.close(fd)
        return out, receipt

    def test_actual_direct_nonzero_offset_and_hash(self):
        out, receipt = self.copy(offset=BLOCK, length=2 * BLOCK)
        self.assertEqual(out.getvalue(), self.data[BLOCK:3 * BLOCK])
        self.assertEqual(receipt['sha256'], hashlib.sha256(out.getvalue()).hexdigest())
        self.assertEqual(receipt['bytes_read'], 2 * BLOCK)
        validate_proof(receipt['direct_io_alignment'], BLOCK, 2 * BLOCK)
        self.assertGreater(receipt['direct_io_alignment']['memory_alignment'], 0)

    def test_statx_unavailable_fails_closed(self):
        with patch('direct_io.ctypes.CDLL', return_value=object()):
            with self.assertRaisesRegex(Refusal, 'unavailable'):
                direct_open(self.source)

    def test_non_linux_qualification_refused(self):
        with patch('direct_io.sys.platform', 'darwin'), patch('direct_io.ctypes.CDLL') as libc:
            with self.assertRaisesRegex(Refusal, 'Linux statx'):
                statx_alignment(-1)
            libc.assert_not_called()

    def test_statx_syscall_unsupported_fails_closed(self):
        with patch('direct_io.ctypes.CDLL') as libc:
            libc.return_value.statx.return_value = -1
            with self.assertRaisesRegex(Refusal, 'statx DIO alignment failed'):
                direct_open(self.source)

    def test_statx_missing_mask_and_zero_alignments(self):
        for mask, memory, offset in [(0, 512, 512), (STATX_DIOALIGN, 0, 512),
                                     (STATX_DIOALIGN, 512, 0)]:
            def response(fd, path, flags, requested, result):
                self.assertEqual(path, b'')
                self.assertEqual(flags, 0x5000)  # EMPTY_PATH | DONT_SYNC
                self.assertEqual(requested, STATX_DIOALIGN)
                struct.pack_into('=I', result, 0, mask)
                struct.pack_into('=II', result, 152, memory, offset)
                return 0
            with self.subTest(mask=mask, memory=memory, offset=offset):
                with patch('direct_io.ctypes.CDLL') as libc:
                    libc.return_value.statx.side_effect = response
                    with self.assertRaisesRegex(Refusal, 'missing or unsupported'):
                        direct_open(self.source)

    def test_statx_failure_closes_open_fd(self):
        real_open, opened = os.open, []
        def tracked(*args):
            fd = real_open(*args)
            opened.append(fd)
            return fd
        with patch('reader.os.open', side_effect=tracked), patch(
                'reader.statx_alignment', side_effect=Refusal('unsupported')):
            with self.assertRaises(Refusal):
                direct_open(self.source)
        with self.assertRaises(OSError):
            os.fstat(opened[0])

    def test_actual_buffer_misalignment_refused(self):
        with mmap.mmap(-1, BLOCK + 1) as allocation:
            view = memoryview(allocation)[1:]
            try:
                with patch('direct_io.statx_alignment', return_value=(STATX_DIOALIGN, 512, 512)):
                    with self.assertRaisesRegex(Refusal, 'alignment incompatible'):
                        qualify(-1, view, 0, BLOCK)
            finally:
                view.release()

    def test_range_and_block_alignment_refused(self):
        with mmap.mmap(-1, BLOCK) as allocation:
            for offset, length, alignment in [(1, BLOCK, 512), (0, BLOCK - 1, 512),
                                               (0, 2 * BLOCK, 2 * BLOCK)]:
                with self.subTest(offset=offset, length=length, alignment=alignment):
                    with patch('direct_io.statx_alignment', return_value=(STATX_DIOALIGN, 1, alignment)):
                        with self.assertRaisesRegex(Refusal, 'alignment incompatible'):
                            qualify(-1, allocation, offset, length)

    def test_qualification_failure_prevents_reads_and_output(self):
        fd = direct_open(self.source)
        output, receipt, clock = io.BytesIO(), {}, Clock()
        try:
            with patch('direct_io.statx_alignment', return_value=(STATX_DIOALIGN, 1, 2 * BLOCK)):
                with patch('reader.os.preadv') as read, self.assertRaises(Refusal):
                    copy_range(fd, self.source, self.config, lambda: None, self.lease(clock=clock),
                               output, 0, BLOCK, RATE, 60, receipt, clock, clock.wait, read)
                read.assert_not_called()
            self.assertEqual(output.getvalue(), b'')
        finally:
            os.close(fd)

    def test_pacing_before_each_read_no_catchup(self):
        clock, times = Clock(), []
        def read(fd, buffers, offset):
            times.append(clock.now)
            result = os.preadv(fd, buffers, offset)
            if len(times) == 1:
                clock.now += 5
            return result
        self.copy(length=3 * BLOCK, clock=clock, readv=read)
        self.assertEqual(times, [.25, 5.5, 5.75])

    def test_short_read_rejected(self):
        with self.assertRaisesRegex(Refusal, 'Short direct'):
            self.copy(readv=lambda *_: 512)

    def test_source_changed_after_read_rejected_before_output(self):
        out = io.BytesIO()
        def read(fd, buffers, offset):
            count = os.preadv(fd, buffers, offset)
            os.utime(self.source, ns=(1, 1))
            return count
        with self.assertRaisesRegex(Refusal, 'Source identity'):
            self.copy(readv=read, sink=out)
        self.assertEqual(out.getvalue(), b'')

    def test_path_replacement_even_open_inode_unchanged(self):
        fd = direct_open(self.source)
        try:
            self.source.rename(self.root / 'old')
            self.source.write_bytes(self.data)
            with self.assertRaisesRegex(Refusal, 'Source identity'):
                source_check(fd, self.source, self.config['file'])
        finally:
            os.close(fd)

    def test_metadata_change(self):
        self.meta.write_text('{}')
        with self.assertRaises(Refusal):
            metadata(self.meta, self.config)

    def test_metadata_hash_checked_even_with_matching_stat(self):
        changed = copy.deepcopy(self.config)
        changed['metadata_sha256'] = '0' * 64
        with self.assertRaisesRegex(Refusal, 'Metadata content'):
            metadata(self.meta, changed)

    def test_direct_io_no_fallback(self):
        with patch('reader.os.open', side_effect=OSError('O_DIRECT unsupported')) as opened:
            with self.assertRaises(OSError):
                direct_open(self.source)
        self.assertEqual(opened.call_count, 1)

    def test_direct_flag_required(self):
        with patch('reader.fcntl.fcntl', return_value=0):
            with self.assertRaisesRegex(Refusal, 'flag absent'):
                direct_open(self.source)

    def test_bounds(self):
        for offset, length in [(-BLOCK, BLOCK), (1, BLOCK), (0, 0), (0, BLOCK + 1),
                               (4 * BLOCK, BLOCK), (0, CHUNK + BLOCK), (True, BLOCK)]:
            with self.subTest(offset=offset, length=length), self.assertRaises(Refusal):
                range_check(self.config, offset, length)

    def test_lease_failures_and_attempt_isolation(self):
        for change in [lambda x: x.update(ok=False), lambda x: x.update(sequence=0),
                       lambda x: x['binding'].update(attempt=str(uuid.uuid4())),
                       lambda x: x['binding'].update(offset=BLOCK),
                       lambda x: x.update(identities={}), lambda x: x.update(sequence=True)]:
            lease = self.lease()
            frame = dict(ok=True, sequence=1, **copy.deepcopy(lease.expected))
            change(frame)
            with self.assertRaises(Refusal):
                lease.accept(frame)
            self.assertTrue(lease.cancel.is_set())
        clock = Clock()
        lease = self.lease(clock=clock)
        clock.now = 8.01
        with self.assertRaises(Refusal):
            lease.check()

    def test_canceled_before_read(self):
        with self.assertRaisesRegex(Refusal, 'Canceled before'), patch('reader.os.preadv') as read:
            self.copy(wait=lambda _: True, readv=read)
        read.assert_not_called()

    def test_rate_and_deadline(self):
        clock = Clock()
        def wait(delay):
            clock.now = 61
            return False
        with self.assertRaises(Refusal):
            self.copy(clock=clock, wait=wait)

    def metadata_boundary(self, boundary, failure):
        clock = Clock()
        lease = self.lease(clock=clock)
        output, receipt, calls = io.BytesIO(), {}, []
        reads = []
        def checked_metadata():
            metadata(self.meta, self.config)
            calls.append(True)
            if len(calls) == boundary:
                if failure == 'cancel':
                    lease.cancel.set()
                elif failure == 'stale':
                    clock.now += 9
                else:
                    clock.now = 61
                    lease.accept(dict(ok=True, sequence=1, **lease.expected))
        def read(fd, buffers, offset):
            reads.append(offset)
            return os.preadv(fd, buffers, offset)
        fd = direct_open(self.source)
        try:
            with self.assertRaisesRegex(Refusal, 'deadline' if failure == 'deadline' else 'lease'):
                copy_range(fd, self.source, self.config, checked_metadata, lease, output,
                           0, BLOCK, RATE, 60, receipt, clock, clock.wait, read)
        finally:
            os.close(fd)
        self.assertEqual(len(reads), 0 if boundary == 1 else 1)
        self.assertEqual(receipt['bytes_read'], 0 if boundary == 1 else BLOCK)
        self.assertEqual(len(output.getvalue()), 0 if boundary == 1 else BLOCK)

    def test_cancel_after_pre_read_metadata_prevents_read(self):
        self.metadata_boundary(1, 'cancel')

    def test_stale_after_pre_read_metadata_prevents_read(self):
        self.metadata_boundary(1, 'stale')

    def test_deadline_after_pre_read_metadata_prevents_read(self):
        self.metadata_boundary(1, 'deadline')

    def test_cancel_during_final_metadata_prevents_completion(self):
        self.metadata_boundary(3, 'cancel')

    def test_stale_during_final_metadata_prevents_completion(self):
        self.metadata_boundary(3, 'stale')

    def test_deadline_during_final_metadata_prevents_completion(self):
        self.metadata_boundary(3, 'deadline')


class ProcessTests(Fixtures):
    def launch(self, length=BLOCK, deadline=5):
        code = """import json,sys
from reader import run
from contracts import load_json
r=run(load_json(sys.argv[1]),sys.argv[2],sys.argv[3],sys.argv[4],0,int(sys.argv[5]),4194304,int(sys.argv[6]),0,1)
print(json.dumps(r),file=sys.stderr,flush=True)
sys.exit(0 if r['status']=='complete' else 1)
"""
        out = tempfile.TemporaryFile()
        process = subprocess.Popen([sys.executable, '-c', code, str(self.config_path), str(self.source),
                                    str(self.meta), self.attempt, str(length), str(deadline)],
                                   cwd=Path(__file__).parent, stdin=subprocess.PIPE,
                                   stdout=out, stderr=subprocess.PIPE)
        frame = dict(ok=True, sequence=0, binding=binding(self.config, self.attempt, 0, length),
                     identities=self.config['identities'])
        process.stdin.write(json.dumps(frame).encode() + b'\n')
        process.stdin.flush()
        self.addCleanup(self.cleanup_process, process, out)
        return process, out

    @staticmethod
    def cleanup_process(process, out):
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdin.close()
        process.stderr.close()
        out.close()

    def result(self, process):
        process.wait(timeout=7)
        return json.loads(process.stderr.read())

    def test_shutdown_with_stdin_still_open(self):
        process, output = self.launch()
        receipt = self.result(process)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(receipt['status'], 'complete')
        self.assertFalse(process.stdin.closed)
        output.seek(0)
        self.assertEqual(output.read(), self.data[:BLOCK])

    def test_real_source_receipt_accepts_only_matching_received_bytes(self):
        process, output = self.launch(2 * BLOCK)
        source = self.result(process)
        store = self.store()
        output.seek(0)
        store.receive(self.attempt, 0, 2 * BLOCK, output)
        result = store.accept(self.attempt, source, process.returncode)
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(len(Store(store.path).verified()), 1)

    def test_midchunk_signal_rejects(self):
        process, _ = self.launch(4 * BLOCK)
        time.sleep(.4)
        process.send_signal(signal.SIGTERM)
        receipt = self.result(process)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(receipt['status'], 'rejected')
        self.assertLess(receipt['bytes_read'], 4 * BLOCK)

    def test_midchunk_eof_rejects(self):
        process, _ = self.launch(4 * BLOCK)
        time.sleep(.4)
        process.stdin.close()
        receipt = self.result(process)
        self.assertEqual(receipt['status'], 'rejected')
        self.assertLess(receipt['bytes_read'], 4 * BLOCK)

    def test_midchunk_identity_change_rejects(self):
        process, _ = self.launch(4 * BLOCK)
        time.sleep(.4)
        os.utime(self.source, ns=(1, 1))
        self.assertEqual(self.result(process)['status'], 'rejected')


class StoreTests(Fixtures):
    def test_empty_status_cli_includes_acquisition_identity(self):
        import acquire
        store, output = self.store(), io.StringIO()
        argv = ['acquire.py', '--target-contract', str(self.config_path),
                '--acquisition', store.path.name, 'status']
        # Only deployment guards are replaced; CLI parsing, Store loading,
        # locking, verification, identity derivation and JSON output are real.
        with patch('sys.argv', argv), patch('sys.stdout', output), patch(
                'acquire.secure_path', return_value=self.config_path), patch(
                'acquire.validate_target', return_value=(store.path.parent, 0)):
            acquire.main()
        self.assertEqual(json.loads(output.getvalue()), {
            'status': 'verified', 'acquisition': store.path.name,
            'fingerprint': digest(self.config), 'snapshot_uid': self.config['snapshot_uid'],
            'chunks': []})

    def test_receive_is_not_acceptance_and_failed_source_retained(self):
        store = self.store()
        result = store.receive(self.attempt, 0, BLOCK, io.BytesIO(self.data[:BLOCK]))
        self.assertEqual(result['status'], 'received_unverified')
        self.assertEqual(store.verified(), [])
        with self.assertRaises(Refusal):
            store.accept(self.attempt, self.receipt(self.attempt, 0, self.data[:BLOCK]), 1)
        self.assertTrue((store.attempt_path(self.attempt) / 'payload.partial').exists())

    def test_truncated_and_oversized_partial_preserved(self):
        store = self.store()
        for data in [b'short', self.data[:BLOCK] + b'x']:
            attempt = str(uuid.uuid4())
            with self.assertRaises(Refusal):
                store.receive(attempt, 0, BLOCK, io.BytesIO(data))
            folder = store.attempt_path(attempt)
            self.assertTrue((folder / 'payload.partial').exists())
            self.assertEqual(json.loads((folder / 'target-receipt.json').read_text())['status'], 'partial')

    def test_completion_mismatches(self):
        store = self.store()
        store.receive(self.attempt, 0, BLOCK, io.BytesIO(self.data[:BLOCK]))
        original = self.receipt(self.attempt, 0, self.data[:BLOCK])
        for mutate in [lambda r: r.update(status='rejected'), lambda r: r.update(sha256='0' * 64),
                       lambda r: r['binding'].update(offset=BLOCK),
                       lambda r: r['binding'].update(fingerprint='0' * 64),
                       lambda r: r.update(bytes_read=1), lambda r: r.update(direct_io_opened=False),
                       lambda r: r.pop('direct_io_alignment'),
                       lambda r: r['direct_io_alignment'].update(statx_mask=0),
                       lambda r: r['direct_io_alignment'].update(memory_alignment=0),
                       lambda r: r['direct_io_alignment'].update(offset_alignment=0),
                       lambda r: r['direct_io_alignment'].update(offset_alignment=2 * BLOCK),
                       lambda r: r['direct_io_alignment'].update(buffer_address_mod=1),
                       lambda r: r.update(final_stat={})]:
            receipt = copy.deepcopy(original)
            mutate(receipt)
            with self.assertRaises(Refusal):
                store.accept(self.attempt, receipt, 0)
        self.assertEqual(store.verified(), [])

    def test_target_tamper_before_accept(self):
        store = self.store()
        store.receive(self.attempt, 0, BLOCK, io.BytesIO(self.data[:BLOCK]))
        (store.attempt_path(self.attempt) / 'payload.partial').write_bytes(b'Z' * BLOCK)
        with self.assertRaisesRegex(Refusal, 'Target bytes'):
            store.accept(self.attempt, self.receipt(self.attempt, 0, self.data[:BLOCK]), 0)

    def test_duplicate_overlap_and_unique_attempts(self):
        store = self.store()
        first = self.accepted(store, 0, self.data[:2 * BLOCK])
        with self.assertRaises(FileExistsError):
            store.receive(first['binding']['attempt'], 0, BLOCK, io.BytesIO(self.data[:BLOCK]))
        for offset in [0, BLOCK]:
            attempt = str(uuid.uuid4())
            data = self.data[offset:offset + BLOCK]
            store.receive(attempt, offset, BLOCK, io.BytesIO(data))
            with self.assertRaisesRegex(Refusal, 'Duplicate or overlapping'):
                store.accept(attempt, self.receipt(attempt, offset, data), 0)

    def test_resume_rehash_and_tamper(self):
        store = self.store()
        first = self.accepted(store, 0, self.data[:BLOCK])
        reopened = Store(store.path)
        self.assertEqual(len(reopened.verified()), 1)
        (store.attempt_path(first['binding']['attempt']) / 'payload.chunk').write_bytes(b'Z' * BLOCK)
        with self.assertRaisesRegex(Refusal, 'changed'):
            reopened.verified()

    def test_receipt_tamper_on_resume_rejected(self):
        store = self.store()
        self.accepted(store, 0, self.data[:BLOCK])
        path = next((store.path / 'accepted').glob('*.json'))
        row = json.loads(path.read_text())
        row['source_exit_code'] = 1
        path.write_text(json.dumps(row))
        with self.assertRaisesRegex(Refusal, 'completion'):
            Store(store.path).verified()

    def test_accept_does_not_rehash_prior_chunks(self):
        store = self.store()
        self.accepted(store, 0, self.data[:BLOCK])
        import target
        with patch('target.file_hash', wraps=target.file_hash) as hashed:
            self.accepted(store, BLOCK, self.data[BLOCK:2 * BLOCK])
        self.assertEqual(hashed.call_count, 1)

    def test_wrong_fingerprint_on_resume(self):
        store = self.store()
        self.accepted(store, 0, self.data[:BLOCK])
        changed = copy.deepcopy(self.config)
        changed['created'] = 'different'
        (store.path / 'acquisition.json').write_text(json.dumps(changed))
        with self.assertRaisesRegex(Refusal, 'fingerprint'):
            Store(store.path).verified()

    def test_missing_range_and_full_assembly_no_overwrite(self):
        store = self.store()
        self.accepted(store, BLOCK, self.data[BLOCK:2 * BLOCK])
        with self.assertRaises(Refusal):
            store.assemble()
        self.accepted(store, 0, self.data[:BLOCK])
        with self.assertRaises(Refusal):
            store.assemble()
        self.accepted(store, 2 * BLOCK, self.data[2 * BLOCK:])
        result = store.assemble()
        self.assertEqual(result['bytes'], len(self.data))
        self.assertEqual(result['sha256'], hashlib.sha256(self.data).hexdigest())
        self.assertEqual((store.path / 'snapshot.img').read_bytes(), self.data)
        with self.assertRaises(Refusal):
            store.assemble()

    def test_symlink_and_hardlink_refusal(self):
        store = self.store()
        row = self.accepted(store, 0, self.data[:BLOCK])
        path = store.attempt_path(row['binding']['attempt']) / 'payload.chunk'
        os.link(path, self.root / 'hardlink')
        with self.assertRaises(Refusal):
            store.verified()
        (self.root / 'hardlink').unlink()
        path.rename(self.root / 'saved')
        path.symlink_to(self.root / 'saved')
        with self.assertRaises(Refusal):
            store.verified()

    def test_space_reserve(self):
        store = self.store()
        store.reserve = 2 ** 62
        with self.assertRaisesRegex(Refusal, 'reserve'):
            store.receive(self.attempt, 0, BLOCK, io.BytesIO(self.data[:BLOCK]))

    def test_target_guard_rejects_nonroot_and_production(self):
        contract = dict(version=1, root=str(self.root), marker='/nonexistent',
                        expected_marker=dict(vmid=117, purpose='convergence-stabilization', production=False),
                        product_uuid=str(uuid.uuid4()), reserve_bytes=0)
        with patch('target.os.geteuid', return_value=1000), self.assertRaises(Refusal):
            validate_target(contract)
        contract['expected_marker']['production'] = True
        with patch('target.os.geteuid', return_value=0), self.assertRaises(Refusal):
            validate_target(contract)
        contract['expected_marker']['production'] = False
        with patch('target.os.geteuid', return_value=0), self.assertRaises(Refusal):
            validate_target(contract)  # fixture directory is not a separate mount


if __name__ == '__main__':
    unittest.main(verbosity=2)

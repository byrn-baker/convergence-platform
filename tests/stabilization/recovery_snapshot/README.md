# Bounded snapshot acquisition helpers

These helpers acquire one already reviewed, retained raw base snapshot in bounded
chunks. They do not discover a snapshot, create Kubernetes objects, connect to a
VM, acquire a source lock, run a health probe, or authorize production reads.
The coordinator owns those decisions and supplies the identities and fresh leases.
No installation-specific source identity or credential is embedded here.

The modules have separate responsibilities:

- `contracts.py`: canonical fingerprint, attempt identity and aligned range bounds.
- `direct_io.py`: Linux statx alignment qualification and receipt proof validation.
- `reader.py`: one leased, paced O_DIRECT range; raw stdout and a JSON stderr receipt.
- `target.py`: exact target guard, private chunk storage and verified acquisition index.
- `acquire.py`: root target CLI for initialization, receipt collection, acceptance,
  resume verification and final assembly.

## Source contract

The private fingerprint JSON has exactly these fields:

```text
version: 1
snapshot_uid: canonical UUID
file: full stat identity of the exact image
metadata_file: full stat identity of its exact metadata file
metadata_sha256: SHA256 of that small metadata file
created: exact metadata Created value
labels: exact metadata Labels object
identities: coordinator-pinned watchdog identities, including snapshot_uid
```

Stat identity contains `device_major`, `device_minor`, `inode`, `size`, `mtime_ns`,
`ctime_ns`, `uid`, `gid`, `mode`, `nlink`. Both files must have one link and must not
be writable by group/others. Image size must be a positive multiple of 1 MiB.
Metadata must be at most 4096 bytes, Parent empty, Removed false and UserCreated
true. A retained base with no backing image/encryption is a prerequisite verified
by the coordinator. Longhorn's metadata Name can retain the old head name; it is
not used as identity. The fingerprint's canonical SHA256 binds every chunk to the
same exact metadata and source identities throughout the acquisition.

Each process reads at most 64 MiB, aligned to 1 MiB, from an explicit offset.
Rate defaults to and cannot exceed 4 MiB/s. It waits a full block/rate interval
before every read and accumulates no catch-up credit during stalls. O_DIRECT and
aligned mmap/preadv are mandatory, with no buffered fallback. The open fd must
retain O_DIRECT according to F_GETFL and return STATX_DIOALIGN with nonzero memory
and offset alignment. Qualification uses libc statx on that fd with AT_EMPTY_PATH
and AT_STATX_DONT_SYNC; it does not request a filesystem sync. Before any image
read, the actual mmap address, starting offset, range length and every 1 MiB block
must satisfy those returned alignments. The source receipt records the returned
mask/alignments and zero alignment remainders without disclosing its memory address.
Full path/fd stat
and metadata content are checked before and after each block and at completion.
The reader never writes source metadata or checksum sidecars.

This is Linux-only and requires a libc statx wrapper plus a kernel/filesystem that
positively reports STATX_DIOALIGN (introduced in Linux 6.1). Missing symbols,
unsupported syscalls, omitted result masks, zero alignments and incompatible
buffers/ranges fail closed, including filesystems that support some direct I/O
but do not report this capability. There is no guessed syscall number, buffered
retry or assumed page alignment. O_DIRECT alone is insufficient: Linux ext4 can
fall back to buffered reads for unsupported inode features. The coordinator must
qualify the actual source node kernel, filesystem and immutable snapshot identity;
a positive local fixture is not production qualification. Kernel/filesystem bugs,
concurrent administrative changes and storage latency remain outside this proof.
See the [statx ABI](https://man7.org/linux/man-pages/man2/statx.2.html) and
[Linux 6.8 ext4 direct-I/O checks](https://github.com/torvalds/linux/blob/v6.8/fs/ext4/file.c#L36).

Source CLI requires UID10001, using an exact read-only file mount and a confined
process prepared by the coordinator. Paths are configurable for reuse, but their
full identities must match the fingerprint. Examples below are review templates,
not instructions to run against an unqualified source:

```sh
python3 reader.py --fingerprint /tools/fingerprint.json \
  --attempt "$ATTEMPT_UUID" --offset "$OFFSET" --length 67108864 \
  --source /snapshot/source.img --metadata /snapshot/source.meta
```

Before starting either side, persist a new attempt UUID, acquisition UUID, offset
and length. The watchdog feeds newline-delimited JSON on reader stdin:

```text
{
  "ok": true,
  "binding": {"fingerprint": CANONICAL_SHA256, "snapshot_uid": UUID,
              "attempt": ATTEMPT_UUID, "offset": OFFSET, "length": LENGTH},
  "identities": EXACT_FINGERPRINT_IDENTITIES,
  "sequence": INCREASING_INTEGER
}
```

Use `contracts.binding()` and `contracts.digest()` to derive these values. Every
retry needs a fresh attempt UUID and a newly successful health/identity admission;
old attempt leases are rejected. Lease TTL is eight seconds. EOF, stale/invalid
lease, signal, deadline, short read or changed identity rejects the chunk. The
stdin listener uses select/os.read and is stopped and joined before process exit,
including when stdin remains open. Output is unbuffered and cancellation-aware.

This bounds submitted reads, not kernel completion latency. A blocked kernel read
cannot always be interrupted immediately. Direct I/O plus pacing does not provide
a device-level latency guarantee. The external watchdog must continuously check
source health, CR identities/removal/rebuild/purge, source retention and relevant
telemetry; stop leases on uncertainty. Detection is not an exclusive source lock.

## Target contract and admission

The target CLI always requires root and a root-private contract file containing:

```text
version: 1
root: exact absolute separate mount, e.g. /srv/convergence-lab
marker: root-owned target marker path
expected_marker: {vmid: TARGET_VMID, purpose: "convergence-stabilization", production: false}
product_uuid: canonical lowercase UUID pinned from the intended VM
reserve_bytes: explicit nonnegative required free-space reserve
```

The contract must match the marker exactly. The mount must differ from `/`, contain
no symlinks, be root-owned and not group/other writable. Its `.target-vmid` and
`.target-product-uuid` must match the contract and actual DMI UUID. Marker files
must be regular, root-owned, single-linked and not group/other writable. Existing
`materials/` must be root-owned0700. Root-only recovery data never enters the agent
user's home. All output is0600 under0700 directories; no secrets are printed.

The coordinator supplies a reserve appropriate to complete acquisition plus
assembly and existing material. Receiving checks `chunk length + reserve` free;
assembly checks `full source length + reserve`. At100GiB, retained chunks plus the
assembled image use about200GiB before partial retries, overhead or other stores.
The earlier isolated target contract used256GiB reserve; lowering that is an
explicit coordinator capacity decision, not automatic behavior.

```sh
python3 acquire.py --target-contract /root/target.json --acquisition "$ACQUISITION_UUID" \
  init --fingerprint /root/fingerprint.json
python3 acquire.py --target-contract /root/target.json --acquisition "$ACQUISITION_UUID" \
  receive --attempt "$ATTEMPT_UUID" --offset "$OFFSET" --length "$LENGTH"
```

Receive consumes raw stdin, requires exact length and EOF, fsyncs bytes, and writes
a `received_unverified` receipt. Deadline defaults90s, maximum300s. Truncation,
oversize or cancellation retains the unique partial and a partial receipt. Neither
a complete byte count nor a matching hash alone accepts a chunk.

The coordinator captures the *actual source process/remote exec exit status* and
exact source receipt, confirms source status complete and the approved guard run,
then transfers that receipt privately to the target. Pipe success alone is not
source success: collect each process status separately, never infer it from the
last shell pipeline command. The source receipt is not a cryptographic attestation
of process exit; the coordinator is trusted to supply the observed exit code.

```sh
python3 acquire.py --target-contract /root/target.json --acquisition "$ACQUISITION_UUID" \
  accept --attempt "$ATTEMPT_UUID" --source-receipt /root/source-receipt.json \
  --source-exit-code 0
```

Acceptance requires exit0, complete source receipt, O_DIRECT flag and positive
compatible statx alignment proof, matching
fingerprint/snapshot/attempt/offset/length, unchanged full stat, bounded read rate,
exact read/written/received byte counts, matching SHA256 and a fresh hash of target
bytes. It rejects duplicate/overlapping ranges. Only then is the payload promoted
and an atomic immutable accepted certificate published. Existing file names are
never overwritten. A crash between payload promotion and certificate publication
leaves unaccepted orphan material; do not skip it or manufacture a receipt. Use a
fresh healthy attempt for that range, retaining the orphan for reconciliation.
Older source receipts without the alignment proof are deliberately rejected; keep
their evidence separate and reacquire through the qualified reader. Deploy all
helper modules together, including `direct_io.py` on both source and target.

## Resume and final assembly

`acquisition.json` plus the append-only `accepted/` certificates form the manifest.
Target files, directories and receipts reject symlinks/hardlinks and wrong owners
or permissions. Serialize all store operations through this CLI. No agent or
other writer may modify its private material concurrently.

```sh
python3 acquire.py --target-contract /root/target.json --acquisition "$ACQUISITION_UUID" status
python3 acquire.py --target-contract /root/target.json --acquisition "$ACQUISITION_UUID" assemble
```

On resume, `status` validates the completion evidence and **rehashes every accepted
target chunk before returning it as verified**. Only those returned ranges may be
skipped. Do this once per resume, not before every new chunk; acceptance hashes the
new chunk and checks the existing range index without quadratic data rereads.
Unaccepted partials are never resumed in place. Failed chunks require new attempt
IDs and fresh external health admission.

Assembly requires every range, exactly contiguous from0 to the full fingerprinted
source size, with no duplicates, overlaps, gaps or mixed fingerprints. It rehashes
chunks, creates a unique full-image partial, verifies each chunk again while
copying, fsyncs, and publishes `snapshot.img` without replacing existing names.
`assembly-receipt.json` records the whole-image hash/size/fingerprint. A crash after
publishing the image but before its receipt leaves an unqualified image that must
be independently reconciled; retries never overwrite it. All incomplete material
remains available for diagnosis; there is no automatic cleanup/delete command.

At4MiB/s,100GiB takes at least7.11hours before orchestration overhead. Pod lifetime,
source hold, health coverage, lease delivery and renewal across that duration are
coordinator responsibilities. The helper does not qualify full filesystem or
SQLite consistency: offline integrity checks and cross-store reconciliation must
follow successful assembly before any recovery claim.

## Synthetic verification

```sh
python3 -B tests/stabilization/recovery_snapshot/test_snapshot.py
```

Tests create private temporary files only. They exercise actual local O_DIRECT,
nonzero offsets, one1MiB paced step without catch-up, short reads, stat/path/metadata
changes, no direct-I/O fallback, invalid/stale/cross-attempt leases, real child
processes with stdin held open, mid-chunk EOF/signals, real source-to-target receipt
acceptance, truncation/oversize/hash disagreement, duplicate/overlapping ranges,
wrong fingerprints, receipt tampering, target tamper on resume, full assembly and
no overwrite. Target mount/DMI positive qualification and source confinement are
deployment gates; local fixture tests do not claim either or any production read.

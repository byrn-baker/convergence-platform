# Isolated IDS load and backlog rehearsal

`ids-load.py` is a qualification harness, not a production benchmark command.
It starts the actual IDS FastAPI app, SQLite Store, serialized storage worker,
maintenance tasks and delivery loops. HTTPX ASGI requests drive finite synthetic
arrivals. Explicit mock downstream transports accept only `sink.invalid`; they
implement the Loki, VictoriaLogs bulk and case ACK shapes and retain a separate
SQLite receipt ledger. No production configuration or credential is loaded.

The harness runs **two separate OS processes**. The first drives arrivals, an
outage, storage delay and an admission burst, then exits with a known durable
undelivered tail. The second opens those databases, compares the exact retained
IDs/payload/checkpoint digest, replays every original batch, drains delivery and
reconciles all logical sink identities. Failed attempts remain on disk; a new
output directory is mandatory for each invocation. Nonzero exit means failure;
only `summary.json` with `status: PASS` is a completed run.

## Workload basis

A November15,2026 production metrics inventory measured132.28events/second over
five minutes and145.67events/second as the maximum five-minute average over24hours.
The one-hour type rates were flow106.078/s, DNS11.983/s, alert10.696/s,
TLS2.85/s and stats0.0667/s. Two sensors contributed approximately131.520/s and
0.154/s. The fixture uses synthetic sensor names and documentation address space,
with a deterministic approximation of that mix and imbalance.

Use150events/second as the measured-intake profile and300/s as stress above twice
the observed five-minute peak. Neither is evidence of the true sub-second peak.
Twenty-event batches and approximately1024-byte events are **assumptions**; the
baseline did not measure batch or payload-size distributions. Run both profiles
sequentially so the rehearsal stays within one CPU and512MiB total memory.

Default stages per profile:

1. Thirty seconds of open-loop arrivals, sixteen retrying senders, and a bounded
   256-batch client queue. The producer records scheduling lag and fails beyond
   250ms, so an overloaded generator cannot silently claim the requested load.
2. Downstream503 outage from20% through50% of arrival duration. Admission must
   retain/retry batches until accepted; the durable inbox capacity is1024pending
   events. Each sink also commits one batch but loses its ACK, exercising stable
   replay identities. Logical receipts are deduplicated only by the fixture;
   real log/archive delivery remains at least once.
3. Two milliseconds added to each actual storage operation, plus one two-second
   worker stall30% through the arrivals. Health is sampled independently of
   storage/metrics. Its latency includes scheduling delay, with a750ms maximum.
   Metrics requests run independently of queue observation, with each probe due
   100ms after the preceding completion;
   their timer starts at the scheduled due time and ends when the response completes.
   The report must contain a successful metrics request overlapping the deliberate
   stall with at least1.75seconds of delay for the default two-second stall, while
   health continues responding. This catches hidden storage waits in the observer.
   These delays model service time; they do not reproduce physical disk contention.
4. A held real storage worker and128simultaneous unique requests. The harness
   requires503backpressure and at most64admitted worker entries, then retries
   the rejected requests intact. The synthetic burst is separate from paced load.
5. Drain, then accept20final alert records while all sinks are unavailable.
   A copy-only retention experiment advances time40days and scans at most512rows
   per call: acknowledged expired records must disappear and all20pending rows
   must survive. Original evidence is not pruned.
6. Exit the first process, start a fresh one, compare original EVE IDs, stored payloads and all three delivery flags,
   replay all original batches with inserted=0, and drain all three destinations.
   Reconcile exact event/case key sets and unchanged log/archive event payloads;
   run SQLite integrity checks and report sink attempts separately from identities.

## Reproducible capped invocation

The provided launcher records the exact command and container resource readback
before and after execution, checks the exit/OOM state, and removes only its own
container. Its output root must not exist:

```sh
bash tests/stabilization/ids-load-run.sh YOUR_REVIEWED_LOCAL_IMAGE_ID \
  /path/to/new-private-load-evidence/rate150 150
bash tests/stabilization/ids-load-run.sh YOUR_REVIEWED_LOCAL_IMAGE_ID \
  /path/to/new-private-load-evidence/rate300 300
```

Results from this launcher are under `rate150/run/` or `rate300/run/`; container
inspection and launch logs are retained beside that directory. The equivalent
manual invocation below is useful when a coordinator already owns containment.

Prerequisite: a locally built current IDS image with the dependencies from
`services/ids-ingest/requirements.txt`; the source mounted below is what runs.
Pin the image by its resolved digest/ID and retain `docker inspect` resource
settings alongside results. No image is pulled by this command.

```sh
mkdir -p /path/to/new-private-load-evidence
chmod 700 /path/to/new-private-load-evidence
# Use a current reviewed local IDS image ID; execute once for150, once for300.
docker run --rm --pull=never --network none \
  --cpus 1 --memory 512m --memory-swap 512m --pids-limit 128 \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$(id -u):$(id -g)" --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD:/src:ro" -v /path/to/new-private-load-evidence:/evidence:rw \
  YOUR_REVIEWED_LOCAL_IMAGE_ID \
  python /src/tests/stabilization/ids-load.py \
  --output /evidence/rate150 --rate 150
```

Do not mount production data, a Docker socket, credentials or live configuration.
The fixture's bearer strings are synthetic, and all generated case observations
have investigation disabled. The read-only source mount must contain the complete
platform checkout, including `services/ids-ingest`.

`summary.json` records the measured baseline, workload, phase PIDs, source and
harness hashes, Python package versions, health/ingest/metrics latency, end-to-end admission delay including the client queue, offered
mix/scheduling lag, response/retry counts, sampled backlog maxima, admission
high-water mark, retention result and restart/sink reconciliation. First successful delivery after the outage and a conservative post-arrivals drain upper bound are separate timing fields; the latter is not a claim that the backlog remained full until arrivals ended. Each child
records peak RSS and CPU time; aggregate limits are established externally by
Docker, not inferred from those per-process statistics. Full manifest, stores and
phase logs remain in the private evidence directory.

## Acceptance limits

This qualifies the specified short synthetic service workload only. It does not
close full S08: physical IDS capture loss, actual downstream server performance,
TLS/ingress/client-network latency, full-history database size, retention disk
headroom, production storage contention and stable-release soak remain separate.
A responsive health endpoint proves process liveness, not advancing ingestion or
fresh capture. Metrics may legitimately wait for storage or return503when busy.
No production promotion/rollback or backup acquisition is performed by the harness.

## Executed service qualification, November15,2026

Both final profiles passed with the same harness SHA256
`92aa386bce082d1af3aa7ebc8a29964f981c547019eb612b7a60118515416111`.
The local dependency image ID was
`sha256:19c1f3fa5fc70f2a62e37e2b9c7cb78d1f402bef3aafdd9bfadab33a950dc564`;
current service source was mounted read-only. Container readback verified one CPU,
512MiB, no swap beyond that limit, no network, read-only root and dropped
capabilities. Both containers exited0 without OOM and were removed.

| Measured result |150events/s|300events/s|
|---|---:|---:|
|Paced events over30seconds|4,500|9,000|
|Retained events including128burst and20restart tail|4,648|9,148|
|Reconciled logical cases|395|761|
|Health including scheduler lag,p99|25.6ms|31.0ms|
|Health including scheduler lag,maximum|50.0ms|62.1ms|
|Metrics including scheduler lag,maximum|2.006s|1.968s|
|Health maximum during that same storage stall|2.87ms|3.08ms|
|End-to-end admission,p99 during the induced outage|8.60s|11.95s|
|End-to-end admission,maximum|8.89s|12.16s|
|503responses retained and retried|1,776|2,423|
|Observed worker admission high-water mark|64|64|
|Observed client queue high-water mark of256batches|52|171|
|Largest sampled pending destination queue|1,010|1,021|
|Largest child-process RSS|105.9MiB|140.9MiB|

All log/archive event identities and payloads reconciled, all case identities
reconciled, every original batch replayed with inserted=0, and both stores passed
SQLite integrity checks. Both processes retained exactly20pending events across
restart. The copy-only40day retention check preserved those20and removed expired
acknowledged rows. All generated event payloads were exactly1024bytes. Maximum
producer scheduling lag was40.5ms/40.6ms, below the declared250ms bound.

Sink attempts exceeded unique identities after the intentional lost ACKs. This
is expected at-least-once delivery, not an exactly-once claim. Likewise, long
admission delays and503responses during the forced small-inbox outage are real
backpressure: health responsiveness must not be presented as normal ingest latency.

The first attempt used an incorrect40-character expected case fingerprint and
was stopped; the IDS contract uses64characters. That harness oracle was corrected
and rejection now fails fast. The invalid attempt is retained privately and is
not product failure evidence. Independent review of attempt2 then found that
metrics timing began after waiting on queue observation, hiding the deliberate
storage stall. Attempt3 separates the probes, records scheduled/start/completed
timestamps, and requires the stalled metrics response and same-window responsive
health checks in both profiles. The results above supersede attempt2 timings.
The restart claim is limited to EVE IDs, stored payloads and three delivery flags;
it does not compare every database table. Five earlier negative checks passed: actual
64-character case identity, changed receipt replay rejection, extra log event
rejection, NaN threshold rejection before writes, and preservation of existing
output before Docker invocation. A bounded Grok plan review timed out without a
response; no acceptance is inferred from it.

Private evidence lives under
`~/.local/state/convergence-stabilization-release-20261115/ids-load/`, with complete
final results in `attempt3-150/` and `attempt3-300/`; earlier attempts are retained. These executed checks establish the
narrow service result above; independent review and the remaining S08 qualification
boundaries are still separate gates.

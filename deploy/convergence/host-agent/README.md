# Convergence rental VM heartbeat agent (prototype)

This Linux/Python 3 prototype reports **its own collection and delivery health**
from the outer rental VM. It does not detect attacks. Customers with root can stop
or falsify it; a valid agent credential proves enrollment, not truth. Investigate
boundary abuse using independently observed hypervisor/vNIC, firewall and IDS
evidence. Same-hypervisor bridge visibility is a separate deployment task.

The only generated event is `heartbeat`, with collection state, queue event count,
allocated queue bytes and cumulative dropped events. It never reads command lines,
environment variables, shell history, customer files, processes, sockets, packet
payloads or nested lab disks. It runs no commands and has no inbound listener,
remote-command channel or self-update. Tenant/VM/allocation identity is absent
from the agent payload and is supplied by receiver enrollment.

## Try the code without installing anything

From the repository root:

```sh
python3 -m unittest discover -s deploy/convergence/host-agent -v
```

These tests use temporary synthetic state and mocked HTTPS connections. They cover
retries and restarts, full spool/loss reporting, replay ACKs, bounded batches,
redirect rejection, certificate failures and private-file handling. They are not
live tenant, network, platform or resource qualification.

## Configuration for a separately approved deployment

`config.example.json` contains placeholders only. An operator must first configure
a new per-allocation credential in the receiver's `host_agents` list; do not reuse
sensor/status credentials or a previous customer's identity. The receiver's
server-side enrollment must include a finite validity window and mapping version.
See [the v1 contract](../../../specs/1013-convergence-host-telemetry/contract.md).

Configuration keys:

| Key | Meaning |
| --- | --- |
| `endpoint` | Full verified HTTPS URL ending in `/v1/host-events`. No credentials, query or fragment. Redirects are refused. |
| `token_file` | Absolute path to a regular file owned by the service UID, mode `0600` or `0400`; one opaque Bearer token, never placed in a URL. |
| `spool_dir` | Private, service-owned directory, mode `0700`, under trusted parent directories. |
| `ca_file` | Optional absolute path to the private CA bundle; hostname/certificate validation remains enabled. Default uses system trust. |
| `interval_seconds` | Integer 5–3600, default 30. |
| `spool_bytes` | Integer 128 KiB–32 MiB, default 32 MiB. Includes allocated file/directory blocks and a reserved write margin. |

The agent connects directly using Python's verified `HTTPSConnection`; proxy
environment variables are not used. Unknown config keys are rejected. Credential
values and untrusted HTTP response bodies are never logged. Each request has a
15-second socket timeout and a response limit of 8192 bytes.

The example `netclaw-host-agent.service.example` uses a dedicated static non-root account,
`StateDirectory`, systemd credentials, no capabilities, a read-only system and
restricted socket families. Review the `/opt/netclaw` and `/etc` paths before use.
Provision the `netclaw-host-agent` system account/group separately, without a login
shell, before starting the unit. `StateDirectory` must be a real private directory
owned by that account; the agent rejects symlink state paths (including old
DynamicUser state-directory links). Do not repurpose an existing customer spool.
The intended token source `/etc/netclaw-host-agent/token` is root-owned `0600`;
`LoadCredential` gives the agent a private service-readable copy. Configure the
example under the actual service name `netclaw-host-agent.service`, because the
credential path includes that name. It is an example, not an installer; verify
unit/kernel support and outbound HTTPS/DNS connectivity in the target environment.
Network policy must independently restrict egress if required.

The service examples set a **64 MiB emergency memory cap and 5% CPU ceiling**.
These are protection settings, not measured consumption or promises. The design
targets remain at most 32 MiB RSS, 1% average vCPU, 32 MiB spool and ~30-second
heartbeats, pending measurement under realistic outage/backlog conditions.

## Delivery, loss and recovery

Queued events have stable UUIDs and retain their original timestamps across
restarts. A single process holds an exclusive spool lock. Event/state replacement
is atomic and file/directory writes are fsynced. The agent sends at most 64 events
per request and deletes them only after a complete matching receiver ACK. An
ambiguous ACK retains data; duplicate retransmission is expected. Receiver dedup
is bounded by its retention, not an eternal exactly-once guarantee.

During outages, retry delay grows from one second to five minutes with jitter.
Heartbeat collection continues at its interval. A full queue drops **newly generated
heartbeats**, preserves unacknowledged records and durably increments a loss
counter. The next successfully queued heartbeat reports those drops. Its queue
counts describe state immediately before enqueueing that heartbeat. Old queued
heartbeats arriving later do not prove collection is current.

The byte limit counts allocated regular-file and directory blocks and reserves
at least 64 KiB (three filesystem allocation blocks on larger-block filesystems)
for temporary atomic writes and directory growth. Filesystem-wide inode,
journal and allocation metadata are outside that accounting; use a filesystem
quota for an absolute device-space ceiling. Corrupt/unexpected spool files stop the
agent for operator repair rather than silently deleting evidence. After a crash,
only recognized uncommitted temporary files are removed.

HTTP 400/401/403/404/409 and redirects stop the agent with exit status 2, preserving
its spool; systemd's example does not restart that exit code. Fix enrollment,
validity, clock or schema errors before manually restarting. Network failures,
413/429 and server errors retain records and retry. Never reuse a former
allocation's spool with a new allocation credential: archive it privately and
create fresh state. A missing heartbeat means lost visibility, not compromise.

The typed ingest contract also supports synthetic `connection` observations for
future collector work. This guest agent does not create them and cannot classify
nested EVE lab activity or initiate containment.

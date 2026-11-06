#!/usr/bin/env python3
"""Conservative Proxmox mirror reconciler. No third-party Python dependencies."""
import argparse
import copy
from dataclasses import asdict, dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
import time

TARGET_UUID = "d12a8407-ffaf-4031-b64f-3c3439ed1c91"
TARGET = "tap113i1"
UPLINK = "eno1"
BRIDGE = "vmbr3999"
PREF = 10
MAX_VMS = 256
MAX_OUTPUT = 2 * 1024 * 1024
MODELS = {"virtio", "e1000", "e1000e", "rtl8139", "vmxnet3"}


class GuardError(Exception):
    """A safe, non-secret error category for the journal."""


def require(condition, category):
    if not condition:
        raise GuardError(category)


def read(path, limit=128 * 1024):
    with open(path, "rb") as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit, "input_bound")
    return data


def mac(value):
    require(bool(re.fullmatch(r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}", value)), "mac_invalid")
    value = value.lower()
    require(not int(value[:2], 16) & 1 and value != "00:00:00:00:00:00", "mac_not_unicast")
    return value


def config(data):
    fields = {}
    for line in data.decode("utf-8", errors="strict").splitlines():
        if line.startswith("["):
            break  # Pending settings and historical snapshots are not active config.
        key, sep, value = line.partition(":")
        if sep:
            require(key not in fields, "duplicate_config_key")
            fields[key] = value.strip()
    return fields


def options(value):
    result = {}
    for item in value.split(","):
        key, sep, val = item.partition("=")
        require(bool(sep) and key not in result, "invalid_nic_options")
        result[key] = val
    return result


def nic(fields, name):
    opts = options(fields.get(name, ""))
    addresses = [v for k, v in opts.items() if k in MODELS]
    require(len(addresses) == 1, "nic_model")
    opts["mac"] = mac(addresses[0])
    return opts


def process_binding(vmid, run=Path("/run/qemu-server"), proc=Path("/proc")):
    try:
        pid = int(read(run / f"{vmid}.pid", 32))
        require(pid > 1, "pid_invalid")
        base = proc / str(pid)
        args = read(base / "cmdline").split(b"\0")
        require(b"-id" in args and args[args.index(b"-id") + 1] == str(vmid).encode(), "pid_vm_binding")
        require(Path(os.fsdecode(args[0])).name in {"kvm", "qemu-system-x86_64"}, "pid_executable")
        # Linux stat field 22; split after the last ')' because comm may contain spaces.
        start = read(base / "stat", 4096).rsplit(b")", 1)[1].split()[19]
        return [pid, int(start), hashlib.sha256(b"\0".join(args)).hexdigest()]
    except FileNotFoundError:
        return None


class Host:
    def __init__(self, configs=Path("/etc/pve/qemu-server"), net=Path("/sys/class/net")):
        self.configs, self.net = configs, net

    def run(self, argv):
        # A temporary file bounds retained memory, while timeout bounds command runtime.
        with tempfile.TemporaryFile() as out:
            try:
                result = subprocess.run(argv, stdout=out, stderr=subprocess.DEVNULL,
                                        timeout=10, check=False, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"})
            except subprocess.TimeoutExpired as exc:
                raise GuardError("command_timeout") from exc
            require(result.returncode == 0, "command_failed")
            out.seek(0)
            data = out.read(MAX_OUTPUT + 1)
            require(len(data) <= MAX_OUTPUT, "command_output_bound")
            return data

    def tc(self, *args):
        return json.loads(self.run(["tc", "-j", *args]))

    def index(self, name):
        try:
            return int(read(self.net / name / "ifindex", 32))
        except FileNotFoundError:
            return None

    def master(self, name):
        path = self.net / name / "master"
        return path.resolve().name if path.exists() else None

    def vm(self, vmid):
        data = read(self.configs / f"{vmid}.conf")
        fields = config(data)
        return fields, hashlib.sha256(data).hexdigest(), process_binding(vmid)

    def snapshot(self, selected, expected_mac):
        fields, digest, process = self.vm(113)
        require(options(fields.get("smbios1", "")).get("uuid") == TARGET_UUID, "target_uuid")
        capture = nic(fields, "net1")
        require(capture["mac"] == expected_mac and capture.get("bridge") == BRIDGE, "target_config")
        require(not any(k in capture for k in ("tag", "trunks")) and capture.get("firewall", "0") == "0"
                and capture.get("link_down", "0") == "0", "target_config")
        idx = self.index(TARGET)
        require(process is not None and idx and self.master(TARGET) == BRIDGE, "target_unavailable")
        require((self.net / TARGET / "tun_flags").exists(), "target_not_tap")
        ports = {p.name for p in (self.net / BRIDGE / "brif").iterdir()}
        require(ports == {TARGET}, "capture_bridge_not_isolated")
        for name in (BRIDGE, TARGET):
            addresses = json.loads(self.run(["ip", "-j", "address", "show", "dev", name]))
            require(len(addresses) == 1 and not addresses[0].get("addr_info"), "capture_host_address")
            require({"UP", "LOWER_UP"}.issubset(addresses[0].get("flags", [])), "capture_link_down")
        require(self.master(UPLINK) == "vmbr0" and self.index(UPLINK), "uplink_topology")
        target = [TARGET_UUID, expected_mac, idx, digest, process]
        paths = sorted(self.configs.glob("*.conf"))
        ids = [int(p.stem) for p in paths if re.fullmatch(r"6[0-9]{3}", p.stem)
               and (selected is None or int(p.stem) == selected)]
        require(len(ids) <= MAX_VMS, "vm_bound")
        sources, seen = [], {expected_mac}
        for vmid in ids:
            fields, digest, process = self.vm(vmid)
            if process is None:
                continue
            require({k for k in fields if re.fullmatch(r"net[0-9]+", k)} == {"net0"}, "source_nic_scope")
            source = nic(fields, "net0")
            require(source.get("bridge") == "vmbr0" and source.get("tag") == "301"
                    and "trunks" not in source and source.get("link_down", "0") == "0", "source_config")
            require(source["mac"] not in seen, "duplicate_mac")
            seen.add(source["mac"])
            tap = f"tap{vmid}i0"
            idx = self.index(tap)
            require(idx and (self.net / tap / "tun_flags").exists(), "source_tap_unavailable")
            if source.get("firewall", "0") == "1":
                fwbr, fwln, fwpr = f"fwbr{vmid}i0", f"fwln{vmid}i0", f"fwpr{vmid}p0"
                require(self.master(tap) == fwbr and self.master(fwln) == fwbr
                        and self.master(fwpr) == "vmbr0", "source_firewall_topology")
                require(int(read(self.net / fwln / "iflink", 32)) == self.index(fwpr)
                        and int(read(self.net / fwpr / "iflink", 32)) == self.index(fwln), "source_veth_binding")
            else:
                require(source.get("firewall", "0") == "0" and self.master(tap) == "vmbr0", "source_topology")
            sources.append({"vmid": vmid, "tap": tap, "ifindex": idx, "mac": source["mac"],
                            "binding": [digest, process]})
        return {"target": target, "sources": sources, "uplink": self.index(UPLINK)}


@dataclass(frozen=True)
class Rule:
    dev: str
    ifindex: int
    vmid: int
    mac: str = ""

    @property
    def kind(self):
        return "flower" if self.mac else "matchall"

    @property
    def protocol(self):
        return "802.1Q" if self.mac else "all"

    @property
    def handle(self):
        return 0x4E430000 + self.vmid

    def command(self, operation, cookie=""):
        args = ["tc", "filter", operation, "dev", self.dev, "ingress", "chain", "0",
                "pref", str(PREF), "protocol", self.protocol, "handle", hex(self.handle), self.kind]
        if operation == "add":
            args += ["skip_hw"]
            if self.mac:
                args += ["dst_mac", self.mac, "vlan_id", "301"]
            # mirred's default PIPE executes the next action; gact CONTINUE resumes
            # classification, including an existing pref 50 ingress policer.
            args += ["action", "mirred", "egress", "mirror", "dev", TARGET, "cookie", cookie,
                     "action", "gact", "continue"]
        return args


def desired(snapshot):
    rules = []
    for source in snapshot["sources"]:
        rules += [Rule(source["tap"], source["ifindex"], source["vmid"]),
                  Rule(UPLINK, snapshot["uplink"], source["vmid"], source["mac"])]
    return rules


def number(value):
    return int(value, 0) if isinstance(value, str) else int(value)


def at_handle(entry, rule):
    opts = entry.get("options", {})
    return (entry.get("pref") == PREF and entry.get("chain", 0) == 0
            and "handle" in opts and number(opts["handle"]) == rule.handle)


def owned(entry, rule, cookie):
    actions = entry.get("options", {}).get("actions", [])
    return (at_handle(entry, rule) and entry.get("kind") == rule.kind
            and entry.get("protocol", "").lower() == rule.protocol.lower()
            and len(actions) == 2 and actions[0].get("kind") == "mirred"
            and actions[0].get("cookie", "").lower() == cookie)


def correct(entry, rule, cookie):
    if not owned(entry, rule, cookie):
        return False
    opts = entry["options"]
    mirror, resume = opts["actions"]
    keys = opts.get("keys", {})
    return (mirror.get("mirred_action") == "mirror" and mirror.get("direction") == "egress"
            and mirror.get("to_dev") == TARGET and mirror.get("control_action") == {"type": "pipe"}
            and resume.get("kind") == "gact" and resume.get("control_action") == {"type": "continue"}
            and opts.get("skip_hw") is True
            and (not rule.mac or (keys.get("dst_mac") == rule.mac and keys.get("vlan_id") == 301)))


class Controller:
    def __init__(self, host, state_dir, apply=False):
        self.host, self.directory, self.apply = host, Path(state_dir), apply
        self.path = self.directory / "state.json"
        self.boot = read(Path("/proc/sys/kernel/random/boot_id"), 64).decode().strip()
        self.lock = None
        if apply:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            st = self.directory.lstat()
            require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid() and not st.st_mode & 0o077, "state_directory_permissions")
            self.lock = os.open(self.directory / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.state = {"boot": self.boot, "cookie": secrets.token_hex(16), "rules": [], "snapshot": None}
        if self.path.exists():
            st = self.path.lstat()
            require(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid() and stat.S_IMODE(st.st_mode) == 0o600, "state_permissions")
            loaded = json.loads(read(self.path, MAX_OUTPUT))
            require(re.fullmatch(r"[0-9a-f]{32}", loaded.get("cookie", "")) is not None
                    and len(loaded.get("rules", [])) <= MAX_VMS * 2, "state_schema")
            # Kernel rules cannot survive a boot. Do not attach ownership to reused indices.
            if loaded["boot"] == self.boot:
                for item in loaded["rules"]:
                    rule = Rule(**item)
                    require(6000 <= rule.vmid <= 6999 and rule.ifindex > 0
                            and rule.dev in {UPLINK, f"tap{rule.vmid}i0"}
                            and (rule.dev == UPLINK) == bool(rule.mac), "state_rule_scope")
                    if rule.mac:
                        mac(rule.mac)
                self.state = loaded

    def save(self):
        if not self.apply:
            return
        fd, temporary = tempfile.mkstemp(prefix=".state-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(self.state, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            dirfd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def entries(self, dev):
        return self.host.tc("filter", "show", "dev", dev, "ingress")

    def cleanup(self):
        failures = []
        for item in list(self.state["rules"]):
            rule = Rule(**item)
            try:
                # Disappeared/recreated interfaces have already lost their old filters.
                if self.host.index(rule.dev) == rule.ifindex:
                    entries = [e for e in self.entries(rule.dev) if at_handle(e, rule)]
                    for entry in entries:
                        require(owned(entry, rule, self.state["cookie"]), "cleanup_ownership_collision")
                        if self.apply:
                            self.host.run(rule.command("delete"))
                if self.apply:
                    self.state["rules"].remove(item)
                    self.save()
            except (GuardError, OSError, ValueError) as exc:
                failures.append(type(exc).__name__)
        if self.apply and not self.state["rules"]:
            self.state["snapshot"] = None
            self.save()
        require(not failures, "cleanup_incomplete")

    def reconcile(self, snapshot, guard):
        rules = desired(snapshot)
        if self.state["snapshot"] != snapshot:
            self.cleanup()
        existing = {}
        qdiscs = {}
        for rule in rules:
            if rule.dev not in existing:
                existing[rule.dev] = self.entries(rule.dev)
                qdiscs[rule.dev] = self.host.tc("qdisc", "show", "dev", rule.dev)
            for entry in existing[rule.dev]:
                if entry.get("chain", 0) == 0 and entry.get("pref", 65535) <= PREF:
                    # tc emits a header row without options before concrete filters.
                    if "options" not in entry:
                        continue
                    require(any(owned(entry, r, self.state["cookie"]) for r in rules if r.dev == rule.dev)
                            and any(asdict(r) in self.state["rules"] for r in rules
                                    if r.dev == rule.dev and owned(entry, r, self.state["cookie"])), "filter_collision")
        if not self.apply:
            return {"mode": "dry-run", "source_count": len(snapshot["sources"]),
                    "commands": [r.command("add", self.state["cookie"]) for r in rules]}
        for rule in rules:
            matches = [e for e in existing[rule.dev] if owned(e, rule, self.state["cookie"])]
            if matches:
                require(len(matches) == 1 and correct(matches[0], rule, self.state["cookie"]), "installed_rule_drift")
                continue
            require(guard() == snapshot, "lifecycle_changed")
            if not any(q.get("kind") in {"clsact", "ingress"} for q in qdiscs[rule.dev]):
                self.host.run(["tc", "qdisc", "add", "dev", rule.dev, "clsact"])
                qdiscs[rule.dev].append({"kind": "clsact"})
            # Write intent before mutation so crash recovery can identify a successful add.
            item = asdict(rule)
            if item not in self.state["rules"]:
                self.state["rules"].append(item)
            self.save()
            self.host.run(rule.command("add", self.state["cookie"]))
            require(any(correct(e, rule, self.state["cookie"]) for e in self.entries(rule.dev)), "installed_rule_verification")
        require(guard() == snapshot, "lifecycle_changed")
        self.state["snapshot"] = copy.deepcopy(snapshot)
        self.save()
        return {"mode": "apply", "source_count": len(snapshot["sources"]), "rule_count": len(rules)}


class EventLog:
    """Report state changes immediately; repeat an unchanged error once a minute."""
    def __init__(self):
        self.previous = None
        self.emitted_at = 0.0

    def emit(self, event):
        now = time.monotonic()
        encoded = json.dumps(event)
        if encoded != self.previous or ("error" in event and now - self.emitted_at >= 60):
            print(encoded, flush=True)
            self.previous, self.emitted_at = encoded, now


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--cleanup", action="store_true", help="requires --apply to remove owned rules")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--capture-vm", type=int, default=6095)
    scope.add_argument("--all-6xxx", action="store_true")
    parser.add_argument("--target-mac", required=True)
    parser.add_argument("--state-dir", default="/var/lib/netclaw-proxmox-mirror")
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    require(6000 <= args.capture_vm <= 6999, "scope_outside_6xxx")
    require(1 <= args.interval <= 60, "interval_bound")
    expected_mac = mac(args.target_mac)
    controller = Controller(Host(), args.state_dir, args.apply)
    stopped = False

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if args.cleanup:
        controller.cleanup()
        print(json.dumps({"mode": "cleanup" if args.apply else "dry-run-cleanup"}))
        return 0
    # Service restart always cleans previous session's owned rules before rebinding.
    if args.apply:
        controller.cleanup()
    guard = lambda: controller.host.snapshot(None if args.all_6xxx else args.capture_vm, expected_mac)
    events = EventLog()
    try:
        while not stopped:
            try:
                result = controller.reconcile(guard(), guard)
                events.emit(result)
            except (GuardError, OSError, ValueError, KeyError, IndexError) as exc:
                if args.apply:
                    controller.cleanup()
                events.emit({"error": str(exc) if isinstance(exc, GuardError) else type(exc).__name__})
                if args.once or not args.apply:
                    return 1
            if args.once or not args.apply:
                return 0
            until = time.monotonic() + args.interval
            while not stopped and time.monotonic() < until:
                time.sleep(min(0.2, max(0, until - time.monotonic())))
    finally:
        # --once apply intentionally leaves validated rules installed for pilot inspection.
        if args.apply and (stopped or not args.once):
            controller.cleanup()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (GuardError, OSError, ValueError, KeyError, IndexError) as error:
        print(json.dumps({"error": str(error) if isinstance(error, GuardError) else type(error).__name__}), file=sys.stderr)
        sys.exit(1)

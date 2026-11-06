#!/usr/bin/env python3
"""Bounded, passive QEMU TAP observer. Python standard library; Linux only."""
import argparse
import collections
import ctypes
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import struct
import time
import uuid

MAX_VMS = 256
MAX_NICS = 1024
MAX_CONNECTIONS = 256
MAX_SNAPSHOT = 512 * 1024
MAX_CONFIG = 128 * 1024
MAX_RATE = 2000
SAMPLE_DENOMINATOR = 64
INTERVAL = 15
LIMITATIONS = [
    "Passive sampled metadata only; never a safety, IDS or firewall verdict.",
    "TCP initial SYN only; UDP and ICMP randomly sampled 1/64 in kernel; established TCP omitted.",
    "No VLAN-tagged frames, fragments, IPv6 extensions, tunnels, non-IP or malformed packets parsed.",
    "Only configured QEMU TAP guest egress; no containers, guest-agent data or payload retention.",
    "Inventory/lifecycle checked every 15 seconds; changes entirely between checks may be invisible.",
    "Latest 256 distinct flows per interval; capture and rate limits can lose evidence.",
    "Rental allocation unresolved; observed IP addresses are claims, not ownership.",
    "No per-flow firewall verdict or firewall counter collection; disconnect history is incomplete.",
]


def read_limited(path, maximum=MAX_CONFIG):
    with open(path, "rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("input exceeds bound")
    return data


def integer_file(path):
    number = int(read_limited(path, 64))
    if number < 0:
        raise ValueError("negative counter")
    return number


def parse_config(data, vm_id):
    """Extract only identity/network fields. Never export names or arbitrary config."""
    vm_uuid = None
    nics = []
    for line in data.decode("utf-8", errors="strict").splitlines():
        # Proxmox snapshot sections contain historical NIC/UUID values.
        if line.startswith("["):
            break
        key, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.strip()
        if key == "smbios1":
            for token in value.split(","):
                if token.startswith("uuid="):
                    vm_uuid = str(uuid.UUID(token[5:]))
        if re.fullmatch(r"net[0-9]{1,3}", key):
            options = dict(token.split("=", 1) for token in value.split(",") if "=" in token)
            macs = [v for k, v in options.items() if k in
                    {"virtio", "e1000", "e1000e", "rtl8139", "vmxnet3", "ne2k_pci", "pcnet", "i82551", "i82557b", "i82559er"}]
            if len(macs) != 1 or not re.fullmatch(r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}", macs[0]):
                raise ValueError("unsupported NIC config")
            bridge = options.get("bridge", "")
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{0,32}", bridge):
                raise ValueError("invalid bridge")
            vlan = int(options["tag"]) if "tag" in options else None
            if vlan is not None and not 1 <= vlan <= 4094:
                raise ValueError("invalid VLAN")
            nics.append({"name": key, "tap": f"tap{vm_id}i{int(key[3:])}",
                         "mac": macs[0].lower(), "bridge": bridge, "vlan": vlan})
    return vm_uuid, nics


def inventory(config_dir=Path("/etc/pve/qemu-server"), sys_dir=Path("/sys/class/net"),
              run_dir=Path("/run/qemu-server"), proc_dir=Path("/proc"), vm_id_range=None):
    vms, bindings, errors = [], {}, []
    paths = sorted(config_dir.glob("*.conf"))
    if vm_id_range is not None:
        minimum, maximum = vm_id_range
        # Select by filename before counting or opening any configuration.
        paths = [path for path in paths if re.fullmatch(r"[1-9][0-9]*", path.stem)
                 and minimum <= int(path.stem) <= maximum]
    if not config_dir.is_dir() or len(paths) > MAX_VMS:
        return [], {}, ["Inventory unavailable or exceeds 256 VM limit."]
    total_nics = 0
    for path in paths:
        try:
            if not re.fullmatch(r"[1-9][0-9]*", path.stem):
                continue
            vm_id = int(path.stem)
            data = read_limited(path)
            identity = hashlib.sha256(data).hexdigest()
            vm_uuid, nics = parse_config(data, vm_id)
            total_nics += len(nics)
            if total_nics > MAX_NICS:
                raise ValueError("NIC count exceeds limit")
            power = "stopped"
            try:
                pid = integer_file(run_dir / f"{vm_id}.pid")
                comm = read_limited(proc_dir / str(pid) / "comm", 128).strip()
                power = "running" if comm in (b"kvm", b"qemu-system-x86", b"qemu-system-x86_64") else "unknown"
            except FileNotFoundError:
                pass
            except (OSError, ValueError):
                power = "unknown"
            for nic in nics:
                interface = sys_dir / nic["tap"]
                nic.update(ifindex=None, present=False, capture_state="absent", firewall_drop_packets=None)
                for field in ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets"):
                    nic[field] = None
                try:
                    index = integer_file(interface / "ifindex")
                    if index < 1:
                        raise ValueError("invalid ifindex")
                    nic.update(ifindex=index, present=True, capture_state="unsupported")
                    for field in ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets"):
                        nic[field] = integer_file(interface / "statistics" / field)
                    # Verify stable index across the counter read.
                    if integer_file(interface / "ifindex") != index:
                        raise ValueError("interface changed")
                    if power == "running":
                        bindings[index] = (vm_id, vm_uuid, nic["tap"], index, identity, pid)
                except FileNotFoundError:
                    nic.update(ifindex=None, present=False, capture_state="absent")
                except (OSError, ValueError):
                    nic["capture_state"] = "error"
                    errors.append("Interface inventory failed; capture disabled until next successful refresh.")
            vms.append({"vm_id": vm_id, "vm_uuid": vm_uuid, "power_state": power, "nics": nics})
        except (OSError, ValueError, UnicodeError):
            errors.append("VM config inventory failed or exceeded bounds; capture disabled.")
            # Include the VM without asserting identity or network details.
            if path.stem.isdecimal() and int(path.stem) > 0:
                vms.append({"vm_id": int(path.stem), "vm_uuid": None, "power_state": "unknown", "nics": []})
    return vms, bindings if not errors else {}, list(dict.fromkeys(errors))


class Filter:
    """Small forward-only classic BPF assembler. No promiscuity changes."""
    def __init__(self):
        self.code, self.labels, self.fixups = [], {}, []

    def label(self, name):
        self.labels[name] = len(self.code)

    def emit(self, op, k=0, jt=0, jf=0):
        self.code.append([op, jt, jf, k])

    def jump(self, name):
        self.fixups.append((len(self.code), name))
        self.emit(0x05)

    def require(self, op, k, target="reject"):
        self.emit(op, k, jt=1)
        self.jump(target)

    def build(self):
        for position, label in self.fixups:
            self.code[position][3] = self.labels[label] - position - 1
        if len(self.code) > 4096:
            raise ValueError("BPF bound exceeded")
        return [tuple(row) for row in self.code]


def capture_filter(indices):
    f = Filter()
    # Linux ancillary data: reject host TX (PACKET_OUTGOING), permit only live TAPs.
    f.emit(0x20, 0xFFFFF004)
    f.emit(0x15, 4, jf=1)
    f.jump("reject")
    f.emit(0x20, 0xFFFFF008)
    for index in sorted(indices):
        f.emit(0x15, index, jf=1)
        f.jump("ethernet")
    f.jump("reject")
    f.label("ethernet")
    f.emit(0x28, 12)
    f.emit(0x15, 0x0800, jf=1)
    f.jump("ipv4")
    f.emit(0x15, 0x86DD, jf=1)
    f.jump("ipv6")
    f.jump("reject")
    f.label("ipv4")
    f.emit(0x30, 14)
    f.emit(0x54, 0xF0)
    f.require(0x15, 0x40)
    f.emit(0x28, 20)  # MF or nonzero fragment offset
    f.emit(0x45, 0x3FFF, jf=1)
    f.jump("reject")
    f.emit(0xB1, 14)  # X = IPv4 IHL * 4
    f.emit(0x87)  # A = X
    f.require(0x35, 20)
    f.emit(0x30, 23)
    for proto, label in ((6, "tcp4"), (17, "udp4"), (1, "icmp4")):
        f.emit(0x15, proto, jf=1)
        f.jump(label)
    f.jump("reject")
    f.label("tcp4")
    f.emit(0x50, 27)  # flags at Ethernet + IHL + 13
    f.emit(0x54, 0x12)
    f.require(0x15, 0x02)
    f.emit(0x87)
    f.emit(0x04, 28)  # L2 + IP + TCP prefix through flags
    f.emit(0x16)
    for label, extra in (("udp4", 18), ("icmp4", 14)):
        f.label(label)
        f.emit(0x20, 0xFFFFF038)  # SKF_AD_RANDOM
        f.emit(0x54, SAMPLE_DENOMINATOR - 1)
        f.require(0x15, 0)
        f.emit(0x87)
        f.emit(0x04, extra)
        f.emit(0x16)
    f.label("ipv6")
    f.emit(0x30, 14)
    f.emit(0x54, 0xF0)
    f.require(0x15, 0x60)
    f.emit(0x30, 20)
    for proto, label in ((6, "tcp6"), (17, "udp6"), (58, "icmp6")):
        f.emit(0x15, proto, jf=1)
        f.jump(label)
    f.jump("reject")
    f.label("tcp6")
    f.emit(0x30, 67)
    f.emit(0x54, 0x12)
    f.require(0x15, 0x02)
    f.emit(0x06, 68)
    for label, size in (("udp6", 58), ("icmp6", 54)):
        f.label(label)
        f.emit(0x20, 0xFFFFF038)
        f.emit(0x54, SAMPLE_DENOMINATOR - 1)
        f.require(0x15, 0)
        f.emit(0x06, size)
    f.label("reject")
    f.emit(0x06, 0)
    return f.build()


class SockFilter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]


class SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(SockFilter))]


def attach_filter(sock, indices):
    code = capture_filter(indices)
    array = (SockFilter * len(code))(*(SockFilter(*row) for row in code))
    program = SockFprog(len(code), array)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.setsockopt(sock.fileno(), socket.SOL_SOCKET, 26,
                       ctypes.byref(program), ctypes.sizeof(program)) != 0:
        raise OSError(ctypes.get_errno(), "attach capture filter failed")


def open_capture(indices):
    # Protocol zero keeps capture disabled until the header-only filter is installed.
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW | socket.SOCK_NONBLOCK, 0)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024)
        attach_filter(sock, indices)
        class SockaddrLL(ctypes.Structure):
            _fields_ = [("family", ctypes.c_ushort), ("protocol", ctypes.c_ushort),
                        ("ifindex", ctypes.c_int), ("hatype", ctypes.c_ushort),
                        ("pkttype", ctypes.c_ubyte), ("halen", ctypes.c_ubyte),
                        ("addr", ctypes.c_ubyte * 8)]
        address = SockaddrLL(socket.AF_PACKET, socket.htons(3), 0, 0, 0, 0)
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.bind(sock.fileno(), ctypes.byref(address), ctypes.sizeof(address)) != 0:
            raise OSError(ctypes.get_errno(), "bind passive capture failed")
        return sock
    except BaseException:
        sock.close()
        raise


def parse_packet(data):
    if len(data) < 14:
        return None
    ethertype = struct.unpack_from("!H", data, 12)[0]
    if ethertype == 0x0800:
        if len(data) < 34 or data[14] >> 4 != 4:
            return None
        size = (data[14] & 15) * 4
        if size < 20 or len(data) < 14 + size or struct.unpack_from("!H", data, 20)[0] & 0x3FFF:
            return None
        proto, version, offset = data[23], 4, 14 + size
        src, dst = data[26:30], data[30:34]
        ip_length = struct.unpack_from("!H", data, 16)[0]
        if ip_length < size:
            return None
        transport_length = ip_length - size
    elif ethertype == 0x86DD:
        if len(data) < 54 or data[14] >> 4 != 6:
            return None
        proto, version, offset = data[20], 6, 54
        src, dst = data[22:38], data[38:54]
        transport_length = struct.unpack_from("!H", data, 18)[0]
    else:
        return None
    ports = (None, None)
    if proto == 6:
        if len(data) < offset + 14 or transport_length < 20 or data[offset + 13] & 0x12 != 2:
            return None
        if data[offset + 12] >> 4 < 5:
            return None
        protocol = "tcp"
        ports = struct.unpack_from("!HH", data, offset)
    elif proto == 17:
        if len(data) < offset + 4 or transport_length < 8:
            return None
        protocol = "udp"
        ports = struct.unpack_from("!HH", data, offset)
    elif (version, proto) in ((4, 1), (6, 58)) and transport_length >= 4:
        protocol = "icmp" if version == 4 else "icmpv6"
    else:
        return None
    return {"ip_version": version, "protocol": protocol,
            "src_ip": str(ipaddress.ip_address(src)), "dst_ip": str(ipaddress.ip_address(dst)),
            "src_port": ports[0], "dst_port": ports[1]}


class Window:
    def __init__(self):
        self.flows = collections.OrderedDict()
        self.discarded = 0
        self.rate_second, self.rate_count = None, 0

    def add(self, binding, packet, now, monotonic):
        second = int(monotonic)
        if second != self.rate_second:
            self.rate_second, self.rate_count = second, 0
        self.rate_count += 1
        if self.rate_count > MAX_RATE:
            self.discarded += 1
            return
        key = (binding, tuple(packet.values()))
        if key in self.flows:
            self.flows[key]["packet_count"] += 1
            self.flows[key]["observed_at"] = now
        else:
            if len(self.flows) >= MAX_CONNECTIONS:
                self.discarded += self.flows.popitem(last=False)[1]["packet_count"]
            self.flows[key] = {"vm_id": binding[0], "vm_uuid": binding[1], "tap": binding[2],
                               "ifindex": binding[3], "observed_at": now, "direction": "guest_egress",
                               **packet, "packet_count": 1, "evidence": "packet_metadata", "verdict": "unknown"}

    def reconcile(self, bindings):
        for key in list(self.flows):
            if bindings.get(key[0][3]) != key[0]:
                self.discarded += self.flows.pop(key)["packet_count"]
        return list(self.flows.values())


def write_snapshot(path, snapshot):
    data = json.dumps(snapshot, allow_nan=False, separators=(",", ":")).encode()
    if len(data) > MAX_SNAPSHOT:
        raise ValueError("snapshot exceeds 512 KiB; refusing publication")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run(args):
    started = time.time()
    boot_id = str(uuid.UUID(read_limited(Path("/proc/sys/kernel/random/boot_id"), 64).decode().strip()))
    stop = False

    def halt(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, halt)
    signal.signal(signal.SIGINT, halt)
    sock, bindings, window, sequence, drops = None, {}, Window(), 0, 0
    selector = selectors.DefaultSelector()
    deadline = time.monotonic()
    try:
        while not stop:
            if time.monotonic() >= deadline:
                vms, new_bindings, errors = inventory(vm_id_range=args.vm_id_range)
                if args.capture_vm:
                    new_bindings = {i: b for i, b in new_bindings.items() if b[0] in args.capture_vm}
                connections = window.reconcile(new_bindings)
                capture_ok = True
                # Reopen on identity changes: discard the old kernel queue as well.
                if sock is None or new_bindings != bindings:
                    if sock is not None:
                        try:
                            drops += struct.unpack("II", sock.getsockopt(263, 6, 8))[1]
                        except OSError:
                            errors.append("Capture drop statistics unavailable.")
                        selector.unregister(sock)
                        sock.close()
                        sock = None
                        errors.append("Capture queue reset after lifecycle change; queued metadata may be lost.")
                    try:
                        sock = open_capture(new_bindings)
                        selector.register(sock, selectors.EVENT_READ)
                    except OSError:
                        capture_ok = False
                        errors.append("Packet capture unavailable; inventory remains visible.")
                bindings = new_bindings
                if sock is not None:
                    try:
                        drops += struct.unpack("II", sock.getsockopt(263, 6, 8))[1]
                    except OSError:
                        errors.append("Capture drop statistics unavailable.")
                for vm in vms:
                    for nic in vm["nics"]:
                        if nic["ifindex"] in bindings:
                            nic["capture_state"] = "observing" if capture_ok else "error"
                sequence += 1
                snapshot = {"schema_version": 1, "sequence": sequence, "observed_at": time.time(),
                            "collector_started_at": started, "node": args.node, "boot_id": boot_id,
                            "collection": {"inventory_ok": not any("inventory" in e.lower() for e in errors),
                                           "packet_capture_ok": capture_ok and not any("inventory" in e.lower() for e in errors),
                                           "sampled": True, "capture_drops": drops,
                                           "events_discarded": window.discarded, "limitations": LIMITATIONS +
                                           ([f"Inventory and capture restricted to VM IDs {args.vm_id_range[0]}:{args.vm_id_range[1]} inclusive; no coverage outside this range."]
                                            if args.vm_id_range else []) +
                                           (["Capture further restricted to explicitly selected VM IDs within inventory scope."]
                                            if args.capture_vm else []) + errors},
                            "vms": vms, "connections": connections}
                write_snapshot(args.output, snapshot)
                discarded = window.discarded
                window = Window()
                window.discarded = discarded
                deadline = time.monotonic() + INTERVAL
            # Fixed-size batches allow shutdown and inventory refresh even at saturation.
            if sock is None:
                selector.select(timeout=min(1, max(0, deadline - time.monotonic())))
                continue
            for event, mask in selector.select(timeout=min(1, max(0, deadline - time.monotonic()))):
                for _ in range(256):
                    try:
                        data, address = sock.recvfrom(96)
                    except BlockingIOError:
                        break
                    except OSError:
                        selector.unregister(sock)
                        sock.close()
                        sock = None
                        deadline = time.monotonic()
                        break
                    # Kernel filter already restricts interfaces/direction. Recheck both.
                    if address[2] == 4:
                        window.discarded += 1
                        continue
                    try:
                        index = socket.if_nametoindex(address[0])
                    except OSError:
                        window.discarded += 1
                        continue
                    binding = bindings.get(index)
                    if binding is None or binding[2] != address[0]:
                        window.discarded += 1
                        continue
                    packet = parse_packet(data)
                    if packet is None:
                        window.discarded += 1
                        continue
                    window.add(binding, packet, time.time(), time.monotonic())
    finally:
        if sock is not None:
            sock.close()
        selector.close()


def parse_vm_id_range(value):
    if not re.fullmatch(r"[1-9][0-9]{0,8}:[1-9][0-9]{0,8}", value):
        raise argparse.ArgumentTypeError("VM ID range must be MIN:MAX with positive integers up to 999999999")
    minimum, maximum = map(int, value.split(":"))
    if minimum > maximum:
        raise argparse.ArgumentTypeError("VM ID range minimum must not exceed maximum")
    return minimum, maximum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", required=True)
    parser.add_argument("--vm-id-range", type=parse_vm_id_range,
                        help="Limit inventory and capture to inclusive MIN:MAX VM IDs (for example 6000:6999)")
    parser.add_argument("--capture-vm", type=int, action="append", default=[],
                        help="Limit capture to this VM ID (repeatable) within the inventory scope")
    parser.add_argument("--output", type=Path, default=Path("/var/lib/netclaw-proxmox-observer/snapshot.json"))
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", args.node):
        parser.error("invalid node")
    if any(vm_id <= 0 for vm_id in args.capture_vm):
        parser.error("capture VM IDs must be positive")
    run(args)


if __name__ == "__main__":
    main()

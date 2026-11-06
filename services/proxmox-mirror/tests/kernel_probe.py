#!/usr/bin/env python3
"""Explicit opt-in: sudo unshare --net python3 tests/kernel_probe.py.

Creates disposable interfaces only in a fresh network namespace. Refuses the
initial namespace and any namespace already containing a non-loopback link.
"""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

spec = importlib.util.spec_from_file_location("controller", Path(__file__).parents[1] / "controller.py")
c = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = c
spec.loader.exec_module(c)


def run(*args):
    return subprocess.check_output(args, stderr=subprocess.STDOUT)


def tc(*args):
    return json.loads(run("tc", "-j", *args))


def drain(sock):
    packets = []
    while True:
        try:
            packets.append(sock.recv(65535))
        except socket.timeout:
            return packets


def main():
    assert os.geteuid() == 0
    assert os.readlink("/proc/self/ns/net") != os.readlink("/proc/1/ns/net"), "must use unshare --net"
    assert [x["ifname"] for x in json.loads(run("ip", "-j", "link"))] == ["lo"]
    run("sysctl", "-q", "-w", "net.ipv6.conf.default.disable_ipv6=1")
    for left, right in (("guest", "tap6095i0"), ("tap113i1", "sensor"), ("wire", "eno1")):
        run("ip", "link", "add", left, "type", "veth", "peer", "name", right)
        for dev in (left, right):
            run("ip", "link", "set", dev, "up")
    run("tc", "qdisc", "add", "dev", "tap6095i0", "root", "handle", "1:", "htb")
    run("tc", "qdisc", "add", "dev", "tap6095i0", "ingress")
    run("tc", "filter", "add", "dev", "tap6095i0", "ingress", "pref", "50", "protocol", "all",
        "u32", "match", "u32", "0", "0", "action", "police", "rate", "8kbit", "burst", "1k", "conform-exceed", "drop/ok")
    before = tc("qdisc", "show", "dev", "tap6095i0")
    rule = c.Rule("tap6095i0", 1, 6095)
    cookie = "ab" * 16
    run(*rule.command("add", cookie))
    entries = tc("filter", "show", "dev", rule.dev, "ingress")
    assert any(c.correct(e, rule, cookie) for e in entries), entries
    assert tc("qdisc", "show", "dev", "tap6095i0") == before
    sink = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    sink.bind(("sensor", 0))
    sink.settimeout(0.15)
    send = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
    send.bind(("guest", 0))
    frame = bytes.fromhex("02000000609502000000000188b5") + b"mirror-policer-test" + b"x" * 256
    for _ in range(100):
        send.send(frame)
    received = [p for p in drain(sink) if b"mirror-policer-test" in p]
    assert len(received) == 100, len(received)
    stats = tc("-s", "filter", "show", "dev", rule.dev, "ingress")
    police = [e for e in stats if e.get("pref") == 50 and "actions" in e.get("options", {})][0]["options"]["actions"][0]
    assert police["stats"]["packets"] == 100 and police["stats"]["drops"] > 0, police
    run("tc", "qdisc", "add", "dev", "eno1", "clsact")
    external = c.Rule("eno1", 1, 6095, "02:00:00:00:60:95")
    run(*external.command("add", cookie))
    assert any(c.correct(e, external, cookie) for e in tc("filter", "show", "dev", "eno1", "ingress"))
    wire = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
    wire.bind(("wire", 0))
    for dest, vlan in (("020000006095", 301), ("020000006096", 301), ("020000006095", 302)):
        packet = bytes.fromhex(dest + "0200000000018100") + vlan.to_bytes(2, "big") + bytes.fromhex("88b5") + b"external-test" + b"x" * 64
        wire.send(packet)
    received = [p for p in drain(sink) if b"external-test" in p]
    assert len(received) == 1, received
    # Deletion of the sink must not poison original source policing, nor rebind
    # old mirred actions to a fresh device with the same name.
    run("ip", "link", "delete", "tap113i1")
    run("ip", "link", "add", "tap113i1", "type", "veth", "peer", "name", "sensor")
    for dev in ("tap113i1", "sensor"):
        run("ip", "link", "set", dev, "up")
    fresh = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    fresh.bind(("sensor", 0))
    fresh.settimeout(0.15)
    send.send(frame)
    assert not [p for p in drain(fresh) if b"mirror-policer-test" in p]
    stats = tc("-s", "filter", "show", "dev", rule.dev, "ingress")
    police = [e for e in stats if e.get("pref") == 50 and "actions" in e.get("options", {})][0]["options"]["actions"][0]
    assert police["stats"]["packets"] == 101, stats
    run(*rule.command("delete"))
    run(*external.command("delete"))
    assert tc("qdisc", "show", "dev", "tap6095i0") == before
    assert any(e.get("pref") == 50 for e in tc("filter", "show", "dev", rule.dev, "ingress"))
    print("PASS: mirror continuation, existing HTB/ingress preservation, VLAN/MAC selection, target recreation, exact cleanup")


if __name__ == "__main__":
    main()

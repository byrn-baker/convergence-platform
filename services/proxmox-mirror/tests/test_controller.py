import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("mirror_controller", Path(__file__).parents[1] / "controller.py")
c = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = c
spec.loader.exec_module(c)


def snapshot(vmid=6095):
    return {"target": [c.TARGET_UUID, "bc:24:11:60:30:01", 677, "config", [90, 100, "process"]],
            "sources": [{"vmid": vmid, "tap": f"tap{vmid}i0", "ifindex": 42,
                         "mac": "02:00:00:00:60:95", "binding": ["config", [91, 101, "process"]]}],
            "uplink": 2}


def entry(rule, cookie):
    return {"pref": 10, "chain": 0, "kind": rule.kind, "protocol": rule.protocol,
            "options": {"handle": hex(rule.handle), "skip_hw": True,
                        "keys": {"dst_mac": rule.mac, "vlan_id": 301} if rule.mac else {},
                        "actions": [{"kind": "mirred", "cookie": cookie, "mirred_action": "mirror",
                                     "direction": "egress", "to_dev": c.TARGET, "control_action": {"type": "pipe"}},
                                    {"kind": "gact", "control_action": {"type": "continue"}}]}}


class FakeHost:
    def __init__(self):
        self.indices = {"tap6095i0": 42, "eno1": 2}
        self.filters = {dev: [] for dev in self.indices}
        self.qdiscs = {"tap6095i0": [{"kind": "htb", "handle": "1:"}, {"kind": "ingress", "handle": "ffff:"}],
                       "eno1": []}
        self.commands = []

    def index(self, dev):
        return self.indices.get(dev)

    def tc(self, kind, _show, _dev, dev, *rest):
        return copy.deepcopy((self.filters if kind == "filter" else self.qdiscs)[dev])

    def run(self, args):
        self.commands.append(args)
        dev = args[args.index("dev") + 1]
        if args[1] == "qdisc":
            self.qdiscs[dev].append({"kind": "clsact"})
            return
        handle = int(args[args.index("handle") + 1], 0)
        if args[2] == "delete":
            self.filters[dev] = [e for e in self.filters[dev] if c.number(e.get("options", {}).get("handle", 0)) != handle]
        else:
            dest = args[args.index("dst_mac") + 1] if "dst_mac" in args else ""
            rule = c.Rule(dev, self.indices[dev], handle - 0x4E430000, dest)
            self.filters[dev].append(entry(rule, args[args.index("cookie") + 1]))


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = FakeHost()
        self.controller = c.Controller(self.host, Path(self.temp.name) / "state", True)
        self.snap = snapshot()

    def tearDown(self):
        os.close(self.controller.lock)
        self.temp.cleanup()

    def reconcile(self):
        return self.controller.reconcile(self.snap, lambda: self.snap)

    def test_preserves_root_ingress_policer_and_idempotent(self):
        police = {"pref": 50, "chain": 0, "kind": "u32", "options": {"handle": "0x800"}}
        self.host.filters["tap6095i0"].append(police)
        original = copy.deepcopy(self.host.qdiscs["tap6095i0"])
        self.reconcile()
        first = len(self.host.commands)
        self.reconcile()
        self.assertEqual(first, len(self.host.commands))
        self.assertEqual(original, self.host.qdiscs["tap6095i0"])
        self.controller.cleanup()
        self.assertEqual([police], self.host.filters["tap6095i0"])
        self.assertFalse(any("replace" in cmd or cmd[:3] == ["tc", "qdisc", "delete"] for cmd in self.host.commands))

    def test_default_dry_run_does_not_create_state_or_mutate(self):
        path = Path(self.temp.name) / "dry"
        dry = c.Controller(self.host, path)
        self.assertEqual(dry.reconcile(self.snap, lambda: self.snap)["mode"], "dry-run")
        self.assertFalse(path.exists())
        self.assertFalse(self.host.commands)

    def test_collision_aborts_before_any_mutation(self):
        collision = entry(c.desired(self.snap)[1], "00" * 16)
        self.host.filters["eno1"].append(collision)
        with self.assertRaisesRegex(c.GuardError, "filter_collision"):
            self.reconcile()
        self.assertFalse(self.host.commands)
        self.assertEqual([collision], self.host.filters["eno1"])

    def test_state_intent_cleanup_after_add_failure(self):
        original = self.host.run

        def fail_after_add(args):
            original(args)
            if args[:3] == ["tc", "filter", "add"]:
                raise c.GuardError("simulated_crash")

        with patch.object(self.host, "run", side_effect=fail_after_add):
            with self.assertRaises(c.GuardError):
                self.reconcile()
        self.assertEqual(len(self.controller.state["rules"]), 1)
        self.controller.cleanup()
        self.assertFalse(self.host.filters["tap6095i0"])
        self.assertEqual(os.stat(self.controller.path).st_mode & 0o777, 0o600)

    def test_target_restart_and_same_index_process_change_rebind(self):
        self.reconcile()
        self.host.commands.clear()
        self.snap["target"][-1][1] += 1
        self.reconcile()
        operations = [x[2] for x in self.host.commands if x[1] == "filter"]
        self.assertEqual(operations, ["delete", "delete", "add", "add"])

    def test_scope_empty_removes_all_owned_rules(self):
        self.reconcile()
        self.snap["sources"] = []
        self.reconcile()
        self.assertFalse(self.controller.state["rules"])
        self.assertFalse(any(self.host.filters.values()))

    def test_reused_source_ifindex_does_not_delete_replacement_rules(self):
        self.reconcile()
        self.host.indices["tap6095i0"] = 88
        replacement = entry(c.Rule("tap6095i0", 88, 6095), "ff" * 16)
        self.host.filters["tap6095i0"] = [replacement]
        self.controller.cleanup()
        self.assertEqual(self.host.filters["tap6095i0"], [replacement])

    def test_cleanup_collision_preserves_unowned_rule_and_intent(self):
        self.reconcile()
        self.host.filters["tap6095i0"][0]["options"]["actions"][0]["cookie"] = "ff" * 16
        with self.assertRaisesRegex(c.GuardError, "cleanup_incomplete"):
            self.controller.cleanup()
        self.assertEqual(len(self.controller.state["rules"]), 1)
        self.assertEqual(len(self.host.filters["tap6095i0"]), 1)

    def test_changed_guard_prevents_install(self):
        changed = copy.deepcopy(self.snap)
        changed["target"][2] = 678
        with self.assertRaisesRegex(c.GuardError, "lifecycle_changed"):
            self.controller.reconcile(self.snap, lambda: changed)
        self.assertFalse(self.host.commands)

    def test_policer_bypass_drift_rejected(self):
        self.reconcile()
        self.host.filters["tap6095i0"][0]["options"]["actions"][1]["control_action"]["type"] = "pass"
        with self.assertRaisesRegex(c.GuardError, "installed_rule_drift"):
            self.reconcile()

    def test_no_destination_tap_egress_or_uplink_transmit_rule(self):
        commands = [r.command("add", "ab" * 16) for r in c.desired(self.snap)]
        self.assertEqual(len(commands), 2)
        for command in commands:
            self.assertEqual(command[5], "ingress")
            self.assertEqual(command[-3:], ["action", "gact", "continue"])
        self.assertIn("dst_mac", commands[1])
        self.assertIn("vlan_id", commands[1])
        self.assertNotIn(c.TARGET, [r.dev for r in c.desired(self.snap)])


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.net = self.base / "net"
        self.conf = self.base / "conf"
        self.net.mkdir()
        self.conf.mkdir()
        for name, index, master in (("vmbr0", 1, None), ("vmbr3999", 3, None), ("eno1", 2, "vmbr0"),
                                    ("tap113i1", 677, "vmbr3999"), ("tap6095i0", 42, "vmbr0")):
            path = self.net / name
            path.mkdir()
            (path / "ifindex").write_text(str(index))
            if name.startswith("tap"):
                (path / "tun_flags").write_text("0x1002")
            if master:
                (path / "master").symlink_to(self.net / master)
        (self.net / "vmbr3999" / "brif").mkdir()
        (self.net / "vmbr3999" / "brif" / "tap113i1").touch()
        self.target = f"smbios1: uuid={c.TARGET_UUID}\nnet0: virtio=02:00:00:00:00:01,bridge=vmbr0,tag=3\nnet1: virtio=BC:24:11:60:30:01,bridge=vmbr3999\n"
        (self.conf / "113.conf").write_text(self.target)
        (self.conf / "6095.conf").write_text("net0: virtio=02:00:00:00:60:95,bridge=vmbr0,tag=301\n")
        self.host = c.Host(self.conf, self.net)
        self.proc = patch.object(c, "process_binding", return_value=[99, 100, "hash"])
        self.proc.start()
        self.cmd = patch.object(self.host, "run", return_value=b'[{"addr_info": [], "flags": ["UP", "LOWER_UP"]}]')
        self.cmd.start()

    def tearDown(self):
        self.proc.stop()
        self.cmd.stop()
        self.temp.cleanup()

    def snap(self, selected=6095):
        return self.host.snapshot(selected, "bc:24:11:60:30:01")

    def test_only_selected_ids_and_active_config(self):
        (self.conf / "7999.conf").write_bytes(b"\xff")
        (self.conf / "6001.conf").write_bytes(b"\xff")
        result = self.snap()
        self.assertEqual([s["vmid"] for s in result["sources"]], [6095])
        self.assertEqual(c.config(b"net0: current\n[PENDING]\nnet0: future\n"), {"net0": "current"})

    def test_target_uuid_and_mac_fail_closed(self):
        for altered in (self.target.replace(c.TARGET_UUID, "other"), self.target.replace("60:30:01", "60:30:02")):
            (self.conf / "113.conf").write_text(altered)
            with self.assertRaises(c.GuardError):
                self.snap()

    def test_target_or_bridge_down_fail_closed(self):
        for dev in ("vmbr3999", "tap113i1"):
            for flags in (["UP"], ["LOWER_UP"], []):
                def addresses(args):
                    return json.dumps([{"addr_info": [], "flags": flags if args[-1] == dev else ["UP", "LOWER_UP"]}]).encode()
                with patch.object(self.host, "run", side_effect=addresses):
                    with self.assertRaisesRegex(c.GuardError, "capture_link_down"):
                        self.snap()

    def test_missing_target_and_extra_bridge_port_fail_closed(self):
        (self.net / "vmbr3999" / "brif" / "uplink").touch()
        with self.assertRaisesRegex(c.GuardError, "capture_bridge_not_isolated"):
            self.snap()
        (self.net / "vmbr3999" / "brif" / "uplink").unlink()
        (self.net / "tap113i1" / "ifindex").unlink()
        with self.assertRaisesRegex(c.GuardError, "target_unavailable"):
            self.snap()

    def test_source_vlan_and_mac_sanity(self):
        for mac, vlan in (("ff:ff:ff:ff:ff:ff", "301"), ("02:00:00:00:60:95", "302")):
            (self.conf / "6095.conf").write_text(f"net0: virtio={mac},bridge=vmbr0,tag={vlan}\n")
            with self.assertRaises(c.GuardError):
                self.snap()

    def test_duplicate_mac_fail_closed(self):
        (self.conf / "6096.conf").write_bytes((self.conf / "6095.conf").read_bytes())
        with self.assertRaisesRegex(c.GuardError, "duplicate_mac"):
            self.snap(None)

    def test_stopped_source_excluded(self):
        with patch.object(c, "process_binding", side_effect=lambda vmid: [99, 100, "hash"] if vmid == 113 else None):
            self.assertEqual(self.snap()["sources"], [])


class EventLogTests(unittest.TestCase):
    def test_success_changes_errors_throttled_and_recovery_reported(self):
        events = c.EventLog()
        healthy = {"mode": "apply", "source_count": 1, "rule_count": 2}
        error = {"error": "target_unavailable"}
        with patch("builtins.print") as output, patch.object(c.time, "monotonic") as clock:
            for when in (0, 2, 100):
                clock.return_value = when
                events.emit(healthy)
            self.assertEqual(output.call_count, 1)
            clock.return_value = 101
            events.emit({**healthy, "source_count": 6, "rule_count": 12})
            self.assertEqual(output.call_count, 2)
            for when in (102, 104, 161, 162):
                clock.return_value = when
                events.emit(error)
            self.assertEqual(output.call_count, 4)  # Initial error, then 60 seconds later.
            clock.return_value = 163
            events.emit({"error": "capture_link_down"})
            events.emit(healthy)
            self.assertEqual(output.call_count, 6)


if __name__ == "__main__":
    unittest.main()

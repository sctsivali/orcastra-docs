"""LXD cluster (MicroCloud) support in the Full installer: placement on the member the
installer runs on, per-member bridge creation, existing pools only, the member guard in
preflight, the plan check for unreachable members and the KVM-aware instance type default.

Run:  cd installer && python3 -m unittest discover -s tests -v
"""
import json
import unittest

from _fakes import IPS, FakeCtx, FakeLxd  # noqa: F401
from orcastra_core.errors import ConfigError
from orcastra_full_install import topology as T, wizard_lib as W

MEMBERS = (("m1", "Online"), ("m2", "Online"), ("m3", "Online"))
BRIDGE = ("ipv4.address=10.77.0.1/24", "ipv4.nat=true", "ipv6.address=none", "user.orcastra=managed")


class ClusterSupport(unittest.TestCase):
    def _network_ctx(self, member, network=None):
        return FakeCtx(member, FakeLxd(MEMBERS, network=network),
                    values={"NETWORK": "orcabr0", "NETWORK_SUBNET": "10.77.0.1/24"})

    def test_standalone_bridge_is_one_plain_create(self):
        from orcastra_full_install.phases import p04_lxd
        ctx = self._network_ctx(None)
        p04_lxd._network(ctx)
        self.assertEqual(ctx.lxd.calls, [("network", "create", "orcabr0") + BRIDGE])
        self.assertEqual(ctx.state.artifacts["created"]["network"], ["orcabr0"])

    def test_cluster_bridge_is_defined_on_every_member_then_created(self):
        from orcastra_full_install.phases import p04_lxd
        ctx = self._network_ctx("m3")
        p04_lxd._network(ctx)
        self.assertEqual(ctx.lxd.calls, [
            ("network", "create", "orcabr0", "--target", "m1"),
            ("network", "create", "orcabr0", "--target", "m2"),
            ("network", "create", "orcabr0", "--target", "m3"),
            ("network", "create", "orcabr0") + BRIDGE])
        self.assertEqual(ctx.state.artifacts["created"]["network"], ["orcabr0"])

    def test_cluster_bridge_resumes_where_it_stopped(self):
        from orcastra_full_install.phases import p04_lxd
        ctx = self._network_ctx("m3", {"status": "Pending", "locations": ["m1"]})
        p04_lxd._network(ctx)
        self.assertEqual([c[-1] for c in ctx.lxd.calls[:2]], ["m2", "m3"])
        self.assertEqual(ctx.lxd.calls[2], ("network", "create", "orcabr0") + BRIDGE)

    def test_a_created_bridge_is_left_alone(self):
        from orcastra_full_install.phases import p04_lxd
        for member in (None, "m3"):
            ctx = self._network_ctx(member, {"status": "Created", "locations": ["m1", "m2", "m3"]})
            p04_lxd._network(ctx)
            self.assertEqual(ctx.lxd.calls, [])

    def test_cluster_instances_are_pinned_and_stop_on_evacuation(self):
        from orcastra_full_install.phases import p05_instances
        values = {"IMAGE": "ubuntu:24.04"}
        standalone, clustered = FakeCtx(None, values=values), FakeCtx("m3", values=values)
        p05_instances._create(standalone, "vault")
        p05_instances._create(clustered, "vault")
        plain, pinned = standalone.lxd.calls[0], clustered.lxd.calls[0]
        self.assertNotIn("--target", plain)
        self.assertFalse(any("cluster.evacuate" in a for a in plain))
        i = pinned.index("--target")
        self.assertEqual(pinned[i:i + 4], ("--target", "m3", "-c", "cluster.evacuate=stop"))
        self.assertEqual(pinned[:i] + pinned[i + 4:], plain)
        self.assertEqual(standalone.lxd.calls[1:], clustered.lxd.calls[1:])

    def _preflight(self, member, members, instances):
        from unittest import mock
        from orcastra_core.errors import PreflightError
        from orcastra_full_install.phases import p01_preflight
        ctx = FakeCtx(member, FakeLxd(members, instances=instances))
        with mock.patch.object(p01_preflight.shutil, "which", return_value="/usr/bin/x"), \
                mock.patch.object(p01_preflight.hostnet, "has_kvm", return_value=True), \
                mock.patch.object(p01_preflight.kernelcheck, "check_host", return_value=("PASS", "ok")):
            try:
                p01_preflight.run(ctx)
                return ctx, None
            except PreflightError as exc:
                return ctx, exc.message

    def test_preflight_names_the_member_the_instances_go_to(self):
        ctx, err = self._preflight("m3", MEMBERS, [])
        self.assertIsNone(err)
        self.assertTrue(any(m.startswith("LXD cluster") and "member m3 (Online) of 3" in m
                            for lvl, m in ctx.log.lines if lvl == "ok"), ctx.log.lines)

    def test_preflight_refuses_an_evacuated_member(self):
        _, err = self._preflight("m3", (("m1", "Online"), ("m2", "Online"), ("m3", "Evacuated")), [])
        self.assertIn("LXD cluster", err)
        self.assertIn("lxc cluster restore m3", err)

    def test_preflight_refuses_to_resume_from_another_member(self):
        ours = [{"project": "orcastra", "name": T.name(r), "location": "m3",
                 "config": {T.TAG_KEY: r}} for r in T.ROLES]
        _, err = self._preflight("m1", MEMBERS, ours)
        self.assertIn("on member m3", err)
        self.assertIn("Run the installer on m3", err)
        _, err = self._preflight("m3", MEMBERS, ours)
        self.assertIsNone(err)
        standalone = [dict(i, location="none") for i in ours]
        _, err = self._preflight(None, (), standalone)
        self.assertIsNone(err)

    def _pool(self, member, answer):
        from orcastra_full_install.phases import p02_wizard
        pools = [{"name": "local", "driver": "zfs"}, {"name": "remote", "driver": "ceph"},
                 {"name": "remote-fs", "driver": "cephfs"}]
        ctx = FakeCtx(member, FakeLxd(pools=pools), values={"STORAGE_POOL": answer},
                   answered={"STORAGE_POOL"}, facts={"lxd_storage_drivers": ["zfs"]})
        try:
            p02_wizard._pool(ctx)
            return ctx, None
        except ConfigError as exc:
            return ctx, exc.message

    def test_cluster_pool_must_exist_and_hold_instance_disks(self):
        for answer in ("local", "remote"):
            ctx, err = self._pool("m3", answer)
            self.assertIsNone(err)
            self.assertFalse(ctx.facts["pool_create"])
        _, err = self._pool("m3", "orcapool")
        self.assertIn("existing storage pool: local, remote", err)
        _, err = self._pool("m3", "remote-fs")
        self.assertIn("CephFS", err)
        _, err = self._pool(None, "remote-fs")
        self.assertIn("CephFS", err)

    def test_standalone_pool_may_still_be_created(self):
        ctx, err = self._pool(None, "orcapool")
        self.assertIsNone(err)
        self.assertTrue(ctx.facts["pool_create"])

    def test_instance_type_defaults_to_what_the_host_can_run(self):
        from orcastra_full_install.phases import p02_wizard
        ctx = FakeCtx(values={"INSTANCE_TYPE": "vm"}, interactive=True, facts={"kvm": False})
        p02_wizard._instance_type(ctx)
        self.assertEqual(ctx.prompt.defaults["instance-type"], "container")
        self.assertEqual(ctx.values["INSTANCE_TYPE"], "container")
        ctx = FakeCtx(values={"INSTANCE_TYPE": "vm"}, interactive=True, facts={"kvm": True})
        p02_wizard._instance_type(ctx)
        self.assertEqual(ctx.prompt.defaults["instance-type"], "vm")

    def test_unattended_vm_without_kvm_still_fails_loudly(self):
        from orcastra_full_install.phases import p02_wizard
        ctx = FakeCtx(values={"INSTANCE_TYPE": "vm"}, interactive=False, facts={"kvm": False})
        with self.assertRaises(ConfigError) as cm:
            p02_wizard._instance_type(ctx)
        self.assertIn("/dev/kvm", cm.exception.message)

    def _wizard_network(self, status, ours):
        from orcastra_core.proc import Result
        from orcastra_full_install.phases import p02_wizard

        class P:
            def run(self, argv, **kw):
                routes = [{"dst": "default", "dev": "eth0"},
                          {"dst": "10.77.0.0/24", "dev": "orcabr0"}]
                return Result(0, json.dumps(routes), "", argv)

        nets = [{"name": "orcabr0", "type": "bridge", "managed": True, "status": status,
                 "config": {"ipv4.address": "10.77.0.1/24"} if status == "Errored" else {}}]
        ctx = FakeCtx("m3", FakeLxd(MEMBERS, networks=nets),
                   values={"NETWORK": "orcabr0", "NETWORK_SUBNET": "10.77.0.1/24"},
                   answered={"NETWORK", "NETWORK_SUBNET"},
                   artifacts={"created": {"network": ["orcabr0"]}} if ours else None)
        ctx.proc = P()
        p02_wizard._network(ctx)
        return ctx

    def test_wizard_resumes_its_own_unfinished_cluster_bridge(self):
        for status in ("Pending", "Errored"):
            ctx = self._wizard_network(status, ours=True)
            self.assertTrue(ctx.facts["network_create"])
            self.assertEqual(ctx.facts["bridge_gateway"], "10.77.0.1")

    def test_wizard_refuses_someone_elses_unfinished_bridge(self):
        for status in ("Pending", "Errored"):
            with self.assertRaises(ConfigError):
                self._wizard_network(status, ours=False)

    def _plan(self, member, members, network_create):
        from unittest import mock
        from orcastra_core.errors import PreflightError
        from orcastra_full_install import plancheck, registry
        lxd = FakeLxd(members)
        lxd.host_resources = lambda: {"cpu": {"total": 16}}
        lxd.pool_resources = lambda name: {}
        lxd.image_reachable = lambda image: True
        ctx = FakeCtx(member, lxd, values={"STORAGE_POOL": "local", "NETWORK": "orcabr0",
                                        "CMP_VERSION": "1.0.0-RC4-hotfix3"},
                   facts={"kvm": True, "network_create": network_create})
        with mock.patch.object(plancheck.hostnet, "mem_available_gib", return_value=64.0), \
                mock.patch.object(plancheck.hostnet, "mem_total_gib", return_value=64.0), \
                mock.patch.object(registry, "image_exists", return_value=True):
            try:
                plancheck.check_plan(ctx)
                return None
            except PreflightError as exc:
                return exc.message

    def test_plan_needs_every_member_for_a_new_cluster_bridge(self):
        degraded = (("m1", "Offline"), ("m2", "Evacuated"), ("m3", "Online"))
        err = self._plan("m3", degraded, network_create=True)
        self.assertIn("m1 (Offline)", err)
        self.assertNotIn("m2", err)
        self.assertIsNone(self._plan("m3", degraded, network_create=False))
        self.assertIsNone(self._plan("m3", MEMBERS, network_create=True))
        self.assertIsNone(self._plan(None, (), network_create=True))

    def test_subnet_conflict_ignores_only_our_own_bridge(self):
        from orcastra_core.proc import Result

        class P:
            def run(self, argv, **kw):
                return Result(0, json.dumps([{"dst": "10.77.0.0/24", "dev": "orcabr0"},
                                             {"dst": "10.78.0.0/24", "dev": "eth1"}]), "", argv)

        nets = [{"name": "orcabr0", "config": {"ipv4.address": "10.77.0.1/24"}}]
        self.assertIsNone(W.subnet_conflict(P(), nets, "10.77.0.1/24", own="orcabr0"))
        self.assertIn("already in use", W.subnet_conflict(P(), nets, "10.77.0.1/24"))
        self.assertIn("10.78.0.0/24", W.subnet_conflict(P(), nets, "10.78.0.1/24", own="orcabr0"))


if __name__ == "__main__":
    unittest.main()

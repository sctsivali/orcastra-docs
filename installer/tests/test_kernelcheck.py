"""Unit tests for the host kernel check (CVE-2026-99999) and its preflight row. No real host is
read: uname values, os-release and the package changelogs are injected.

Run:  cd installer && python3 -m unittest discover -s tests -v
"""
import gzip
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

INSTALLER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, INSTALLER)

from orcastra_full_install import kernelcheck as K  # noqa: E402
from orcastra_full_install.phases import p01_preflight  # noqa: E402

UBUNTU = {"ID": "ubuntu", "ID_LIKE": "debian", "VERSION_ID": "22.04"}
DEBIAN = {"ID": "debian", "VERSION_ID": "12"}
RHEL = {"ID": "rocky", "ID_LIKE": "rhel centos fedora", "VERSION_ID": "9.6"}
OTHER = {"ID": "arch"}
UBUNTU_V = "#195-Ubuntu SMP Fri Oct 3 12:00:00 UTC 2026"


class KernelAdvisory(unittest.TestCase):
    def setUp(self):
        self.doc = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.doc)

    def changelog(self, release, text, pkg="linux-modules"):
        d = os.path.join(self.doc, f"{pkg}-{release}")
        os.makedirs(d, exist_ok=True)
        with gzip.open(os.path.join(d, "changelog.Debian.gz"), "wt", encoding="utf-8") as fh:
            fh.write(text)

    def assess(self, release, version="", osr=UBUNTU):
        return K.assess(release, version, osr, doc_root=self.doc)

    def test_changelog_naming_the_cve_passes(self):
        self.changelog("5.15.0-200-generic", "linux (5.15.0-200.210) jammy\n  * CVE-2026-99999\n")
        status, detail = self.assess("5.15.0-200-generic", UBUNTU_V)
        self.assertEqual(status, K.PASS)
        self.assertIn("changelog lists the fix", detail)

    def test_ubuntu_reads_the_modules_changelog_not_the_signed_one(self):
        # the real Ubuntu layout: linux-image-<r> is linux-signed and only names the version
        self.changelog("6.8.0-150-generic", "linux-signed (6.8.0-150.150) noble\n", pkg="linux-image")
        self.changelog("6.8.0-150-generic", "linux (6.8.0-150.150) noble\n  * CVE-2026-99999\n")
        self.assertEqual(self.assess("6.8.0-150-generic", UBUNTU_V)[0], K.PASS)

    def test_debian_style_image_changelog_also_counts(self):
        self.changelog("6.1.0-41-amd64", "  * CVE-2026-99999\n", pkg="linux-image")
        self.assertEqual(self.assess("6.1.0-41-amd64", "", DEBIAN)[0], K.PASS)

    def test_meta_packages_are_not_kernels(self):
        self.changelog("virtual", "  * CVE-2026-99999\n", pkg="linux-image")
        self.changelog("extra-6.8.0-150-generic", "  * CVE-2026-99999\n")
        self.assertEqual(K.installed_releases(self.doc), [])
        self.assertNotIn("reboot", self.assess("6.8.0-117-generic", UBUNTU_V)[1])

    def test_missing_changelog_warns_with_the_date(self):
        status, detail = self.assess("5.15.0-185-generic", UBUNTU_V)
        self.assertEqual(status, K.WARN)
        self.assertIn("no fix found for 5.15.0-185-generic", detail)
        self.assertIn(K.DATA_AS_OF, detail)

    def test_changelog_without_the_cve_warns(self):
        self.changelog("5.15.0-185-generic", "linux (5.15.0-185.195) jammy\n  * CVE-2026-11111\n")
        self.assertEqual(self.assess("5.15.0-185-generic", UBUNTU_V)[0], K.WARN)

    def test_fixed_kernel_installed_but_not_booted_says_reboot(self):
        self.changelog("5.15.0-185-generic", "old\n")
        self.changelog("5.15.0-200-generic", "  * CVE-2026-99999\n")
        self.changelog("5.15.0-201-generic", "  * CVE-2026-99999\n")
        status, detail = self.assess("5.15.0-185-generic", UBUNTU_V)
        self.assertEqual(status, K.WARN)
        self.assertIn("reboot into 5.15.0-201-generic", detail)

    def test_installed_kernels_without_the_fix_do_not_suggest_a_reboot(self):
        self.changelog("5.15.0-198-generic", "  * CVE-2026-11111\n")
        detail = self.assess("5.15.0-185-generic", UBUNTU_V)[1]
        self.assertNotIn("reboot", detail)

    def test_debian_package_version_decides(self):
        v = "#1 SMP PREEMPT_DYNAMIC Debian 6.1.190-1 (2026-10-10)"
        self.assertEqual(self.assess("6.1.0-41-amd64", v, DEBIAN)[0], K.PASS)
        v_old = "#1 SMP PREEMPT_DYNAMIC Debian 6.1.176-1 (2026-08-01)"
        status, detail = self.assess("6.1.0-37-amd64", v_old, DEBIAN)
        self.assertEqual(status, K.WARN)
        self.assertIn("below 6.1.190", detail)

    def test_upstream_sublevels(self):
        self.assertEqual(self.assess("6.12.113", osr=OTHER)[0], K.PASS)
        self.assertEqual(self.assess("6.12.112", osr=OTHER)[0], K.WARN)
        self.assertEqual(self.assess("5.10.272-1-lts", osr=OTHER)[0], K.PASS)
        self.assertEqual(self.assess("6.18.56-zabbly+", osr=OTHER)[0], K.WARN)

    def test_mainline_release_candidates(self):
        self.assertEqual(self.assess("7.3.0-rc7", osr=OTHER)[0], K.PASS)
        self.assertEqual(self.assess("7.3.0-rc6", osr=OTHER)[0], K.WARN)
        self.assertEqual(self.assess("7.3.0", osr=OTHER)[0], K.PASS)
        self.assertEqual(self.assess("7.4.1", osr=OTHER)[0], K.PASS)

    def test_end_of_life_branch_is_exposed(self):
        status, detail = self.assess("6.8.12", osr=OTHER)
        self.assertEqual(status, K.WARN)
        self.assertIn("received no upstream fix", detail)

    def test_predates_the_bug(self):
        self.assertEqual(self.assess("2.6.20", osr=OTHER)[0], K.PASS)

    def test_rhel_is_unverified(self):
        status, detail = self.assess("5.14.0-570.el9_6.x86_64", osr=RHEL)
        self.assertEqual(status, K.WARN)
        self.assertIn("could not verify", detail)
        self.assertIn("rpm", K.check_command(RHEL))

    def test_ubuntu_mainline_build_uses_the_upstream_sublevel(self):
        self.assertEqual(self.assess("6.12.113-061213-generic", UBUNTU_V)[0], K.PASS)

    def test_garbage_is_unverified(self):
        for raw in ("", "garbage", "linux"):
            status, detail = self.assess(raw, osr=OTHER)
            self.assertEqual(status, K.WARN)
            self.assertIn("could not verify", detail)

    def test_never_fails(self):
        samples = [("5.15.0-185-generic", UBUNTU_V, UBUNTU), ("6.1.0-37-amd64", "", DEBIAN),
                   ("5.14.0-570.el9_6.x86_64", "", RHEL), ("4.19.300", "", OTHER),
                   ("6.12.113", "", OTHER), ("???", "", {}), ("6.8.0-138-generic", "", {})]
        statuses = {self.assess(r, v, o)[0] for r, v, o in samples}
        self.assertTrue(statuses <= {K.PASS, K.WARN}, statuses)

    def test_unreadable_changelog_is_not_evidence(self):
        d = os.path.join(self.doc, "linux-image-5.15.0-185-generic")
        os.makedirs(d)
        with open(os.path.join(d, "changelog.Debian.gz"), "wb") as fh:
            fh.write(b"not gzip CVE-2026-99999")
        self.assertEqual(self.assess("5.15.0-185-generic", UBUNTU_V)[0], K.WARN)

    def test_vm_host_detail_names_the_reachable_path(self):
        with mock.patch.object(K, "assess", return_value=(K.WARN, "exposed")):
            self.assertIn("VM guests", K.check_host(UBUNTU, vm_capable=True)[1])
            self.assertNotIn("VM guests", K.check_host(UBUNTU, vm_capable=False)[1])
        with mock.patch.object(K, "assess", return_value=(K.PASS, "ok")):
            self.assertEqual(K.check_host(UBUNTU, vm_capable=True)[1], "ok")


class _Log:
    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        return lambda msg: self.lines.append((level, msg))


class _LXD:
    lxc = "lxc"

    def server(self):
        return {"environment": {"server_version": "5.21.5", "firewall": "nftables",
                                "storage_supported_drivers": [{"Name": "zfs"}]}}

    def instances(self, all_projects=False):
        return []


class _Ctx:
    def __init__(self):
        self.lxd = _LXD()
        self.log = _Log()
        self.facts = {}
        self.project = "orcastra"
        self.cluster_member = None


class PreflightKernelRow(unittest.TestCase):
    def run_preflight(self, result):
        ctx = _Ctx()
        with mock.patch.object(p01_preflight.shutil, "which", return_value="/usr/bin/x"), \
                mock.patch.object(p01_preflight.hostnet, "has_kvm", return_value=True), \
                mock.patch.object(p01_preflight.kernelcheck, "check_host", **result):
            p01_preflight.run(ctx)
        return ctx

    def test_an_exposed_kernel_warns_and_does_not_block(self):
        ctx = self.run_preflight({"return_value": (K.WARN, "exposed or unverified")})
        warns = [m for lvl, m in ctx.log.lines if lvl == "warn"]
        self.assertTrue(any("Kernel CVE-2026-99999" in m for m in warns), ctx.log.lines)
        self.assertTrue(any(K.DOCS in m for lvl, m in ctx.log.lines if lvl == "detail"))
        self.assertEqual(ctx.facts["kernel_advisory"]["status"], K.WARN)

    def test_a_patched_kernel_passes_without_the_link(self):
        ctx = self.run_preflight({"return_value": (K.PASS, "changelog lists the fix")})
        oks = [m for lvl, m in ctx.log.lines if lvl == "ok"]
        self.assertTrue(any("Kernel CVE-2026-99999" in m for m in oks))
        self.assertFalse(any(K.DOCS in m for _, m in ctx.log.lines))

    def test_a_crashing_check_still_warns(self):
        ctx = self.run_preflight({"side_effect": OSError("boom")})
        self.assertEqual(ctx.facts["kernel_advisory"]["status"], K.WARN)
        self.assertIn("could not verify", ctx.facts["kernel_advisory"]["detail"])

    def test_the_other_rows_are_unchanged(self):
        ctx = self.run_preflight({"return_value": (K.WARN, "x")})
        rows = [msg.split("  ")[0].strip() for level, msg in ctx.log.lines
                if level in ("ok", "warn", "error")]
        for expected in ("Operating system", "Python", "tool: ip", "LXD version",
                         "LXD firewall driver", "Existing deployment", "KVM (/dev/kvm)",
                         "Kernel CVE-2026-99999"):
            self.assertIn(expected, rows)
        self.assertEqual(rows[-1], "Kernel CVE-2026-99999")

if __name__ == "__main__":
    unittest.main()

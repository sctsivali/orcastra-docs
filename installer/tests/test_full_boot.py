"""Phase 5 boot check of the Full installer: waiting for cloud-init without trusting one early
exit of `cloud-init status --wait`, the verdict for new and already provisioned instances, and
the record of which instances an earlier run verified.

Run:  cd installer && python3 -m unittest discover -s tests -v
"""
import json
import os
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from _fakes import FakeCtx, FakeLxd, FakeState
from orcastra_core.errors import InstallError
from orcastra_core.proc import Result
from orcastra_full_install import remote as R
from orcastra_full_install import topology as T
from orcastra_full_install.phases import p05_instances as P

DONE = {"status": "done", "extended_status": "done", "errors": [], "recoverable_errors": {}, "stage": None}
# what cloud-init 26.1 prints when systemd_failed() misreads a unit while the boot still runs
MISREAD = {"status": "error", "extended_status": "error - done", "detail": "Failed due to systemd unit failure",
           "errors": ["Failed due to systemd unit failure. Ensure all cloud-init services are enabled, "
                      "and check 'systemctl' or 'journalctl' for more information."],
           "recoverable_errors": {}}
RUNNING = {"status": "running", "extended_status": "running", "errors": [], "recoverable_errors": {},
           "stage": "modules-final"}


def probe(wait_rc=0, finished=True, failed="", status=DONE, err=""):
    body = json.dumps(status) if isinstance(status, dict) else status
    out = f"wait_rc={wait_rc}\nfinished={int(finished)}\nfailed_units={failed}\n{P._MARK}\n{body}\n"
    return Result(0, out, err, [])


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


class Remote:
    """Answers each probe from a script; a probe that ran into its slice costs the slice."""

    def __init__(self, clock, probes):
        self.clock, self.probes, self.scripts = clock, list(probes), []

    def run(self, script, check=True, timeout=None, **kw):
        self.scripts.append(script)
        res = self.probes.pop(0) if len(self.probes) > 1 else self.probes[0]
        self.clock.now += 300 if "wait_rc=124" in res.out else 2
        return res

    def ok(self, script, timeout=120):
        return True


def boot_ctx(probes, **kw):
    clock = Clock()
    ctx = FakeCtx(**kw)
    ctx.remote_obj = Remote(clock, probes)
    ctx.remote = lambda role: ctx.remote_obj
    return ctx, clock


def check(ctx, clock, provisioned=False):
    with mock.patch.object(P, "time", clock):
        P._check_cloud_init(ctx, "authentik", provisioned=provisioned)


class ProbeScript(unittest.TestCase):
    """The probe really runs under the remote prelude (`set -euo pipefail`) with stub tools."""

    def run_probe(self, wait_rc, json_rc, body, failed="", finished=False):
        d = tempfile.mkdtemp()
        for name, text in (("body", body), ("failed", failed)):
            with open(os.path.join(d, name), "w") as fh:
                fh.write(text)
        stubs = {
            "cloud-init": f'case "$*" in *--wait*) exit {wait_rc};; esac\ncat {d}/body\nexit {json_rc}\n',
            "systemctl": f"cat {d}/failed\n",
        }
        for name, text in stubs.items():
            path = os.path.join(d, name)
            with open(path, "w") as fh:
                fh.write("#!/bin/sh\n" + text)
            os.chmod(path, stat.S_IRWXU)
        result_file = os.path.join(d, "result.json")
        if finished:
            open(result_file, "w").close()
        # the prelude sets its own PATH, so the stubs go in front of it
        self.assertIn("set -euo pipefail", R._PRELUDE)
        prelude = R._PRELUDE.replace("export PATH=", f"export PATH={d}:", 1)
        with mock.patch.object(P, "_RESULT", result_file):
            script = prelude + P._probe_script(5)
        cp = subprocess.run(["bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True, timeout=30)
        return cp, P._parse_probe(Result(cp.returncode, cp.stdout, cp.stderr, []))

    def test_an_early_wait_exit_still_reports_the_status(self):
        cp, p = self.run_probe(1, 1, json.dumps(MISREAD))
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual((p.wait_rc, p.finished, p.status["status"]), (1, False, "error"))

    def test_a_finished_run_and_failed_units(self):
        unit = "cloud-final.service loaded failed failed Cloud-init: Final Stage\n"
        cp, p = self.run_probe(0, 0, json.dumps(DONE), failed=unit, finished=True)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertTrue(p.finished)
        self.assertEqual(p.failed_units, ["cloud-final.service"])

    def test_a_crashing_status_command_leaves_its_traceback(self):
        cp, p = self.run_probe(1, 1, "")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(p.status, {})
        self.assertFalse(p.finished)


class ParseProbe(unittest.TestCase):
    def test_done_from_cloud_inits_own_wait_counts_as_finished(self):
        self.assertTrue(P._parse_probe(probe(0, finished=False)).finished)
        self.assertTrue(P._parse_probe(probe(2, finished=False)).finished)
        self.assertFalse(P._parse_probe(probe(1, finished=False, status=MISREAD)).finished)

    def test_unreachable_guest_and_garbage(self):
        p = P._parse_probe(Result(1, "", "Error: LXD VM agent is not currently running", []))
        self.assertEqual((p.wait_rc, p.finished, p.status), (-1, False, {}))
        self.assertIn("agent", p.raw)
        p = P._parse_probe(probe(1, finished=False, status="Traceback (most recent call last):"))
        self.assertEqual(p.status, {})


class WaitLoop(unittest.TestCase):
    def test_the_partner_case_waits_through_a_mid_boot_misread(self):
        ctx, clock = boot_ctx([probe(1, False, status=MISREAD), probe(0, True)])
        check(ctx, clock)
        self.assertEqual(clock.slept, [5.0])
        self.assertFalse([m for lvl, m in ctx.log.lines if lvl in ("warn", "error")])

    def test_a_long_boot_prints_progress_each_slice(self):
        ctx, clock = boot_ctx([probe(124, False, status=RUNNING), probe(124, False, status=RUNNING), probe(0, True)])
        check(ctx, clock)
        beats = [m for lvl, m in ctx.log.lines if lvl == "detail"]
        self.assertEqual(len(beats), 2)
        self.assertIn("after 5 min (stage modules-final)", beats[0])
        self.assertEqual(clock.slept, [])

    def test_repeated_early_exits_back_off(self):
        ctx, clock = boot_ctx([probe(1, False, status=MISREAD)] * 5 + [probe(0, True)])
        check(ctx, clock)
        self.assertEqual(clock.slept, [5.0, 10.0, 20.0, 30.0, 30.0])

    def test_an_unreachable_guest_is_probed_again(self):
        down = Result(1, "", "Error: LXD VM agent is not currently running", [])
        ctx, clock = boot_ctx([down, down, probe(0, True)])
        check(ctx, clock)
        self.assertEqual(clock.slept, [5.0, 10.0])

    def test_timeout_names_the_stage_and_how_to_resume(self):
        ctx, clock = boot_ctx([probe(124, False, status=RUNNING)])
        with self.assertRaises(InstallError) as cm:
            check(ctx, clock)
        self.assertIn(f"within {P.CLOUD_INIT_TIMEOUT // 60} minutes", cm.exception.message)
        self.assertIn("stage modules-final", cm.exception.message)
        self.assertIn("lxc exec --project orcastra orca-authentik -- tail -f", cm.exception.remediation)
        self.assertIn("resumes", cm.exception.remediation)
        self.assertGreaterEqual(clock.now - 1000.0, P.CLOUD_INIT_TIMEOUT)

    def test_a_unit_that_stays_failed_ends_the_wait_early(self):
        ctx, clock = boot_ctx([probe(1, False, failed="cloud-final.service", status=MISREAD)])
        with self.assertRaises(InstallError) as cm:
            check(ctx, clock)
        self.assertIn("cloud-final.service failed on orca-authentik", cm.exception.message)
        self.assertIn("journalctl -u cloud-final.service", cm.exception.remediation)
        self.assertLess(clock.now - 1000.0, P._FAILED_UNIT_GRACE + 60)

    def test_a_failed_unit_that_recovers_is_not_fatal(self):
        ctx, clock = boot_ctx([probe(1, False, failed="cloud-config.service", status=MISREAD),
                               probe(1, False, status=MISREAD), probe(0, True)])
        check(ctx, clock)

    def test_disabled_and_missing_cloud_init(self):
        disabled = {"status": "disabled", "extended_status": "disabled", "errors": [], "recoverable_errors": {}}
        ctx, clock = boot_ctx([probe(0, False, status=disabled)])
        with self.assertRaises(InstallError) as cm:
            check(ctx, clock)
        self.assertIn("disabled", cm.exception.message)
        ctx, clock = boot_ctx([probe(0, False, status=disabled)])
        check(ctx, clock, provisioned=True)
        self.assertEqual(ctx.log.lines, [])
        ctx, clock = boot_ctx([probe(127, False, status="")])
        with self.assertRaises(InstallError) as cm:
            check(ctx, clock)
        self.assertIn("not installed", cm.exception.message)


class Verdict(unittest.TestCase):
    ERRORS = {"status": "error", "extended_status": "error - done", "recoverable_errors": {},
              "errors": ["('scripts_user', RuntimeError('Runparts: 1 failures (runcmd) in 1 attempted commands'))"]}

    def test_errors_stop_a_new_instance_with_the_way_out(self):
        ctx, clock = boot_ctx([probe(1, True, status=self.ERRORS)])
        with self.assertRaises(InstallError) as cm:
            check(ctx, clock)
        self.assertIn("finished with errors on orca-authentik: ('scripts_user'", cm.exception.message)
        fix = cm.exception.remediation
        self.assertIn("lxc exec --project orcastra orca-authentik -- cloud-init status --long", fix)
        self.assertIn("lxc exec --project orcastra orca-authentik -- tail -n 100 /var/log/cloud-init-output.log", fix)
        self.assertIn(f"{T.PROTECT_KEY}=false; lxc delete --project orcastra orca-authentik", fix)

    def test_errors_only_warn_on_an_instance_set_up_earlier(self):
        ctx, clock = boot_ctx([probe(1, True, status=self.ERRORS)])
        check(ctx, clock, provisioned=True)
        warns = [m for lvl, m in ctx.log.lines if lvl == "warn"]
        self.assertEqual(len(warns), 1)
        self.assertIn("set up by an earlier run", warns[0])

    def test_recoverable_errors_warn_and_continue(self):
        degraded = dict(DONE, extended_status="degraded done",
                        recoverable_errors={"DEPRECATED": ["Key 'apt_update' is deprecated in 22.2"]})
        ctx, clock = boot_ctx([probe(2, True, status=degraded)])
        check(ctx, clock)
        warns = [m for lvl, m in ctx.log.lines if lvl == "warn"]
        self.assertEqual(warns, ["orca-authentik: cloud-init finished with warnings "
                                 "(DEPRECATED: Key 'apt_update' is deprecated in 22.2)"])

    def test_an_unreadable_status_after_the_run_is_not_called_clean(self):
        ctx, clock = boot_ctx([probe(1, True, status="", err="Traceback: KeyError 'UnitFileState'")])
        with self.assertRaises(InstallError) as cm:
            check(ctx, clock)
        self.assertIn("status unreadable: Traceback", cm.exception.message)


class Provisioned(unittest.TestCase):
    class Lxd(FakeLxd):
        def __init__(self, existing):
            super().__init__()
            self.existing = existing

        def instance(self, name):
            role = name.replace(T.PREFIX, "")
            if role not in self.existing:
                return None
            return {"config": {T.TAG_KEY: role, T.PROTECT_KEY: "true"}, "type": "virtual-machine",
                    "status": "Running", "devices": {"eth0": {"security.ipv4_filtering": "true",
                                                              "security.mac_filtering": "true"}}}

        def status(self, name):
            return "Running"

    def run_phase(self, existing, probes, artifacts=None, phases=None):
        ctx, clock = boot_ctx(probes, lxd=self.Lxd(existing), values={"IMAGE": "ubuntu:24.04"})
        ctx.state = FakeState(artifacts, phases)
        with mock.patch.object(P, "time", clock):
            try:
                P.run(ctx)
            except InstallError as exc:
                ctx.raised = exc
        return ctx

    def test_a_fresh_install_records_each_verified_instance(self):
        ctx = self.run_phase([], [probe(0, True)])
        self.assertFalse(hasattr(ctx, "raised"))
        self.assertEqual(ctx.state.artifacts["provisioned"], sorted(T.ROLES))
        self.assertEqual(ctx.state.artifacts["instances"], sorted(T.ROLES))

    def test_a_deployment_from_before_the_record_counts_as_provisioned(self):
        errors = Verdict.ERRORS
        ctx = self.run_phase(T.ROLES, [probe(1, True, status=errors)],
                             artifacts={"instances": list(T.ROLES)}, phases={"ssh": "done"})
        self.assertFalse(hasattr(ctx, "raised"))
        self.assertEqual(len([1 for lvl, _ in ctx.log.lines if lvl == "warn"]), len(T.ROLES))
        self.assertEqual(ctx.state.artifacts["provisioned"], sorted(T.ROLES))

    def test_a_resumed_first_install_is_still_strict(self):
        ctx = self.run_phase(["vault", "authentik"], [probe(1, True, status=Verdict.ERRORS)],
                             artifacts={"instances": ["authentik", "vault"]})
        self.assertIn("orca-vault", ctx.raised.message)
        self.assertEqual(ctx.state.artifacts["provisioned"], [])

    def test_a_recreated_instance_loses_its_record(self):
        existing = [r for r in T.ROLES if r != "cmp"]
        good = [probe(0, True)] * 3
        ctx = self.run_phase(existing, good + [probe(1, True, status=Verdict.ERRORS)],
                             artifacts={"instances": list(T.ROLES), "provisioned": list(T.ROLES)},
                             phases={"ssh": "done"})
        self.assertIn("orca-cmp", ctx.raised.message)
        self.assertEqual(ctx.state.artifacts["provisioned"], sorted(existing))


if __name__ == "__main__":
    unittest.main()

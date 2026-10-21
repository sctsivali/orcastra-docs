"""Phase 5 - Instances. Creates the four instances one at a time (parallel image unpacks
are known to wedge the LXD API), pins each address with a static DHCP lease, starts it,
and waits until the guest is fully booted: agent up, cloud-init done, address right,
DNS and egress working."""
from __future__ import annotations

import json
import time
from typing import Dict, List, NamedTuple

from orcastra_core.errors import InstallError
from orcastra_core.proc import Result
from orcastra_core.retry import wait_until

from .. import topology as T

TITLE = "Create and boot the instances"

# boot.autostart.priority: higher starts first, the CMP last
_PRIORITY = {"vault": "40", "authentik": "30", "opensearch": "20", "cmp": "10"}

# a first boot on a partner host took 24 minutes; each probe blocks in `status --wait` for one
# slice at most, so the operator sees progress
CLOUD_INIT_TIMEOUT = 2700
_SLICE = 300
_FAILED_UNIT_GRACE = 60
# written when cloud-init's last stage ends, errors or not. `status --wait` can return before
# that: cloud-init 26.1 reports a systemd unit state it reads mid-boot as an error
_RESULT = "/run/cloud-init/result.json"
# cloud-init.service was split into cloud-init-network and cloud-init-main in 24.3
_UNITS = ("cloud-init-local.service", "cloud-init.service", "cloud-init-network.service",
          "cloud-init-main.service", "cloud-config.service", "cloud-final.service")
_MARK = "--- cloud-init status ---"


def _create(ctx, role: str) -> None:
    name, size = T.name(role), ctx.sizes[role]
    args = ["init", ctx.values.get("IMAGE", T.IMAGE_DEFAULT), name, "--profile", T.PROFILE,
            "-c", f"limits.cpu={size['cpu']}", "-c", f"limits.memory={size['mem']}GiB",
            "-c", f"{T.TAG_KEY}={role}", "-c", "boot.autostart=true", "-c", f"{T.PROTECT_KEY}=true",
            "-c", f"boot.autostart.priority={_PRIORITY[role]}", "-c", "boot.autostart.delay=5"]
    if ctx.cluster_member:
        # pinned to this member: its bridge is node-local, this host is the SSH bastion and the
        # forwards listen here, so an evacuation stops the instance instead of moving it away
        args += ["--target", ctx.cluster_member, "-c", "cluster.evacuate=stop"]
    if ctx.is_vm:
        args.append("--vm")
    else:
        args += ["-c", "security.nesting=true",
                 "-c", "security.syscalls.intercept.mknod=true",
                 "-c", "security.syscalls.intercept.setxattr=true"]
        if role == "opensearch":
            args += ["-c", "limits.kernel.memlock=unlimited"]
    ctx.log.info(f"Creating {name} ({'VM' if ctx.is_vm else 'container'}) ...")
    ctx.lxd.must(*args, what=f"create {name}", timeout=1800)
    ctx.lxd.must("config", "device", "override", name, "root", f"size={size['disk']}GiB",
                 what=f"set root size of {name}")
    # the firewalls trust source addresses, so an instance must not be able to send with
    # another one's MAC or IPv4 (LXD enforces both on the host side of the NIC)
    ctx.lxd.must("config", "device", "override", name, "eth0", f"ipv4.address={ctx.ip(role)}",
                 "security.mac_filtering=true", "security.ipv4_filtering=true",
                 what=f"pin {ctx.ip(role)} on {name}")


def _ensure_filtering(ctx, name: str, inst: dict) -> None:
    """Instances created by an earlier installer version get the NIC filters too."""
    nic = (inst.get("devices") or {}).get("eth0", {})
    if nic.get("security.ipv4_filtering") == "true" and nic.get("security.mac_filtering") == "true":
        return
    res = ctx.lxd.cmd("config", "device", "set", name, "eth0", "security.mac_filtering=true",
                      "security.ipv4_filtering=true")
    if not res.ok:
        ctx.log.warn(f"Could not enable MAC/IPv4 filtering on {name} while it runs "
                     f"({res.err.strip()[-120:]}). Stop it, run `lxc config device set --project "
                     f"{ctx.project} {name} eth0 security.mac_filtering=true "
                     "security.ipv4_filtering=true`, and start it again.")


def protect_hint(ctx, name: str) -> str:
    return (f"lxc stop --project {ctx.project} {name} && lxc config set --project {ctx.project} "
            f"{name} {T.PROTECT_KEY}=true && lxc start --project {ctx.project} {name}")


def _ensure_protection(ctx, name: str, inst: dict) -> None:
    """Instances from an installer before deletion protection. LXD only changes the flag on a
    stopped VM, and stopping the control plane is the operator's call, so a running VM is
    reported with the commands for the next maintenance window instead."""
    if (inst.get("config") or {}).get(T.PROTECT_KEY) == "true":
        return
    if inst.get("type") == "virtual-machine" and inst.get("status") == "Running":
        ctx.log.warn(f"{name} is not protected from deletion. In a maintenance window: {protect_hint(ctx, name)}")
        return
    ctx.lxd.must("config", "set", name, f"{T.PROTECT_KEY}=true", what=f"protect {name} from deletion")


class _Probe(NamedTuple):
    wait_rc: int              # `cloud-init status --wait`: 124 slice over, 127 missing, -1 no probe
    finished: bool
    failed_units: List[str]
    status: Dict              # `cloud-init status --format json`, {} when unreadable
    raw: str                  # output tail for the message when the probe or the JSON failed


def _probe_script(wait: int) -> str:
    # important: the remote prelude runs under `set -e`, so each step that can fail is guarded.
    # An unguarded `status --wait` exit dropped the JSON and stopped RC4 installs mid-boot.
    return (f"rc=0; timeout {wait} cloud-init status --wait >/dev/null 2>&1 || rc=$?\n"
            'echo "wait_rc=$rc"\n'
            f"if [ -e {_RESULT} ]; then echo finished=1; else echo finished=0; fi\n"
            f"failed=$(systemctl list-units --state=failed --plain --no-legend {' '.join(_UNITS)} "
            "2>/dev/null | awk '{print $1}' | tr '\\n' ' ') || true\n"
            'echo "failed_units=$failed"\n'
            f"echo '{_MARK}'\n"
            "cloud-init status --format json || true\n")


def _parse_probe(res: Result) -> _Probe:
    head, _, body = res.out.partition(_MARK)
    fields = dict(line.split("=", 1) for line in head.splitlines() if "=" in line)
    try:
        status = json.loads(body) if body.strip() else {}
    except ValueError:
        status = {}
    try:
        wait_rc = int(fields.get("wait_rc", ""))
    except ValueError:
        wait_rc = -1
    status = status if isinstance(status, dict) else {}
    # rc 0 or 2 with "done" means cloud-init's own wait saw the run end; trusted even if the
    # result file lives elsewhere on some image
    finished = fields.get("finished") == "1" or (status.get("status") == "done" and wait_rc in (0, 2))
    raw = (res.err.strip() or body.strip() or res.out.strip())[-300:]
    return _Probe(wait_rc, finished, fields.get("failed_units", "").split(), status, raw)


def _wait_cloud_init(ctx, r, name: str) -> _Probe:
    """Blocks until cloud-init's last stage ended, cloud-init is disabled or missing, one of
    its units stays failed, or the timeout passes, and returns the last probe. An early exit
    of `status --wait` (a mid-boot misread, a probe that could not reach the guest) is only a
    reason to look again."""
    start = time.monotonic()
    deadline = start + CLOUD_INIT_TIMEOUT
    pause, failed_since = 5.0, None
    while True:
        wait = int(max(1, min(_SLICE, deadline - time.monotonic())))
        p = _parse_probe(r.run(_probe_script(wait), check=False, timeout=wait + 60))
        if p.finished or (p.wait_rc == 127 and not p.status) or p.status.get("status") == "disabled":
            return p
        now = time.monotonic()
        failed_since = (failed_since or now) if p.failed_units else None
        if now >= deadline or (failed_since is not None and now - failed_since >= _FAILED_UNIT_GRACE):
            return p
        if p.wait_rc == 124:
            stage = f" (stage {p.status['stage']})" if p.status.get("stage") else ""
            ctx.log.detail(f"{name}: cloud-init still running after {int(now - start) // 60} min{stage}")
            pause = 5.0
            continue
        ctx.log.debug(f"{name}: cloud-init has not finished (status --wait exit {p.wait_rc}), "
                      f"checking again in {int(pause)}s")
        time.sleep(min(pause, max(0.0, deadline - now)))
        pause = min(pause * 2, 30.0)


def _check_cloud_init(ctx, role: str, *, provisioned: bool) -> None:
    """A new instance must finish cloud-init cleanly. An instance an earlier run verified only
    gets a warning: its bootstrap is done, and a later boot must not block a re-run."""
    name = T.name(role)
    p = _wait_cloud_init(ctx, ctx.remote(role), name)
    state = p.status.get("status") or "unknown"
    errors = [str(e) for e in p.status.get("errors") or []]
    exec_ = f"lxc exec --project {ctx.project} {name} --"
    look = f"`{exec_} cloud-init status --long` and `{exec_} tail -n 100 /var/log/cloud-init-output.log`"
    rebuild = ("The instance is new and holds nothing yet: after fixing the cause, delete it with "
               f"`lxc stop --project {ctx.project} {name} --force; lxc config set --project "
               f"{ctx.project} {name} {T.PROTECT_KEY}=false; lxc delete --project {ctx.project} "
               f"{name}` and re-run the installer, which creates it again.")
    if state == "disabled":
        if provisioned:
            return  # operators may switch cloud-init off once an instance is set up
        problem, fix = (f"cloud-init is disabled in {name}, so its first boot set nothing up",
                        f"Check the image and `{exec_} cloud-init status --long`. {rebuild}")
    elif p.wait_rc == 127 and not p.status:
        problem, fix = (f"cloud-init is not installed in {name}",
                        f"Use an Ubuntu cloud image (the default ubuntu:24.04). {rebuild}")
    elif p.finished:
        recoverable = p.status.get("recoverable_errors") or {}
        if state == "done" and not errors:
            if recoverable:
                summary = "; ".join(f"{level}: {msgs[0] if msgs else ''}" for level, msgs in recoverable.items())
                ctx.log.warn(f"{name}: cloud-init finished with warnings ({summary[:300]})")
            return
        detail = "; ".join(errors) or (state if p.status else f"status unreadable: {p.raw}")
        problem = f"cloud-init finished with errors on {name}: {detail[:600]}"
        fix = f"Check {look}. {rebuild}"
    elif p.failed_units:
        problem = f"cloud-init unit(s) {', '.join(p.failed_units)} failed on {name}"
        fix = f"Check {look} and `{exec_} journalctl -u {p.failed_units[0]}`. {rebuild}"
    else:
        stage = f", stage {p.status['stage']}" if p.status.get("stage") else ""
        tail = f": {p.raw}" if p.raw and not p.status else ""
        problem = (f"cloud-init did not finish on {name} within {CLOUD_INIT_TIMEOUT // 60} minutes "
                   f"(last status {state}{stage}){tail}")
        fix = (f"A slow package mirror or host can take this long. Follow it with `{exec_} tail -f "
               "/var/log/cloud-init-output.log`, and once `cloud-init status` there says done, "
               "re-run the installer: it resumes here.")
    if provisioned:
        ctx.log.warn(f"{problem}. {name} was set up by an earlier run, so the install continues.")
        return
    raise InstallError(problem, remediation=fix)


def _provisioned(ctx) -> List[str]:
    """Roles whose first boot an earlier run verified."""
    recorded = ctx.state.artifact("provisioned")
    if recorded is not None:
        return list(recorded)
    # deployments from before this record reached the ssh phase only after every instance booted
    return list(ctx.state.artifact("instances") or []) if ctx.state.is_done("ssh") else []


def _wait_booted(ctx, role: str, *, provisioned: bool) -> None:
    name, r = T.name(role), ctx.remote(role)
    if not wait_until(lambda: r.ok("true", timeout=20), timeout=420, interval=5):
        raise InstallError(f"{name} did not become reachable through lxc exec within 7 minutes.",
                           remediation=f"Check `lxc console --project {ctx.project} {name} --show-log`.")
    _check_cloud_init(ctx, role, provisioned=provisioned)
    ip = ctx.ip(role)
    if not wait_until(lambda: r.ok(f"ip -4 -o addr show | grep -q ' {ip}/'"), timeout=120):
        raise InstallError(f"{name} did not get its address {ip}.",
                           remediation="Check the bridge DHCP (dnsmasq) and the ipv4.address override.")
    if not wait_until(lambda: r.ok("getent hosts archive.ubuntu.com >/dev/null "
                                   "&& curl -fsS -o /dev/null --max-time 15 https://download.docker.com"),
                      timeout=180, interval=5):
        raise InstallError(f"{name} has no working DNS or internet egress.",
                           remediation="The instances need outbound HTTPS for packages and images. "
                                       "Check NAT on the bridge and the host firewall.")


def run(ctx) -> None:
    done = list(ctx.state.artifact("instances") or [])
    provisioned = set(_provisioned(ctx))
    ctx.state.set_artifact("provisioned", sorted(provisioned))
    for role in T.ROLES:
        name = T.name(role)
        inst = ctx.lxd.instance(name)
        if inst is None:
            _create(ctx, role)
            done.append(role)
            ctx.state.set_artifact("instances", sorted(set(done)))
            provisioned.discard(role)
            ctx.state.set_artifact("provisioned", sorted(provisioned))
        elif (inst.get("config") or {}).get(T.TAG_KEY) != role:
            raise InstallError(f"An instance named {name} already exists and was not created by "
                               "this installer.",
                               remediation="Rename or remove it, or use another --lxd-project.")
        else:
            if role not in done:
                done.append(role)
                ctx.state.set_artifact("instances", sorted(set(done)))
            _ensure_filtering(ctx, name, inst)
            _ensure_protection(ctx, name, inst)
        if ctx.lxd.status(name) != "Running":
            ctx.lxd.must("start", name, what=f"start {name}", timeout=600)
        _wait_booted(ctx, role, provisioned=role in provisioned)
        if role not in provisioned:
            provisioned.add(role)
            ctx.state.set_artifact("provisioned", sorted(provisioned))
        ctx.log.ok(f"{name} is up at {ctx.ip(role)}")

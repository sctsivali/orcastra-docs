"""Phase 5 - Instances. Creates the four instances one at a time (parallel image unpacks
are known to wedge the LXD API), pins each address with a static DHCP lease, starts it,
and waits until the guest is fully booted: agent up, cloud-init done, address right,
DNS and egress working."""
from __future__ import annotations

from orcastra_core.errors import InstallError
from orcastra_core.retry import wait_until

from .. import topology as T

TITLE = "Create and boot the instances"

# boot.autostart.priority: higher starts first, the CMP last
_PRIORITY = {"vault": "40", "authentik": "30", "opensearch": "20", "cmp": "10"}


def _create(ctx, role: str) -> None:
    name, size = T.name(role), ctx.sizes[role]
    args = ["init", ctx.values.get("IMAGE", T.IMAGE_DEFAULT), name, "--profile", T.PROFILE,
            "-c", f"limits.cpu={size['cpu']}", "-c", f"limits.memory={size['mem']}GiB",
            "-c", f"{T.TAG_KEY}={role}", "-c", "boot.autostart=true",
            "-c", f"boot.autostart.priority={_PRIORITY[role]}", "-c", "boot.autostart.delay=5"]
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


def _wait_booted(ctx, role: str) -> None:
    name, r = T.name(role), ctx.remote(role)
    if not wait_until(lambda: r.ok("true", timeout=20), timeout=420, interval=5):
        raise InstallError(f"{name} did not become reachable through lxc exec within 7 minutes.",
                           remediation=f"Check `lxc console --project {ctx.project} {name} --show-log`.")
    res = r.run("cloud-init status --wait >/dev/null 2>&1; cloud-init status --format json || true",
                check=False, timeout=1500)
    if '"status": "done"' not in res.out and '"status":"done"' not in res.out:
        detail = res.out.strip()[-300:]
        if '"degraded' in res.out or "recoverable_errors" in res.out:
            ctx.log.warn(f"{name}: cloud-init finished with warnings: {detail}")
        else:
            raise InstallError(f"cloud-init did not finish cleanly on {name}: {detail}",
                               remediation=f"Inspect /var/log/cloud-init-output.log in {name}.")
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
    for role in T.ROLES:
        name = T.name(role)
        inst = ctx.lxd.instance(name)
        if inst is None:
            _create(ctx, role)
            done.append(role)
            ctx.state.set_artifact("instances", sorted(set(done)))
        elif (inst.get("config") or {}).get(T.TAG_KEY) != role:
            raise InstallError(f"An instance named {name} already exists and was not created by "
                               "this installer.",
                               remediation="Rename or remove it, or use another --lxd-project.")
        else:
            if role not in done:
                done.append(role)
                ctx.state.set_artifact("instances", sorted(set(done)))
            _ensure_filtering(ctx, name, inst)
        if ctx.lxd.status(name) != "Running":
            ctx.lxd.must("start", name, what=f"start {name}", timeout=600)
        _wait_booted(ctx, role)
        ctx.log.ok(f"{name} is up at {ctx.ip(role)}")

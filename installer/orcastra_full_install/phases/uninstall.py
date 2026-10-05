"""Uninstall. Removes exactly what the installer recorded: the four instances, our forward
ports (or the forward we created), the profile, project, bridge and pool when we created
them and nothing else uses them, the host tooling, SSH entries and host firewall fixes.
Destroys all data in the instances, so it asks for the typed word `orcastra`."""
from __future__ import annotations

import os
import shutil
import sys
import time

from orcastra_core.errors import AbortByUser

from .. import hostfix
from .. import topology as T
from . import p14_exposure, p15_watchdog
from .p06_ssh import INCLUDE_MARK

TITLE = "Uninstall"


def _confirm(ctx) -> None:
    ctx.log.warn("This deletes the four Orcastra instances and ALL their data (Vault secrets, "
                 "Authentik users, logs, the CMP database). It cannot be undone.")
    if "-y" in sys.argv or "--assume-yes" in sys.argv:
        return  # only an explicit flag on this command line skips the typed confirmation
    if not ctx.interactive:
        raise AbortByUser("Uninstall needs confirmation.", remediation="Re-run with --assume-yes.")
    if ctx.prompt.ask("Type orcastra to confirm") != "orcastra":
        raise AbortByUser("Uninstall cancelled.")


def _ssh_cleanup(ctx) -> None:
    for home in ctx.state.artifact("ssh_homes") or ["/root"]:
        for rel in (".ssh/config.d/orcastra.conf", ".ssh/orcastra_ed25519", ".ssh/orcastra_known_hosts"):
            p = os.path.join(home, rel)
            if os.path.exists(p):
                os.unlink(p)
        cfg = os.path.join(home, ".ssh", "config")
        if os.path.exists(cfg):
            with open(cfg, encoding="utf-8") as fh:
                lines = fh.readlines()
            kept = [l for l in lines if INCLUDE_MARK not in l]
            if kept != lines:
                with open(cfg, "w", encoding="utf-8") as fh:
                    fh.writelines(kept)


def _host_tooling(ctx) -> None:
    ctx.proc.run(["systemctl", "disable", "--now", "orcastra-maintain.timer"])
    for p in (p15_watchdog.TIMER, p15_watchdog.SERVICE, p15_watchdog.WRAPPER, p15_watchdog.PYZ):
        if os.path.exists(p):
            os.unlink(p)
    ctx.proc.run(["systemctl", "daemon-reload"])
    try:
        os.rmdir(p15_watchdog.LIB)  # only when empty, never anything we did not put there
    except OSError:
        pass


def run(ctx) -> None:
    _confirm(ctx)
    lxd = ctx.lxd
    for role in T.ROLES:
        inst = lxd.instance(T.name(role))
        if inst and (inst.get("config") or {}).get(T.TAG_KEY) == role:
            lxd.must("delete", "--force", T.name(role), what=f"delete {T.name(role)}", timeout=900)
            ctx.log.ok(f"Deleted {T.name(role)}")
    p14_exposure.remove_ours(ctx, ctx.state.artifact("forward_ports") or {})
    ctx.log.ok("Removed the port forwards this installer added")

    created = ctx.state.artifact("created") or {}
    if T.PROFILE in created.get("profile", []) and lxd.profile(T.PROFILE):
        lxd.cmd("profile", "delete", T.PROFILE)
    for proj in created.get("project", []):
        res = lxd.cmd("project", "delete", proj, project=False)
        (ctx.log.ok if res.ok else ctx.log.warn)(
            f"Project {proj} " + ("deleted" if res.ok else f"kept ({res.err.strip()[-120:]})"))
    hostfix.revert(ctx)
    for net in created.get("network", []):
        res = lxd.cmd("network", "delete", net, project=False)
        (ctx.log.ok if res.ok else ctx.log.warn)(
            f"Network {net} " + ("deleted" if res.ok else f"kept, still in use ({res.err.strip()[-120:]})"))
    for pool in created.get("pool", []):
        res = lxd.cmd("storage", "delete", pool, project=False)
        (ctx.log.ok if res.ok else ctx.log.warn)(
            f"Storage pool {pool} " + ("deleted" if res.ok else f"kept, still in use ({res.err.strip()[-120:]})"))

    _host_tooling(ctx)
    _ssh_cleanup(ctx)
    if getattr(ctx.flags, "keep_secrets", False):
        dest = f"{ctx.deploy_dir}.removed-{time.strftime('%Y%m%dT%H%M%S')}"
        os.replace(ctx.deploy_dir, dest)
        ctx.log.ok(f"Secrets, keys and logs kept in {dest}")
    elif os.path.isdir(ctx.deploy_dir):
        shutil.rmtree(ctx.deploy_dir)
        ctx.log.ok(f"Removed {ctx.deploy_dir}")
    ctx.log.ok("Uninstall complete")

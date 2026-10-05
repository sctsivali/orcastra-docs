"""Phase 1 - Host preflight. Checks that this machine can drive LXD at all, before any
question is asked: privileges, the lxc client and daemon, required host tools, and
whether another Orcastra deployment already lives on this host (one per host in v1)."""
from __future__ import annotations

import shutil
import sys

from orcastra_core.errors import PreflightError

from .. import hostnet
from .. import topology as T

TITLE = "Host preflight"

_TOOLS = ("ip", "ss", "openssl", "ssh", "ssh-keygen")


def _version_tuple(v: str) -> tuple:
    out = []
    for part in v.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits or 0))
    return tuple(out)


def run(ctx) -> None:
    rows = []
    fail = []

    def add(name: str, status: str, detail: str, fix: str = "") -> None:
        rows.append((name, status, detail))
        if status == "FAIL":
            fail.append(f"{name}: {detail}. {fix}".strip())

    osr = hostnet.os_release()
    pretty = osr.get("PRETTY_NAME", "unknown")
    add("Operating system", "PASS" if osr.get("ID") in ("ubuntu", "debian") else "WARN", pretty)
    add("Python", "PASS" if sys.version_info >= (3, 8) else "FAIL",
        sys.version.split()[0], "Python 3.8 or newer is required.")

    for tool in _TOOLS:
        found = shutil.which(tool)
        add(f"tool: {tool}", "PASS" if found else "FAIL", found or "missing",
            "Install it with apt (iproute2, openssh-client, openssl).")

    lxc = ctx.lxd.lxc
    if not shutil.which(lxc) and not shutil.which("lxc"):
        add("lxc client", "FAIL", "not found", "Install LXD (snap install lxd) and run `lxd init`.")
    else:
        server = {}
        try:
            server = ctx.lxd.server()
        except Exception as exc:  # noqa: BLE001 - reported as a failed check
            add("LXD daemon", "FAIL", str(exc)[:200],
                "Make sure the LXD daemon runs and this user may use it (root or the lxd group).")
        if server:
            env = server.get("environment", {})
            ver = env.get("server_version", "0")
            ok = _version_tuple(ver) >= (5, 0)
            add("LXD version", "PASS" if ok else "FAIL", ver, "LXD 5.0 or newer is required.")
            add("LXD firewall driver", "PASS", env.get("firewall", "unknown"))
            ctx.facts["lxd_version"] = ver
            ctx.facts["lxd_firewall"] = env.get("firewall", "")
            ctx.facts["lxd_storage_drivers"] = [d.get("Name") for d in env.get("storage_supported_drivers", [])]

            # one deployment per host: refuse a second one in another project
            ours = []
            for inst in ctx.lxd.instances(all_projects=True):
                if T.TAG_KEY in (inst.get("config") or {}):
                    ours.append((inst.get("project", "default"), inst["name"]))
            foreign = [f"{p}/{n}" for p, n in ours if p != ctx.project]
            if foreign:
                add("Existing deployment", "FAIL", ", ".join(foreign),
                    f"This host already runs an Orcastra deployment in another LXD project. "
                    f"Re-run with --lxd-project {foreign[0].split('/')[0]} to manage it.")
            elif ours:
                add("Existing deployment", "PASS", f"{len(ours)} instance(s) in project {ctx.project} (resume)")
            else:
                add("Existing deployment", "PASS", "none")

    add("KVM (/dev/kvm)", "PASS" if hostnet.has_kvm() else "WARN",
        "present" if hostnet.has_kvm() else "absent (only container instances possible)")
    ctx.facts["kvm"] = hostnet.has_kvm()

    width = max(len(r[0]) for r in rows)
    for name, status, detail in rows:
        line = f"{name.ljust(width)}  {detail}"
        {"PASS": ctx.log.ok, "WARN": ctx.log.warn}.get(status, ctx.log.error)(line)
    if fail:
        raise PreflightError("Host preflight failed:\n  - " + "\n  - ".join(fail),
                             remediation="Fix the items above and re-run the installer.")

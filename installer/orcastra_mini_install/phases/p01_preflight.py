"""Phase 1 - preflight. Collect environment facts, print a pass/warn/fail table, abort on
any FAIL. Missing Docker is only a WARN (phase 2 installs it). Warnings don't block."""
import os
import shutil

from ..errors import PreflightError
from ..netutil import tcp_port_free

TITLE = "Preflight checks"


def _osrelease():
    data = {}
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            for line in fh:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    data[k] = v.strip().strip('"')
    except OSError:
        pass
    return data


def _meminfo_gib():
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return None


def _in_docker_group():
    try:
        import grp
        gids = os.getgrouplist(os.getlogin() if hasattr(os, "getlogin") else "", os.getgid())
    except Exception:
        try:
            gids = os.getgroups()
        except Exception:
            return False
    try:
        docker_gid = grp.getgrnam("docker").gr_gid
        return docker_gid in gids
    except (KeyError, Exception):
        return False


def run(ctx):
    results = []  # (name, severity, detail, remediation)

    def add(name, sev, detail, remediation=None):
        results.append((name, sev, detail, remediation))

    osr = _osrelease()
    osid = (osr.get("ID") or "").lower()
    idlike = (osr.get("ID_LIKE") or "").lower()
    ctx.cfg["os_id"] = osid
    ctx.cfg["os_codename"] = osr.get("VERSION_CODENAME", "")
    debian_family = osid in ("ubuntu", "debian") or "debian" in idlike
    add("OS", "PASS" if debian_family else "FAIL",
        f"{osr.get('PRETTY_NAME', 'unknown')}",
        None if debian_family else "Ubuntu/Debian required for automated Docker install. "
        "On other distros, follow docs/mini/quick-start.md manually.")

    arch = os.uname().machine
    arch_ok = arch in ("x86_64", "amd64", "aarch64", "arm64")
    add("Arch", "PASS" if arch_ok else "FAIL", arch,
        None if arch_ok else "Published images target amd64/arm64 only.")

    ram = _meminfo_gib()
    if ram is None:
        add("RAM", "WARN", "could not read /proc/meminfo")
    elif ram < 2.0:
        add("RAM", "FAIL", f"{ram:.1f} GiB", "Need at least 2 GiB (4 GiB recommended).")
    else:
        add("RAM", "PASS" if ram >= 4 else "WARN", f"{ram:.1f} GiB",
            None if ram >= 4 else "4 GiB recommended for the full stack.")

    free_gib = shutil.disk_usage(ctx.install_dir).free / 1024 ** 3
    if free_gib < 5:
        add("Disk", "FAIL", f"{free_gib:.1f} GiB free", "Need at least 10 GiB free for images + volumes.")
    else:
        add("Disk", "PASS" if free_gib >= 10 else "WARN", f"{free_gib:.1f} GiB free")

    cpus = os.cpu_count() or 1
    add("CPU", "PASS" if cpus >= 2 else "WARN", f"{cpus} cores")

    has_docker = bool(shutil.which("docker"))
    add("Docker engine", "PASS" if has_docker else "WARN",
        "present" if has_docker else "missing",
        None if has_docker else "Phase 2 can install it (Ubuntu/Debian apt).")

    if has_docker:
        cv = ctx.proc.run(["docker", "compose", "version"])
        compose_ok = cv.ok
        add("Compose v2", "PASS" if compose_ok else "WARN",
            cv.out.strip().splitlines()[0] if compose_ok else "missing",
            None if compose_ok else "Phase 2 installs docker-compose-plugin.")
        info = ctx.proc.run(["docker", "info"])
        add("Docker daemon", "PASS" if info.ok else "FAIL",
            "running" if info.ok else "not reachable",
            None if info.ok else "Start it: sudo systemctl start docker (and ensure your user can use it).")
    else:
        ctx.cfg["compose_ok"] = False

    is_root = os.geteuid() == 0
    priv_ok = is_root or _in_docker_group()
    add("Privilege", "PASS" if priv_ok else "FAIL",
        "root" if is_root else ("docker group" if priv_ok else "unprivileged"),
        None if priv_ok else "Run as root (sudo) or add your user to the docker group.")
    ctx.cfg["is_root"] = is_root

    for tool in ("openssl", "curl"):
        present = bool(shutil.which(tool))
        sev = "PASS" if present else ("FAIL" if tool == "openssl" else "WARN")
        add(tool, sev, "present" if present else "missing",
            None if present else f"Install it: sudo apt-get install -y {tool}")

    port = ctx.https_port
    if tcp_port_free("0.0.0.0", port):
        add(f"HTTPS port {port}", "PASS", "free")
    else:
        # An existing orcastra-mini nginx publishing it is fine (idempotent re-run).
        rows_owner = _port_owner_is_ours(ctx, port)
        add(f"HTTPS port {port}", "PASS" if rows_owner else "FAIL",
            "in use by this deployment" if rows_owner else "in use",
            None if rows_owner else f"Free the port or pass --https-port. Inspect with: ss -ltnp | grep :{port}")

    if tcp_port_free("127.0.0.1", 8200):
        add("Vault loopback 8200", "PASS", "free")
    else:
        rows_owner = _port_owner_is_ours(ctx, 8200)
        add("Vault loopback 8200", "PASS" if rows_owner else "FAIL",
            "in use by this deployment" if rows_owner else "in use by another process",
            None if rows_owner else "Port 127.0.0.1:8200 must be free for Vault init/unseal.")

    _print_table(ctx, results)
    ctx.preflight = results

    fails = [r for r in results if r[1] == "FAIL"]
    if fails:
        first = fails[0]
        raise PreflightError(
            f"{len(fails)} blocking check(s) failed; first: {first[0]} ({first[2]})",
            remediation=first[3] or "See the table above and the listed remediations.")
    warns = [r for r in results if r[1] == "WARN"]
    if warns:
        ctx.log.warn(f"{len(warns)} warning(s) - proceeding. Review the table above.")


def _port_owner_is_ours(ctx, port):
    """True if the port is published by a container whose name carries our prefix."""
    res = ctx.proc.run(["docker", "ps", "--format", "{{.Names}} {{.Ports}}"])
    if not res.ok:
        return False
    prefix = ctx.cfg.get("container_prefix", "orcastra-mini")
    for line in res.out.splitlines():
        if f":{port}->" in line and prefix in line:
            return True
    return False


def _print_table(ctx, results):
    sym = {"PASS": ctx.log._c("PASS", "green"), "WARN": ctx.log._c("WARN", "yellow"),
           "FAIL": ctx.log._c("FAIL", "red"), "INFO": "INFO"}
    width = max(len(r[0]) for r in results) + 2
    for name, sev, detail, remediation in results:
        ctx.log._emit(f"  {sym[sev]:<6} {name:<{width}} {detail}", 20, f"{sev} {name}: {detail}")
        if remediation and sev in ("FAIL", "WARN"):
            ctx.log.detail("       -> " + remediation)

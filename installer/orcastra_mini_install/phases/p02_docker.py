"""Phase 2 - ensure Docker engine + compose v2. If missing on Ubuntu/Debian, install via
the official Docker apt repo (docs.docker.com/engine/install) after confirmation."""
import shutil

from ..errors import AbortByUser, DockerError

TITLE = "Ensure Docker + Compose"


def _docker_ok(ctx):
    if not shutil.which("docker"):
        return False
    return ctx.proc.run(["docker", "info"]).ok


def _compose_ok(ctx):
    return ctx.proc.run(["docker", "compose", "version"]).ok


def run(ctx):
    if _docker_ok(ctx) and _compose_ok(ctx):
        ctx.log.ok("Docker engine and Compose v2 present.")
        return

    if ctx.flags.skip_docker_install:
        raise DockerError("Docker or Compose v2 missing and --skip-docker-install set.",
                          remediation="Install Docker + the compose plugin, then re-run.")

    osid = ctx.cfg.get("os_id", "")
    if osid not in ("ubuntu", "debian"):
        raise DockerError(f"Cannot auto-install Docker on '{osid}'.",
                          remediation="Install Docker Engine + compose plugin per "
                                      "docs.docker.com, then re-run with --skip-docker-install.")

    if not ctx.prompt.confirm(
            "Docker (or the compose plugin) is missing. Install it now via the official "
            "Docker apt repository?", default=True):
        raise AbortByUser("Docker is required.",
                          remediation="Install it manually, then re-run.")

    if not ctx.cfg.get("is_root"):
        # We need root to write apt keyrings and install packages.
        if not shutil.which("sudo"):
            raise DockerError("Root privileges required to install Docker.",
                              remediation="Re-run as root.")
        sudo = ["sudo"]
    else:
        sudo = []

    codename = ctx.cfg.get("os_codename") or _detect_codename(ctx)
    if not codename:
        raise DockerError("Could not determine the distro codename (VERSION_CODENAME).",
                          remediation="Set it in /etc/os-release or install Docker manually.")

    _install_docker(ctx, sudo, osid, codename)

    if not _docker_ok(ctx):
        raise DockerError("Docker still not reachable after install.",
                          remediation="Check 'systemctl status docker' and your permissions "
                                      "(a freshly-added docker group needs a re-login).")
    if not _compose_ok(ctx):
        raise DockerError("Compose v2 plugin still missing after install.",
                          remediation="Install docker-compose-plugin manually.")
    ctx.log.ok("Docker installed and reachable.")


def _detect_codename(ctx):
    res = ctx.proc.run(["lsb_release", "-cs"])
    return res.out.strip() if res.ok else ""


def _arch(ctx):
    res = ctx.proc.run(["dpkg", "--print-architecture"])
    return res.out.strip() if res.ok else "amd64"


def _install_docker(ctx, sudo, osid, codename):
    arch = _arch(ctx)
    log = ctx.log
    log.info(f"Installing Docker for {osid} {codename} ({arch}) ...")

    def sh(argv, **kw):
        res = ctx.proc.run(sudo + argv, mutating=True, **kw)
        if not res.ok and not ctx.dry_run:
            raise DockerError(f"command failed: {' '.join(argv)}",
                              remediation=(res.err or res.out).strip()[:400] or
                              "See the install log for details.")
        return res

    sh(["apt-get", "update"], timeout=300)
    sh(["apt-get", "install", "-y", "ca-certificates", "curl"], timeout=300)
    sh(["install", "-m", "0755", "-d", "/etc/apt/keyrings"])
    sh(["curl", "-fsSL", f"https://download.docker.com/linux/{osid}/gpg",
        "-o", "/etc/apt/keyrings/docker.asc"], timeout=120)
    sh(["chmod", "a+r", "/etc/apt/keyrings/docker.asc"])

    repo = (f"deb [arch={arch} signed-by=/etc/apt/keyrings/docker.asc] "
            f"https://download.docker.com/linux/{osid} {codename} stable\n")
    if ctx.dry_run:
        log.detail("[dry-run] would write /etc/apt/sources.list.d/docker.list")
    else:
        # tee via sudo so the redirection runs with privilege
        res = ctx.proc.run(sudo + ["tee", "/etc/apt/sources.list.d/docker.list"],
                           input=repo, mutating=True)
        if not res.ok:
            raise DockerError("could not write docker.list",
                              remediation=(res.err or "").strip()[:400])

    sh(["apt-get", "update"], timeout=300)
    sh(["apt-get", "install", "-y", "docker-ce", "docker-ce-cli", "containerd.io",
        "docker-buildx-plugin", "docker-compose-plugin"], timeout=600)
    ctx.proc.run(sudo + ["systemctl", "enable", "--now", "docker"], mutating=True)

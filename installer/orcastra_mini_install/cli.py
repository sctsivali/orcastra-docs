"""Argument parsing, answer-file merge, mode resolution, and the phase dispatch loop."""
import argparse
import os
import sys

from . import __version__
from .context import InstallContext
from .errors import AbortByUser, InstallError
from .log import Log
from .proc import Proc
from .prompt import Prompter
from .state import State

DEFAULT_INSTALL_DIR = "/opt/orcastra-mini"

# value flag -> default (applied after answer-file merge)
VALUE_DEFAULTS = {
    "install_dir": DEFAULT_INSTALL_DIR,
    "https_port": 6969,
    "container_prefix": "orcastra-mini",
    "version_tag": "1.0.0-RC1",
    "client_cert_ttl_days": 365,
    "jwt_ttl_seconds": 3600,
    "ca_ttl_hours": 87600,
    "role_max_ttl_hours": 8760,
    "postgres_user": "orcastra",
    "postgres_db": "orcastra",
    "unseal_shares": 5,
    "unseal_threshold": 3,
    "admin_cn": "admin",
}
INT_FLAGS = {"https_port", "client_cert_ttl_days", "jwt_ttl_seconds", "ca_ttl_hours",
             "role_max_ttl_hours", "unseal_shares", "unseal_threshold"}


def build_parser():
    p = argparse.ArgumentParser(
        prog="orcastra-mini-install",
        description="Automated installer for the Orcastra Mini single-instance deployment.")
    p.add_argument("--version", action="version", version=f"orcastra-mini-install {__version__}")

    g = p.add_argument_group("general")
    g.add_argument("--install-dir", default=None)
    g.add_argument("--non-interactive", action="store_true")
    g.add_argument("-y", "--assume-yes", action="store_true")
    g.add_argument("--answers", metavar="FILE", help="flat KEY=value answer file")
    g.add_argument("--log-file", default=None)
    g.add_argument("--verbose", action="store_true")
    g.add_argument("--quiet", action="store_true")
    g.add_argument("--dry-run", action="store_true",
                   help="render files + print the plan; run nothing mutating")
    g.add_argument("--version-tag", default=None, help="image tag (e.g. 1.0.0-RC1)")

    n = p.add_argument_group("host / network")
    n.add_argument("--host", default=None, help="DNS name or IP to reach the dashboard")
    hostkind = n.add_mutually_exclusive_group()
    hostkind.add_argument("--host-is-ip", dest="host_is_ip", action="store_true", default=None)
    hostkind.add_argument("--host-is-dns", dest="host_is_ip", action="store_false", default=None)
    n.add_argument("--https-port", type=int, default=None)
    n.add_argument("--container-prefix", default=None)
    n.add_argument("--tunnel", action="store_true", help="reach the host over an SSH tunnel (HOST=localhost)")

    c = p.add_argument_group("certs / ttls")
    c.add_argument("--client-cert-ttl-days", type=int, default=None)
    c.add_argument("--jwt-ttl-seconds", type=int, default=None)
    c.add_argument("--ca-ttl-hours", type=int, default=None)
    c.add_argument("--role-max-ttl-hours", type=int, default=None)
    c.add_argument("--server-cert", default=None, help="BYO TLS server cert (skip openssl)")
    c.add_argument("--server-key", default=None)

    r = p.add_argument_group("registry")
    r.add_argument("--skip-docker-install", action="store_true")
    r.add_argument("--image-backend", default=None)
    r.add_argument("--image-frontend", default=None)

    v = p.add_argument_group("vault")
    v.add_argument("--convenience", action="store_true",
                   help="persist unseal keys locally + optional auto-unseal (less secure)")
    v.add_argument("--auto-unseal-unit", action="store_true",
                   help="(convenience) install a systemd unit that unseals on boot")
    v.add_argument("--unseal-shares", type=int, default=None)
    v.add_argument("--unseal-threshold", type=int, default=None)
    v.add_argument("--skip-vault-bootstrap", action="store_true")

    b = p.add_argument_group("bootstrap")
    b.add_argument("--quick", action="store_true",
                   help="server-side turnkey admin bootstrap (key born on server, shredded)")
    b.add_argument("--skip-admin-bootstrap", action="store_true")
    b.add_argument("--admin-cn", default=None)
    b.add_argument("--bootstrap-via-curl", action="store_true")

    l = p.add_argument_group("lifecycle / phase control")
    l.add_argument("--repair", action="store_true", help="re-validate all phases, fix what's broken")
    l.add_argument("--from", dest="from_phase", metavar="PHASE", default=None)
    l.add_argument("--only", dest="only_phase", metavar="PHASE", default=None)
    l.add_argument("--stop-after", dest="stop_after", metavar="PHASE", default=None,
                   help="run through PHASE then stop (e.g. 'write' to only generate files)")
    l.add_argument("--force", action="append", metavar="PHASE", default=[],
                   help="re-run PHASE even if done (repeatable)")
    l.add_argument("--rotate-secret", action="append", metavar="NAME", default=[])
    l.add_argument("--uninstall", action="store_true")
    l.add_argument("--purge-volumes", action="store_true")
    l.add_argument("--keep-certs", action="store_true")
    return p


def parse_answer_file(path: str) -> dict:
    out = {}
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, val = line.split("=", 1)
            out[k.strip()] = val.strip()
    return out


def merge_answers(flags, answers: dict):
    """Precedence: explicit CLI flag > answer-file > default. Value flags default to None
    so 'unset' is detectable; booleans use the answer-file only when the CLI left them False."""
    truthy = {"1", "true", "yes", "on"}
    for dest, default in VALUE_DEFAULTS.items():
        if getattr(flags, dest, None) is None:
            key = dest.upper()
            if key in answers:
                val = answers[key]
                setattr(flags, dest, int(val) if dest in INT_FLAGS else val)
            else:
                setattr(flags, dest, default)
    # host + tri-state host_is_ip
    if flags.host is None and "HOST" in answers:
        flags.host = answers["HOST"]
    if flags.host_is_ip is None and "HOST_IS_IP" in answers:
        flags.host_is_ip = answers["HOST_IS_IP"].lower() in truthy
    # booleans the answer file may flip on
    for dest, key in (("assume_yes", "ASSUME_YES"), ("non_interactive", "NON_INTERACTIVE"),
                      ("tunnel", "TUNNEL"), ("convenience", "CONVENIENCE"), ("quick", "QUICK"),
                      ("skip_docker_install", "SKIP_DOCKER_INSTALL"),
                      ("auto_unseal_unit", "AUTO_UNSEAL_UNIT")):
        if not getattr(flags, dest, False) and answers.get(key, "").lower() in truthy:
            setattr(flags, dest, True)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    flags = parser.parse_args(argv)

    answers = parse_answer_file(flags.answers) if flags.answers else {}
    merge_answers(flags, answers)

    install_dir = flags.install_dir
    os.makedirs(install_dir, exist_ok=True)
    log_file = flags.log_file or os.path.join(install_dir, "install.log")

    interactive = sys.stdin.isatty() and not flags.non_interactive and not flags.answers
    log = Log(log_file, verbose=flags.verbose, color=(False if flags.quiet else None))
    proc = Proc(log, dry_run=flags.dry_run)
    state = State.load(os.path.join(install_dir, "install-state.json"))
    prompt = Prompter(log, interactive=interactive, assume_yes=flags.assume_yes)

    ctx = InstallContext(
        install_dir=install_dir, flags=flags, log=log, proc=proc, state=state,
        prompt=prompt, interactive=interactive, dry_run=flags.dry_run)

    # seed cfg from flags so early phases (and uninstall) have prefix/port
    ctx.cfg.update({
        "container_prefix": flags.container_prefix,
        "https_port": flags.https_port,
        "image_tag": flags.version_tag,
        "postgres_user": flags.postgres_user,
        "postgres_db": flags.postgres_db,
    })
    if state.data.get("config"):
        ctx.cfg.update(state.data["config"])  # prior run wins for resume

    log.banner(f"Orcastra Mini installer {__version__}")
    if flags.dry_run:
        log.warn("DRY-RUN: no images pulled, no containers started, no system changes.")

    try:
        if flags.uninstall:
            from .phases import uninstall as un
            un.run(ctx)
            return 0
        return _run_phases(ctx)
    except AbortByUser as exc:
        log.error(str(exc))
        if exc.remediation:
            log.info(exc.remediation)
        return 2
    except InstallError as exc:
        log.error(f"[{exc.phase or '?'}] {exc.message}")
        if exc.remediation:
            print()
            log.info("How to fix: " + exc.remediation)
        log.info(f"Full log: {log_file}")
        return 1
    except KeyboardInterrupt:
        log.error("Interrupted.")
        return 130


def _run_phases(ctx) -> int:
    from .phases import PHASES
    names = [n for n, _ in PHASES]

    if ctx.flags.only_phase and ctx.flags.only_phase not in names:
        raise InstallError(f"unknown --only phase: {ctx.flags.only_phase}",
                           remediation=f"valid phases: {', '.join(names)}")
    if ctx.flags.from_phase and ctx.flags.from_phase not in names:
        raise InstallError(f"unknown --from phase: {ctx.flags.from_phase}",
                           remediation=f"valid phases: {', '.join(names)}")
    if ctx.flags.stop_after and ctx.flags.stop_after not in names:
        raise InstallError(f"unknown --stop-after phase: {ctx.flags.stop_after}",
                           remediation=f"valid phases: {', '.join(names)}")

    start = names.index(ctx.flags.from_phase) if ctx.flags.from_phase else 0
    selected = {names[start:][i] for i in range(len(names) - start)}
    if ctx.flags.only_phase:
        selected = {ctx.flags.only_phase}

    run_anyway = bool(ctx.flags.repair or ctx.flags.only_phase or ctx.flags.from_phase)
    total = len(PHASES)
    for idx, (name, mod) in enumerate(PHASES, 1):
        if name not in selected:
            continue
        if ctx.state.is_done(name) and not run_anyway and name not in ctx.flags.force:
            ctx.log.phase(idx, total, f"{mod.TITLE}  (already done, skipping)")
            continue
        ctx.log.phase(idx, total, mod.TITLE)
        ctx.state.set_phase(name, "running")
        try:
            mod.run(ctx)
        except InstallError as exc:
            if exc.phase is None:
                exc.phase = name
            ctx.state.set_phase(name, "failed")
            raise
        ctx.state.set_phase(name, "done")
        if ctx.flags.stop_after and name == ctx.flags.stop_after:
            ctx.log.info(f"Stopped after phase '{name}' as requested (--stop-after).")
            return 0
    ctx.log.info("")
    return 0

"""Command line: argument parsing, answer merging, context construction and the phase loop.

  orcastra-full [install] [--answers FILE] [--ip-vault ...]   build or resume the deployment
  orcastra-full status | verify | unseal | credentials | uninstall | maintain
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from typing import Dict, List, Optional

from orcastra_core.answers import combined_answers, parse_answer_file
from orcastra_core.errors import AbortByUser, InstallError
from orcastra_core.log import Log
from orcastra_core.proc import Proc
from orcastra_core.prompt import Prompter, open_tty
from orcastra_core.state import State

from . import __version__
from . import config as C
from . import topology as T
from .context import FullContext

COMMANDS = ("install", "status", "verify", "unseal", "credentials", "uninstall", "maintain")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="orcastra-full",
        description="Install and operate Orcastra CMP Full (Authentik, Vault, OpenSearch, CMP) "
                    "on this LXD host.")
    p.add_argument("--version", action="version", version=f"orcastra-full {__version__}")
    p.add_argument("command", nargs="?", default="install", choices=COMMANDS,
                   help="what to do (default: install, which also resumes)")
    g = p.add_argument_group("general")
    g.add_argument("--answers", metavar="FILE", help="KEY=value answer file (implies --non-interactive)")
    g.add_argument("--non-interactive", action="store_true", help="never prompt, fail on missing answers")
    g.add_argument("-y", "--assume-yes", action="store_true", help="answer yes to confirmations")
    g.add_argument("--deploy-dir", default=T.DEPLOY_DIR, help=argparse.SUPPRESS)
    g.add_argument("--verbose", action="store_true")
    g.add_argument("--quiet", action="store_true", help="only report problems (maintain)")
    g.add_argument("--dry-run", action="store_true",
                   help="run the checks and the wizard, print the plan, change nothing")
    lc = p.add_argument_group("phase control")
    lc.add_argument("--from", dest="from_phase", metavar="PHASE")
    lc.add_argument("--only", dest="only_phase", metavar="PHASE")
    lc.add_argument("--force", action="append", default=[], metavar="PHASE",
                    help="re-run PHASE even if it is done (repeatable)")
    lc.add_argument("--repair", action="store_true", help="re-run every phase")
    lc.add_argument("--keep-secrets", action="store_true",
                    help="uninstall: keep the secrets, keys and logs in a renamed directory")
    s = p.add_argument_group("deployment settings (same keys as the answer file)")
    for key, (default, help_) in C.FIELDS.items():
        if key == "ADMIN_PASSWORD":
            continue  # never on the command line (process list), only in the answer file
        s.add_argument("--" + key.lower().replace("_", "-"), dest=key, default=None,
                       metavar=key, help=help_ + (f" (default {default})" if default else ""))
    return p


def _values(flags: argparse.Namespace, state: State):
    """Returns (values, answered): `answered` holds the keys the operator settled (CLI,
    answer file, environment, or a previous run). The wizard asks for everything else in an
    interactive run, offering the default, so every option stays a choice."""
    answers = combined_answers(flags.answers)
    cli = {k: getattr(flags, k, None) for k in C.FIELDS}
    values = C.merge(cli, answers)
    answered = {k for k in C.FIELDS if cli.get(k) not in (None, "") or answers.get(k)}
    for k, v in (state.data.get("config") or {}).items():
        if k not in answered:
            values[k] = v  # previous answers win over defaults on a re-run
            answered.add(k)
    # control keys never come from the environment: an inherited ORCASTRA_ASSUME_YES must
    # not silently confirm an uninstall
    from_file = parse_answer_file(flags.answers) if flags.answers else {}
    if from_file.get("ASSUME_YES", "").lower() in ("1", "true", "yes"):
        flags.assume_yes = True
    return values, answered


def build_context(flags: argparse.Namespace, *, need_install: bool) -> FullContext:
    deploy = flags.deploy_dir
    dry = bool(flags.dry_run)
    if dry and not os.path.isdir(deploy):
        fd, log_file = tempfile.mkstemp(prefix="orcastra-full-dryrun-", suffix=".log")
        os.close(fd)
    else:
        os.makedirs(deploy, mode=0o700, exist_ok=True)
        log_file = os.path.join(deploy, "maintain.log" if flags.command == "maintain" else "install.log")
    log = Log(log_file, verbose=flags.verbose, color=(False if flags.quiet else None),
              name="orcastra_full_install")
    state = State.load(os.path.join(deploy, "state.json"), strict=True, dry_run=dry)
    if need_install and not state.data.get("config"):
        raise InstallError("Nothing is installed on this host yet.",
                           remediation="Run `orcastra-full install` first.")
    values, answered = _values(flags, state)
    noninteractive = flags.non_interactive or bool(flags.answers) or flags.command == "maintain"
    tty = None if noninteractive else open_tty()
    prompt = Prompter(log, interactive=tty is not None, assume_yes=flags.assume_yes, tty=tty)
    ctx = FullContext(deploy_dir=deploy, flags=flags, log=log, proc=Proc(log, dry_run=dry),
                      state=state, prompt=prompt, interactive=tty is not None, dry_run=dry,
                      values=values)
    ctx.answered = answered
    ctx.secrets.load()
    return ctx


def _run_phases(ctx: FullContext) -> int:
    from .phases import ALWAYS, PHASES
    names = [n for n, _ in PHASES]
    for opt in (ctx.flags.only_phase, ctx.flags.from_phase, *ctx.flags.force):
        if opt and opt not in names:
            raise InstallError(f"unknown phase {opt!r}", remediation="valid phases: " + ", ".join(names))
    start = names.index(ctx.flags.from_phase) if ctx.flags.from_phase else 0
    selected = set(names[start:])
    if ctx.flags.only_phase:
        selected = {ctx.flags.only_phase, "preflight", "wizard"}
    if ctx.dry_run:
        selected = {"preflight", "wizard"}
    rerun = ctx.flags.repair or bool(ctx.flags.only_phase) or bool(ctx.flags.from_phase)
    total = len(PHASES)
    for idx, (name, mod) in enumerate(PHASES, 1):
        if name not in selected:
            continue
        if ctx.state.is_done(name) and name not in ALWAYS and not rerun and name not in ctx.flags.force:
            ctx.log.phase(idx, total, f"{mod.TITLE}  (done, skipping)")
            continue
        ctx.log.phase(idx, total, mod.TITLE)
        ctx.state.set_phase(name, "running")
        try:
            mod.run(ctx)
        except InstallError as exc:
            exc.phase = exc.phase or name
            ctx.state.set_phase(name, "failed")
            raise
        ctx.state.set_phase(name, "done")
        if name == "wizard" and not ctx.dry_run and ctx.state.artifact("instances"):
            from . import maintain
            maintain.run(ctx, quiet=True)  # a reboot since the last run may have sealed Vault
    if ctx.dry_run:
        ctx.log.ok("Dry run complete: nothing was created or changed.")
    return 0


def _dispatch(ctx: FullContext) -> int:
    cmd = ctx.flags.command
    if cmd == "install":
        ctx.log.banner(f"Orcastra CMP Full installer {__version__}")
        return _run_phases(ctx)
    from . import commands
    return getattr(commands, cmd)(ctx)


def main(argv: Optional[List[str]] = None) -> int:
    flags = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if os.geteuid() != 0 and not flags.dry_run:
        print("Error: run as root (sudo), the installer drives LXD and writes under "
              f"{flags.deploy_dir}.", file=sys.stderr)
        return 1
    log = None
    try:
        ctx = build_context(flags, need_install=flags.command not in ("install",))
        log = ctx.log
        return _dispatch(ctx)
    except AbortByUser as exc:
        (log.error if log else print)(str(exc.message))
        if exc.remediation:
            (log.info if log else print)(exc.remediation)
        return 2
    except InstallError as exc:
        if log is None:
            print(f"Error: {exc.message}" + (f"\nHow to fix: {exc.remediation}" if exc.remediation else ""),
                  file=sys.stderr)
            return 1
        log.error(f"[{exc.phase or flags.command}] {exc.message}")
        if exc.remediation:
            log.info("How to fix: " + exc.remediation)
        log.info(f"Full log: {log.log_file}. Re-running the same command resumes from here.")
        return 1
    except KeyboardInterrupt:
        (log.error if log else print)("Interrupted. Re-run the same command to resume.")
        return 130

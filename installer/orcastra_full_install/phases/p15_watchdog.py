"""Phase 15 - Host tooling. Installs this installer as `/usr/local/sbin/orcastra-full`
(status, verify, unseal, credentials, orcahub, uninstall keep working after the install) and
the systemd timer that runs `orcastra-full maintain` every minute. Runs on every pass, so a
newer installer replaces the tool and the watchdog code instead of leaving the old ones."""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import zipapp

from orcastra_core.errors import InstallError

TITLE = "Host tooling: orcastra-full command and Vault watchdog timer"

LIB = "/usr/local/lib/orcastra"
PYZ = f"{LIB}/orcastra-full-install.pyz"
WRAPPER = "/usr/local/sbin/orcastra-full"
SERVICE = "/etc/systemd/system/orcastra-maintain.service"
TIMER = "/etc/systemd/system/orcastra-maintain.timer"

_WRAPPER = """#!/bin/sh
# Managed by orcastra-full
export PATH=/snap/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
exec /usr/bin/env python3 {pyz} "$@"
"""

_SERVICE = """[Unit]
Description=Orcastra watchdog: unseal Vault after a restart, renew the dashboard token
After=network-online.target snap.lxd.daemon.service lxd.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={wrapper} maintain --quiet
"""

_TIMER = """[Unit]
Description=Run the Orcastra watchdog every minute

[Timer]
OnBootSec=60
OnUnitActiveSec=60
AccuracySec=10s

[Install]
WantedBy=timers.target
"""


def _self_archive(dest: str) -> None:
    """Copy the running .pyz, or build one from the source tree when run unpacked."""
    me = os.path.abspath(sys.argv[0])
    if me.endswith(".pyz") and os.path.isfile(me):
        if me != dest:
            shutil.copyfile(me, dest)
        return
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # orcastra_full_install
    src = os.path.dirname(root)
    build = tempfile.mkdtemp(prefix="orcastra-pyz-")
    try:
        for pkg in ("orcastra_full_install", "orcastra_core"):
            shutil.copytree(os.path.join(src, pkg), os.path.join(build, pkg),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        with open(os.path.join(build, "__main__.py"), "w", encoding="utf-8") as fh:
            fh.write("import sys\nfrom orcastra_full_install.cli import main\nsys.exit(main())\n")
        zipapp.create_archive(build, target=dest, interpreter="/usr/bin/env python3")
    finally:
        shutil.rmtree(build, ignore_errors=True)


def _write(path: str, body: str, mode: int) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, mode)


def _sha256(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _current(ctx) -> bool:
    """The installed tool is this very archive and the units are the ones we write."""
    me = os.path.abspath(sys.argv[0])
    if not me.endswith(".pyz") or _sha256(me) != _sha256(PYZ):
        return False
    for path, body in ((WRAPPER, _WRAPPER.format(pyz=PYZ)), (SERVICE, _SERVICE.format(wrapper=WRAPPER)),
                       (TIMER, _TIMER)):
        try:
            with open(path, encoding="utf-8") as fh:
                if fh.read() != body:
                    return False
        except OSError:
            return False
    return ctx.proc.run(["systemctl", "is-enabled", "--quiet", "orcastra-maintain.timer"]).ok


def run(ctx) -> None:
    from .. import __version__
    if _current(ctx):
        ctx.log.ok(f"{WRAPPER} and orcastra-maintain.timer are up to date ({__version__})")
        return
    os.makedirs(LIB, mode=0o755, exist_ok=True)
    _self_archive(PYZ)
    os.chmod(PYZ, 0o700)
    _write(WRAPPER, _WRAPPER.format(pyz=PYZ), 0o700)
    _write(SERVICE, _SERVICE.format(wrapper=WRAPPER), 0o644)
    _write(TIMER, _TIMER, 0o644)
    for argv in (["systemctl", "daemon-reload"],
                 ["systemctl", "enable", "--now", "orcastra-maintain.timer"]):
        res = ctx.proc.run(argv)
        if not res.ok:
            raise InstallError(f"{' '.join(argv)} failed: {res.err.strip()[-200:]}")
    res = ctx.proc.run([WRAPPER, "maintain"], timeout=300)
    if not res.ok:
        raise InstallError("The watchdog's first run failed: " + (res.err or res.out)[-400:])
    ctx.log.ok(f"Installed {WRAPPER} {__version__} and orcastra-maintain.timer (every 60s)")

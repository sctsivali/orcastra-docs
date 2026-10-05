"""TCP reachability probes that tell the three outcomes apart: open, refused (allowed by
the firewall, nothing listening) and filtered (silently dropped)."""
from __future__ import annotations

import errno
import socket

from orcastra_core.errors import InstallError

OPEN, REFUSED, FILTERED = "open", "refused", "filtered"

# bash's /dev/tcp needs no extra package in the guest, and exit 124 is the timeout
# `|| rc=$?` keeps a refused/timed-out connect from tripping the remote `set -e`
_REMOTE = ("rc=0; timeout 4 bash -c '</dev/tcp/{ip}/{port}' 2>/dev/null || rc=$?; "
           "if [ $rc -eq 0 ]; then echo open; elif [ $rc -eq 124 ]; then echo filtered; "
           "else echo refused; fi")


def from_host(ip: str, port: int, timeout: float = 4.0) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((ip, port))
        return OPEN
    except socket.timeout:
        return FILTERED
    except OSError as exc:
        if exc.errno in (errno.ECONNREFUSED,):
            return REFUSED
        if exc.errno in (errno.EHOSTUNREACH, errno.ENETUNREACH):
            return FILTERED
        return FILTERED
    finally:
        s.close()


def from_instance(remote, ip: str, port: int) -> str:
    res = remote.run(_REMOTE.format(ip=ip, port=port), check=False, timeout=30)
    word = res.out.strip().splitlines()[-1] if res.out.strip() else ""
    if word not in (OPEN, REFUSED, FILTERED):
        raise InstallError(f"probe from {remote.name} to {ip}:{port} gave no verdict: "
                           f"{(res.err or res.out).strip()[-200:]}")
    return word


def allowed(outcome: str) -> bool:
    return outcome in (OPEN, REFUSED)

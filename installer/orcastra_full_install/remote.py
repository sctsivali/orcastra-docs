"""Run commands and write files inside an instance through `lxc exec`.

Rules that keep secrets out of process lists and temp files:
- the script text is argv, so it must never contain a secret (enforced: a registered
  secret found in a script aborts before anything runs);
- secrets travel on stdin as `export K='v'` lines that the script `eval`s first;
- file contents travel on stdin and are written with umask 077 to a temp name, then
  chmod/chown and an atomic rename.
"""
from __future__ import annotations

import shlex
from typing import Dict, Optional

from orcastra_core.errors import InstallError
from orcastra_core.proc import Result

from .lxd import Lxd

_PRELUDE = ("set -euo pipefail\n"
            "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin "
            "DEBIAN_FRONTEND=noninteractive HOME=/root LC_ALL=C.UTF-8\n")


class RemoteError(InstallError):
    """A command inside an instance failed."""


class Remote:
    def __init__(self, lxd: Lxd, instance: str) -> None:
        self.lxd = lxd
        self.name = instance

    def _guard(self, script: str) -> None:
        for secret in getattr(self.lxd.log.redactor, "_secrets", ()):
            if secret in script:
                raise InstallError("internal error: a secret was about to be placed in argv",
                                   remediation="Report this as an installer bug.")

    def run(self, script: str, *, secrets: Optional[Dict[str, str]] = None,
            stdin: Optional[str] = None, check: bool = True, timeout: int = 900,
            what: str = "", sensitive: bool = False) -> Result:
        """Run a bash script in the instance as root. With `secrets`, stdin carries them
        and cannot also carry other data."""
        self._guard(script)
        body = _PRELUDE
        data = stdin
        if secrets:
            if stdin is not None:
                raise ValueError("secrets and stdin are mutually exclusive")
            body += 'eval "$(cat)"\n'
            data = "".join(f"export {k}={shlex.quote(v)}\n" for k, v in secrets.items())
        body += script
        argv = [self.lxd.lxc, "exec", "--project", self.lxd.project, "--force-noninteractive",
                self.name, "--", "bash", "-c", body]
        res = self.lxd.proc.run(argv, input=data if data is not None else "", mutating=True,
                                timeout=timeout, quiet_output=sensitive)
        if check and not res.ok:
            tail = (res.err.strip() or res.out.strip())[-600:]
            raise RemoteError(f"{what or 'command'} failed on {self.name} (exit {res.rc}): {tail}",
                              remediation=f"Inspect with `lxc exec --project {self.lxd.project} "
                                          f"{self.name} -- bash`, then re-run the installer.")
        return res

    def ok(self, script: str, *, timeout: int = 120) -> bool:
        return self.run(script, check=False, timeout=timeout).ok

    def write(self, path: str, content: str, *, mode: str = "0644",
              owner: str = "root:root", what: str = "") -> None:
        """Atomic write of `content` to `path` inside the instance."""
        q = shlex.quote(path)
        script = (f"install -d -m 0755 \"$(dirname {q})\"\n"
                  "umask 077\n"
                  f"tmp=\"$(mktemp -p \"$(dirname {q})\" .orcastra-tmp.XXXXXXXX)\"\n"
                  "cat > \"$tmp\"\n"
                  f"chown -h {shlex.quote(owner)} \"$tmp\"\n"
                  f"chmod {mode} \"$tmp\"\n"
                  f"mv -f \"$tmp\" {q}\n")
        self.run(script, stdin=content, what=what or f"write {path}")

    def read(self, path: str) -> Optional[str]:
        """File contents (never logged: most files compared this way hold secrets)."""
        res = self.run(f"cat {shlex.quote(path)}", check=False, timeout=60, sensitive=True)
        return res.out if res.ok else None

    def same_content(self, path: str, content: str) -> bool:
        """True when the file already holds exactly `content` (lets re-runs skip restarts)."""
        current = self.read(path)
        return current is not None and current == content

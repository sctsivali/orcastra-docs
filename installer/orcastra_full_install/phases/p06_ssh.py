"""Phase 6 - SSH bastion access. The host is the only SSH client the instances accept.
Host keys are read through `lxc exec` (a channel LXD already authenticates), so the
known_hosts file is exact from the start and StrictHostKeyChecking can stay on."""
from __future__ import annotations

import os
import pwd

from orcastra_core.errors import InstallError
from orcastra_core.fsutil import atomic_write

from .. import topology as T

TITLE = "SSH access from the host"

INCLUDE_MARK = "# orcastra-full"


def ssh_config_text(ctx, key: str, known: str) -> str:
    out = [f"{INCLUDE_MARK}: generated, do not edit (re-run the installer instead)"]
    for role in T.ROLES:
        out += [f"Host {T.name(role)}",
                f"    HostName {ctx.ip(role)}",
                "    User ubuntu",
                f"    IdentityFile {key}",
                "    IdentitiesOnly yes",
                f"    UserKnownHostsFile {known}",
                "    StrictHostKeyChecking yes",
                ""]
    return "\n".join(out)


def ensure_include(home: str, conf: str, uid: int, gid: int) -> str:
    """Put `Include <conf>` at the very top of ~/.ssh/config (an Include further down would
    only apply inside the preceding Host block). Returns the config path."""
    sshdir = os.path.join(home, ".ssh")
    os.makedirs(sshdir, mode=0o700, exist_ok=True)
    cfg = os.path.join(sshdir, "config")
    line = f"Include {conf}  {INCLUDE_MARK}\n"
    current = open(cfg, encoding="utf-8").read() if os.path.exists(cfg) else ""
    if line not in current:
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write(line + current)
    os.chmod(cfg, 0o600)
    os.chown(sshdir, uid, gid)
    os.chown(cfg, uid, gid)
    return cfg


_WRITE_AS_USER = ('umask 077; d="$(dirname "$1")"; mkdir -p "$d"; '
                  't="$(mktemp -p "$d" .orcastra.XXXXXXXX)"; cat > "$t"; chmod "$2" "$t"; mv -f "$t" "$1"')


def _as_user(ctx, user: str, path: str, content: str, mode: str) -> None:
    """Write into another user's home AS that user (runuser), so a symlink they planted
    can only ever point root's write at files they could already change themselves."""
    res = ctx.proc.run(["runuser", "-u", user, "--", "sh", "-c", _WRITE_AS_USER, "_", path, mode],
                       input=content)
    if not res.ok:
        raise InstallError(f"Could not write {path} as {user}: {res.err.strip()[-200:]}")


def _install_for_user(ctx, pw, key_src: str, known: str) -> None:
    home, user = pw.pw_dir, pw.pw_name
    key = os.path.join(home, ".ssh", "orcastra_ed25519")
    kh = os.path.join(home, ".ssh", "orcastra_known_hosts")
    conf = os.path.join(home, ".ssh", "config.d", "orcastra.conf")
    with open(key_src, encoding="utf-8") as fh:
        _as_user(ctx, user, key, fh.read(), "600")
    with open(known, encoding="utf-8") as fh:
        _as_user(ctx, user, kh, fh.read(), "644")
    _as_user(ctx, user, conf, ssh_config_text(ctx, key, kh), "600")
    cfg = os.path.join(home, ".ssh", "config")
    current = ctx.proc.run(["runuser", "-u", user, "--", "sh", "-c", 'cat "$1" 2>/dev/null || true',
                            "_", cfg], quiet_output=True).out
    line = f"Include {conf}  {INCLUDE_MARK}\n"
    if line not in current:
        _as_user(ctx, user, cfg, line + current, "600")
    homes = ctx.state.artifact("ssh_homes") or []
    if home not in homes:
        ctx.state.set_artifact("ssh_homes", homes + [home])


def _install_for_root(ctx, key: str, known: str) -> None:
    confdir = "/root/.ssh/config.d"
    os.makedirs(confdir, mode=0o700, exist_ok=True)
    conf = os.path.join(confdir, "orcastra.conf")
    atomic_write(ctx, conf, ssh_config_text(ctx, key, known), mode=0o600, backup=False)
    ensure_include("/root", conf, 0, 0)
    homes = ctx.state.artifact("ssh_homes") or []
    if "/root" not in homes:
        ctx.state.set_artifact("ssh_homes", homes + ["/root"])


def run(ctx) -> None:
    key = os.path.join(ctx.ssh_dir, "id_ed25519")
    known = os.path.join(ctx.ssh_dir, "known_hosts")
    lines = []
    for role in T.ROLES:
        pub = ctx.remote(role).read("/etc/ssh/ssh_host_ed25519_key.pub")
        if not pub:
            raise InstallError(f"Cannot read the SSH host key of {T.name(role)}.")
        ktype, kdata = pub.split()[:2]
        lines.append(f"{T.name(role)},{ctx.ip(role)} {ktype} {kdata}")
    atomic_write(ctx, known, "\n".join(lines) + "\n", mode=0o644, backup=False)

    _install_for_root(ctx, key, known)
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        try:
            pw = pwd.getpwnam(sudo_user)
        except KeyError:
            pw = None
        if pw and ctx.prompt.confirm(f"Also give {sudo_user} SSH access to the instances "
                                     "(copies the operator key into their ~/.ssh)?", default=False):
            _install_for_user(ctx, pw, key, known)

    conf = "/root/.ssh/config.d/orcastra.conf"
    for role in T.ROLES:
        name = T.name(role)
        res = ctx.proc.run(["ssh", "-F", conf, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                            name, "true"], timeout=30)
        if not res.ok:
            raise InstallError(f"SSH to {name} failed: {res.err.strip()[-300:]}",
                               remediation="Check sshd in the instance and the cloud-init log.")
        res = ctx.proc.run(["ssh", "-F", conf, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                            "-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password",
                            name, "true"], timeout=30)
        if res.ok or "Permission denied" not in res.err:
            raise InstallError(f"{name} did not refuse password login as expected.")
        ctx.log.ok(f"ssh {name} works (key only, host key pinned)")

"""Backups of an installed Mini: a logical database dump, the deployment files, and cold copies
of the data volumes, restorable as one unit.

A cold copy (the stack stopped while the volumes are archived) is what an upgrade restores on
failure: it brings back the database, Vault and Redis exactly as the previous release left them,
which a dump alone cannot do for Vault. The dump is for the operator's own records and for a
restore onto another host.
"""
from __future__ import annotations

import json
import os
import tarfile
import time
from typing import List, Optional

from orcastra_core.errors import InstallError

from . import templates

VOLUMES = ("postgres-data", "vault-data", "redis-data")
# An image every Mini install already has, used to read and write volumes.
TOOL_IMAGE = "postgres:17-alpine"
FILES = (".env", "docker-compose.yml", "config", "install-state.json")
MANIFEST = "manifest.json"


def backups_root(ctx) -> str:
    return os.path.join(ctx.install_dir, "backups")


def list_backups(ctx) -> List[str]:
    root = backups_root(ctx)
    if not os.path.isdir(root):
        return []
    found = [d for d in sorted(os.listdir(root)) if os.path.isfile(os.path.join(root, d, MANIFEST))]
    return [os.path.join(root, d) for d in found]


def env_value(ctx, key: str, default: Optional[str] = None) -> Optional[str]:
    try:
        with open(ctx.env_path, encoding="utf-8") as fh:
            value = templates.get_env_value(fh.read(), key)
    except OSError:
        return default
    return default if value in (None, "") else value


def volume_name(ctx, short: str) -> str:
    return f"{ctx.project}_{short}"


def new_backup_dir(ctx, label: str) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = os.path.join(backups_root(ctx), f"{stamp}-{label}")
    os.makedirs(path, mode=0o700, exist_ok=False)
    return path


def dump_database(ctx, dest: str) -> str:
    """pg_dump -Fc of the running database into dest/db.dump."""
    user = env_value(ctx, "POSTGRES_USER", "orcastra")
    db = env_value(ctx, "POSTGRES_DB", "orcastra")
    inside = "/tmp/orcastra-mini-backup.dump"
    res = ctx.proc.run(ctx.compose_argv("exec", "-T", "postgres", "pg_dump", "-U", user, "-d", db,
                                        "-Fc", "-f", inside), mutating=True, timeout=1800)
    if not res.ok:
        raise InstallError("the database dump failed", remediation=res.combined.strip()[-400:])
    target = os.path.join(dest, "db.dump")
    cp = ctx.proc.run(["docker", "cp", f"{ctx.project}-postgres:{inside}", target], mutating=True, timeout=600)
    ctx.proc.run(ctx.compose_argv("exec", "-T", "postgres", "rm", "-f", inside), mutating=True)
    if not cp.ok:
        raise InstallError("the database dump could not be copied out", remediation=cp.combined.strip()[-400:])
    os.chmod(target, 0o600)
    return target


def _regular_only(member: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
    # config/backend is written by the backend container: a link planted there must not enter a
    # backup that root later restores.
    return member if member.isfile() or member.isdir() else None


def archive_files(ctx, dest: str) -> str:
    target = os.path.join(dest, "files.tar.gz")
    with tarfile.open(target, "w:gz") as tar:
        for name in FILES:
            path = os.path.join(ctx.install_dir, name)
            if os.path.exists(path) and not os.path.islink(path):
                tar.add(path, arcname=name, filter=_regular_only)
    os.chmod(target, 0o600)
    return target


def _inside(root: str, name: str) -> bool:
    real_root = os.path.realpath(root)
    # realpath also follows links already on disk: a directory swapped for a link to /root after
    # the backup resolves outside the install dir and is refused.
    target = os.path.realpath(os.path.join(root, name))
    return target == real_root or target.startswith(real_root + os.sep)


def restore_files(ctx, src: str) -> None:
    archive = os.path.join(src, "files.tar.gz")
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            # Only the deployment's own regular files and directories, never a link, a device, or
            # a path that lands outside the install dir.
            if not (member.isfile() or member.isdir()) or not _inside(ctx.install_dir, member.name):
                raise InstallError(f"refusing to restore {member.name!r} from {archive}")
        if hasattr(tarfile, "data_filter"):
            tar.extractall(ctx.install_dir, members=members, filter="data")
        else:  # Python without extraction filters: the checks above are the filter
            tar.extractall(ctx.install_dir, members=members)


def _volume_exists(ctx, volume: str) -> bool:
    return ctx.proc.run(["docker", "volume", "inspect", volume]).ok


def snapshot_volumes(ctx, dest: str) -> List[str]:
    """Archive each data volume while the stack is stopped. Returns the volumes archived."""
    done = []
    for short in VOLUMES:
        volume = volume_name(ctx, short)
        if not _volume_exists(ctx, volume):
            continue
        res = ctx.proc.run(["docker", "run", "--rm", "--network", "none",
                            "-v", f"{volume}:/v:ro", "-v", f"{dest}:/b",
                            "--entrypoint", "tar", TOOL_IMAGE, "-C", "/v", "-cf", f"/b/{short}.tar", "."],
                           mutating=True, timeout=3600)
        if not res.ok:
            raise InstallError(f"the {volume} volume could not be archived", remediation=res.combined.strip()[-400:])
        os.chmod(os.path.join(dest, f"{short}.tar"), 0o600)
        done.append(short)
    return done


def restore_volumes(ctx, src: str) -> None:
    for short in VOLUMES:
        archive = os.path.join(src, f"{short}.tar")
        if not os.path.isfile(archive):
            continue
        volume = volume_name(ctx, short)
        ctx.proc.run(["docker", "volume", "create", volume], mutating=True)
        res = ctx.proc.run(["docker", "run", "--rm", "--network", "none",
                            "-v", f"{volume}:/v", "-v", f"{src}:/b:ro",
                            "--entrypoint", "sh", TOOL_IMAGE, "-c",
                            f"find /v -mindepth 1 -delete && tar -C /v -xpf /b/{short}.tar"],
                           mutating=True, timeout=3600)
        if not res.ok:
            raise InstallError(f"the {volume} volume could not be restored", remediation=res.combined.strip()[-400:])


def write_manifest(dest: str, **fields) -> None:
    path = os.path.join(dest, MANIFEST)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(fields, fh, indent=2, sort_keys=True)
    os.chmod(path, 0o600)


def read_manifest(src: str) -> dict:
    with open(os.path.join(src, MANIFEST), encoding="utf-8") as fh:
        return json.load(fh)


def volumes_size_bytes(ctx) -> int:
    total = 0
    for short in VOLUMES:
        volume = volume_name(ctx, short)
        if not _volume_exists(ctx, volume):
            continue
        res = ctx.proc.run(["docker", "run", "--rm", "--network", "none", "-v", f"{volume}:/v:ro",
                            "--entrypoint", "du", TOOL_IMAGE, "-sk", "/v"], timeout=600)
        if res.ok and res.out.split():
            total += int(res.out.split()[0]) * 1024
    return total

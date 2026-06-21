"""Filesystem helpers: atomic writes with timestamped backups, honoring dry-run."""
import os
import tempfile
import time


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def backup_path(path: str) -> str:
    return f"{path}.bak.{int(time.time())}"


def atomic_write(ctx, path: str, content: str, *, mode: int = 0o644, backup: bool = True):
    """Write `content` to `path` atomically (write temp in same dir, then os.replace).
    Backs up an existing target to `<path>.bak.<ts>` first. Returns the backup path or None."""
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    bkp = None
    if backup and os.path.exists(path):
        bkp = backup_path(path)
        if ctx.dry_run:
            ctx.log.detail(f"[dry-run] would back up {path} -> {bkp}")
        else:
            with open(path, "rb") as src, open(bkp, "wb") as dst:
                dst.write(src.read())
            os.chmod(bkp, mode)
            ctx.log.detail(f"backed up {os.path.basename(path)} -> {os.path.basename(bkp)}")
    if ctx.dry_run:
        ctx.log.detail(f"[dry-run] would write {path} ({len(content)} bytes, mode {oct(mode)})")
        return bkp
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    ctx.log.detail(f"wrote {path} (mode {oct(mode)})")
    return bkp

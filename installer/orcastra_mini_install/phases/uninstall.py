"""Uninstall path. Stops the stack and removes generated files. Volumes (Postgres data,
Vault raft/PKI, Redis) are kept unless --purge-volumes is given with an explicit confirm.
The Vault keys file is never auto-deleted - losing it orphans the encrypted Vault data."""
import os

from ..errors import AbortByUser
from ..fsutil import backup_path

TITLE = "Uninstall"


def run(ctx):
    if not os.path.exists(ctx.compose_path):
        ctx.log.warn(f"No docker-compose.yml in {ctx.install_dir}; nothing to stop.")
    else:
        purge = ctx.flags.purge_volumes
        if purge:
            ctx.log.warn("--purge-volumes will DESTROY the database, the Vault PKI CA, and the "
                         "tamper-evident audit chain. This is irreversible.")
            if not ctx.prompt.confirm("Permanently delete all data volumes?", default=False):
                raise AbortByUser("Volume purge declined.")
        args = ["down"] + (["-v"] if purge else [])
        res = ctx.proc.run(ctx.compose_argv(*args), mutating=True, timeout=300)
        if res.ok:
            ctx.log.ok("Stack stopped" + (" and volumes removed." if purge else " (volumes kept)."))
        else:
            ctx.log.warn("docker compose down reported an error:")
            ctx.log.detail(res.combined[-300:])

    removed, kept = [], []
    targets = [ctx.env_path, ctx.compose_path, ctx.nginx_conf_path, ctx.vault_hcl_path,
               ctx.state_path, ctx.bootstrap_helper_path, ctx.bootstrap_close_path]
    if not ctx.flags.keep_certs:
        targets += [ctx.server_crt, ctx.server_key]
    else:
        kept.append(ctx.nginx_certs_dir)

    for path in targets:
        if os.path.exists(path):
            _backup_and_remove(ctx, path)
            removed.append(os.path.basename(path))

    if os.path.exists(ctx.vault_keys_path):
        kept.append(ctx.vault_keys_path)
        ctx.log.warn(f"Kept Vault keys file: {ctx.vault_keys_path} - delete it manually if the "
                     "deployment is truly gone (it cannot be recovered).")
    if os.path.exists(ctx.admin_p12_path):
        kept.append(ctx.admin_p12_path)

    ctx.log.ok(f"Removed: {', '.join(removed) or '(nothing)'}")
    if kept:
        ctx.log.info("Kept: " + ", ".join(kept))


def _backup_and_remove(ctx, path):
    if ctx.dry_run:
        ctx.log.detail(f"[dry-run] would back up + remove {path}")
        return
    bkp = backup_path(path)
    try:
        with open(path, "rb") as src, open(bkp, "wb") as dst:
            dst.write(src.read())
        os.chmod(bkp, 0o600)
    except OSError:
        pass
    os.unlink(path)

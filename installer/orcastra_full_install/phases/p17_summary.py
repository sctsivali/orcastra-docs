"""Phase 17 - Summary. Writes credentials.txt (0600) next to the other secrets and prints
where everything is and what to do next."""
from __future__ import annotations

import os

from orcastra_core.fsutil import atomic_write

from .. import topology as T

TITLE = "Summary"


def credentials_text(ctx) -> str:
    s = ctx.secrets
    lines = [
        "Orcastra CMP Full - credentials (keep this file private)",
        "",
        f"Orcastra CMP            {ctx.public_url('cmp')}",
        f"  sign in as akadmin (Authentik), password: {s.get('admin_password')}",
        f"Authentik admin         {ctx.public_url('authentik')}/if/admin/",
        f"OpenSearch Dashboards   {ctx.public_url('logs')}",
        f"  user admin, password: {s.get('os_admin_password')}",
        f"  user audit_viewer, password: {s.get('os_audit_viewer_password')}",
        f"CMP API                 {ctx.public_url('api')}",
        "",
        f"Vault (private only)    http://{ctx.ip('vault')}:8200",
        f"  unseal keys + root token: {ctx.vault_init_path}",
        "",
        "SSH (from this host):   " + "  ".join(f"ssh {T.name(r)}" for r in T.ROLES),
    ]
    return "\n".join(lines) + "\n"


def run(ctx) -> None:
    path = ctx.path("credentials.txt")
    atomic_write(ctx, path, credentials_text(ctx), mode=0o600, backup=False)
    log = ctx.log
    log.banner("Orcastra CMP Full is installed")
    log.info(f"Open {ctx.public_url('cmp')} and sign in as akadmin.")
    log.info(f"Credentials: {path}   (or run `orcastra-full credentials`)")
    log.info("")
    log.info("Instances:")
    for role in T.ROLES:
        log.info(f"  {T.name(role):18} {ctx.ip(role):15} ssh {T.name(role)}")
    log.info("")
    log.info("From a laptop, through this host:")
    log.info(f"  ssh -J <you>@{ctx.host_address} -i <key> ubuntu@{ctx.ip('vault')}   "
             "(add your key to the instances first)")
    log.info("")
    log.info("Day-2 commands: orcastra-full status | verify | unseal | credentials | uninstall")
    log.info("")
    log.warn("The web endpoints are plain HTTP. Keep them on a trusted network or VPN.")
    log.warn(f"Copy {ctx.vault_init_path} to offline storage now. It is the only way to "
             "unseal Vault if this host is lost.")
    log.info("Next: register a cluster in the CMP, then create users in Authentik and add them "
             "to role_admin, role_partner or role_tenant.")
    if os.path.exists(ctx.log.log_file or ""):
        log.info(f"Install log: {ctx.log.log_file}")

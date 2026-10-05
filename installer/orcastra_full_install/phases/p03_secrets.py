"""Phase 3 - Secrets. Creates every credential once (reused on re-runs) and the operator
SSH key the instances will trust. All of it lives under the deployment dir (0700)."""
from __future__ import annotations

import os

from orcastra_core.errors import InstallError

TITLE = "Secrets and operator SSH key"


def run(ctx) -> None:
    os.makedirs(ctx.deploy_dir, mode=0o700, exist_ok=True)
    os.chmod(ctx.deploy_dir, 0o700)
    store = ctx.secrets
    wanted = ctx.values.get("ADMIN_PASSWORD")
    if wanted:
        if store.maybe("admin_password") not in (None, wanted):
            ctx.log.info("A new akadmin password was given, it will be applied in the Authentik phase.")
        store.put("admin_password", wanted)
    store.ensure_all()
    ctx.log.ok(f"{len(store.data)} credentials ready in {store.path} (mode 0600)")

    os.makedirs(ctx.ssh_dir, mode=0o700, exist_ok=True)
    key = os.path.join(ctx.ssh_dir, "id_ed25519")
    if not os.path.exists(key):
        res = ctx.proc.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
                            "orcastra-operator", "-f", key])
        if not res.ok:
            raise InstallError("ssh-keygen failed: " + res.err.strip()[-200:])
        ctx.log.ok("Generated the operator SSH key (ed25519)")
    else:
        ctx.log.ok("Operator SSH key already exists, reusing it")
    os.chmod(key, 0o600)

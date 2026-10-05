"""Day-2 sub-commands that work on an existing deployment."""
from __future__ import annotations

from orcastra_core.errors import VaultError

from . import maintain as M
from . import status as S
from . import vault_api as V
from .phases import p16_verify, p17_summary, uninstall as U


def status(ctx) -> int:
    return S.run(ctx)


def verify(ctx) -> int:
    p16_verify.run(ctx)
    return 0


def maintain(ctx) -> int:
    return M.run(ctx, quiet=bool(ctx.flags.quiet))


def unseal(ctx) -> int:
    st = V.seal_status(ctx)
    if not st.get("sealed", True):
        ctx.log.ok("Vault is already unsealed")
        return 0
    init = V.load_init(ctx)
    keys = V.unseal_keys(init) if init else []
    if not keys:
        if not ctx.interactive:
            raise VaultError("No unseal keys on this host.", remediation="Run interactively to type them.")
        need = int(st.get("t") or 3)
        keys = [ctx.prompt.ask_secret(f"Unseal key {i + 1}/{need}", confirm=False) for i in range(need)]
    if not V.unseal(ctx, keys):
        raise VaultError("Vault is still sealed after the given keys.")
    ctx.log.ok("Vault unsealed")
    if M.restart_backend(ctx):
        ctx.log.ok("Restarted the CMP backend so it reloads the cluster list")
    return 0


def credentials(ctx) -> int:
    print(p17_summary.credentials_text(ctx))
    return 0


def uninstall(ctx) -> int:
    U.run(ctx)
    return 0

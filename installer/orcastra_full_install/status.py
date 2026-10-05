"""`orcastra-full status`: one screen with instance state, container health, Vault seal
state, token TTL and certificate expiry. Read-only."""
from __future__ import annotations

import os

from . import compose, pki
from . import topology as T
from . import vault_api as V
from .httpapi import HttpError
from .phases import p13_cmp

_STACKS = {
    "authentik": (f"{T.REMOTE_DIR}/authentik", "authentik", ""),
    "opensearch": (f"{T.REMOTE_DIR}/opensearch", "opensearch", ""),
    "cmp": (p13_cmp.DIR, T.COMPOSE_PROJECT, p13_cmp.FILES),
}


def run(ctx) -> int:
    bad = 0
    log = ctx.log
    log.banner("Orcastra CMP Full status")
    for role in T.ROLES:
        st = ctx.lxd.status(T.name(role)) or "missing"
        (log.ok if st == "Running" else log.error)(f"{T.name(role):18} {ctx.ip(role):15} {st}")
        bad += st != "Running"
        if st == "Running" and role in _STACKS:
            d, proj, f = _STACKS[role]
            for row in compose.ps(ctx.remote(role), d, proj, f):
                health = row.get("Health") or row.get("State", "")
                good = health in ("healthy", "running")
                (log.ok if good else log.error)(f"    {row.get('Service', '?'):24} {health}")
                bad += not good
    try:
        seal = V.seal_status(ctx)
        sealed = seal.get("sealed", True)
        (log.error if sealed else log.ok)(f"Vault {'SEALED' if sealed else 'unsealed'} "
                                          f"(storage {seal.get('storage_type')}, version {seal.get('version')})")
        bad += bool(sealed)
        tok = ctx.secrets.maybe("vault_dashboard_token")
        if tok and not sealed:
            info = V.lookup_self(ctx, tok)
            ttl_h = int(info.get("ttl") or 0) // 3600
            (log.ok if ttl_h > 72 else log.warn)(f"Dashboard token TTL {ttl_h}h (renewed by the watchdog)")
    except HttpError as exc:
        log.error(f"Vault unreachable: {exc}")
        bad += 1
    timer = ctx.proc.run(["systemctl", "is-active", "orcastra-maintain.timer"])
    (log.ok if timer.out.strip() == "active" else log.warn)(f"Watchdog timer: {timer.out.strip() or 'unknown'}")
    node = os.path.join(ctx.pki_dir, "node.pem")
    if os.path.exists(node):
        log.ok(f"OpenSearch node certificate valid until {pki.expiry(node)}")
    for svc in ("cmp", "authentik", "api", "logs"):
        log.info(f"{svc:10} {ctx.public_url(svc)}")
    return 1 if bad else 0

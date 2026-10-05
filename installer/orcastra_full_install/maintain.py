"""The host watchdog (`orcastra-full maintain`, run every minute by a systemd timer).

Vault seals itself on every restart and nothing else in the stack notices: the CMP keeps
serving the cluster list it read at its own startup, and registration and certificate
issuance quietly fail. The watchdog unseals Vault with the keys held on this host, then
restarts the CMP backend so it re-reads the clusters. It also renews the dashboard
token, which is periodic and would otherwise expire."""
from __future__ import annotations

import fcntl
import json
import os
import time
from typing import Dict

from . import topology as T
from . import vault_api as V
from .httpapi import HttpError

RENEW_EVERY = 6 * 3600       # check the token TTL at most every 6 hours
RENEW_BELOW = 15 * 24 * 3600  # renew when less than 15 days are left of the 30-day period


def _stamp_path(ctx) -> str:
    return ctx.path("maintain.json")


def _stamps(ctx) -> Dict[str, float]:
    try:
        with open(_stamp_path(ctx), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_stamps(ctx, data: Dict[str, float]) -> None:
    tmp = _stamp_path(ctx) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, _stamp_path(ctx))


def restart_backend(ctx) -> bool:
    from .phases.p13_cmp import DIR, FILE
    res = ctx.remote("cmp").run(
        f"cd {DIR} && docker compose -p {T.COMPOSE_PROJECT} -f {FILE} restart backend",
        check=False, timeout=300)
    return res.ok


def run(ctx, *, quiet: bool = False, force_renew: bool = False) -> int:
    """Returns 0 when everything is fine (or was fixed), 1 when Vault could not be fixed."""
    if not os.path.exists(ctx.vault_init_path):
        return 0
    lock = open(ctx.path(".maintain.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0  # another run (timer or installer) is already on it
    try:
        try:
            st = V.seal_status(ctx)
        except HttpError as exc:
            ctx.log.warn(f"Vault at {ctx.ip('vault')}:8200 is unreachable: {exc}")
            return 1
        if st.get("sealed", True) and st.get("initialized"):
            init = V.load_init(ctx)
            if not init:
                ctx.log.error("Vault is sealed and the unseal keys are not on this host.")
                return 1
            if not V.unseal(ctx, V.unseal_keys(init)):
                ctx.log.error("Vault stayed sealed after applying the unseal keys.")
                return 1
            V.wait_active(ctx)
            ctx.log.ok("Vault was sealed, unsealed it with the host-held keys")
            if ctx.lxd.status(T.name("cmp")) == "Running" and restart_backend(ctx):
                ctx.log.ok("Restarted the CMP backend so it reloads the cluster list from Vault")
        elif not quiet:
            ctx.log.ok("Vault is unsealed")

        token = ctx.secrets.maybe("vault_dashboard_token")
        stamps = _stamps(ctx)
        if token and (force_renew or time.time() - stamps.get("token_check", 0) > RENEW_EVERY):
            try:
                info = V.lookup_self(ctx, token)
                ttl = int(info.get("ttl") or 0)
                if force_renew or ttl < RENEW_BELOW:
                    auth = V.renew_self(ctx, token)
                    ctx.log.ok(f"Renewed the dashboard token (TTL now {auth.get('lease_duration', 0) // 3600}h)")
                elif not quiet:
                    ctx.log.ok(f"Dashboard token TTL {ttl // 3600}h, no renewal needed")
                stamps["token_check"] = time.time()
                _save_stamps(ctx, stamps)
            except HttpError as exc:
                ctx.log.error(f"Dashboard token check failed: {exc}")
                return 1
        return 0
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()

"""Vault operations over its HTTP API from the host. Using the API (not the vault CLI in
the guest) keeps unseal keys and tokens out of every process list."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from orcastra_core.errors import VaultError
from orcastra_core.fsutil import atomic_write

from .httpapi import Client, HttpError

POLICY_NAME = "orcastra-policy"
TOKEN_PERIOD = "720h"


def client(ctx, token: Optional[str] = None, timeout: float = 15.0) -> Client:
    headers = {"X-Vault-Token": token} if token else {}
    return Client(f"http://{ctx.ip('vault')}:8200", headers=headers, timeout=timeout)


def seal_status(ctx) -> Dict[str, Any]:
    return client(ctx).get("/v1/sys/seal-status") or {}


def load_init(ctx) -> Optional[Dict[str, Any]]:
    if not os.path.exists(ctx.vault_init_path):
        return None
    with open(ctx.vault_init_path, encoding="utf-8") as fh:
        data = json.load(fh)
    for k in data.get("keys_base64", []) + [data.get("root_token", "")]:
        ctx.log.add_secret(k)
    return data


def initialize(ctx, shares: int = 5, threshold: int = 3) -> Dict[str, Any]:
    """Init Vault and persist the result atomically BEFORE doing anything else with it: a
    crash after init but before saving would make the data unrecoverable."""
    data = client(ctx, timeout=60).put("/v1/sys/init",
                                       {"secret_shares": shares, "secret_threshold": threshold})
    for k in data.get("keys_base64", []) + [data.get("root_token", "")]:
        ctx.log.add_secret(k)
    atomic_write(ctx, ctx.vault_init_path, json.dumps(data, indent=2), mode=0o600, backup=False)
    return data


def unseal_keys(init: Dict[str, Any]) -> List[str]:
    """Only as many shares as the threshold: a server that keeps answering "sealed" (a
    broken or impersonated Vault) must not collect every share."""
    threshold = int(init.get("keys_threshold") or 3)
    return list(init["keys_base64"][:threshold])


def unseal(ctx, keys: List[str]) -> bool:
    c = client(ctx)
    for key in keys:
        st = c.put("/v1/sys/unseal", {"key": key}) or {}
        if not st.get("sealed", True):
            return True
    return not seal_status(ctx).get("sealed", True)


def ensure_mount(c: Client, path: str, mtype: str, options: Optional[Dict[str, str]] = None,
                 max_ttl: Optional[str] = None) -> bool:
    mounts = c.get("/v1/sys/mounts") or {}
    mounts = mounts.get("data", mounts)
    if f"{path}/" in mounts:
        return False
    body: Dict[str, Any] = {"type": mtype}
    if options:
        body["options"] = options
    if max_ttl:
        body["config"] = {"max_lease_ttl": max_ttl}
    c.post(f"/v1/sys/mounts/{path}", body)
    return True


def setup_pki(ctx, root: str) -> None:
    """kv-v2 at secret/, root CA at pki/, intermediate at pki_int/, role lxd
    (docs/deployment/vm2-vault.md steps 4 and 5)."""
    c = client(ctx, root, timeout=60)
    ensure_mount(c, "secret", "kv", {"version": "2"})
    if ensure_mount(c, "pki", "pki", max_ttl="87600h") or not _has_ca(c, "pki"):
        c.post("/v1/pki/root/generate/internal",
               {"common_name": "Orcastra Root CA", "ttl": "87600h"})
    if ensure_mount(c, "pki_int", "pki", max_ttl="43800h") or not _has_ca(c, "pki_int"):
        csr = c.post("/v1/pki_int/intermediate/generate/internal",
                     {"common_name": "Orcastra Intermediate CA"})["data"]["csr"]
        cert = c.post("/v1/pki/root/sign-intermediate",
                      {"csr": csr, "format": "pem_bundle", "ttl": "43800h"})["data"]["certificate"]
        c.post("/v1/pki_int/intermediate/set-signed", {"certificate": cert})
    c.post("/v1/pki_int/roles/lxd", {
        "allowed_domains": "orcastra.io,lxd.local", "allow_subdomains": True,
        "allow_any_name": True, "max_ttl": "8760h", "key_type": "ec", "key_bits": 384})


def _has_ca(c: Client, mount: str) -> bool:
    try:
        status, body = c.request("GET", f"/v1/{mount}/ca/pem", ok=(200, 204, 404))
        return status == 200 and bool(body)
    except HttpError:
        return False


def write_policy(ctx, root: str, text: str) -> None:
    client(ctx, root).put(f"/v1/sys/policies/acl/{POLICY_NAME}", {"policy": text})


def create_dashboard_token(ctx, root: str, period: str = TOKEN_PERIOD) -> str:
    """A periodic orphan token with the dashboard policy. It keeps the `default` policy,
    which grants renew-self, so the host watchdog can renew it forever."""
    data = client(ctx, root).post("/v1/auth/token/create-orphan", {
        "policies": [POLICY_NAME], "period": period, "display_name": "orcastra-dashboard",
        "renewable": True})
    return data["auth"]["client_token"]


def lookup_self(ctx, token: str) -> Dict[str, Any]:
    return (client(ctx, token).get("/v1/auth/token/lookup-self") or {}).get("data", {})


def renew_self(ctx, token: str) -> Dict[str, Any]:
    return (client(ctx, token).post("/v1/auth/token/renew-self", {}) or {}).get("auth", {})


def enable_audit(ctx, root: str) -> None:
    c = client(ctx, root)
    devices = c.get("/v1/sys/audit") or {}
    devices = devices.get("data", devices)
    if "file/" not in devices:
        c.put("/v1/sys/audit/file", {"type": "file",
                                     "options": {"file_path": "/var/log/vault/audit.log"}})


def test_issue(ctx, token: str, root: str) -> str:
    """Issue a certificate the way the CMP does (scoped token), then revoke it with root,
    since the dashboard policy deliberately has no revoke path."""
    data = client(ctx, token).post("/v1/pki_int/issue/lxd", {
        "common_name": "orcastra-installer-selftest.lxd.local", "ttl": "5m"})["data"]
    client(ctx, root).post("/v1/pki_int/revoke", {"serial_number": data["serial_number"]})
    return data["serial_number"]


def wait_active(ctx, timeout: int = 120) -> bool:
    """After an unseal, raft needs a few seconds to elect itself leader. /sys/health answers
    200 only on the active node (429 standby, 503 sealed, 501 uninitialized)."""
    from orcastra_core.retry import wait_until
    c = client(ctx)
    return wait_until(lambda: c.request("GET", "/v1/sys/health", ok=(200,))[0] == 200,
                      timeout=timeout, interval=2)


def require_unsealed(ctx) -> None:
    st = seal_status(ctx)
    if st.get("sealed", True):
        raise VaultError("Vault is sealed.", remediation="Run `orcastra-full unseal`.")

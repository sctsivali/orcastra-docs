"""Phase 9 - Vault on orca-vault (docs/deployment/vm2-vault.md, steps 1 to 6).

Differences from the manual guide, each fixing a real problem:
- integrated `raft` storage (the guide's vault.hcl has no storage stanza);
- the dashboard token is periodic and renewed by the host watchdog (a non-root token
  created with -ttl=0 silently gets the 768h default and expires after ~32 days);
- init output is saved on the host before anything else, then Vault is unsealed and
  configured over its HTTP API from the host.
"""
from __future__ import annotations

from orcastra_core.errors import VaultError
from orcastra_core.retry import wait_until

from .. import _blocks
from .. import topology as T
from .. import vault_api as V
from ..httpapi import HttpError

TITLE = "Vault: install, init, unseal, PKI, policy, token, audit"

VAULT_HCL = """# Managed by orcastra-full
ui            = true
disable_mlock = true
api_addr      = "http://{ip}:8200"
cluster_addr  = "http://{ip}:8201"

storage "raft" {{
  path    = "/opt/vault/data"
  node_id = "orca-vault"
}}

# HTTP listener on the private bridge only (see docs/deployment/vm2-vault.md, TLS note)
listener "tcp" {{
  address         = "0.0.0.0:8200"
  cluster_address = "0.0.0.0:8201"
  tls_disable     = 1
}}
"""

_INSTALL = r"""
if ! dpkg -s vault >/dev/null 2>&1 || [ "$(dpkg-query -W -f '${{Version}}' vault)" != "{ver}" ]; then
  for i in 1 2 3 4 5; do
    curl -fsSL https://apt.releases.hashicorp.com/gpg | gpg --dearmor --yes -o /usr/share/keyrings/hashicorp-archive-keyring.gpg && break
    sleep $((i * 3))
  done
  . /etc/os-release
  echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com ${{UBUNTU_CODENAME:-$VERSION_CODENAME}} main" > /etc/apt/sources.list.d/hashicorp.list
  for i in 1 2 3 4 5; do
    apt-get update -q && apt-get install -y -q --allow-downgrades "vault={ver}" && break
    sleep $((i * 5))
  done
  apt-mark hold vault >/dev/null
fi
dpkg -s vault >/dev/null
install -d -o vault -g vault -m 0750 /opt/vault/data /var/log/vault
"""

_MEMLOCK_DROPIN = r"""
lim="$(ulimit -Hl)"; [ "$lim" = unlimited ] && lim=infinity || lim="${lim}K"
install -d /etc/systemd/system/vault.service.d
printf '[Service]\nLimitMEMLOCK=%s\nAmbientCapabilities=\n' "$lim" > /etc/systemd/system/vault.service.d/orcastra.conf
systemctl daemon-reload
"""


def _install(ctx) -> None:
    r = ctx.remote("vault")
    r.run(_INSTALL.format(ver=T.PINS["vault_apt"]), what="install Vault", timeout=1200)
    if not ctx.is_vm:
        r.run(_MEMLOCK_DROPIN, what="Vault memlock drop-in")
    hcl = VAULT_HCL.format(ip=ctx.ip("vault"))
    changed = not r.same_content("/etc/vault.d/vault.hcl", hcl)
    if changed:
        r.write("/etc/vault.d/vault.hcl", hcl, mode="0640", owner="vault:vault", what="vault.hcl")
    r.run("systemctl enable vault >/dev/null 2>&1; "
          + ("systemctl restart vault" if changed else "systemctl start vault"),
          what="start Vault")
    if not wait_until(lambda: "initialized" in V.seal_status(ctx), timeout=120, interval=3):
        log = r.run("journalctl -u vault -n 30 --no-pager", check=False).out
        raise VaultError("Vault did not start listening on :8200.\n" + log[-800:],
                         remediation="Fix the error above in orca-vault, then re-run.")


def _init_unseal(ctx) -> dict:
    st = V.seal_status(ctx)
    init = V.load_init(ctx)
    if not st.get("initialized"):
        init = V.initialize(ctx)
        ctx.log.ok(f"Vault initialized (5 key shares, threshold 3), saved to {ctx.vault_init_path}")
    elif init is None:
        raise VaultError("Vault is already initialized but its unseal keys are not on this host.",
                         remediation="Restore vault-init.json into the deployment dir, or delete the "
                                     "orca-vault instance and re-run to start Vault from scratch.")
    if V.seal_status(ctx).get("sealed", True):
        if not V.unseal(ctx, V.unseal_keys(init)):
            raise VaultError("Vault stayed sealed after applying the unseal keys.")
    if not V.wait_active(ctx):
        raise VaultError("Vault is unsealed but did not become the active node within 2 minutes.",
                         remediation="Check `journalctl -u vault` in orca-vault.")
    ctx.log.ok("Vault is unsealed and active")
    return init


def _token(ctx, root: str) -> str:
    tok = ctx.secrets.maybe("vault_dashboard_token")
    if tok:
        try:
            info = V.lookup_self(ctx, tok)
            if V.POLICY_NAME in info.get("policies", []):
                return tok
        except HttpError as exc:
            if exc.status not in (403, 400):
                raise
            ctx.log.warn("The stored dashboard token is no longer valid, creating a new one.")
    tok = V.create_dashboard_token(ctx, root)
    ctx.secrets.put("vault_dashboard_token", tok)
    ctx.state.set_phase("cmp", "pending")  # the CMP .env must pick up the new token
    return tok


def run(ctx) -> None:
    _install(ctx)
    init = _init_unseal(ctx)
    root = init["root_token"]
    V.setup_pki(ctx, root)
    ctx.log.ok("Secrets engine secret/ (kv-v2), PKI pki/ + pki_int/, role lxd")
    V.write_policy(ctx, root, _blocks.VAULT_POLICY)
    tok = _token(ctx, root)
    info = V.lookup_self(ctx, tok)
    if "root" in info.get("policies", []) or not info.get("period") or not info.get("renewable"):
        raise VaultError(f"The dashboard token is not a renewable periodic non-root token: {info}")
    ctx.log.ok(f"Dashboard token: policy {V.POLICY_NAME}, period {info['period'] // 3600}h, renewable")

    r = ctx.remote("vault")
    V.enable_audit(ctx, root)
    if not r.same_content("/etc/logrotate.d/vault-audit", _blocks.VAULT_LOGROTATE):
        r.write("/etc/logrotate.d/vault-audit", _blocks.VAULT_LOGROTATE, what="audit logrotate")
    ctx.log.ok("Audit device file -> /var/log/vault/audit.log (logrotate daily)")
    serial = V.test_issue(ctx, tok, root)
    ctx.log.ok(f"PKI self-test: issued and revoked a certificate with the dashboard token ({serial[:23]}...)")

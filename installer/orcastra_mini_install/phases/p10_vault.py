"""Phase 10 - Vault init, unseal, and PKI.

Two modes:
  GUIDED (default)  - the unseal keys + root token are shown once and never written to disk;
                      the operator must save them and unseal manually after each reboot.
  CONVENIENCE       - keys + token are persisted to vault-init.json (0600) and an optional
                      systemd unit auto-unseals on boot. Easier, weaker security.

The root token is written to .env as VAULT_TOKEN (as the manual guide does: it is useless
while Vault is sealed, and the backend does not renew Vault tokens, so a non-root scoped
token would risk expiry). All vault calls use the mandatory http://127.0.0.1:8200 scheme.
"""
import json
import os

from ..dockerutil import compose_ps, service_state, tail_logs, wait_for
from ..errors import AbortByUser, VaultError
from ..fsutil import atomic_write
from .. import templates

TITLE = "Vault init / unseal / PKI"

_CA_CN = "Orcastra Mini CA"


def run(ctx):
    if ctx.dry_run:
        ctx.log.detail("[dry-run] would init/unseal Vault and configure PKI (role 'lxd').")
        return
    if ctx.flags.skip_vault_bootstrap:
        ctx.log.warn("--skip-vault-bootstrap: leaving Vault for manual setup.")
        return

    # Fully-configured re-run? If the backend is healthy, Vault is already unsealed + tokened.
    env_token = _env_token(ctx)
    if env_token and _backend_healthy(ctx):
        ctx.log.ok("Vault already configured and backend healthy; nothing to do.")
        return

    status = _status(ctx)
    if status is None:
        raise VaultError("Cannot reach Vault to read its status.",
                         remediation="Check the vault container: docker compose logs vault")

    keys_mem, root_mem = None, None
    if not status.get("initialized"):
        keys_mem, root_mem = _init(ctx)
        status = _status(ctx)

    if status.get("sealed", True):
        keys = keys_mem or _acquire_keys(ctx)
        _unseal(ctx, keys)
        status = _status(ctx)
        if status.get("sealed", True):
            raise VaultError("Vault is still sealed after unseal attempts.",
                             remediation="Re-check the unseal keys (need the threshold count).")
    ctx.log.ok("Vault is unsealed.")

    root = root_mem or _acquire_root_token(ctx)
    _setup_pki(ctx, root)

    # Write VAULT_TOKEN (root) into .env and recreate the backend so it picks it up.
    if env_token != root:
        ctx.log.add_secret(root)
        text = open(ctx.env_path, encoding="utf-8").read()
        atomic_write(ctx, ctx.env_path, templates.set_env_value(text, "VAULT_TOKEN", root), mode=0o600)
        ctx.log.info("Wrote VAULT_TOKEN to .env; recreating backend ...")
        up = ctx.proc.run(ctx.compose_argv("up", "-d", "backend"), mutating=True, timeout=300)
        if not up.ok:
            raise VaultError("failed to recreate backend", remediation=up.combined[-400:])

    _wait_backend(ctx)
    if ctx.flags.convenience and ctx.flags.auto_unseal_unit:
        _install_auto_unseal_unit(ctx)
    ctx.log.ok("Vault PKI ready (role 'lxd') and backend healthy.")


# -- vault primitives --------------------------------------------------------
def _status(ctx):
    res = ctx.proc.run(ctx.vault_exec_argv(["status", "-format=json"]))
    # `vault status` exits 2 when sealed, but still prints JSON on stdout.
    out = res.out.strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def _init(ctx):
    shares = int(ctx.flags.unseal_shares)
    threshold = int(ctx.flags.unseal_threshold)
    ctx.log.info(f"Initialising Vault ({threshold}-of-{shares} unseal) ...")
    res = ctx.proc.run(ctx.vault_exec_argv(
        ["operator", "init", f"-key-shares={shares}", f"-key-threshold={threshold}", "-format=json"]),
        mutating=True, timeout=120)
    if not res.ok:
        raise VaultError("vault operator init failed.", remediation=res.combined[-400:])
    data = json.loads(res.out)
    keys = data["unseal_keys_b64"]
    root = data["root_token"]
    for k in keys:
        ctx.log.add_secret(k)
    ctx.log.add_secret(root)

    if ctx.flags.convenience:
        payload = json.dumps({"unseal_keys_b64": keys, "root_token": root,
                              "key_threshold": threshold}, indent=2)
        atomic_write(ctx, ctx.vault_keys_path, payload + "\n", mode=0o600, backup=False)
        ctx.log.warn("CONVENIENCE MODE: Vault unseal keys + root token written to "
                     f"{ctx.vault_keys_path} (mode 600).")
        ctx.log.warn("Anyone who can read that file can unseal Vault and mint admin certs. "
                     "Use only on a trusted, disk-encrypted host; delete + rotate to harden.")
    else:
        _present_keys_once(ctx, keys, root, threshold)
    ctx.state.set_artifact("vault_initialized", True)
    return keys, root


def _present_keys_once(ctx, keys, root, threshold):
    print()
    ctx.log.banner("SAVE THESE NOW - shown only once, never written to disk")
    for i, k in enumerate(keys, 1):
        print(f"  Unseal Key {i}: {k}")
    print(f"  Initial Root Token: {root}")
    print(f"  (You need any {threshold} unseal keys to unseal Vault after each reboot.)")
    print()
    if ctx.interactive:
        ctx.prompt.pause("Type ENTER once you have stored these keys somewhere safe and offline:")
    else:
        ctx.log.warn("Non-interactive guided init: capture the keys from the log/console NOW.")


def _unseal(ctx, keys):
    ctx.log.info("Unsealing Vault ...")
    for k in keys:
        res = ctx.proc.run(ctx.vault_exec_argv(["operator", "unseal", k]),
                           secret_args=[k], mutating=True)
        st = _status(ctx)
        if st and not st.get("sealed", True):
            return


def _acquire_keys(ctx):
    persisted = _load_persisted(ctx)
    if persisted:
        return persisted["unseal_keys_b64"]
    threshold = int(ctx.flags.unseal_threshold)
    if not ctx.interactive:
        raise AbortByUser("Vault is sealed and no unseal keys are available.",
                          remediation="Run interactively to paste keys, or use --convenience.")
    ctx.log.info(f"Vault is sealed. Paste any {threshold} unseal keys:")
    out = []
    for i in range(threshold):
        out.append(ctx.prompt.ask(f"Unseal key {i + 1}", key="unseal-key"))
    return out


def _acquire_root_token(ctx):
    persisted = _load_persisted(ctx)
    if persisted and persisted.get("root_token"):
        return persisted["root_token"]
    if not ctx.interactive:
        raise AbortByUser("A Vault root token is required to configure PKI but none is available.",
                          remediation="Run interactively to paste it, or use --convenience mode.")
    return ctx.prompt.ask("Paste the Vault root token", key="vault-root-token")


def _load_persisted(ctx):
    if not os.path.exists(ctx.vault_keys_path):
        return None
    try:
        return json.load(open(ctx.vault_keys_path, encoding="utf-8"))
    except (ValueError, OSError):
        return None


# -- PKI ---------------------------------------------------------------------
def _setup_pki(ctx, root):
    ca = f"{ctx.cfg['ca_ttl_hours']}h"
    role_ttl = f"{ctx.cfg['role_max_ttl_hours']}h"

    def vault(args, ok_substr=None, check=True):
        res = ctx.proc.run(ctx.vault_exec_argv(args, token=root),
                           secret_args=[f"VAULT_TOKEN={root}"], mutating=True, timeout=120)
        if not res.ok and ok_substr and ok_substr in res.combined.lower():
            return res  # benign "already" condition
        if not res.ok and check:
            raise VaultError(f"vault {' '.join(args[:2])} failed.", remediation=res.combined[-400:])
        return res

    # kv-v2 at secret/ and pki at pki_int/ (idempotent: tolerate "already in use").
    vault(["secrets", "enable", "-path=secret", "-version=2", "kv"], ok_substr="already in use")
    vault(["secrets", "enable", "-path=pki_int", "pki"], ok_substr="already in use")
    vault(["secrets", "tune", f"-max-lease-ttl={ca}", "pki_int"])

    # Root CA: only generate if absent.
    has_ca = ctx.proc.run(ctx.vault_exec_argv(["read", "pki_int/cert/ca"], token=root),
                          secret_args=[f"VAULT_TOKEN={root}"]).ok
    if has_ca:
        ctx.log.detail("PKI root CA already present; keeping it.")
    else:
        vault(["write", "pki_int/root/generate/internal", f"common_name={_CA_CN}", f"ttl={ca}"])
        ctx.log.detail(f"Generated PKI root CA ({_CA_CN}, ttl {ca}).")

    # Role 'lxd': create/update (allow_any_name, max_ttl below the CA).
    vault(["write", "pki_int/roles/lxd", "allow_any_name=true", f"max_ttl={role_ttl}"])
    ctx.log.detail(f"PKI role 'lxd' max_ttl {role_ttl} (CA {ca}).")
    ctx.state.set_artifact("vault_pki", True)


# -- backend health ----------------------------------------------------------
def _env_token(ctx):
    if not os.path.exists(ctx.env_path):
        return ""
    return templates.get_env_value(open(ctx.env_path, encoding="utf-8").read(), "VAULT_TOKEN") or ""


def _backend_healthy(ctx):
    state, health = service_state(compose_ps(ctx), "backend")
    return state == "running" and health == "healthy"


def _wait_backend(ctx):
    ctx.log.info("Waiting for the backend to become healthy (start grace up to 120s) ...")
    if not wait_for(ctx, lambda: _backend_healthy(ctx), timeout=200, interval=5):
        ctx.log.warn("Backend not healthy yet; recent logs:")
        ctx.log.detail(tail_logs(ctx, "backend", 40))
        raise VaultError("backend did not become healthy after Vault setup.",
                         remediation="Check VAULT_TOKEN in .env and: docker compose logs backend")


def _install_auto_unseal_unit(ctx):
    ctx.log.warn("Auto-unseal unit requested - the host will unseal Vault on boot using the "
                 "persisted keys (removes the manual-unseal protection).")
    # The unit feeds the persisted keys via the compose vault exec on boot.
    script = ctx.bootstrap_helper_path  # reuse install dir; write a dedicated helper
    unseal_sh = os.path.join(ctx.install_dir, "vault-unseal.sh")
    body = _UNSEAL_SCRIPT.format(compose=ctx.compose_path, project=ctx.project,
                                 keys=ctx.vault_keys_path)
    atomic_write(ctx, unseal_sh, body, mode=0o750, backup=False)
    unit = _UNSEAL_UNIT.format(script=unseal_sh)
    sudo = [] if ctx.cfg.get("is_root") else ["sudo"]
    if ctx.proc.run(sudo + ["tee", "/etc/systemd/system/orcastra-vault-unseal.service"],
                    input=unit, mutating=True).ok:
        ctx.proc.run(sudo + ["systemctl", "daemon-reload"], mutating=True)
        ctx.proc.run(sudo + ["systemctl", "enable", "orcastra-vault-unseal.service"], mutating=True)
        ctx.log.ok("Installed orcastra-vault-unseal.service (auto-unseal on boot).")


_UNSEAL_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail
KEYS_FILE="{keys}"
COMPOSE="{compose}"
PROJECT="{project}"
for i in $(seq 1 30); do docker compose -p "$PROJECT" -f "$COMPOSE" exec -T vault true 2>/dev/null && break; sleep 2; done
python3 - "$KEYS_FILE" "$COMPOSE" "$PROJECT" <<'PY'
import json, subprocess, sys
keys=json.load(open(sys.argv[1]))["unseal_keys_b64"]; compose=sys.argv[2]; project=sys.argv[3]
for k in keys:
    subprocess.run(["docker","compose","-p",project,"-f",compose,"exec","-T","-e","VAULT_ADDR=http://127.0.0.1:8200","vault","vault","operator","unseal",k])
PY
"""

_UNSEAL_UNIT = """[Unit]
Description=Orcastra Mini - unseal Vault on boot
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart={script}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""

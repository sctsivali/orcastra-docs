"""Phase 13 - post-install summary."""
import os

TITLE = "Summary"


def run(ctx):
    c = ctx.cfg
    log = ctx.log
    print()
    log.banner("Orcastra Mini is deployed")
    print(f"  Dashboard:        {c['base_url']}")
    print(f"  Install dir:      {ctx.install_dir}")
    print(f"  Compose project:  {ctx.project}  (HTTPS port {ctx.https_port})")
    print()

    print("  Files:")
    print(f"    .env (secrets, 0600):   {ctx.env_path}")
    print(f"    docker-compose.yml:     {ctx.compose_path}")
    if os.path.exists(ctx.admin_p12_path):
        print(f"    admin.p12 (import it):  {ctx.admin_p12_path}")
    if os.path.exists(ctx.bootstrap_helper_path):
        print(f"    bootstrap-admin.sh:     {ctx.bootstrap_helper_path}  (run on your workstation)")
    if os.path.exists(ctx.vault_keys_path):
        print(f"    vault-init.json (KEYS): {ctx.vault_keys_path}  (0600 - protect this)")
    print()

    if ctx.flags.convenience:
        log.warn("CONVENIENCE Vault mode: unseal keys are on disk. Back them up offline and "
                 "consider switching to manual unseal once set up.")
    else:
        log.warn("GUIDED Vault mode: unseal keys were shown only once and are NOT stored. "
                 "After any reboot, unseal Vault before sign-in works:")
        print(f"    docker compose -p {ctx.project} -f {ctx.compose_path} exec -e "
              "VAULT_ADDR=http://127.0.0.1:8200 vault vault operator unseal <key>   (x"
              f"{ctx.flags.unseal_threshold})")
    print()

    print("  Next steps:")
    if not ctx.state.artifact("bootstrap_closed"):
        print("    1) Enroll the first admin (bootstrap-admin.sh on your workstation), then")
        print(f"       close the window:  {ctx.bootstrap_close_path}")
    else:
        print("    1) Import your admin certificate and sign in.")
    print("    2) Administration -> Identities -> Issue Identity for partners/tenants.")
    print("    3) Administration -> Audit Log -> Verify integrity.")
    print()
    log.info("Keep .env and any Vault keys out of version control. "
             f"Full install log: {log.log_file}")

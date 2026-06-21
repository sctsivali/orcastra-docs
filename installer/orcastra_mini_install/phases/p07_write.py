"""Phase 7 - write the deployment files (compose pull-variant, nginx, vault.hcl, .env at
0600). Existing files are backed up first; .env keeps any VAULT_TOKEN from a prior run."""
import os

from .. import templates
from ..errors import InstallError
from ..fsutil import atomic_write, ensure_dir

TITLE = "Write deployment files"

UPLOADS_DIR = "/var/orcastra/uploads"


def run(ctx):
    ensure_dir(os.path.join(ctx.config_dir, "nginx", "certs"))
    ensure_dir(os.path.join(ctx.config_dir, "vault"))

    atomic_write(ctx, ctx.compose_path, templates.render_compose(ctx.cfg["image_tag"]), mode=0o644)
    atomic_write(ctx, ctx.nginx_conf_path, templates.nginx_conf(), mode=0o644)
    atomic_write(ctx, ctx.vault_hcl_path, templates.vault_hcl(), mode=0o644)

    env_text = templates.render_env(_env_values(ctx))
    atomic_write(ctx, ctx.env_path, env_text, mode=0o600)

    _ensure_uploads_dir(ctx)
    ctx.log.ok(f"Wrote docker-compose.yml, config/, and .env to {ctx.install_dir}")


def _env_values(ctx):
    s = ctx.secrets
    c = ctx.cfg
    # Preserve a VAULT_TOKEN set by a prior run (phase 10 writes it after PKI setup).
    prior_token = ""
    if os.path.exists(ctx.env_path):
        prior_token = templates.get_env_value(open(ctx.env_path, encoding="utf-8").read(),
                                              "VAULT_TOKEN") or ""
    return {
        "CONTAINER_PREFIX": c["container_prefix"],
        "APP_VERSION": c["image_tag"],
        "HTTPS_PORT": str(c["https_port"]),
        "POSTGRES_USER": c["postgres_user"],
        "POSTGRES_PASSWORD": s["POSTGRES_PASSWORD"],
        "POSTGRES_DB": c["postgres_db"],
        "DATABASE_URL": c["database_url"],
        "SECRET_KEY": s["SECRET_KEY"],
        "LOCAL_JWT_SECRET": s["LOCAL_JWT_SECRET"],
        "NEXTAUTH_SECRET": s["NEXTAUTH_SECRET"],
        "AUTH_PROXY_SECRET": s["AUTH_PROXY_SECRET"],
        "BOOTSTRAP_ADMIN_TOKEN": s["BOOTSTRAP_ADMIN_TOKEN"],
        "LOCAL_JWT_TTL_SECONDS": str(c["jwt_ttl_seconds"]),
        "CLIENT_CERT_TTL_DAYS": str(c["client_cert_ttl_days"]),
        "VAULT_TOKEN": prior_token,
        "NEXTAUTH_URL": c["base_url"],
        "NEXT_PUBLIC_API_URL": c["base_url"],
        "CORS_ORIGINS": c["base_url"],
    }


def _ensure_uploads_dir(ctx):
    if os.path.isdir(UPLOADS_DIR):
        return
    if ctx.dry_run:
        ctx.log.detail(f"[dry-run] would create {UPLOADS_DIR}")
        return
    try:
        os.makedirs(UPLOADS_DIR, exist_ok=True)
    except PermissionError:
        sudo = [] if ctx.cfg.get("is_root") else ["sudo"]
        res = ctx.proc.run(sudo + ["mkdir", "-p", UPLOADS_DIR], mutating=True)
        if not res.ok:
            raise InstallError(f"could not create {UPLOADS_DIR}",
                               remediation=f"Create it manually: sudo mkdir -p {UPLOADS_DIR}")

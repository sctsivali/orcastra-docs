"""Phase 7 - write the deployment files (compose pull-variant, nginx, vault.hcl, .env at
0600). Existing files are backed up first; .env keeps any VAULT_TOKEN from a prior run."""
import os

from .. import templates
from orcastra_core.errors import InstallError
from orcastra_core.fsutil import atomic_write, ensure_dir

TITLE = "Write deployment files"

UPLOADS_DIR = "/var/orcastra/uploads"


def run(ctx):
    ensure_dir(os.path.join(ctx.config_dir, "nginx", "certs"), dry_run=ctx.dry_run)
    ensure_dir(os.path.join(ctx.config_dir, "vault"), dry_run=ctx.dry_run)

    atomic_write(ctx, ctx.compose_path, templates.render_compose(ctx.cfg["image_tag"], ctx.flags.image_backend, ctx.flags.image_frontend), mode=0o644)
    atomic_write(ctx, ctx.nginx_conf_path, templates.nginx_conf(), mode=0o644)
    atomic_write(ctx, ctx.vault_hcl_path, templates.vault_hcl(), mode=0o644)

    env_text = templates.render_env(_env_values(ctx))
    vport = int(getattr(ctx.flags, "vault_host_port", None) or 8200)
    if vport != 8200:
        # The compose file publishes Vault on ${VAULT_HOST_PORT:-8200}; only a non-default port is written.
        env_text = env_text.rstrip("\n") + f"\n\n# Vault's loopback port on this host\nVAULT_HOST_PORT={vport}\n"
    atomic_write(ctx, ctx.env_path, env_text, mode=0o600)

    _ensure_uploads_dir(ctx)
    ensure_backend_config_dir(ctx)
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
        "COMPOSE_PROJECT_NAME": c["container_prefix"],
        "APP_VERSION": c["image_tag"],
        "HTTPS_PORT": str(c["https_port"]),
        "HTTPS_BIND": c.get("https_bind") or "0.0.0.0",
        "UPLOADS_DIR": c.get("uploads_dir") or UPLOADS_DIR,
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


BACKEND_UID = 1000  # the backend image runs as this uid (backend/Dockerfile)


def ensure_backend_owned(ctx, path, mode=0o750):
    """Give `path` to the backend's uid: it writes uploads, node display names and recovery
    bundles there, and the first Mini installer left such directories owned by root."""
    try:
        if os.stat(path).st_uid != BACKEND_UID and not ctx.dry_run:
            os.chown(path, BACKEND_UID, BACKEND_UID)
            os.chmod(path, mode)
            ctx.log.detail(f"  {path} now belongs to uid {BACKEND_UID}, the backend's user")
    except PermissionError:
        ctx.log.warn(f"{path} is not writable by uid {BACKEND_UID}; run: sudo chown {BACKEND_UID}:{BACKEND_UID} {path}")


def backend_config_dir(ctx):
    return os.path.join(ctx.config_dir, "backend")


# important: only config/backend is the backend's (mounted at /app/config). config/ itself and
# nginx's key and template stay root's: a backend that owned config/ could swap them, or plant a
# link that a later restore, run as root, would follow.
def ensure_backend_config_dir(ctx):
    path = backend_config_dir(ctx)
    if ctx.dry_run:
        ctx.log.detail(f"[dry-run] would create {path} for uid {BACKEND_UID}")
        return path
    os.makedirs(path, exist_ok=True)
    ensure_backend_owned(ctx, path)
    return path


def reown_backend_config(ctx):
    """After a restore (extracted as root) the backend's directory and files are its own again.
    Links are never followed."""
    path = backend_config_dir(ctx)
    if ctx.dry_run or not os.path.isdir(path) or os.path.islink(path):
        return
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            full = os.path.join(root, name)
            if not os.path.islink(full):
                os.chown(full, BACKEND_UID, BACKEND_UID)
    os.chown(path, BACKEND_UID, BACKEND_UID)


def _ensure_uploads_dir(ctx):
    uploads = ctx.cfg.get("uploads_dir") or UPLOADS_DIR
    if os.path.isdir(uploads):
        ensure_backend_owned(ctx, uploads)
        return
    if ctx.dry_run:
        ctx.log.detail(f"[dry-run] would create {uploads}")
        return
    try:
        os.makedirs(uploads, exist_ok=True)
        ensure_backend_owned(ctx, uploads)
    except PermissionError:
        sudo = [] if ctx.cfg.get("is_root") else ["sudo"]
        res = ctx.proc.run(sudo + ["mkdir", "-p", uploads], mutating=True)
        if not res.ok:
            raise InstallError(f"could not create {uploads}",
                               remediation=f"Create it manually: sudo mkdir -p {uploads}")

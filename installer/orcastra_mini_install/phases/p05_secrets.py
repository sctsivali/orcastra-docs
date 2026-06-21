"""Phase 5 - secrets. Generate (or reuse from an existing .env) all secrets, enforcing
LOCAL_JWT_SECRET != SECRET_KEY and keeping DATABASE_URL's password in sync."""
import base64
import os
import secrets as pysecrets

from .. import templates

TITLE = "Secrets"

# name -> generator
_GENERATORS = {
    "POSTGRES_PASSWORD": lambda: pysecrets.token_hex(32),
    "SECRET_KEY": lambda: pysecrets.token_hex(32),
    "LOCAL_JWT_SECRET": lambda: pysecrets.token_hex(32),
    "NEXTAUTH_SECRET": lambda: base64.b64encode(os.urandom(32)).decode(),
    "AUTH_PROXY_SECRET": lambda: pysecrets.token_hex(32),
    "BOOTSTRAP_ADMIN_TOKEN": lambda: pysecrets.token_hex(32),
}


def run(ctx):
    existing = _load_existing(ctx)
    rotate = set(ctx.flags.rotate_secret or [])
    reused, fresh = [], []

    for name, gen in _GENERATORS.items():
        prior = existing.get(name)
        if prior and name not in rotate:
            ctx.secrets[name] = prior
            reused.append(name)
        else:
            ctx.secrets[name] = gen()
            fresh.append(name)

    # Key separation: LOCAL_JWT_SECRET must differ from SECRET_KEY (backend warns otherwise).
    while ctx.secrets["LOCAL_JWT_SECRET"] == ctx.secrets["SECRET_KEY"]:
        ctx.secrets["LOCAL_JWT_SECRET"] = _GENERATORS["LOCAL_JWT_SECRET"]()

    user = ctx.cfg["postgres_user"]
    db = ctx.cfg["postgres_db"]
    pwd = ctx.secrets["POSTGRES_PASSWORD"]
    ctx.cfg["database_url"] = f"postgresql+asyncpg://{user}:{pwd}@postgres:5432/{db}"

    for name, value in ctx.secrets.items():
        ctx.log.add_secret(value)
        ctx.state.record_secret(name, value)
    ctx.log.add_secret(ctx.cfg["database_url"])

    if reused:
        ctx.log.ok(f"Reused {len(reused)} existing secret(s): {', '.join(reused)}")
    if fresh:
        ctx.log.ok(f"Generated {len(fresh)} secret(s): {', '.join(fresh)}")
    if rotate:
        ctx.log.warn(f"Rotated on request: {', '.join(sorted(rotate))}")


def _load_existing(ctx):
    """Read current secret values from .env if it exists (for idempotent re-runs)."""
    out = {}
    if not os.path.exists(ctx.env_path):
        return out
    text = open(ctx.env_path, encoding="utf-8").read()
    for name in _GENERATORS:
        val = templates.get_env_value(text, name)
        if val and not val.startswith("<") and val != "":
            out[name] = val
    return out

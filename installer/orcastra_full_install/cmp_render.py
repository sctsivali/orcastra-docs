"""The CMP `.env`, rendered from the template in docs/deployment/vm4-dashboard.md (step 6)
by replacing values per key. Every `<PLACEHOLDER>` must be filled, and every key the
installer sets must exist in the template, so drift in the guide fails here."""
from __future__ import annotations

from typing import Dict

from orcastra_core.errors import InstallError

from . import _blocks
from . import topology as T


def overrides(ctx) -> Dict[str, str]:
    s = ctx.secrets
    pg = s.get("cmp_postgres_password")
    ver = ctx.values["CMP_VERSION"]
    size = ctx.sizes["cmp"]
    out = {
        "APP_VERSION": ver,
        "API_VERSION": ver,
        "POSTGRES_PASSWORD": pg,
        # bound to loopback: Docker-published ports bypass the guest firewall's INPUT rules
        "POSTGRES_PORT": "127.0.0.1:5432",
        "DATABASE_URL": f"postgresql+asyncpg://orcastra:{pg}@postgres:5432/orcastra_dashboard",
        "NEXT_PUBLIC_API_URL": ctx.public_url("api"),
        "VAULT_ADDR": f"http://{ctx.ip('vault')}:8200",
        "VAULT_TOKEN": s.get("vault_dashboard_token"),
        "REDIS_PORT": "127.0.0.1:6381",
        "CORS_ORIGINS": ctx.public_url("cmp"),
        # only the CMP's own proxies may set client-IP headers (LXD forwards keep the real
        # client address, so a LAN client must not be trusted as a proxy)
        "TRUSTED_PROXY_CIDRS": f"127.0.0.0/8,::1/128,{T.DOCKER_POOL}",
        "REDIS_ENCRYPTION_KEY": s.get("cmp_redis_encryption_key"),
        "SECRET_KEY": s.get("cmp_secret_key"),
        "AUTHENTIK_ISSUER": ctx.issuer,
        "NEXT_PUBLIC_AUTHENTIK_LOGOUT_URL": ctx.issuer + "end-session/",
        "AUTHENTIK_CLIENT_ID": s.get("oidc_client_id"),
        "AUTHENTIK_CLIENT_SECRET": s.get("oidc_client_secret"),
        "AUTHENTIK_API_URL": f"http://{ctx.ip('authentik')}:9000",
        "AUTHENTIK_API_TOKEN": s.get("authentik_api_token"),
        "NEXTAUTH_URL": ctx.public_url("cmp"),
        "NEXTAUTH_SECRET": s.get("cmp_nextauth_secret"),
        "OPENSEARCH_HOST": ctx.ip("opensearch"),
        "OPENSEARCH_PASSWORD": s.get("fluentbit_password"),
    }
    out.update(T.backend_limits(size["cpu"], size["mem"]))
    return out


def env(ctx) -> str:
    values = overrides(ctx)
    seen = set()
    lines = []
    for line in _blocks.CMP_ENV.splitlines():
        key = line.split("=", 1)[0] if "=" in line and not line.lstrip().startswith("#") else None
        if key in values:
            line = f"{key}={values[key]}"
            seen.add(key)
        lines.append(line)
    missing = sorted(set(values) - seen)
    if missing:
        raise InstallError(f"internal error: the guide's .env template no longer has {missing}")
    text = "# Managed by orcastra-full, rendered from docs/deployment/vm4-dashboard.md\n" + \
        "\n".join(lines) + "\n"
    if "<" in "".join(l for l in text.splitlines() if not l.startswith("#")):
        raise InstallError("internal error: an unfilled <PLACEHOLDER> remains in the CMP .env")
    return text

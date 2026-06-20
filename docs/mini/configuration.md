# Configuration

Orcastra Mini is configured through `.env` (copied from `.env.example`, which already defaults
to the mini profile). This page lists the settings that matter for the single-host build.
`AUTHENTIK_*` and `OPENSEARCH_*` are left unset; their code paths stay disabled.

!!! warning "Secrets"
    Generate every secret yourself and keep `.env` out of version control. The values below
    are placeholders. Use `openssl rand -hex 32` for the HS256/secret keys.

## Profile

| Variable | Default | Purpose |
|---|---|---|
| `AUTH_MODE` | `client-cert` | Selects the profile. `client-cert` is mini; `authentik` is the full version. |
| `NEXT_PUBLIC_AUTH_MODE` | `client-cert` | Exposed to the browser so the UI uses the certificate sign-in. Match `AUTH_MODE`. |
| `HTTPS_PORT` | `6969` | The single public HTTPS port published by nginx. |
| `CONTAINER_PREFIX` | `orcastra-mini` | Names the containers and images. |
| `APP_VERSION` | `1.0.0-RC1` | Version string shown in the UI footer. |

## Session and trust boundary

| Variable | Default | Purpose |
|---|---|---|
| `LOCAL_JWT_SECRET` | (required) | Signs minted session tokens. Must differ from `SECRET_KEY`. |
| `LOCAL_JWT_TTL_SECONDS` | `3600` | Session-token lifetime. Active revocation is per request, so this is the re-auth cadence, not the revocation window. |
| `AUTH_PROXY_SECRET` | (required) | Shared secret nginx injects; the backend honours `X-SSL-*` only when it matches. |
| `TRUSTED_AUTH_PROXY_CIDRS` | (empty) | Optional CIDR of the reverse proxy allowed to supply `X-SSL-*`, for defense in depth. |
| `BOOTSTRAP_ADMIN_TOKEN` | (empty) | One-time token to enroll the first admin. Blank it again afterward. |
| `CLIENT_CERT_TTL_DAYS` | `365` | Default validity for certificates issued through Vault PKI. |

## Audit

| Variable | Default | Purpose |
|---|---|---|
| `AUDIT_DB_ENABLED` | `true` | Persist the hash-chained audit log to PostgreSQL (replaces OpenSearch). |

## Core services

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | (required) | Application secret key. |
| `POSTGRES_PASSWORD` | (required) | Database password. |
| `DATABASE_URL` | (derived) | Async PostgreSQL connection string. |
| `VAULT_ENABLED` | `true` | Enable Vault integration. |
| `VAULT_ADDR` | `http://vault:8200` | Vault address on the Docker network. |
| `VAULT_TOKEN` | (required) | Token that can use `pki_int` to issue certificates. |

## URLs

| Variable | Example | Purpose |
|---|---|---|
| `NEXTAUTH_URL` | `https://your-host.example.com:6969` | Public URL the session layer expects. |
| `NEXT_PUBLIC_API_URL` | `https://your-host.example.com:6969` | Public API base used by the browser. |
| `CORS_ORIGINS` | `https://your-host.example.com:6969` | Allowed browser origin. |

All three URL values must use the same host and `HTTPS_PORT`. The frontend bakes the API URL
through a runtime placeholder, so the published image stays portable across hosts.

!!! tip "After changing .env"
    Recreate the affected services so the new values take effect:

    ```bash
    docker compose -f docker-compose.mini.yml up -d
    ```

    A change to a build-time value (for example `APP_VERSION`) needs a rebuild
    (`make up-mini`).

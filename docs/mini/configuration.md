# Configuration

Orcastra Mini is configured through `.env` in the deployment directory (`/opt/orcastra-mini/.env`
for an installer deployment, mode `600`). The installer writes it from the template in the
[Quick Start](quick-start.md#env); a manual deployment creates it from the same template. The
backend container reads the whole file, and the Compose file passes the values the other
containers need.

There is no `AUTH_MODE` setting: the Compose file starts the backend with `ORCASTRA_EDITION=mini`,
and the Mini images are built for the Mini edition. `AUTHENTIK_*` and `OPENSEARCH_*` are not
used.

!!! warning "Secrets"
    Generate every secret yourself and keep `.env` out of version control. Use
    `openssl rand -hex 32` for each one. The backend refuses to start when `LOCAL_JWT_SECRET` or
    `AUTH_PROXY_SECRET` is shorter than 32 characters, or when `LOCAL_JWT_SECRET` equals
    `SECRET_KEY`.

## Deployment

| Variable | Default | Purpose |
|---|---|---|
| `CONTAINER_PREFIX` | `orcastra-mini` | The prefix of every container name (`orcastra-mini-backend`, ...). The installer and upgrades use it as the Compose project name too. |
| `COMPOSE_PROJECT_NAME` | `orcastra-mini` | The Compose project name, which names the volumes (`<project>_postgres-data`, `<project>_vault-data`, `<project>_redis-data`). Keep it equal to `CONTAINER_PREFIX`; an upgrade from RC1 adds it with that value. |
| `APP_VERSION` | `1.0.0` | The release to run: the Compose file pulls `backend-<APP_VERSION>` and `frontend-<APP_VERSION>`. Change releases with the installer's [`--upgrade`](upgrading.md), which also backs up and migrates. |
| `HTTPS_PORT` | `6969` | The single public HTTPS port published by nginx. |
| `HTTPS_BIND` | `0.0.0.0` | The host address nginx listens on. `0.0.0.0` is every interface; `127.0.0.1` keeps the dashboard reachable only from the host itself (for example over an SSH tunnel), and a specific interface address limits it to that network. |
| `UPLOADS_DIR` | `/var/orcastra/uploads` | Host directory for image and ISO uploads, mounted into the backend at `/app/uploads`. The backend runs as uid `1000`, so the directory must belong to uid `1000` (the installer sets this, together with the `config/` directory; see [Operations](operations.md#directories-the-backend-writes)). |

## Database

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_USER` | `orcastra` | PostgreSQL user. |
| `POSTGRES_PASSWORD` | (required) | PostgreSQL password. |
| `POSTGRES_DB` | `orcastra` | PostgreSQL database. |
| `DATABASE_URL` | (required) | Async connection string for the backend, `postgresql+asyncpg://<user>:<password>@postgres:5432/<db>`. Its password must equal `POSTGRES_PASSWORD`. |

## Secrets

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | (required) | Application secret key. |
| `LOCAL_JWT_SECRET` | (required) | Signs the session tokens the backend mints at sign-in. At least 32 characters and different from `SECRET_KEY`. |
| `NEXTAUTH_SECRET` | (required) | Secret of the frontend's session layer (NextAuth). |
| `AUTH_PROXY_SECRET` | (required) | Shared secret nginx sends with every request it forwards. The frontend and backend trust the certificate headers only when it matches. At least 32 characters. |
| `BOOTSTRAP_ADMIN_TOKEN` | (empty) | One-time token that enrolls the first Administrator while none can sign in. Blank it again afterward; Host Health warns while it is set and an Administrator exists. |

## Sessions and certificates

| Variable | Default | Purpose |
|---|---|---|
| `LOCAL_JWT_TTL_SECONDS` | `900` | Session-token lifetime in seconds (60 to 86400). The browser renews the session from its certificate before it ends, so this bounds how long a captured token stays usable, not how long anyone stays signed in. Revocation does not wait for it. |
| `CLIENT_CERT_TTL_DAYS` | `365` | Default and maximum validity, in days, of the operator certificates the backend issues through Vault PKI (1 to 3650). The Vault role's `max_ttl` must allow it. |
| `CERT_SELF_RENEW_ENABLED` | `true` | Lets operators renew their own certificate from **My Certificate** inside the renewal window. With `false`, Administrators and Partners issue replacement certificates on the Operators page. |

## Vault

| Variable | Default | Purpose |
|---|---|---|
| `VAULT_ENABLED` | `true` | Kept for reference. The Compose file always sets `VAULT_ENABLED=true` for the backend, and Mini refuses to start without Vault. |
| `VAULT_ADDR` | `http://vault:8200` | Vault address on the Docker network. |
| `VAULT_TOKEN` | (set after Vault is initialized) | Token for the KV mount `secret/` (cluster registry and credentials: read, write, list, delete) and `pki_int/issue/<role>` (certificates). The installer writes the root token here. |
| `VAULT_PKI_ROLE` | `lxd` | The `pki_int` role operator certificates are issued from (`pki_int/issue/<role>`). The installer and the Quick Start create the role `lxd`. |

## URLs

| Variable | Example | Purpose |
|---|---|---|
| `NEXTAUTH_URL` | `https://your-host.example.com:6969` | Public URL the session layer expects. |
| `NEXT_PUBLIC_API_URL` | `https://your-host.example.com:6969` | Public API base used by the browser. |
| `CORS_ORIGINS` | `https://your-host.example.com:6969` | Allowed browser origin. |

All three URL values must use the same host and `HTTPS_PORT`, and the host must match the nginx
server certificate's SAN. The frontend reads the API URL when it starts, so the published image
works on any host.

## Audit log

| Variable | Default | Purpose |
|---|---|---|
| `AUDIT_DB_ENABLED` | `true` | Writes the hash-chained audit log to PostgreSQL and enables **Administration > Audit Log**. Must stay `true`: sign-ins and operator changes are recorded before they take effect, so the backend refuses to start with `false`. |
| `AUDIT_DB_RETENTION_DAYS` | `0` | `0` keeps every row. A positive number removes rows older than that many days, once a day, and records where the chain was cut so verification still covers the rows that remain (1 to 36500). |

## Optional settings

These keys are not in the template. Add them to `.env` only to change the default. The first
group is read by the Compose file; the second by the backend, which receives the whole `.env`.

| Variable | Default | Purpose |
|---|---|---|
| `TRUSTED_AUTH_PROXY_CIDRS` | (empty) | Comma-separated networks the backend accepts certificate headers from, in addition to the proxy secret. If set, it must cover both nginx and the frontend on the Compose network. |
| `VAULT_UNSEAL_UI_ENABLED` | `true` | Lets an Administrator submit Vault unseal key shares from the dashboard. `false` keeps unsealing on the host only. |
| `WEB_CONCURRENCY` | `2` | Backend worker processes. They share tickets and sessions through Redis; raise it for many concurrent consoles. |
| `RUN_MIGRATIONS` | `true` | The backend migrates the database to the release's schema when it starts. |
| `VAULT_HOST_PORT` | `8200` | Loopback port on the host where Vault answers for init and unseal. Change it when the host already runs a Vault on 8200; the installer's `--vault-host-port` writes it. |
| `MINI_IMAGE_REPO` | `svlct/orcastra-dashboard-mini` | Repository the backend and frontend images are pulled from (for example a private mirror). |
| `BACKEND_MEM_LIMIT`, `BACKEND_CPUS` | `2g`, `2` | Memory and CPU limits of the backend container. |
| `BACKEND_TMPFS_SIZE` | `256m` | Size of the backend's `/tmp` tmpfs. |
| `POSTGRES_SHM_SIZE` | `256mb` | Shared memory of the PostgreSQL container. |
| `AUTOHEAL_INTERVAL`, `AUTOHEAL_START_PERIOD`, `AUTOHEAL_DEFAULT_STOP_TIMEOUT` | `10`, `300`, `30` | Seconds between checks, grace period after start, and stop timeout of the `autoheal` container, which restarts the backend, frontend or nginx when Docker marks it unhealthy. |
| `CERT_RENEW_WINDOW_DAYS` | `30` | How many days before expiry self-service renewal opens (1 to 365). |
| `CERT_RENEW_OVERLAP_HOURS` | `24` | How long a renewed certificate's predecessor keeps working when the new certificate is never used (0 to 720). |
| `CERT_EXPIRY_WARNING_DAYS` | `14` | When the expiry banner, the **Expiring soon** marker and the Host Health count start (1 to 365). |
| `CLIENT_CERT_CN_SUFFIX` | `users.orcastra.internal` | Suffix of the common name of issued certificates, and the domain of the email a first Administrator gets when enrolled without one. |
| `RELEASE_CHECK_ENABLED` | `true` | Reads the public Mini release manifest and tells Administrators about newer or deprecated releases. |
| `RELEASE_FEED_URL` | (empty) | An `https` mirror of the release manifest. Empty reads the published Mini manifest. |

The Compose file fixes `ORCASTRA_EDITION=mini`, `AUTH_ENABLED=true`, `VAULT_ENABLED=true`, and the
Redis settings for the backend; `.env` does not change them.

!!! tip "After changing .env"
    Recreate the affected services from the deployment directory so the new values take effect:

    ```bash
    cd /opt/orcastra-mini
    docker compose -p orcastra-mini up -d
    ```

    Use your own directory and `CONTAINER_PREFIX` if they differ. To move to another release,
    use the installer's [`--upgrade`](upgrading.md) instead of editing `APP_VERSION` by hand.

# Quick Start

This guide deploys Orcastra Mini 1.0.0 on a single host by pulling the published container
images. No source build is required. The [Automated Install](automated-install.md) runs the same
steps with one command. Commands assume a host reachable at
`https://your-host.example.com:6969`.

## Prerequisites

- Docker with the Compose plugin.
- A Docker Hub account with access to the Orcastra Mini images.
- `openssl` (for the nginx server certificate and the first admin certificate).
- LXD/Incus/MicroCloud nodes with the API enabled, if you intend to manage real clusters.
- No Authentik and no OpenSearch are required.

## 1. Sign in to Docker Hub

The images are distributed through Docker Hub. Sign in with an account that has access:

```bash
docker login
```

## 2. Create the deployment directory

You will create four files: a Compose file, two configuration files, and an environment file.

```bash
mkdir -p orcastra-mini/config/nginx/certs orcastra-mini/config/vault
cd orcastra-mini
```

## 3. Create the deployment files

Run each command below as-is. It writes the file with its contents in one paste (the quoted
`'EOF'` keeps the `${...}` placeholders and `$variables` literal). Fill in your secrets in
`.env` afterward.

### `docker-compose.yml`

This Compose file runs the published backend and frontend images alongside PostgreSQL, Redis,
Vault, and nginx, plus a small `autoheal` container that restarts the backend, frontend or nginx
when Docker marks it unhealthy. Only nginx is published to the host, on `HTTPS_BIND` and
`HTTPS_PORT`.

```bash
cat > docker-compose.yml <<'EOF'
# =============================================================================
# Orcastra Mini: one host, TLS client-certificate sign-in
# =============================================================================
# nginx is the only published port. It asks every browser for a client certificate and forwards
# it, with AUTH_PROXY_SECRET, to the frontend and backend, which are reachable only on the
# compose network. Vault runs beside them and answers on loopback for init and unseal.
#
#   docker compose -p "$CONTAINER_PREFIX" -f docker-compose.mini.yml --env-file .env up -d
#
# Names kept from Mini 1.0.0-RC1 so an upgrade reuses the same project, services and volumes:
# the project is CONTAINER_PREFIX, the services postgres/redis/vault/backend/frontend/nginx, the
# volumes postgres-data/redis-data/vault-data.
# =============================================================================

x-logging: &default-logging
  driver: json-file
  options:
    max-size: "50m"
    max-file: "5"

services:
  postgres:
    logging: *default-logging
    image: postgres:17-alpine
    container_name: ${CONTAINER_PREFIX:-orcastra-mini}-postgres
    restart: always
    environment:
      - POSTGRES_USER=${POSTGRES_USER}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=${POSTGRES_DB}
    volumes:
      - postgres-data:/var/lib/postgresql/data
    shm_size: ${POSTGRES_SHM_SIZE:-256mb}
    networks: [orcastra-mini]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s

  redis:
    logging: *default-logging
    image: redis:8-alpine
    container_name: ${CONTAINER_PREFIX:-orcastra-mini}-redis
    restart: always
    command: redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru
    volumes:
      - redis-data:/data
    networks: [orcastra-mini]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 3
      start_period: 5s

  vault:
    logging: *default-logging
    image: hashicorp/vault:1.17
    container_name: ${CONTAINER_PREFIX:-orcastra-mini}-vault
    restart: always
    # Starts as root only to fix the data volume's ownership, then runs as the vault user.
    user: root
    entrypoint: ["sh", "-c", "chown -R 100:1000 /vault/data && exec docker-entrypoint.sh server"]
    cap_add: [IPC_LOCK]
    ports:
      - "127.0.0.1:${VAULT_HOST_PORT:-8200}:8200"   # loopback only, for init and unseal
    volumes:
      - vault-data:/vault/data
      - ./config/vault/vault.hcl:/vault/config/vault.hcl:ro
    networks: [orcastra-mini]

  backend:
    logging: *default-logging
    image: ${MINI_IMAGE_REPO:-svlct/orcastra-dashboard-mini}:backend-${APP_VERSION:-1.0.0}
    container_name: ${CONTAINER_PREFIX:-orcastra-mini}-backend
    restart: always
    # No published port: only nginx and the frontend reach the backend.
    env_file: [.env]
    environment:
      - ORCASTRA_EDITION=mini
      - APP_VERSION=${APP_VERSION:-1.0.0}
      - RUN_MIGRATIONS=${RUN_MIGRATIONS:-true}
      - DATABASE_URL=${DATABASE_URL}
      - AUTH_ENABLED=true
      - LOCAL_JWT_SECRET=${LOCAL_JWT_SECRET:?LOCAL_JWT_SECRET is required}
      - LOCAL_JWT_TTL_SECONDS=${LOCAL_JWT_TTL_SECONDS:-900}
      - AUTH_PROXY_SECRET=${AUTH_PROXY_SECRET:?AUTH_PROXY_SECRET is required}
      - TRUSTED_AUTH_PROXY_CIDRS=${TRUSTED_AUTH_PROXY_CIDRS:-}
      - BOOTSTRAP_ADMIN_TOKEN=${BOOTSTRAP_ADMIN_TOKEN:-}
      - CLIENT_CERT_TTL_DAYS=${CLIENT_CERT_TTL_DAYS:-365}
      - AUDIT_DB_ENABLED=${AUDIT_DB_ENABLED:-true}
      - AUDIT_DB_RETENTION_DAYS=${AUDIT_DB_RETENTION_DAYS:-0}
      - CERT_SELF_RENEW_ENABLED=${CERT_SELF_RENEW_ENABLED:-true}
      - VAULT_ENABLED=true
      - VAULT_ADDR=${VAULT_ADDR:-http://vault:8200}
      - VAULT_TOKEN=${VAULT_TOKEN}
      - VAULT_PKI_ROLE=${VAULT_PKI_ROLE:-lxd}
      # After a host reboot Vault starts sealed; an Administrator unseals it from Host Health.
      - VAULT_UNSEAL_UI_ENABLED=${VAULT_UNSEAL_UI_ENABLED:-true}
      - REDIS_ENABLED=true
      - REDIS_URL=redis://redis:6379/0
      # Two workers share tickets and sessions through Redis; raise for many concurrent consoles.
      - WEB_CONCURRENCY=${WEB_CONCURRENCY:-2}
      # LAN clients have private addresses; the frontend forwards nginx's view of each one.
      - ALLOW_PRIVATE_FORWARDED_IPS=true
      - SECRET_KEY=${SECRET_KEY}
      - CORS_ORIGINS=${CORS_ORIGINS}
      # The address integrators reach: the add-on API document's servers entry and webhook source.
      - PUBLIC_API_BASE_URL=${NEXTAUTH_URL}
      - ORCASTRA_DOMAIN=${ORCASTRA_DOMAIN:-orcastra.io}
    volumes:
      # Only config/backend: the rest of config/ (nginx's key, vault.hcl) stays out of the backend.
      - ./config/backend:/app/config:rw
      - ${UPLOADS_DIR:-/var/orcastra/uploads}:/app/uploads:rw
    networks: [orcastra-mini]
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on:
      redis: {condition: service_healthy}
      postgres: {condition: service_healthy}
      vault: {condition: service_started}
    mem_limit: ${BACKEND_MEM_LIMIT:-2g}
    cpus: ${BACKEND_CPUS:-2}
    stop_grace_period: 30s
    labels: ["${CONTAINER_PREFIX:-orcastra-mini}.autoheal=true"]
    security_opt:
      - no-new-privileges:true
    tmpfs:
      - /tmp:mode=1777,size=${BACKEND_TMPFS_SIZE:-256m}
    healthcheck:
      test: ["CMD", "python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:4050/health', timeout=3).status == 200 else 1)"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 120s

  frontend:
    logging: *default-logging
    image: ${MINI_IMAGE_REPO:-svlct/orcastra-dashboard-mini}:frontend-${APP_VERSION:-1.0.0}
    container_name: ${CONTAINER_PREFIX:-orcastra-mini}-frontend
    restart: always
    # No published port: only nginx reaches the frontend.
    environment:
      - NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
      - INTERNAL_BACKEND_URL=http://backend:4050
      - AUTH_PROXY_SECRET=${AUTH_PROXY_SECRET:?AUTH_PROXY_SECRET is required}
      - NEXTAUTH_URL=${NEXTAUTH_URL}
      - NEXTAUTH_SECRET=${NEXTAUTH_SECRET}
      - AUTH_TRUST_HOST=true
      - CSP_SCRIPT_POLICY=${CSP_SCRIPT_POLICY:-enforce}
      - NEXT_PUBLIC_BUG_REPORT_URL=${NEXT_PUBLIC_BUG_REPORT_URL:-}
      - NEXT_PUBLIC_COMMUNITY_URL=${NEXT_PUBLIC_COMMUNITY_URL:-}
      - MAP_TILE_URL=${MAP_TILE_URL:-}
      - MAP_TILE_URL_DARK=${MAP_TILE_URL_DARK:-}
      - MAP_TILE_ATTRIBUTION=${MAP_TILE_ATTRIBUTION:-}
      - MAP_TILE_ATTRIBUTION_URL=${MAP_TILE_ATTRIBUTION_URL:-}
    networks: [orcastra-mini]
    depends_on:
      backend: {condition: service_healthy}
    labels: ["${CONTAINER_PREFIX:-orcastra-mini}.autoheal=true"]
    healthcheck:
      test: ["CMD", "node", "-e", "require('http').get({host:'127.0.0.1',port:2025,path:'/healthz',timeout:5000},r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1)).on('timeout',()=>process.exit(1))"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 60s

  nginx:
    logging: *default-logging
    image: nginx:1.27-alpine
    container_name: ${CONTAINER_PREFIX:-orcastra-mini}-nginx
    restart: always
    ports:
      - "${HTTPS_BIND:-0.0.0.0}:${HTTPS_PORT:-6969}:443"
    environment:
      - AUTH_PROXY_SECRET=${AUTH_PROXY_SECRET:?AUTH_PROXY_SECRET is required}
      - NGINX_ENVSUBST_FILTER=^AUTH_PROXY_SECRET$$
    volumes:
      - ./config/nginx/mini.conf:/etc/nginx/templates/default.conf.template:ro
      - ./config/nginx/certs:/etc/nginx/certs:ro
    networks: [orcastra-mini]
    depends_on: [frontend]
    labels: ["${CONTAINER_PREFIX:-orcastra-mini}.autoheal=true"]
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--no-check-certificate", "--spider", "https://127.0.0.1:443/healthz"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s

  # Restarts a labelled container Docker marks unhealthy (an app whose event loop is wedged stays
  # alive and serves nothing, which restart: always never sees). Datastores and Vault are not
  # labelled: restarting them on a health blip is worse than the blip, and a restarted Vault comes
  # back sealed. important: docker.sock is root-equivalent; network_mode none leaves it no network.
  autoheal:
    logging: *default-logging
    image: willfarrell/autoheal:1.2.0
    container_name: ${CONTAINER_PREFIX:-orcastra-mini}-autoheal
    restart: always
    network_mode: none
    environment:
      # The project's own label: a host running other stacks keeps its autoheal decisions apart.
      - AUTOHEAL_CONTAINER_LABEL=${CONTAINER_PREFIX:-orcastra-mini}.autoheal
      - AUTOHEAL_INTERVAL=${AUTOHEAL_INTERVAL:-10}
      - AUTOHEAL_START_PERIOD=${AUTOHEAL_START_PERIOD:-300}
      - AUTOHEAL_DEFAULT_STOP_TIMEOUT=${AUTOHEAL_DEFAULT_STOP_TIMEOUT:-30}
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    security_opt:
      - no-new-privileges:true

networks:
  orcastra-mini:
    driver: bridge

volumes:
  redis-data:
  postgres-data:
  vault-data:
EOF
```

### `config/nginx/mini.conf`

nginx terminates TLS, requests the client certificate (trust-on-first-use), and forwards the
certificate with the proxy secret. It sends certificate sign-in (`/api/v1/auth/`) and WebSocket
traffic straight to the backend, MCP clients (`/api/v1/mcp`) to the backend without any
certificate headers, and everything else to the frontend. The single-use tickets in WebSocket
URLs are kept out of the access log.

```bash
mkdir -p config/nginx/certs
cat > config/nginx/mini.conf <<'EOF'
# nginx for Orcastra Mini: TLS, the client-certificate request, and routing.
# Mounted as a template so ${AUTH_PROXY_SECRET} is substituted at start (NGINX_ENVSUBST_FILTER
# limits substitution to that variable, leaving $ssl_* and the rest to nginx).
#
# Routing:
#   /api/v1/auth/*  backend: certificate sign-in and first-administrator enrollment
#   /api/v1/mcp     backend: MCP clients authenticate with integration keys, never certificates
#   WebSocket       backend: consoles, terminals, feeds (single-use tickets)
#   everything else frontend, which proxies the rest of /api/v1 with the session
#
# The certificate headers are always overwritten from the TLS layer, and every hop to the
# frontend or backend carries the proxy secret: a header a browser sends never counts.

map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

# Only the socket endpoints (paths ending in /ws, or /ws/<name>) go to the backend as WebSockets;
# an Upgrade header on any other path still reaches the frontend.
map "$http_upgrade:$uri" $orcastra_upstream {
    default                                  http://frontend:2025;
    "~*^websocket:/api/v1/.+/ws(/[^/]+)?$"   http://backend:4050;
}

# Socket URLs carry a single-use ticket in the query string; keep it out of the access log.
map $http_upgrade $logged_request {
    default   $request;
    websocket "$request_method $uri [ws]";
}

log_format mini_redacted '$remote_addr - $remote_user [$time_local] '
                         '"$logged_request" $status $body_bytes_sent '
                         '"$http_referer" "$http_user_agent"';

server {
    listen 443 ssl;
    http2 on;
    server_name _;

    access_log /var/log/nginx/access.log mini_redacted;

    ssl_certificate     /etc/nginx/certs/server.crt;
    ssl_certificate_key /etc/nginx/certs/server.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # Trust on first use: ask for any client certificate and let the application decide whose it
    # is. optional_no_ca consults no CRL; revocation is enforced by the application per request.
    ssl_verify_client optional_no_ca;
    ssl_verify_depth  2;

    # No session resumption: every new connection presents the browser's current certificate,
    # so switching certificates takes effect on the next connection.
    ssl_session_tickets off;
    ssl_session_cache   off;
    keepalive_timeout   15s;

    # The escaped certificate rides on every request as a header (2 to 4 KB).
    large_client_header_buffers 8 16k;

    client_max_body_size 0;
    resolver 127.0.0.11 valid=30s ipv6=off;

    add_header Strict-Transport-Security "max-age=31536000" always;

    location ^~ /api/v1/auth/ {
        set $auth_upstream http://backend:4050;
        proxy_pass $auth_upstream;
        proxy_http_version 1.1;

        proxy_set_header X-SSL-Client-Verify  $ssl_client_verify;
        proxy_set_header X-SSL-Client-Cert    $ssl_client_escaped_cert;
        proxy_set_header X-SSL-Client-S-DN    $ssl_client_s_dn;
        proxy_set_header X-Auth-Proxy-Secret  "${AUTH_PROXY_SECRET}";
        proxy_set_header X-Orcastra-Proxy-Auth "";

        proxy_set_header Host               $http_host;
        proxy_set_header X-Real-IP          $remote_addr;
        proxy_set_header X-Orcastra-Client-IP $remote_addr;
        proxy_set_header X-Forwarded-For    $remote_addr;
        proxy_set_header X-Forwarded-Proto  https;
    }

    location ^~ /api/v1/mcp {
        set $mcp_upstream http://backend:4050;
        proxy_pass $mcp_upstream;
        proxy_http_version 1.1;
        proxy_buffering off;

        # Integration-key callers: no certificate headers, no proxy secret.
        proxy_set_header X-SSL-Client-Verify  "";
        proxy_set_header X-SSL-Client-Cert    "";
        proxy_set_header X-SSL-Client-S-DN    "";
        proxy_set_header X-Auth-Proxy-Secret  "";
        proxy_set_header X-Orcastra-Proxy-Auth "";

        proxy_set_header Host               $http_host;
        proxy_set_header X-Real-IP          $remote_addr;
        proxy_set_header X-Orcastra-Client-IP $remote_addr;
        proxy_set_header X-Forwarded-For    $remote_addr;
        proxy_set_header X-Forwarded-Proto  https;
        proxy_read_timeout 3600s;
    }

    location / {
        set $upstream $orcastra_upstream;
        proxy_pass $upstream;
        proxy_http_version 1.1;
        proxy_request_buffering off;

        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection $connection_upgrade;

        proxy_set_header X-SSL-Client-Verify $ssl_client_verify;
        proxy_set_header X-SSL-Client-Cert   $ssl_client_escaped_cert;
        proxy_set_header X-SSL-Client-S-DN   $ssl_client_s_dn;
        proxy_set_header X-Auth-Proxy-Secret "${AUTH_PROXY_SECRET}";
        proxy_set_header X-Orcastra-Proxy-Auth "";

        # Host with its port, so redirects and sign-in callbacks keep the published port.
        proxy_set_header Host               $http_host;
        proxy_set_header X-Real-IP          $remote_addr;
        proxy_set_header X-Orcastra-Client-IP $remote_addr;
        proxy_set_header X-Forwarded-For    $remote_addr;
        proxy_set_header X-Forwarded-Host   $http_host;
        proxy_set_header X-Forwarded-Proto  https;

        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
EOF
```

### `config/vault/vault.hcl`

```bash
mkdir -p config/vault
cat > config/vault/vault.hcl <<'EOF'
ui = true

storage "raft" {
  path    = "/vault/data"
  node_id = "orcastra-mini"
}

listener "tcp" {
  address         = "0.0.0.0:8200"
  cluster_address = "0.0.0.0:8201"
  # TLS is disabled here because Vault is only reachable on the private Docker network
  # (and 127.0.0.1 on the host for init/unseal). Front it with nginx/TLS if exposed.
  tls_disable = 1
}

api_addr     = "http://vault:8200"
cluster_addr = "http://vault:8201"
disable_mlock = false
EOF
```

### `.env`

Generate each secret with `openssl rand -hex 32`. The PostgreSQL password appears twice: once
on its own and once inside `DATABASE_URL`. `HTTPS_BIND` is the host address nginx listens on and
`UPLOADS_DIR` the host directory for image and ISO uploads. [Configuration](configuration.md)
explains every key.

```bash
cat > .env <<'EOF'
# Deployment
CONTAINER_PREFIX=orcastra-mini
APP_VERSION=1.0.0
HTTPS_PORT=6969
# The address nginx listens on; 0.0.0.0 is every interface.
HTTPS_BIND=0.0.0.0
UPLOADS_DIR=/var/orcastra/uploads

# Database
POSTGRES_USER=orcastra
POSTGRES_PASSWORD=<set a strong value>
POSTGRES_DB=orcastra
DATABASE_URL=postgresql+asyncpg://orcastra:<same as POSTGRES_PASSWORD>@postgres:5432/orcastra

# Secrets (openssl rand -hex 32 each; LOCAL_JWT_SECRET must differ from SECRET_KEY)
SECRET_KEY=
LOCAL_JWT_SECRET=
NEXTAUTH_SECRET=
AUTH_PROXY_SECRET=
BOOTSTRAP_ADMIN_TOKEN=

# Sessions and certificates
LOCAL_JWT_TTL_SECONDS=900
CLIENT_CERT_TTL_DAYS=365
CERT_SELF_RENEW_ENABLED=true

# Vault (set VAULT_TOKEN after step 6)
VAULT_ENABLED=true
VAULT_ADDR=http://vault:8200
VAULT_TOKEN=
VAULT_PKI_ROLE=lxd

# URLs (use your host and HTTPS_PORT)
NEXTAUTH_URL=https://your-host.example.com:6969
NEXT_PUBLIC_API_URL=https://your-host.example.com:6969
CORS_ORIGINS=https://your-host.example.com:6969

# Audit log (0 keeps every row)
AUDIT_DB_ENABLED=true
AUDIT_DB_RETENTION_DAYS=0
EOF
```

!!! warning "Keep secrets out of version control"
    Treat `.env` as sensitive. Never commit it. The values above are placeholders.

## 4. Provide the nginx TLS server certificate

This is the server (site) certificate nginx presents over HTTPS. It is separate from the
client certificates operators sign in with. A self-signed certificate is fine for a single
host.

Set the Common Name (`-subj "/CN=..."`) and the Subject Alternative Name (SAN) to the exact
host or IP you open in the browser. Browsers match the SAN (not the CN), and it must agree
with the `.env` URLs (`NEXTAUTH_URL` / `NEXT_PUBLIC_API_URL` / `CORS_ORIGINS`). Use `DNS:` for
a hostname and `IP:` for an address.

=== "Hostname"

    ```bash
    openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
      -keyout config/nginx/certs/server.key \
      -out config/nginx/certs/server.crt \
      -subj "/CN=your-host.example.com" \
      -addext "subjectAltName=DNS:your-host.example.com"
    ```

    `.env` URLs use `https://your-host.example.com:6969`.

=== "Private IP only"

    ```bash
    openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
      -keyout config/nginx/certs/server.key \
      -out config/nginx/certs/server.crt \
      -subj "/CN=192.0.2.50" \
      -addext "subjectAltName=IP:192.0.2.50"
    ```

    Replace `192.0.2.50` with your host's IP. Use the `IP:` form (not `DNS:`), and set the
    three `.env` URLs to `https://192.0.2.50:6969`.

!!! note "Self-signed certificate warning"
    A self-signed certificate makes the browser show a "not secure" warning the first time.
    Proceed past it, or import `server.crt` into the browser/OS trust store. Certificate
    sign-in still works, because it does not depend on the server certificate being trusted.

## 5. Pull the images and start

The backend runs as uid `1000` and writes into `config/backend/` (node display names, recovery
certificates; mounted at `/app/config`) and into the uploads directory. Create both and give them
to that uid. Run this in the deployment directory, with your `UPLOADS_DIR` if you changed it:

```bash
sudo mkdir -p config/backend /var/orcastra/uploads
sudo chown 1000:1000 config/backend /var/orcastra/uploads && sudo chmod 750 config/backend /var/orcastra/uploads
```

The rest of `config/` is not mounted into the backend, so `server.key` and the nginx template
stay root's. Then pull and start:

```bash
docker compose pull
docker compose up -d
docker compose ps
```

## 6. Initialise and unseal Vault

Vault starts sealed. Initialise once, then unseal after every restart (manual, by design). Once
an Administrator exists, they can also unseal Vault from the dashboard
([Operations](operations.md#vault-unseal-after-a-restart)).

!!! note "Point the CLI at HTTP"
    Vault runs over HTTP on the private Docker network (`tls_disable`), but the `vault` CLI
    defaults to HTTPS. Pass `-e VAULT_ADDR=http://127.0.0.1:8200` so the in-container CLI uses
    the right scheme. Without it you get `server gave HTTP response to HTTPS client`.

```bash
# Initialise (save the unseal keys and root token somewhere safe and offline)
docker compose exec -e VAULT_ADDR=http://127.0.0.1:8200 vault vault operator init

# Unseal (repeat with 3 different unseal keys)
docker compose exec -e VAULT_ADDR=http://127.0.0.1:8200 vault vault operator unseal <key-1>
docker compose exec -e VAULT_ADDR=http://127.0.0.1:8200 vault vault operator unseal <key-2>
docker compose exec -e VAULT_ADDR=http://127.0.0.1:8200 vault vault operator unseal <key-3>
```

Enable the PKI used to issue client certificates. The issuing CA must outlive the certificates
it signs, so give the CA a long TTL and the role a shorter maximum:

```bash
docker compose exec vault sh -c '
  export VAULT_ADDR=http://127.0.0.1:8200 ;
  export VAULT_TOKEN=<root-token> ;
  vault secrets enable -path=secret -version=2 kv ;
  vault secrets enable -path=pki_int pki ;
  vault secrets tune -max-lease-ttl=87600h pki_int ;
  vault write pki_int/root/generate/internal common_name="Orcastra Mini CA" ttl=87600h ;
  vault write pki_int/roles/lxd allow_any_name=true max_ttl=8760h ;
'
```

Put a token that can use `pki_int` into `.env` as `VAULT_TOKEN` (the root token works to start;
a scoped token is better for production), then recreate the backend:

```bash
docker compose up -d backend
```

!!! danger "CA TTL must exceed leaf TTL"
    If the CA and the role share the same TTL, issuance fails with
    "notAfter would result in a value beyond the expiration of the CA". The 10-year CA and
    1-year role above avoid this.

## 7. Bootstrap the first administrator

Certificate enrollment is trust-on-first-use. Generate a client certificate, then exchange the
one-time bootstrap token for the admin role. The first certificate to bootstrap becomes admin,
after which the window closes.

!!! tip "Run this on the machine with your browser"
    The certificate is self-signed and trusted by fingerprint, so generate it on your own
    computer (where the browser is) and run the `curl` from there, pointed at the URL you use
    to reach the dashboard. Then nothing has to be copied off the server.

```bash
# Self-signed client cert (the app trusts by fingerprint, not by CA)
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout admin.key -out admin.crt -subj "/CN=admin"
openssl pkcs12 -export -inkey admin.key -in admin.crt -out admin.p12   # set a password

# Enroll as admin (presents the cert to nginx, which forwards it to the backend)
curl -sk https://your-host.example.com:6969/api/v1/auth/cert-bootstrap \
  --cert admin.crt --key admin.key \
  -H 'Content-Type: application/json' \
  -d '{"bootstrap_token":"<BOOTSTRAP_ADMIN_TOKEN>","email":"admin@example.com"}'
```

`<BOOTSTRAP_ADMIN_TOKEN>` is the same value you set in `.env` in step 3. Use your own email: it
is how organizations, keys and the audit log know you, and it cannot be changed later. Without
`email`, the Administrator gets `admin@users.orcastra.internal` (from `CN=admin`). The commands
write `admin.crt`, `admin.key`, and `admin.p12` to the current directory; you import `admin.p12`
in step 8.

!!! tip "Or enroll in the browser"
    Instead of the `curl`, import `admin.p12` into your browser and open the dashboard. While no
    Administrator exists and `BOOTSTRAP_ADMIN_TOKEN` is set, the sign-in page shows the
    certificate's fingerprint and a form for the bootstrap token, an email and a display name.
    **Enroll as Administrator** enrolls the certificate and signs you in.

!!! note "Server has no public IP"
    Forward the dashboard port over the SSH session you already use, then treat
    `https://localhost:6969` as the dashboard:

    ```bash
    ssh -L 6969:127.0.0.1:6969 user@your-server
    ```

    Use `https://localhost:6969` in the `curl` above and in the browser, and set the three
    `.env` URLs to `https://localhost:6969` (then `docker compose up -d`). If your computer can
    already reach a private or VPN IP of the server, use that IP instead and skip the tunnel.

!!! note "Already created admin.p12 on the server?"
    Copy it to your computer with `scp`, or paste it through your existing SSH terminal with
    base64:

    ```bash
    base64 -w0 admin.p12; echo                          # on the server, copy the output
    echo 'PASTE_THE_BASE64' | base64 -d > admin.p12     # on macOS/Linux
    ```

    On Windows, save the copied text to `admin.b64`, then decode it with the built-in
    `certutil` (Command Prompt or PowerShell):

    ```powershell
    certutil -decode admin.b64 admin.p12
    ```

After it succeeds, close the window: blank `BOOTSTRAP_ADMIN_TOKEN` in `.env` and recreate the
backend.

```bash
docker compose up -d backend
```

## 8. Sign in and issue certificates for the other operators

1. Import the `admin.p12` from step 7 (the file you generated, copied to this machine if it was
   created on the server) into your browser or OS keychain.
2. Open `https://your-host.example.com:6969` and select the certificate when prompted.
3. As admin, open **Administration > Operators > Issue Certificate**. Enter the operator's email
   (it cannot be changed later), an optional display name, the role, an optional validity in
   days, and an organization if the operator belongs to one. The `.p12` downloads through the
   browser and its import password is shown once. Deliver both to the person through separate
   channels. (This download is separate from the `admin.p12` you generated yourself in step 7.)
4. The person imports that `.p12` and signs in the same way.

Revoke, reactivate, delete, restore, or change a role from the same page. Changes take effect
on the operator's next request. Issue a second Administrator certificate so that one lost device
does not lock everyone out. See [Certificate Authentication](certificate-auth.md) for the full
lifecycle and [Operations](operations.md) for day-2 tasks.

## Verify the deployment

```bash
docker compose ps    # all services healthy
```

- Browsing to the host serves the sign-in page over HTTPS.
- After importing a certificate, the dashboard loads and the role gates the navigation
  (Administrators see **Administration > Operators** and **Audit Log**, Partners see
  **Operators**, and Tenants see neither).
- **Administration > Audit Log > Verify Integrity** reports an intact chain.
- **Cluster > Host Health** shows the **Certificate Sign-In** and **Audit Log** checks as healthy
  once `BOOTSTRAP_ADMIN_TOKEN` is cleared.

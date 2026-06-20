# Quick Start

This guide deploys Orcastra Mini on a single host by pulling the published container images.
No source build is required. Commands assume a host reachable at
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

### `docker-compose.yml`

This Compose file runs the published backend and frontend images alongside PostgreSQL, Redis,
Vault, and nginx. Only nginx is published to the host.

```yaml
services:
  postgres:
    image: postgres:17-alpine
    container_name: ${CONTAINER_PREFIX:-orcastra-mini}-postgres
    restart: always
    environment:
      - POSTGRES_USER=${POSTGRES_USER}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=${POSTGRES_DB}
    volumes:
      - postgres-data:/var/lib/postgresql/data
    networks: [orcastra-mini]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s

  redis:
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
    image: hashicorp/vault:1.17
    container_name: ${CONTAINER_PREFIX:-orcastra-mini}-vault
    restart: always
    # Start as root only to fix the data-volume ownership, then drop to the vault user.
    user: root
    entrypoint: ["sh", "-c", "chown -R 100:1000 /vault/data && exec docker-entrypoint.sh server"]
    cap_add: [IPC_LOCK]
    ports:
      - "127.0.0.1:8200:8200"   # local-only admin access for init/unseal
    volumes:
      - vault-data:/vault/data
      - ./config/vault/vault.hcl:/vault/config/vault.hcl:ro
    networks: [orcastra-mini]

  backend:
    image: svlct/orcastra-dashboard-mini:backend-1.0.0-RC1
    container_name: ${CONTAINER_PREFIX:-orcastra-mini}-backend
    restart: always
    # No host port. The backend is reachable only on the Docker network.
    env_file: [.env]
    environment:
      - APP_VERSION=${APP_VERSION:-1.0.0-RC1}
      - DEBUG=${DEBUG:-false}
      - DATABASE_URL=${DATABASE_URL}
      - AUTH_ENABLED=true
      - AUTH_MODE=client-cert
      - LOCAL_JWT_SECRET=${LOCAL_JWT_SECRET:?LOCAL_JWT_SECRET is required}
      - LOCAL_JWT_TTL_SECONDS=${LOCAL_JWT_TTL_SECONDS:-3600}
      - BOOTSTRAP_ADMIN_TOKEN=${BOOTSTRAP_ADMIN_TOKEN:-}
      - AUTH_PROXY_SECRET=${AUTH_PROXY_SECRET:?AUTH_PROXY_SECRET is required}
      - TRUSTED_AUTH_PROXY_CIDRS=${TRUSTED_AUTH_PROXY_CIDRS:-}
      - CLIENT_CERT_TTL_DAYS=${CLIENT_CERT_TTL_DAYS:-365}
      - AUDIT_DB_ENABLED=true
      - VAULT_ENABLED=true
      - VAULT_ADDR=${VAULT_ADDR:-http://vault:8200}
      - VAULT_TOKEN=${VAULT_TOKEN}
      - VAULT_PKI_ROLE=${VAULT_PKI_ROLE:-lxd}
      - SECRET_KEY=${SECRET_KEY}
      - CORS_ORIGINS=${CORS_ORIGINS}
    volumes:
      - ./config:/app/config:rw
      - /var/orcastra/uploads:/app/uploads:rw
    networks: [orcastra-mini]
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on:
      redis: {condition: service_healthy}
      postgres: {condition: service_healthy}
      vault: {condition: service_started}
    security_opt:
      - no-new-privileges:true
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:4050/health')"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 120s

  frontend:
    image: svlct/orcastra-dashboard-mini:frontend-1.0.0-RC1
    container_name: ${CONTAINER_PREFIX:-orcastra-mini}-frontend
    restart: always
    # No host port - only nginx talks to the frontend.
    environment:
      - NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
      - NEXT_PUBLIC_AUTH_MODE=client-cert
      - INTERNAL_BACKEND_URL=http://backend:4050
      - AUTH_MODE=client-cert
      - AUTH_PROXY_SECRET=${AUTH_PROXY_SECRET:?AUTH_PROXY_SECRET is required}
      - NEXTAUTH_URL=${NEXTAUTH_URL}
      - NEXTAUTH_SECRET=${NEXTAUTH_SECRET}
      - AUTH_TRUST_HOST=true
    networks: [orcastra-mini]
    depends_on:
      backend: {condition: service_healthy}
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://127.0.0.1:2025"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

  nginx:
    image: nginx:1.27-alpine
    container_name: ${CONTAINER_PREFIX:-orcastra-mini}-nginx
    restart: always
    ports:
      - "${HTTPS_PORT:-6969}:443"
    environment:
      - AUTH_PROXY_SECRET=${AUTH_PROXY_SECRET:?AUTH_PROXY_SECRET is required}
      - NGINX_ENVSUBST_FILTER=^AUTH_PROXY_SECRET$$
    volumes:
      - ./config/nginx/mini.conf:/etc/nginx/templates/default.conf.template:ro
      - ./config/nginx/certs:/etc/nginx/certs:ro
    networks: [orcastra-mini]
    depends_on: [frontend]
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--no-check-certificate", "--spider", "https://127.0.0.1:443"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s

networks:
  orcastra-mini:
    driver: bridge

volumes:
  redis-data:
  postgres-data:
  vault-data:
```

### `config/nginx/mini.conf`

nginx terminates TLS, requests the client certificate (trust-on-first-use), and forwards the
verified certificate to the application. The session token is redacted from WebSocket access
log lines.

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

map $http_upgrade $orcastra_upstream {
    default   http://frontend:2025;
    websocket http://backend:4050;
}

# Console/terminal/monitoring WebSockets carry the session token as ?token=<JWT>. Redact the
# query string from WebSocket request lines so a replayable token is not written to the log.
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

    # Trust-on-first-use: request a client cert but do not reject unknown CAs. The
    # application decides trust from the fingerprint; revocation is enforced in the app layer.
    ssl_verify_client optional_no_ca;
    ssl_verify_depth  2;

    client_max_body_size 0;
    resolver 127.0.0.11 valid=30s ipv6=off;

    # Cert-login / bootstrap: straight to the backend with the verified cert + proxy secret.
    location ^~ /api/v1/auth/ {
        set $auth_upstream http://backend:4050;
        proxy_pass $auth_upstream;
        proxy_http_version 1.1;

        proxy_set_header X-SSL-Client-Verify  $ssl_client_verify;
        proxy_set_header X-SSL-Client-Cert    $ssl_client_escaped_cert;
        proxy_set_header X-SSL-Client-S-DN    $ssl_client_s_dn;
        proxy_set_header X-Auth-Proxy-Secret  "${AUTH_PROXY_SECRET}";

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }

    location / {
        set $upstream $orcastra_upstream;
        proxy_pass $upstream;
        proxy_http_version 1.1;

        # WebSocket upgrade for console / terminal / monitoring.
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection $connection_upgrade;

        # Always overwrite from the TLS layer so a browser cannot inject forged cert headers.
        proxy_set_header X-SSL-Client-Verify $ssl_client_verify;
        proxy_set_header X-SSL-Client-Cert   $ssl_client_escaped_cert;
        proxy_set_header X-SSL-Client-S-DN   $ssl_client_s_dn;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

### `config/vault/vault.hcl`

```hcl
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
```

### `.env`

Generate each secret with `openssl rand -hex 32`. The PostgreSQL password appears twice: once
on its own and once inside `DATABASE_URL`.

```ini
# Profile
CONTAINER_PREFIX=orcastra-mini
APP_VERSION=1.0.0-RC1
HTTPS_PORT=6969
AUTH_MODE=client-cert
NEXT_PUBLIC_AUTH_MODE=client-cert

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

# Session and certificates
LOCAL_JWT_TTL_SECONDS=3600
CLIENT_CERT_TTL_DAYS=365

# Vault (set VAULT_TOKEN after step 6)
VAULT_ENABLED=true
VAULT_ADDR=http://vault:8200
VAULT_TOKEN=

# URLs (use your host and HTTPS_PORT)
NEXTAUTH_URL=https://your-host.example.com:6969
NEXT_PUBLIC_API_URL=https://your-host.example.com:6969
CORS_ORIGINS=https://your-host.example.com:6969

# Audit
AUDIT_DB_ENABLED=true
```

!!! warning "Keep secrets out of version control"
    Treat `.env` as sensitive. Never commit it. The values above are placeholders.

## 4. Provide the nginx TLS server certificate

A self-signed certificate is fine for a single host.

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout config/nginx/certs/server.key \
  -out config/nginx/certs/server.crt \
  -subj "/CN=your-host.example.com" \
  -addext "subjectAltName=DNS:your-host.example.com,IP:<host-ip>"
```

## 5. Pull the images and start

```bash
docker compose pull
docker compose up -d
docker compose ps
```

## 6. Initialise and unseal Vault

Vault starts sealed. Initialise once, then unseal after every restart (manual, by design).

```bash
# Initialise (save the unseal keys and root token somewhere safe and offline)
docker compose exec vault vault operator init

# Unseal (repeat with 3 different unseal keys)
docker compose exec vault vault operator unseal <key-1>
docker compose exec vault vault operator unseal <key-2>
docker compose exec vault vault operator unseal <key-3>
```

Enable the PKI used to issue client certificates. The issuing CA must outlive the certificates
it signs, so give the CA a long TTL and the role a shorter maximum:

```bash
docker compose exec vault sh -c '
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

```bash
# Self-signed client cert (the app trusts by fingerprint, not by CA)
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout admin.key -out admin.crt -subj "/CN=admin"
openssl pkcs12 -export -inkey admin.key -in admin.crt -out admin.p12   # set a password

# Enroll as admin (presents the cert to nginx, which forwards it to the backend)
curl -sk https://your-host.example.com:6969/api/v1/auth/cert-bootstrap \
  --cert admin.crt --key admin.key \
  -H 'Content-Type: application/json' \
  -d '{"bootstrap_token":"<BOOTSTRAP_ADMIN_TOKEN>"}'
```

After it succeeds, close the window: blank `BOOTSTRAP_ADMIN_TOKEN` in `.env` and recreate the
backend.

```bash
docker compose up -d backend
```

## 8. Sign in and issue the other identities

1. Import `admin.p12` into your browser or OS keychain.
2. Open `https://your-host.example.com:6969` and select the certificate when prompted.
3. As admin, open **Administration -> Identities -> Issue Identity**. Choose a username, a role
   (partner or tenant), and a validity period. A password-protected `.p12` downloads and the
   one-time import password is shown once. Deliver both to the user out of band.
4. The user imports the `.p12` and signs in the same way.

Revoke, re-activate, or change a role from the same screen. Changes take effect on the
identity's next request. See [Certificate Authentication](certificate-auth.md) for the full
lifecycle and [Operations](operations.md) for day-2 tasks.

## Verify the deployment

```bash
docker compose ps    # all services healthy
```

- Browsing to the host serves the sign-in page over HTTPS.
- After importing a certificate, the dashboard loads and the role gates the navigation
  (admins see Administration; partners and tenants see a restricted set).
- **Administration -> Audit Log -> Verify integrity** reports a valid chain.

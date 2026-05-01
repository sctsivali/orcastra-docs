# VM 4 - Orcastra Dashboard

**Specifications:** 4 vCPU, 8 GB RAM, 60 GB Storage

The Orcastra Dashboard is the main web application consisting of a Next.js frontend, FastAPI backend, PostgreSQL database, Redis cache, and a Fluent Bit log collector sidecar.

---

## Step 1: Install Docker

Follow the [common Docker installation](index.md#common-docker-installation) steps.

Then configure the Docker storage driver:

```bash
cat > /etc/docker/daemon.json <<EOF
{
  "storage-driver": "vfs"
}
EOF

systemctl restart docker
```

---

## Step 2: Create Configuration Directories

```bash
mkdir -p config/fluent-bit
mkdir -p config/opensearch-dashboards
```

---

## Step 3: Create Docker Compose File

Create `docker-compose.prod.yml`:

```bash
nano docker-compose.prod.yml
```

??? note "Full docker-compose.prod.yml (click to expand)"

    ```yaml
    # ==========================================================================
    # Orcastra Dashboard - Production Docker Compose
    # ==========================================================================
    # USE THIS for on-prem deployment (pulls pre-built images from Docker Hub)
    # DO NOT use docker-compose.yml (that's for development/building from source)
    #
    # Production ports:
    #   Frontend: 4321
    #   Backend:  8765
    #
    # Usage:
    #   docker compose -f docker-compose.prod.yml up -d
    #   docker compose -f docker-compose.prod.yml down
    #   docker compose -f docker-compose.prod.yml logs -f
    # ==========================================================================

    services:
      # PostgreSQL Database
      postgres:
        image: postgres:17-alpine
        container_name: ${CONTAINER_PREFIX:-orcastra-dashboard}-postgres
        restart: always
        ports:
          - "${POSTGRES_PORT:-5432}:5432"
        environment:
          - POSTGRES_USER=${POSTGRES_USER}
          - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
          - POSTGRES_DB=${POSTGRES_DB}
        volumes:
          - postgres-data:/var/lib/postgresql/data
        networks:
          - orcastra-dashboard
        healthcheck:
          test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
          interval: 10s
          timeout: 5s
          retries: 5
          start_period: 10s

      # Redis Cache
      redis:
        image: redis:8-alpine
        container_name: ${CONTAINER_PREFIX:-orcastra-dashboard}-redis
        restart: always
        ports:
          - "${REDIS_PORT:-6381}:6379"
        command: redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru
        networks:
          - orcastra-dashboard
        healthcheck:
          test: ["CMD", "redis-cli", "ping"]
          interval: 10s
          timeout: 3s
          retries: 3
          start_period: 5s
        volumes:
          - redis-data:/data

      # Backend API (port: 8765)
      backend:
        image: svlct/orcastra-dashboard:backend-${APP_VERSION:-1.0.0-RC2}
        container_name: ${CONTAINER_PREFIX:-orcastra-dashboard}-backend
        restart: always
        ports:
          - "${BACKEND_PORT:-8765}:4050"
        env_file:
          - .env
        environment:
          - APP_VERSION=${APP_VERSION:-1.0.0-RC2}
          - API_VERSION=${API_VERSION:-1.0.0-RC2}
          - DEBUG=${DEBUG:-false}
          - DATABASE_URL=${DATABASE_URL}
          - AUTH_ENABLED=${AUTH_ENABLED:-true}
          - AUTHENTIK_ISSUER=${AUTHENTIK_ISSUER}
          - AUTHENTIK_AUDIENCE=${AUTHENTIK_CLIENT_ID}
          - AUTHENTIK_API_URL=${AUTHENTIK_API_URL:-}
          - AUTHENTIK_API_TOKEN=${AUTHENTIK_API_TOKEN:-}
          - ORCASTRA_DOMAIN=${ORCASTRA_DOMAIN:-orcastra.io}
        volumes:
          - ./config:/app/config:rw
        networks:
          - orcastra-dashboard
        extra_hosts:
          - "host.docker.internal:host-gateway"
        depends_on:
          redis:
            condition: service_healthy
          postgres:
            condition: service_healthy
        healthcheck:
          test: ["CMD", "python", "-c",
            "import urllib.request; urllib.request.urlopen('http://localhost:4050/health')"]
          interval: 30s
          timeout: 10s
          retries: 5
          start_period: 120s
        security_opt:
          - no-new-privileges:true
        read_only: false
        tmpfs:
          - /tmp:mode=1777,size=100m

      # Frontend (port: 4321)
      frontend:
        image: svlct/orcastra-dashboard:frontend-${APP_VERSION:-1.0.0-RC2}
        container_name: ${CONTAINER_PREFIX:-orcastra-dashboard}-frontend
        restart: always
        ports:
          - "${FRONTEND_PORT:-4321}:2025"
        environment:
          - NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
          - NEXT_PUBLIC_AUTHENTIK_LOGOUT_URL=${NEXT_PUBLIC_AUTHENTIK_LOGOUT_URL}
          - INTERNAL_BACKEND_URL=http://backend:4050
          - AUTHENTIK_ISSUER=${AUTHENTIK_ISSUER}
          - AUTHENTIK_CLIENT_ID=${AUTHENTIK_CLIENT_ID}
          - AUTHENTIK_CLIENT_SECRET=${AUTHENTIK_CLIENT_SECRET}
          - NEXTAUTH_URL=${NEXTAUTH_URL}
          - NEXTAUTH_SECRET=${NEXTAUTH_SECRET}
          - AUTH_TRUST_HOST=true
        networks:
          - orcastra-dashboard
        depends_on:
          backend:
            condition: service_healthy
        healthcheck:
          test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://127.0.0.1:2025"]
          interval: 30s
          timeout: 10s
          retries: 3
          start_period: 30s

      # Fluent Bit Log Collector (Sidecar)
      fluent-bit:
        image: fluent/fluent-bit:4.2.2-debug
        container_name: ${CONTAINER_PREFIX:-orcastra-dashboard}-fluent-bit
        restart: always
        volumes:
          - ./config/fluent-bit/fluent-bit.conf:/fluent-bit/etc/fluent-bit.conf:ro
          - ./config/fluent-bit/parsers.conf:/fluent-bit/etc/parsers.conf:ro
          - ./config/fluent-bit/parse_json.lua:/fluent-bit/etc/parse_json.lua:ro
          - fluent-bit-data:/fluent-bit/data
          - /var/lib/docker/containers:/var/lib/docker/containers:ro
          - /var/log/containers:/var/log/containers:ro
        environment:
          - OPENSEARCH_HOST=${OPENSEARCH_HOST:?OPENSEARCH_HOST is required}
          - OPENSEARCH_PORT=${OPENSEARCH_PORT:-9200}
          - OPENSEARCH_USER=${OPENSEARCH_USER:-fluentbit}
          - OPENSEARCH_PASSWORD=${OPENSEARCH_PASSWORD:?OPENSEARCH_PASSWORD is required}
        networks:
          - orcastra-dashboard
        depends_on:
          - backend
          - frontend
        healthcheck:
          test: ["CMD", "curl", "-sf", "http://127.0.0.1:2020/api/v1/health"]
          interval: 30s
          timeout: 10s
          retries: 3
          start_period: 15s

    networks:
      orcastra-dashboard:
        driver: bridge

    volumes:
      redis-data:
      postgres-data:
      fluent-bit-data:
    ```

---

## Step 4: Create Fluent Bit Configuration

### Main Configuration

Create `config/fluent-bit/fluent-bit.conf`:

??? note "Full fluent-bit.conf (click to expand)"

    ```ini
    # Fluent Bit Configuration for Orcastra Dashboard
    # Separates: Access Logs (90d) | Audit Logs (3yr) | App Logs (30d)

    [SERVICE]
        Flush              1
        Daemon             Off
        Log_Level          error
        Parsers_File       parsers.conf
        HTTP_Server        On
        HTTP_Listen        0.0.0.0
        HTTP_Port          2020
        Health_Check       On
        # Mark container unhealthy when shipping bursts errors / failed retries.
        HC_Errors_Count    5
        HC_Retry_Failure_Count  5
        HC_Period          60
        # Filesystem buffering sized for multi-day OpenSearch outages.
        storage.path             /fluent-bit/data/
        storage.sync             normal
        storage.checksum         off
        storage.max_chunks_up    256
        storage.backlog.mem_limit 512M

    [INPUT]
        Name              tail
        Path              /var/lib/docker/containers/*/*.log
        Path_Key          container_path
        Tag               docker.raw
        Parser            docker
        DB                /fluent-bit/data/docker.db
        DB.locking        true
        Mem_Buf_Limit     50MB
        Skip_Long_Lines   On
        Refresh_Interval  5
        Read_from_Head    False
        storage.type      filesystem

    [INPUT]
        Name              forward
        Listen            0.0.0.0
        Port              24224
        Tag               forward.raw
        Buffer_Chunk_Size 1M
        Buffer_Max_Size   6M
        storage.type      filesystem

    [FILTER]
        Name              nest
        Match             docker.raw
        Operation         lift
        Nested_under      log

    [FILTER]
        Name              modify
        Match             docker.*
        Add               environment production
        Add               cluster orcastra-dashboard
        Add               collector fluent-bit

    [FILTER]
        Name              rewrite_tag
        Match             docker.raw
        Rule              $log_type ^(access)$ log.access true
        Emitter_Name      emit_access

    [FILTER]
        Name              rewrite_tag
        Match             docker.raw
        Rule              $log_type ^(audit)$ log.audit true
        Emitter_Name      emit_audit

    [FILTER]
        Name              rewrite_tag
        Match             docker.raw
        Rule              $level .+ log.app true
        Emitter_Name      emit_app

    [FILTER]
        Name              rewrite_tag
        Match             docker.raw
        Rule              $message .+ log.app true
        Emitter_Name      emit_app_fallback

    [FILTER]
        Name              rewrite_tag
        Match             forward.raw
        Rule              $log_type ^(access)$ log.access true
        Emitter_Name      emit_fwd_access

    [FILTER]
        Name              rewrite_tag
        Match             forward.raw
        Rule              $log_type ^(audit)$ log.audit true
        Emitter_Name      emit_fwd_audit

    [FILTER]
        Name              rewrite_tag
        Match             forward.raw
        Rule              $level .+ log.app true
        Emitter_Name      emit_fwd_app

    [OUTPUT]
        Name              opensearch
        Match             log.access
        Host              ${OPENSEARCH_HOST}
        Port              ${OPENSEARCH_PORT}
        HTTP_User         ${OPENSEARCH_USER}
        HTTP_Passwd       ${OPENSEARCH_PASSWORD}
        Suppress_Type_Name On
        tls               On
        tls.verify        Off
        net.connect_timeout       10
        net.keepalive             on
        net.keepalive_idle_timeout 30
        Logstash_Format   On
        Logstash_Prefix   orcastra-access
        Logstash_DateFormat %Y.%m.%d
        Retry_Limit       no_limits
        Buffer_Size       10MB
        storage.total_limit_size  2G
        Trace_Error       On
        Replace_Dots      On
        Write_Operation   create
        Id_Key            request_id
        Generate_ID       On

    [OUTPUT]
        Name              opensearch
        Match             log.audit
        Host              ${OPENSEARCH_HOST}
        Port              ${OPENSEARCH_PORT}
        HTTP_User         ${OPENSEARCH_USER}
        HTTP_Passwd       ${OPENSEARCH_PASSWORD}
        Suppress_Type_Name On
        tls               On
        tls.verify        Off
        net.connect_timeout       10
        net.keepalive             on
        net.keepalive_idle_timeout 30
        Logstash_Format   On
        Logstash_Prefix   orcastra-audit
        Logstash_DateFormat %Y.%m.%d
        # Audit logs MUST NOT be dropped (compliance) - unlimited retries.
        Retry_Limit       no_limits
        Buffer_Size       10MB
        storage.total_limit_size  8G
        Trace_Error       On
        Replace_Dots      On
        Write_Operation   create
        Id_Key            event_id
        Generate_ID       On

    [OUTPUT]
        Name              opensearch
        Match             log.app
        Host              ${OPENSEARCH_HOST}
        Port              ${OPENSEARCH_PORT}
        HTTP_User         ${OPENSEARCH_USER}
        HTTP_Passwd       ${OPENSEARCH_PASSWORD}
        Suppress_Type_Name On
        tls               On
        tls.verify        Off
        net.connect_timeout       10
        net.keepalive             on
        net.keepalive_idle_timeout 30
        Logstash_Format   On
        Logstash_Prefix   orcastra-app
        Logstash_DateFormat %Y.%m.%d
        Retry_Limit       no_limits
        Buffer_Size       10MB
        storage.total_limit_size  4G
        Trace_Error       On
        Replace_Dots      On
        Generate_ID       On
    ```

### Parsers Configuration

Create `config/fluent-bit/parsers.conf`:

??? note "Full parsers.conf (click to expand)"

    ```ini
    [PARSER]
        Name              docker
        Format            json
        Time_Key          time
        Time_Format       %Y-%m-%dT%H:%M:%S.%L%z
        Time_Keep         On
        Decode_Field_As   json log

    [PARSER]
        Name              orcastra_json
        Format            json
        Time_Key          @timestamp
        Time_Format       %Y-%m-%dT%H:%M:%S.%L%z
        Time_Keep         On
        Types             latency_ms:float request_size:integer response_size:integer status_code:integer

    [PARSER]
        Name              nextjs_json
        Format            json
        Time_Key          timestamp
        Time_Format       %Y-%m-%dT%H:%M:%S.%LZ
        Time_Keep         On

    [PARSER]
        Name              authentik_json
        Format            json
        Time_Key          timestamp
        Time_Format       %Y-%m-%dT%H:%M:%S.%L%z
        Time_Keep         On

    [PARSER]
        Name              syslog
        Format            regex
        Regex             ^\<(?<pri>[0-9]+)\>(?<time>[^ ]* {1,2}[^ ]* [^ ]*) (?<host>[^ ]*) (?<ident>[a-zA-Z0-9_\/\.\-]*)(?:\[(?<pid>[0-9]+)\])?(?:[^\:]*\:)? *(?<message>.*)$
        Time_Key          time
        Time_Format       %b %d %H:%M:%S

    [MULTILINE_PARSER]
        name              python_traceback
        type              regex
        flush_timeout     1000
        rule              "start_state"  "/^Traceback \(most recent call last\):$/"  "cont"
        rule              "cont"         "/^[\t ]+/"                                  "cont"
        rule              "cont"         "/^\w+Error:/"                               "cont"
        rule              "cont"         "/^\w+Exception:/"                           "cont"
    ```

### Lua Script

Create `config/fluent-bit/parse_json.lua`:

```lua
-- Parse nested JSON from Docker log field and merge into record

function parse_log_json(tag, timestamp, record)
    local log_field = record["log"]

    if log_field == nil or type(log_field) ~= "string" then
        return 0, timestamp, record
    end

    log_field = string.gsub(log_field, "^%s+", "")
    log_field = string.gsub(log_field, "%s+$", "")

    if string.sub(log_field, 1, 1) ~= "{" then
        return 0, timestamp, record
    end

    local cjson_safe = require("cjson.safe")
    local parsed, err = cjson_safe.decode(log_field)

    if parsed and type(parsed) == "table" then
        for key, value in pairs(parsed) do
            record[key] = value
        end
        record["log"] = nil
        return 1, timestamp, record
    else
        return 0, timestamp, record
    end
end
```

---

## Step 5: Generate Secrets

```bash
echo "=== Save these values securely ==="
echo "POSTGRES_PASSWORD: $(openssl rand -hex 16)"
echo "SECRET_KEY:        $(openssl rand -hex 32)"
echo "NEXTAUTH_SECRET:   $(openssl rand -base64 32)"
python3 -c "from cryptography.fernet import Fernet; \
  print('REDIS_ENCRYPTION_KEY:', Fernet.generate_key().decode())" 2>/dev/null \
  || echo "REDIS_ENCRYPTION_KEY: (install python3-cryptography)"
```

!!! warning "PostgreSQL Password Format"
    `POSTGRES_PASSWORD` uses `rand -hex` (not `-base64`) to avoid special characters (`+`, `/`, `=`) that break the `DATABASE_URL` connection string. The **same password** must appear identically in both `POSTGRES_PASSWORD` and `DATABASE_URL`.

---

## Step 6: Create Environment File

Create the `.env` file with values collected from all VMs:

```bash
nano .env
```

```ini
# === Version ===
APP_VERSION=1.0.0-RC2
API_VERSION=1.0.0-RC2
CONTAINER_PREFIX=orcastra-dashboard

# === PostgreSQL ===
POSTGRES_USER=orcastra
POSTGRES_PASSWORD=<GENERATED_HEX_PASSWORD>
POSTGRES_DB=orcastra_dashboard
POSTGRES_PORT=5432
DATABASE_URL=postgresql+asyncpg://orcastra:<SAME_PASSWORD>@postgres:5432/orcastra_dashboard

# === Backend (port: 8765) ===
BACKEND_PORT=8765
ORCASTRA_DOMAIN=
DEBUG=false

# === Frontend (port: 4321) ===
FRONTEND_PORT=4321
NEXT_PUBLIC_API_URL=http://<VM4_IP>:8765
INTERNAL_BACKEND_URL=http://backend:4050

# === Vault (VM 2) ===
VAULT_ENABLED=true
VAULT_ADDR=http://<VM2_IP>:8200
VAULT_TOKEN=<DASHBOARD_TOKEN_FROM_VM2>
VAULT_PKI_ROLE=lxd

# === Redis ===
REDIS_ENABLED=true
REDIS_PORT=6381
REDIS_URL=redis://redis:6379/0

# === Security ===
CORS_ORIGINS=http://<VM4_IP>:4321
RATE_LIMIT_ENABLED=true
REDIS_ENCRYPTION_ENABLED=true
REDIS_ENCRYPTION_KEY=<GENERATED_FERNET_KEY>
SECRET_KEY=<GENERATED_HEX_KEY>

# === Authentik (VM 1) ===
AUTH_ENABLED=true
AUTHENTIK_ISSUER=http://<VM1_IP>:9000/application/o/orcastra-dashboard/
NEXT_PUBLIC_AUTHENTIK_LOGOUT_URL=http://<VM1_IP>:9000/application/o/orcastra-dashboard/end-session/
AUTHENTIK_CLIENT_ID=<CLIENT_ID_FROM_VM1>
AUTHENTIK_CLIENT_SECRET=<CLIENT_SECRET_FROM_VM1>
AUTHENTIK_API_URL=http://<VM1_IP>:9000
AUTHENTIK_API_TOKEN=<API_TOKEN_FROM_VM1>

# === NextAuth ===
NEXTAUTH_URL=http://<VM4_IP>:4321
NEXTAUTH_SECRET=<GENERATED_BASE64_KEY>
AUTH_TRUST_HOST=true

# === Logging (VM 3) ===
OPENSEARCH_HOST=<VM3_IP>
OPENSEARCH_PORT=9200
OPENSEARCH_USER=fluentbit
OPENSEARCH_PASSWORD=<FLUENTBIT_PASSWORD_FROM_VM3>
JSON_LOGS=true
LOG_LEVEL=INFO
```

!!! warning "Placeholder Replacement"
    Replace **all** `<...>` placeholders with actual values. The `OPENSEARCH_HOST` should be the **IP address only** - no `http://` prefix.

---

## Step 7: Start the Dashboard

### Authenticate with Docker Hub

```bash
docker login
```

Follow the instructions to authenticate (copy the confirmation code and visit the activation URL).

### Pull and Start

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

```bash
# Don't forget to logout after pulling
docker logout
```

!!! info "Database Auto-Creation"
    Database tables are automatically created on backend startup via SQLAlchemy `create_all`. No need to run Alembic for fresh deployments. Alembic is only needed for schema migrations on existing databases.

---

## Step 8: Fix Docker-to-Authentik Connectivity

!!! warning "Required for LXD Deployments"
    Docker containers on VM 4 cannot reach LXD port-forwarded IPs by default. The frontend container needs to reach Authentik (VM 1) for OIDC discovery. This step adds iptables rules to route traffic correctly.

### Get Docker Bridge Subnet

```bash
DOCKER_BRIDGE=$(docker network inspect root_orcastra-dashboard \
  --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null \
  || docker network inspect orcastra-dashboard \
  --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null)
echo "Docker subnet: $DOCKER_BRIDGE"
```

### Add iptables Rules

```bash
# Route: Docker → LXD Host IP:9000 → Authentik VM private IP:9000
iptables -t nat -A PREROUTING \
  -s $DOCKER_BRIDGE -d <LXD_HOST_IP> \
  -p tcp --dport 9000 \
  -j DNAT --to-destination <VM1_PRIVATE_IP>:9000

iptables -t nat -A POSTROUTING \
  -d <VM1_PRIVATE_IP> -p tcp --dport 9000 \
  -j MASQUERADE

# (Optional) Route to Vault if VAULT_ADDR uses LXD host IP
iptables -t nat -A PREROUTING \
  -s $DOCKER_BRIDGE -d <LXD_HOST_IP> \
  -p tcp --dport 8200 \
  -j DNAT --to-destination <VM2_PRIVATE_IP>:8200

iptables -t nat -A POSTROUTING \
  -d <VM2_PRIVATE_IP> -p tcp --dport 8200 \
  -j MASQUERADE
```

### Verify Rules

```bash
iptables -t nat -L -n | grep DNAT
```

### Test Connectivity

```bash
docker exec orcastra-dashboard-frontend sh -c \
  "wget -qO- --timeout=5 \
  http://<LXD_HOST_IP>:9000/application/o/orcastra-dashboard/.well-known/openid-configuration \
  2>&1 | head -3"
```

Should output JSON with `"issuer"`, `"authorization_endpoint"`, etc.

### Restart Services

```bash
docker compose -f docker-compose.prod.yml restart frontend backend
```

!!! danger "Persist iptables Rules"
    iptables rules are **not persistent** across reboots. To make them permanent:

    ```bash
    apt install -y iptables-persistent
    netfilter-persistent save
    ```

!!! info "NEXT_PUBLIC_* Variables"
    The Docker image uses a runtime entrypoint script (`entrypoint.sh`) that automatically replaces placeholder URLs with real values from your `.env` file on every container start. No manual patching or image rebuilding needed.

---

## Troubleshooting: API Key Create Returns HTTP 503

If **Settings -> Integrations -> Create API Key** returns `HTTP 503`, verify the Vault policy and token permissions.

### Symptom

- Frontend toast: `Failed to create API key - HTTP 503`
- Backend logs may show Vault access failure on `secret/metadata/integrations/api_keys`

### Root Cause

The Dashboard Vault token is valid, but policy lacks one or both integrations paths:

- `secret/data/integrations/*`
- `secret/metadata/integrations/*`

### Validate from Dashboard VM (Backend Container)

```bash
docker exec orcastra-dashboard-backend python -c "
from app.core.config import get_settings
s = get_settings()
print('vault_enabled:', s.vault_enabled)
print('vault_addr:', s.vault_addr)
print('vault_token_set:', bool(s.vault_token))
"
```

```bash
docker exec orcastra-dashboard-backend python -c "
from app.core.vault_client import get_vault_client
from app.core.config import get_settings
s = get_settings()
vc = get_vault_client(s.vault_addr, s.vault_token)
print(vc.vault_list('secret/metadata/integrations/api_keys'))
"
```

If this returns `403 Client Error: Forbidden`, update policy on VM2 (Vault) per [VM 2 guide](vm2-vault.md#step-5-create-policy-and-token).

### Expected Healthy State

This command should not return 403:

```bash
vault kv list secret/integrations/api_keys
```

First-time setup usually returns:

```text
No value found at secret/metadata/integrations/api_keys
```

---

## Output Summary

After completing VM 4, the Orcastra Dashboard should be accessible at:

| Service | URL |
|---|---|
| Frontend | `http://<VM4_IP>:4321` |
| Backend API | `http://<VM4_IP>:8765` |
| Health Check | `http://<VM4_IP>:8765/health` |

---

**Next:** [Verification & Testing](../operations/verification.md) or [Domain Setup](../operations/domain-setup.md)

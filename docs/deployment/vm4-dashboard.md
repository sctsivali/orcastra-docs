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

Create a dedicated deployment directory and work from it, then create the configuration subdirectories inside it. Keeping the compose file, `.env`, and config together in one place makes upgrades and troubleshooting predictable. Docker Compose also derives the project name from this directory, which is why later steps refer to the `orcastra_orcastra-dashboard` network.

```bash
mkdir -p ~/orcastra && cd ~/orcastra
mkdir -p config/fluent-bit
mkdir -p config/opensearch-dashboards
```

---

## Step 3: Get the Docker Compose File

Fetch the compose file that belongs to the release you are deploying. Pin the tag rather
than taking `main`: the compose file and the images are versioned together, and a compose
file newer than your images can reference variables those images ignore.

```bash
RELEASE=v1.0.0-RC4
curl -fsSL -o docker-compose.prod.yml \
  "https://raw.githubusercontent.com/sctsivali/orcastra-dashboard/${RELEASE}/docker-compose.prod.yml"
```

Confirm you got the whole file rather than a 404 page:

```bash
docker compose -f docker-compose.prod.yml config --services
```

Expected output, one service per line: `postgres`, `redis`, `backend`, `frontend`,
`fluent-bit`, `autoheal`.

!!! warning "The reference copy below can be stale"
    The block below is an offline fallback for air-gapped installs. It is a snapshot, not
    the source of truth, and it has drifted before. Use the `curl` above whenever you can
    reach GitHub.

### What this compose file gives you that older copies did not

If you are working from a copy taken before v1.0.0-RC4, check for each of these. Several
lose data rather than features.

| Element | Why it is there |
|---------|-----------------|
| `autoheal` sidecar, plus `autoheal=true` labels on backend and frontend | `restart: always` only reacts to a process exiting. A uvicorn whose event loops are all blocked stays alive and serves nothing, and no healthcheck failure will restart it on its own. Postgres, Redis and fluent-bit are deliberately left unlabelled. |
| `/var/orcastra/uploads:/app/uploads:rw` | Without it, in-flight image and ISO uploads live in the container layer and are destroyed by every recreate. |
| `mem_limit` and `cpus` on the backend | Four workers were measured at roughly 500 MB each under load. Against too small a cap the kernel OOM-kills them quietly: the restart count stays at 0 and the healthcheck keeps passing. |
| `stop_grace_period: 30s` | Gives uvicorn's 20 second graceful shutdown room to drain. Docker's 10 second default severs live console and terminal sessions on every deploy. |
| Backend healthcheck with `timeout=3` inside the probe | Without an internal timeout, a hung probe and a hung application look identical to Docker. |
| `WEB_CONCURRENCY` and the `ORCASTRA_*` levers | Incident tuning without a rebuild. |
| Six `MAP_TILE_*` variables in the frontend service | The frontend service has no `env_file`, so these reach it only through this block. Setting them in `.env` against an older compose file does nothing at all. |

??? note "Reference copy of docker-compose.prod.yml (click to expand)"

    # =============================================================================
    # Orcastra Dashboard - Production Docker Compose
    # =============================================================================
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
    # =============================================================================

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
        image: svlct/orcastra-dashboard:backend-${APP_VERSION:-latest}
        container_name: ${CONTAINER_PREFIX:-orcastra-dashboard}-backend
        restart: always
        ports:
          - "${BACKEND_PORT:-8765}:4050"
        env_file:
          - .env
        environment:
          - APP_VERSION=${APP_VERSION:-latest}
          - API_VERSION=${API_VERSION:-latest}
          - DEBUG=${DEBUG:-false}
          - DATABASE_URL=${DATABASE_URL}
          - AUTH_ENABLED=${AUTH_ENABLED:-true}
          - AUTHENTIK_ISSUER=${AUTHENTIK_ISSUER}
          - AUTHENTIK_AUDIENCE=${AUTHENTIK_CLIENT_ID}
          - AUTHENTIK_API_URL=${AUTHENTIK_API_URL:-}
          - AUTHENTIK_API_TOKEN=${AUTHENTIK_API_TOKEN:-}
          - ORCASTRA_DOMAIN=${ORCASTRA_DOMAIN:-orcastra.io}
          # Worker count is tunable without a rebuild. Keep at 4 unless an incident calls for it:
          # fewer workers doubles the blast radius of a single wedged event loop.
          - WEB_CONCURRENCY=${WEB_CONCURRENCY:-4}
          # Incident levers, tunable without a rebuild. Defaults live in
          # app/services/lxd/proxy.py; set these only to override during an incident.
          - ORCASTRA_RAW_PROXY_TIMEOUT=${ORCASTRA_RAW_PROXY_TIMEOUT:-20}
          - ORCASTRA_PROXY_CONNECT_TIMEOUT=${ORCASTRA_PROXY_CONNECT_TIMEOUT:-15}
          - ORCASTRA_PROXY_POOL_WORKERS=${ORCASTRA_PROXY_POOL_WORKERS:-8}
          - ORCASTRA_LXD_RETRY_READ=${ORCASTRA_LXD_RETRY_READ:-false}
        volumes:
          - ./config:/app/config:rw
          - /var/orcastra/uploads:/app/uploads:rw
        networks:
          - orcastra-dashboard
        extra_hosts:
          - "host.docker.internal:host-gateway"
        depends_on:
          redis:
            condition: service_healthy
          postgres:
            condition: service_healthy
        # Resource limits: ensures Dashboard Host shows container resources, not the host
        # Measured, not guessed: four uvicorn workers sat at 587/524/461/446 MB under QA load,
        # 2018 MB against a 2g cap, and the kernel OOM-killed two of them. That failure is silent
        # (RestartCount stays 0, the healthcheck passes, no ERROR line is logged) and shows up
        # only as dropped requests and severed console sessions. vm4 is specified at 8 GB, so the
        # backend was simply under-provisioned. Scale this up further as clusters are added:
        # per-worker memory grows with the number of registered clusters.
        mem_limit: ${BACKEND_MEM_LIMIT:-4g}
        cpus: ${BACKEND_CPUS:-2}
        healthcheck:
          # Must stay in sync with backend/Dockerfile HEALTHCHECK. Compose wins at runtime, the
          # Dockerfile value is what a bare `docker run` of the image gets.
          # The probe carries its own 3s timeout: without one, a hung probe is indistinguishable
          # from a hung app, which is the same defect class this release fixes in the app itself.
          test: ["CMD", "python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:4050/health', timeout=3).status == 200 else 1)"]
          interval: 15s
          timeout: 5s
          retries: 5
          start_period: 120s
        # Gives uvicorn's 20s graceful shutdown room to drain before Docker SIGKILLs it. Without
        # this, Docker's 10s default cuts live terminal and console sessions on every deploy.
        stop_grace_period: 30s
        labels:
          # Consumed by the autoheal sidecar below. restart:always only reacts to process exit,
          # and the RC3 outage was a process that stayed alive while serving nothing: the
          # healthcheck failed 4543 consecutive times with no actor subscribed to the signal.
          - "autoheal=true"
        security_opt:
          - no-new-privileges:true
        read_only: false
        tmpfs:
          - /tmp:mode=1777,size=${BACKEND_TMPFS_SIZE:-256m}

      # Frontend (port: 4321)
      frontend:
        image: svlct/orcastra-dashboard:frontend-${APP_VERSION:-latest}
        container_name: ${CONTAINER_PREFIX:-orcastra-dashboard}-frontend
        restart: always
        ports:
          - "${FRONTEND_PORT:-4321}:2025"
        environment:
          - NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
          - NEXT_PUBLIC_AUTHENTIK_LOGOUT_URL=${NEXT_PUBLIC_AUTHENTIK_LOGOUT_URL}
          - FORWARD_CLIENT_IP_HEADERS=${FORWARD_CLIENT_IP_HEADERS:-true}
          - TRUSTED_CLIENT_IP_HEADERS=${TRUSTED_CLIENT_IP_HEADERS:-cf-connecting-ip,true-client-ip,cf-connecting-ipv6,x-forwarded-for,x-real-ip}
          # Internal Docker URL for server-side API proxying (avoids external DNS/firewall issues)
          - INTERNAL_BACKEND_URL=http://backend:4050
          # Regions map basemap. Left unset the dashboard draws the outline map bundled with
          # the image: no outbound request, no API key, works air-gapped. Point MAP_TILE_URL at
          # an XYZ tile template to use your own provider instead. Runtime only, no rebuild.
          - MAP_TILE_URL=${MAP_TILE_URL:-}
          - MAP_TILE_URL_DARK=${MAP_TILE_URL_DARK:-}
          - MAP_TILE_ATTRIBUTION=${MAP_TILE_ATTRIBUTION:-}
          - MAP_TILE_ATTRIBUTION_URL=${MAP_TILE_ATTRIBUTION_URL:-}
          - MAP_TILE_SUBDOMAINS=${MAP_TILE_SUBDOMAINS:-}
          - MAP_TILE_MAX_ZOOM=${MAP_TILE_MAX_ZOOM:-}
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
        labels:
          - "autoheal=true"

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
          # /api/v1/health turns red when HC_Errors_Count or HC_Retry_Failure_Count is exceeded.
          # Combined with restart:always this surfaces shipping failures to Docker, not just HTTP 200.
          test: ["CMD", "curl", "-sf", "http://127.0.0.1:2020/api/v1/health"]
          interval: 30s
          timeout: 10s
          retries: 3
          start_period: 30s

      # Watchdog that restarts containers Docker has marked unhealthy.
      #
      # Why this exists: `restart: always` only reacts to a process EXIT. A uvicorn whose event
      # loops are all blocked stays alive and serves nothing, so it never restarts itself. The
      # healthcheck detected exactly that and failed 4543 consecutive times with nothing
      # subscribed to the signal, which is how a fault became a 50 hour outage.
      #
      # This is defence in depth, not the fix. The fix is that outbound cluster calls are now
      # bounded and off the event loop.
      #
      # Opt-in by label on purpose, never AUTOHEAL_CONTAINER_LABEL=all. Notably NOT labelled:
      #   postgres/redis - restarting a datastore on a health blip is worse than the blip
      #   fluent-bit     - its healthcheck goes red when OpenSearch is unreachable, so labelling
      #                    it would restart-storm the log shipper through any OpenSearch outage
      #                    and discard its buffers on each cycle
      #
      # Docker's own start_period is the anti-loop guard: during it a container reports `starting`
      # and never `unhealthy`, so a container that cannot boot is restarted at most once per cycle
      # rather than in a tight loop.
      #
      # note: mounting docker.sock grants this container root-equivalent control of the host.
      # network_mode: none removes its only exfiltration path, since it talks to the daemon over
      # a unix socket and needs no network at all.
      autoheal:
        image: willfarrell/autoheal:1.2.0
        container_name: ${CONTAINER_PREFIX:-orcastra-dashboard}-autoheal
        restart: always
        network_mode: none
        environment:
          - AUTOHEAL_CONTAINER_LABEL=autoheal
          - AUTOHEAL_INTERVAL=${AUTOHEAL_INTERVAL:-5}
          - AUTOHEAL_START_PERIOD=${AUTOHEAL_START_PERIOD:-120}
          - AUTOHEAL_DEFAULT_STOP_TIMEOUT=${AUTOHEAL_DEFAULT_STOP_TIMEOUT:-30}
        volumes:
          - /var/run/docker.sock:/var/run/docker.sock
        security_opt:
          - no-new-privileges:true

    networks:
      orcastra-dashboard:
        driver: bridge

    volumes:
      redis-data:
      postgres-data:
      fluent-bit-data:

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
        # Audit logs MUST NOT be dropped (compliance), unlimited retries.
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

### JSON Parsing

Nested JSON in the Docker `log` field is lifted to top-level fields by the built-in
`[FILTER] Name nest` / `Operation lift` step already defined in `fluent-bit.conf`. The
pipeline does not use a Lua script, so none needs to be created or mounted.

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
# Pin a release tag, do NOT use latest in production.
APP_VERSION=<PINNED_RELEASE_TAG>
API_VERSION=<SAME_RELEASE_TAG>
CONTAINER_PREFIX=orcastra-dashboard

# === PostgreSQL ===
POSTGRES_USER=orcastra
POSTGRES_PASSWORD=<GENERATED_HEX_PASSWORD>
POSTGRES_DB=orcastra_dashboard
POSTGRES_PORT=5432
DATABASE_URL=postgresql+asyncpg://orcastra:<SAME_PASSWORD>@postgres:5432/orcastra_dashboard

# === Backend (port: 8765) ===
# Uvicorn workers, and the caps that keep them from being OOM-killed silently.
WEB_CONCURRENCY=4
BACKEND_MEM_LIMIT=4g
BACKEND_CPUS=2
# Memory-backed /tmp holding LXD TLS material. /health reports temp_storage.degraded
# past 80 percent, and deliberately still answers 200 so the container healthcheck does
# not restart the backend and clear the evidence.
BACKEND_TMPFS_SIZE=256m
# Autoheal watchdog, for a backend that is alive but serving nothing.
AUTOHEAL_INTERVAL=5
AUTOHEAL_START_PERIOD=120
AUTOHEAL_DEFAULT_STOP_TIMEOUT=30
# Live sessions are shared across workers through Redis. Keep the stale window above
# three times the heartbeat, and raise both or neither.
SESSION_REGISTRY_HEARTBEAT_SECONDS=2
SESSION_REGISTRY_STALE_SECONDS=10
BACKEND_PORT=8765
ORCASTRA_DOMAIN=
DEBUG=false
BACKEND_TMPFS_SIZE=256m

# === Regions map basemap (optional) ===
# Unset draws the outline map bundled in the image: no outbound request, no API key,
# works air-gapped. Point MAP_TILE_URL at an XYZ template to use a provider instead.
# Read at runtime, so a change needs a restart but no rebuild.
MAP_TILE_URL=
MAP_TILE_URL_DARK=
MAP_TILE_ATTRIBUTION=
MAP_TILE_ATTRIBUTION_URL=
MAP_TILE_SUBDOMAINS=
MAP_TILE_MAX_ZOOM=

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
RATE_LIMIT_REQUESTS=500
RATE_LIMIT_SUBNET_REQUESTS=1500
# Bypass budget for ?fresh=true, which skips the cache and fans out to every
# cluster in scope. Counted per token subject, not per IP.
RATE_LIMIT_FRESH_REQUESTS=12
RATE_LIMIT_WINDOW_SECONDS=60
SECURITY_PROBE_BLOCK_ENABLED=true
TRUSTED_PROXY_CIDRS=127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128,fc00::/7,fe80::/10
ALLOW_PRIVATE_FORWARDED_IPS=false
FORWARD_CLIENT_IP_HEADERS=true
TRUSTED_CLIENT_IP_HEADERS=cf-connecting-ip,true-client-ip,cf-connecting-ipv6,x-forwarded-for,x-real-ip
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
```

!!! danger "OpenSearch credentials are not optional"
    The compose file uses `${OPENSEARCH_HOST:?}` and `${OPENSEARCH_PASSWORD:?}`. With either
    unset, `docker compose up` refuses to start the entire stack, not just fluent-bit.
    Earlier versions of this guide grouped them under an optional heading. They never were.

!!! warning "Placeholder Replacement"
    Replace **all** `<...>` placeholders with actual values. The `OPENSEARCH_HOST` should be the **IP address only**, no `http://` prefix.

!!! warning "Pin Image Tags in Production"
    Do not keep `APP_VERSION=latest` on production VMs. Always pin `APP_VERSION`/`API_VERSION` to the exact release tag you intend to deploy so the pulled backend/frontend images match the expected code and security hardening.

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

If you update environment variables and need deterministic rollout:

```bash
docker compose -f docker-compose.prod.yml up -d --force-recreate backend frontend
```

```bash
# Don't forget to logout after pulling
docker logout
```

!!! info "Migrations Run Automatically on Every Start"
    The backend entrypoint runs `python -m app.core.db_bootstrap` and then
    `alembic upgrade head` before the API server starts. A fresh database is built from the
    migrations. There is no `create_all` path: releases up to v1.0.0-RC2 built the schema
    that way, silently diverged from the migrations, and left databases with no revision
    stamp, so it was retired. Set `RUN_MIGRATIONS=false` only when the schema is managed
    out of band.

    A migration failure stops the container rather than serving against a schema the code
    does not match. If it exits during startup, read the `[entrypoint]` lines in
    `docker compose -f docker-compose.prod.yml logs backend`.

### Verify Runtime Environment (Client IP Forwarding)

```bash
docker compose -f docker-compose.prod.yml exec frontend sh -lc \
  'env | egrep "FORWARD_CLIENT_IP_HEADERS|TRUSTED_CLIENT_IP_HEADERS|INTERNAL_BACKEND_URL"'

docker compose -f docker-compose.prod.yml exec backend sh -lc \
  'env | egrep "TRUSTED_PROXY_CIDRS|ALLOW_PRIVATE_FORWARDED_IPS|RATE_LIMIT_"'
```

Expected result: values reflect your `.env` and are not empty.

---

## Step 8: Fix Docker-to-Authentik Connectivity

!!! warning "Required for LXD Deployments"
    Docker containers on VM 4 cannot reach LXD port-forwarded IPs by default. The frontend container needs to reach Authentik (VM 1) for OIDC discovery. This step adds iptables rules to route traffic correctly.

### Get Docker Bridge Subnet

```bash
# The compose network has no explicit name:, so Docker prefixes it with the
# compose project name (the folder you run compose from) -> <project>_orcastra-dashboard.
# Auto-detect the real network name instead of hard-coding the prefix:
NET=$(docker network ls --filter name=orcastra-dashboard --format '{{.Name}}' | head -1)
DOCKER_BRIDGE=$(docker network inspect "$NET" \
  --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null)
echo "Network: $NET  |  Docker subnet: $DOCKER_BRIDGE"

# Guard: an empty result means the stack isn't up yet, bring it up first.
[ -n "$DOCKER_BRIDGE" ] || echo "EMPTY: run 'docker compose -f docker-compose.prod.yml up -d' first, then re-run this."
```

!!! note "The network name is project-prefixed"
    The `networks: orcastra-dashboard:` block in compose has no explicit `name:`, so the
    real network is `<compose-project>_orcastra-dashboard`, and the project name defaults
    to the directory you run compose from. Following
    [Step 2](#step-2-create-configuration-directories) you deploy from `~/orcastra`, so the
    network is `orcastra_orcastra-dashboard`. Running from `/root` instead would give
    `root_orcastra-dashboard`, which is why the lookup above auto-detects the name rather than
    hard-coding it. The network also only exists **after** a successful
    `docker compose -f docker-compose.prod.yml up -d`, so an empty `$DOCKER_BRIDGE` usually
    means the stack is not up yet.

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

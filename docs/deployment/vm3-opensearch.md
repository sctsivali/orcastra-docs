# VM 3 — OpenSearch (Logging)

**Specifications:** 4 vCPU, 16 GB RAM, 100 GB Storage

OpenSearch provides centralized log aggregation and analytics dashboards for the Orcastra platform. It receives logs from Vault (VM 2) and the Dashboard (VM 4) via Fluent Bit.

---

## Prerequisites

!!! warning "Host-Level Configuration Required"
    Before creating the VM, run this on the **LXD host server** (not inside the container):

    ```bash
    sudo sysctl -w vm.max_map_count=262144
    echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
    sudo sysctl -p
    ```

    OpenSearch requires this memory mapping setting and will fail to start without it.

---

## Step 1: Install Docker

Follow the [common Docker installation](index.md#common-docker-installation) steps.

---

## Step 2: Generate Passwords

```bash
OPENSEARCH_PASS="$(openssl rand -base64 24)"
echo "OpenSearch admin password: $OPENSEARCH_PASS"
```

```bash
DASHBOARDS_PASS="$(openssl rand -base64 16)"
echo "Dashboards (kibanaserver) password: $DASHBOARDS_PASS"
```

!!! danger "Save Both Passwords"
    - **OpenSearch admin password** — used for all admin API calls
    - **Dashboards password** — used by OpenSearch Dashboards internally

Create the `.env` file:

```bash
cat > .env << EOF
OPENSEARCH_ADMIN_PASSWORD=$OPENSEARCH_PASS
OPENSEARCH_DASHBOARDS_PASSWORD=$DASHBOARDS_PASS
ARCHIVE_DIR=/opt/opensearch/archive
EOF

chmod 600 .env
```

---

## Step 3: Configure Docker Storage

```bash
nano /etc/docker/daemon.json
```

```json
{
  "storage-driver": "vfs"
}
```

```bash
systemctl restart docker
```

---

## Step 4: Prepare Directories

```bash
mkdir -p opensearch-data config
chmod 777 opensearch-data

# Snapshot archive directory
ARCHIVE_DIR="/opt/opensearch/archive"
mkdir -p "$ARCHIVE_DIR"
chmod 777 "$ARCHIVE_DIR"
```

---

## Step 5: Create Docker Compose

```bash
cat > docker-compose.yml << 'EOF'
services:
  opensearch:
    image: opensearchproject/opensearch:latest
    container_name: opensearch
    restart: always
    environment:
      - cluster.name=orcastra-logging
      - node.name=opensearch-node1
      - discovery.type=single-node
      - bootstrap.memory_lock=true
      - "OPENSEARCH_JAVA_OPTS=-Xms4g -Xmx4g"
      - plugins.security.disabled=false
      - OPENSEARCH_INITIAL_ADMIN_PASSWORD=${OPENSEARCH_ADMIN_PASSWORD:?OPENSEARCH_ADMIN_PASSWORD is required}
    ulimits:
      memlock:
        soft: -1
        hard: -1
      nofile:
        soft: 65536
        hard: 65536
    volumes:
      - opensearch-data:/usr/share/opensearch/data
      - opensearch-snapshots:/usr/share/opensearch/snapshots
      - ./config/opensearch.yml:/usr/share/opensearch/config/opensearch.yml:ro
      - ./config/internal_users.yml:/usr/share/opensearch/config/opensearch-security/internal_users.yml:ro
      - ./config/roles.yml:/usr/share/opensearch/config/opensearch-security/roles.yml:ro
      - ./config/roles_mapping.yml:/usr/share/opensearch/config/opensearch-security/roles_mapping.yml:ro
    ports:
      - "9200:9200"
      - "9300:9300"
    networks:
      - opensearch-net
    healthcheck:
      test: ["CMD-SHELL", "curl -s -k https://localhost:9200 -u admin:${OPENSEARCH_ADMIN_PASSWORD} | grep -q 'opensearch'"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 60s

  opensearch-dashboards:
    image: opensearchproject/opensearch-dashboards:latest
    container_name: opensearch-dashboards
    restart: always
    environment:
      - OPENSEARCH_HOSTS="https://opensearch:9200"
      - DISABLE_SECURITY_DASHBOARDS_PLUGIN=false
      - OPENSEARCH_DASHBOARDS_PASSWORD=${OPENSEARCH_DASHBOARDS_PASSWORD}
    volumes:
      - ./config/opensearch_dashboards.yml:/usr/share/opensearch-dashboards/config/opensearch_dashboards.yml:ro
    ports:
      - "5601:5601"
    networks:
      - opensearch-net
    depends_on:
      opensearch:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "curl -s http://localhost:5601/api/status | grep -E -q '(available|Unauthorized)'"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 60s

networks:
  opensearch-net:
    driver: bridge

volumes:
  opensearch-data:
    driver: local
  opensearch-snapshots:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: ${ARCHIVE_DIR:-/opt/opensearch/archive}
EOF
```

---

## Step 6: Create Configuration Files

### OpenSearch Configuration

```bash
cat > config/opensearch.yml << 'EOF'
cluster.name: orcastra-logging
node.name: opensearch-node1

network.host: 0.0.0.0
http.port: 9200

discovery.type: single-node

# Security - TLS
plugins.security.ssl.transport.pemcert_filepath: esnode.pem
plugins.security.ssl.transport.pemkey_filepath: esnode-key.pem
plugins.security.ssl.transport.pemtrustedcas_filepath: root-ca.pem
plugins.security.ssl.transport.enforce_hostname_verification: false
plugins.security.ssl.http.enabled: true
plugins.security.ssl.http.pemcert_filepath: esnode.pem
plugins.security.ssl.http.pemkey_filepath: esnode-key.pem
plugins.security.ssl.http.pemtrustedcas_filepath: root-ca.pem
plugins.security.allow_unsafe_democertificates: true
plugins.security.allow_default_init_securityindex: true

# Security - Admin DN
plugins.security.authcz.admin_dn:
  - CN=kirk,OU=client,O=client,L=test,C=de

# Security - Features
plugins.security.audit.type: internal_opensearch
plugins.security.enable_snapshot_restore_privilege: true
plugins.security.check_snapshot_restore_write_privileges: true
plugins.security.restapi.roles_enabled: ["all_access", "security_rest_api_access"]
plugins.security.system_indices.enabled: true
plugins.security.system_indices.indices:
  - ".opendistro-alerting-config"
  - ".opendistro-alerting-alert*"
  - ".opendistro-anomaly-results*"
  - ".opendistro-anomaly-detector*"
  - ".opendistro-anomaly-checkpoints"
  - ".opendistro-anomaly-detection-state"
  - ".opendistro-reports-*"
  - ".opendistro-notifications-*"
  - ".opendistro-notebooks"
  - ".opendistro-asynchronous-search-response*"

# Snapshot repository path
path.repo: ["/usr/share/opensearch/snapshots"]

# Index settings
action.auto_create_index: true
EOF
```

### Internal Users

!!! warning "Generate Unique Password Hashes"
    Each user must have a unique bcrypt hash. Generate hashes with:

    ```bash
    # Option 1: Using OpenSearch container
    docker run -it opensearchproject/opensearch:3.5.0 bash -c \
      "plugins/opensearch-security/tools/hash.sh -p 'YOUR_PASSWORD'"

    # Option 2: Using Python
    python3 -c "import bcrypt; print(bcrypt.hashpw(b'YOUR_PASSWORD', \
      bcrypt.gensalt(rounds=12)).decode().replace('\$2b\$', '\$2y\$'))"
    ```

```bash
cat > config/internal_users.yml << 'EOF'
---
_meta:
  type: "internalusers"
  config_version: 2

admin:
  hash: "<BCRYPT_HASH_OF_OPENSEARCH_ADMIN_PASSWORD>"
  reserved: true
  backend_roles:
    - "admin"
  description: "Admin user for Orcastra logging"

fluentbit:
  hash: "<BCRYPT_HASH_OF_FLUENTBIT_PASSWORD>"
  reserved: false
  backend_roles:
    - "log_writer"
  description: "Fluent Bit service account for log ingestion"

audit_viewer:
  hash: "<BCRYPT_HASH_OF_AUDIT_VIEWER_PASSWORD>"
  reserved: false
  backend_roles:
    - "audit_reader"
  description: "Read-only access to audit logs"

kibanaserver:
  hash: "<BCRYPT_HASH_OF_DASHBOARDS_PASSWORD>"
  reserved: true
  backend_roles:
    - "kibana_server"
  description: "OpenSearch Dashboards internal user"
EOF
```

### Roles

```bash
cat > config/roles.yml << 'EOF'
---
_meta:
  type: "roles"
  config_version: 2

log_writer:
  reserved: false
  cluster_permissions:
    - "cluster_monitor"
    - "cluster:admin/ingest/pipeline/put"
    - "cluster:admin/ingest/pipeline/get"
    - "indices:admin/template/get"
    - "indices:admin/template/put"
  index_permissions:
    - index_patterns:
        - "orcastra-access-*"
        - "orcastra-audit-*"
        - "orcastra-app-*"
        - "vault-audit-*"
      allowed_actions:
        - "crud"
        - "create_index"
        - "manage"

audit_reader:
  reserved: false
  cluster_permissions:
    - "cluster_monitor"
  index_permissions:
    - index_patterns:
        - "orcastra-access-*"
        - "orcastra-audit-*"
        - "vault-audit-*"
      allowed_actions:
        - "read"
        - "search"

audit_admin:
  reserved: false
  cluster_permissions:
    - "cluster_all"
  index_permissions:
    - index_patterns:
        - "orcastra-*"
        - "vault-*"
      allowed_actions:
        - "all"
EOF
```

### Roles Mapping

```bash
cat > config/roles_mapping.yml << 'EOF'
---
_meta:
  type: "rolesmapping"
  config_version: 2

all_access:
  reserved: false
  backend_roles:
    - "admin"
  description: "Maps admin backend role to all_access"

log_writer:
  reserved: false
  backend_roles:
    - "log_writer"
  description: "Maps log_writer backend role"

audit_reader:
  reserved: false
  backend_roles:
    - "audit_reader"
  description: "Maps audit_reader backend role"

audit_admin:
  reserved: false
  backend_roles:
    - "admin"
  description: "Maps admin to audit_admin"

kibana_server:
  reserved: true
  users:
    - "kibanaserver"
EOF
```

### OpenSearch Dashboards Configuration

```bash
cat > config/opensearch_dashboards.yml << 'EOF'
server.host: "0.0.0.0"
server.port: 5601
server.name: "orcastra-dashboards"

opensearch.hosts: ["https://opensearch:9200"]
opensearch.ssl.verificationMode: none
opensearch.username: "${OPENSEARCH_DASHBOARDS_USER:-kibanaserver}"
opensearch.password: "${OPENSEARCH_DASHBOARDS_PASSWORD:?OPENSEARCH_DASHBOARDS_PASSWORD is required}"
opensearch.requestHeadersAllowlist: ["authorization", "securitytenant"]

opensearch_security.multitenancy.enabled: true
opensearch_security.multitenancy.tenants.preferred: ["Private", "Global"]
opensearch_security.readonly_mode.roles: ["kibana_read_only"]
opensearch_security.cookie.secure: false

logging.dest: stdout
logging.silent: false
logging.quiet: false
logging.verbose: false
EOF
```

---

## Step 7: Start OpenSearch

```bash
docker compose up -d
```

??? tip "Startup Error: dependency failed"
    If you see `Container opensearch Error dependency opensearch failed to start`:

    ```bash
    sed -i 's/compatibility.override_main_response_version: true/# compatibility.override_main_response_version: true/g' config/opensearch.yml
    docker compose down
    docker compose up -d
    ```

Verify both containers are healthy:

```bash
docker compose ps
```

Both should show `healthy` status after approximately 60 seconds.

---

## Step 8: Create Fluent Bit User

Generate a password for the Fluent Bit service account:

```bash
FLUENTBIT_PASS="$(openssl rand -hex 16)"
echo "Fluent Bit password: $FLUENTBIT_PASS"
echo "FLUENTBIT_PASSWORD=$FLUENTBIT_PASS" >> .env
```

!!! danger "Save This Password"
    The Fluent Bit password is required on both **VM 2** (Vault audit forwarding) and **VM 4** (Dashboard log forwarding).

Wait for OpenSearch to be ready, then create the user:

```bash
until curl -sk -u "admin:$OPENSEARCH_PASS" \
  https://localhost:9200/_cluster/health 2>/dev/null | grep -q status; do
  echo "Waiting for OpenSearch..." && sleep 5
done

curl -sk -u "admin:$OPENSEARCH_PASS" -X PUT \
  "https://localhost:9200/_plugins/_security/api/internalusers/fluentbit" \
  -H "Content-Type: application/json" \
  -d "{\"password\":\"$FLUENTBIT_PASS\",\"backend_roles\":[\"log_writer\"]}"
```

---

## Step 9: Import Dashboard Templates

### Install Git and Create Script

```bash
apt update && apt install git -y
```

Create the dashboard import script and the ndjson template files. The script creates index patterns and imports four pre-built dashboards:

- **Orcastra Logs Overview** — Combined view of all log types
- **Orcastra Access Logs** — HTTP request monitoring and latency tracking
- **Orcastra Activity & Audit Logs** — Security compliance and user activity
- **Vault Security Audit** — Vault operations and secret access patterns

!!! info "Dashboard Templates"
    The four ndjson files contain pre-configured visualizations and dashboard layouts. They are too large to include inline — download them from the [orcastra-dashboard repository](https://github.com/sctsivali/orcastra-docs) or copy them from your deployment package under `config/opensearch-dashboards/`.

Place the following files in `config/opensearch-dashboards/`:

- `access-logs-dashboard-v3.ndjson`
- `audit-logs-dashboard-v3.ndjson`
- `logs-overview-dashboard.ndjson`
- `vault-audit-dashboard.ndjson`

Run the import:

```bash
chmod +x setup_opensearch_dashboards.sh
./setup_opensearch_dashboards.sh \
  --url http://localhost:5601 \
  --password "$OPENSEARCH_PASS" \
  --dashboard-dir config/opensearch-dashboards
```

!!! tip "Password Variable Issue"
    If you see `[ERROR] Admin password is required`, use the literal password instead:

    ```bash
    ./setup_opensearch_dashboards.sh \
      --url http://localhost:5601 \
      --password "your-actual-password" \
      --dashboard-dir config/opensearch-dashboards
    ```

---

## Step 10: Create Index Templates

Index templates must be created before logs start flowing into the indices.

### Vault Audit Ingest Pipeline

```bash
curl -sk -u "admin:$OPENSEARCH_PASS" -X PUT \
  "https://localhost:9200/_ingest/pipeline/vault-audit-parse" \
  -H "Content-Type: application/json" \
  -d '{
  "description": "Parse Vault audit log JSON into structured fields",
  "processors": [
    {
      "json": {
        "field": "log",
        "target_field": "_parsed",
        "if": "ctx.containsKey('\''log'\'') && ctx.log instanceof String && ctx.log.startsWith('\''{'\'')"
      }
    },
    {
      "script": {
        "lang": "painless",
        "description": "Merge parsed fields and flatten nested objects",
        "source": "if (ctx._parsed == null) return; for (def entry : ctx._parsed.entrySet()) { if (entry.getKey() != '\''time'\'') { ctx[entry.getKey()] = entry.getValue(); } } if (ctx.request != null && ctx.request.namespace instanceof Map) { ctx.request.namespace_id = ctx.request.namespace.get('\''id'\''); ctx.request.remove('\''namespace'\''); } if (ctx.auth != null && ctx.auth.policy_results instanceof Map) { ctx.auth.remove('\''policy_results'\''); } if (ctx.request != null && ctx.request.mount_running_version != null) { ctx.request.remove('\''mount_running_version'\''); } if (ctx.request != null && ctx.request.mount_class != null) { ctx.request.remove('\''mount_class'\''); } if (ctx.request != null && ctx.request.mount_point != null) { ctx.request.remove('\''mount_point'\''); }",
        "if": "ctx._parsed != null"
      }
    },
    {
      "remove": {
        "field": ["_parsed", "log"],
        "ignore_missing": true
      }
    }
  ]
}'
```

Should return `{"acknowledged":true}`.

### Vault Audit Index Template

```bash
curl -sk -u "admin:$OPENSEARCH_PASS" -X PUT \
  "https://localhost:9200/_index_template/vault-audit-template" \
  -H "Content-Type: application/json" \
  -d '{
  "index_patterns": ["vault-audit-*"],
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0,
      "index.default_pipeline": "vault-audit-parse"
    },
    "mappings": {
      "properties": {
        "@timestamp": { "type": "date" },
        "time": { "type": "date" },
        "type": { "type": "keyword" },
        "auth": {
          "properties": {
            "client_token": { "type": "keyword" },
            "accessor": { "type": "keyword" },
            "display_name": { "type": "keyword" },
            "policies": { "type": "keyword" },
            "token_policies": { "type": "keyword" },
            "entity_id": { "type": "keyword" },
            "token_type": { "type": "keyword" }
          }
        },
        "request": {
          "properties": {
            "id": { "type": "keyword" },
            "operation": { "type": "keyword" },
            "mount_type": { "type": "keyword" },
            "mount_accessor": { "type": "keyword" },
            "client_id": { "type": "keyword" },
            "client_token": { "type": "keyword" },
            "client_token_accessor": { "type": "keyword" },
            "namespace": { "type": "keyword" },
            "namespace_id": { "type": "keyword" },
            "path": { "type": "keyword" },
            "remote_address": { "type": "ip" },
            "remote_port": { "type": "integer" }
          }
        },
        "response": {
          "properties": {
            "mount_accessor": { "type": "keyword" },
            "mount_type": { "type": "keyword" }
          }
        },
        "error": { "type": "text" },
        "service": { "type": "keyword" },
        "environment": { "type": "keyword" },
        "cluster": { "type": "keyword" }
      }
    }
  },
  "priority": 100,
  "version": 2
}'
```

### Orcastra Audit Index Template

```bash
curl -sk -u "admin:$OPENSEARCH_PASS" -X PUT \
  "https://localhost:9200/_index_template/orcastra-audit-template" \
  -H "Content-Type: application/json" \
  -d '{
  "index_patterns": ["orcastra-audit-*"],
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0
    },
    "mappings": {
      "properties": {
        "@timestamp": { "type": "date" },
        "log_type": { "type": "keyword" },
        "event_id": { "type": "keyword" },
        "request_id": { "type": "keyword" },
        "service": { "type": "keyword" },
        "version": { "type": "keyword" },
        "action": { "type": "keyword" },
        "category": { "type": "keyword" },
        "severity": { "type": "keyword" },
        "actor": {
          "type": "object",
          "properties": {
            "user_id": { "type": "keyword" },
            "user_type": { "type": "keyword" },
            "role": { "type": "keyword" },
            "session_id": { "type": "keyword" },
            "groups": { "type": "keyword" },
            "organizations": { "type": "keyword" }
          }
        },
        "target": {
          "type": "object",
          "properties": {
            "type": { "type": "keyword" },
            "id": { "type": "keyword" },
            "host": { "type": "keyword" },
            "project": { "type": "keyword" }
          }
        },
        "result": { "type": "keyword" },
        "error_code": { "type": "keyword" },
        "error_message": { "type": "text" },
        "details": { "type": "object", "enabled": false },
        "metadata": {
          "type": "object",
          "properties": {
            "duration_ms": { "type": "float" },
            "before": { "type": "object", "enabled": false },
            "after": { "type": "object", "enabled": false }
          }
        },
        "host": {
          "type": "object",
          "properties": {
            "name": { "type": "keyword" },
            "container_id": { "type": "keyword" }
          }
        }
      }
    }
  },
  "priority": 100,
  "version": 1
}'
```

### Orcastra Access Index Template

```bash
curl -sk -u "admin:$OPENSEARCH_PASS" -X PUT \
  "https://localhost:9200/_index_template/orcastra-access-template" \
  -H "Content-Type: application/json" \
  -d '{
  "index_patterns": ["orcastra-access-*"],
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0
    },
    "mappings": {
      "properties": {
        "@timestamp": { "type": "date" },
        "log_type": { "type": "keyword" },
        "request_id": { "type": "keyword" },
        "service": { "type": "keyword" },
        "version": { "type": "keyword" },
        "method": { "type": "keyword" },
        "path": { "type": "keyword" },
        "query_params": { "type": "object", "enabled": false },
        "status_code": { "type": "integer" },
        "latency_ms": { "type": "float" },
        "request_size": { "type": "long" },
        "response_size": { "type": "long" },
        "user": {
          "type": "object",
          "properties": {
            "id": { "type": "keyword" },
            "type": { "type": "keyword" },
            "role": { "type": "keyword" },
            "groups": { "type": "keyword" },
            "organizations": { "type": "keyword" }
          }
        },
        "client": {
          "type": "object",
          "properties": {
            "ip": { "type": "ip" },
            "user_agent": { "type": "text" },
            "origin": { "type": "keyword" }
          }
        },
        "error": { "type": "text" },
        "host": {
          "type": "object",
          "properties": {
            "name": { "type": "keyword" },
            "container_id": { "type": "keyword" }
          }
        }
      }
    }
  },
  "priority": 100,
  "version": 1
}'
```

### Verify Templates

```bash
curl -sk -u "admin:$OPENSEARCH_PASS" \
  "https://localhost:9200/_ingest/pipeline/vault-audit-parse" \
  | python3 -m json.tool | head -5

curl -sk -u "admin:$OPENSEARCH_PASS" \
  "https://localhost:9200/_index_template/vault-audit-template" \
  | python3 -m json.tool | head -5

curl -sk -u "admin:$OPENSEARCH_PASS" \
  "https://localhost:9200/_index_template/orcastra-audit-template" \
  | python3 -m json.tool | head -5

curl -sk -u "admin:$OPENSEARCH_PASS" \
  "https://localhost:9200/_index_template/orcastra-access-template" \
  | python3 -m json.tool | head -5
```

!!! tip "Authentication Errors"
    If you see `Expecting value: line 1 column 1 (char 0)`, the password variable may have been lost. Use the literal password instead:

    ```bash
    curl -sk -u "admin:your-actual-password" ...
    ```

---

## Output Summary

After completing VM 3 setup, you should have the following values saved:

| Value | Used On | Environment Variable (VM 4) |
|---|---|---|
| OpenSearch Admin Password | VM 3 (admin operations) | — |
| Dashboards Password | VM 3 (internal user) | — |
| Fluent Bit Password | VM 2, VM 4 | `OPENSEARCH_PASSWORD` |
| OpenSearch IP | VM 2, VM 4 | `OPENSEARCH_HOST` |

---

**Next:** [VM 4 — Orcastra Dashboard](vm4-dashboard.md)

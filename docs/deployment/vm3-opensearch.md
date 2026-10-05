# VM 3 - OpenSearch (Logging)

**Specifications:** 4 vCPU, 16 GB RAM, 100 GB Storage

OpenSearch provides centralized log aggregation and analytics dashboards for the Orcastra platform. It receives logs from Vault (VM 2) and the Dashboard (VM 4) via Fluent Bit.

This guide produces a hardened single-node cluster: TLS with certificates from your own CA (never
the OpenSearch demo certificates), an index layout that stays within the single-node shard limit
for the full retention period, automatic retention, and optional sign-in through Authentik.

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

Work from a dedicated deployment directory so the compose file, `.env`, certificates, and Docker named volumes stay together in one predictable place. This keeps upgrades and troubleshooting straightforward instead of hunting for files scattered across the home or `/root` directory:

```bash
mkdir -p ~/orcastra && cd ~/orcastra
```

Run the remaining steps from `~/orcastra`. Then generate the OpenSearch passwords:

```bash
OPENSEARCH_PASS="$(openssl rand -base64 33 | tr -d '/+=' | cut -c1-32)"
echo "OpenSearch admin password: $OPENSEARCH_PASS"
```

```bash
DASHBOARDS_PASS="$(openssl rand -base64 33 | tr -d '/+=' | cut -c1-32)"
echo "Dashboards (kibanaserver) password: $DASHBOARDS_PASS"
```

!!! danger "Save Both Passwords"
    - **OpenSearch admin password** used for all admin API calls, and the break-glass sign-in
    - **Dashboards password** used by OpenSearch Dashboards internally

Set the two addresses this VM is reached by, then create the `.env` file:

- `VM3_PRIVATE_IP`: the private IP VM 2 and VM 4 use to reach this VM
- `LOGS_DOMAIN`: the public hostname of OpenSearch Dashboards (see [Domain Setup](../operations/domain-setup.md)), for example `logs.example.com`

```bash
VM3_PRIVATE_IP="<VM3_PRIVATE_IP>"
LOGS_DOMAIN="<LOGS_DOMAIN>"

cat > .env << EOF
OPENSEARCH_ADMIN_PASSWORD=$OPENSEARCH_PASS
OPENSEARCH_DASHBOARDS_PASSWORD=$DASHBOARDS_PASS
ARCHIVE_DIR=/opt/opensearch/archive
VM3_PRIVATE_IP=$VM3_PRIVATE_IP
LOGS_DOMAIN=$LOGS_DOMAIN
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
mkdir -p config certs

# Snapshot archive directory (owned by the opensearch user inside the container, uid 1000)
ARCHIVE_DIR="/opt/opensearch/archive"
mkdir -p "$ARCHIVE_DIR"
chown 1000:1000 "$ARCHIVE_DIR"
chmod 750 "$ARCHIVE_DIR"
```

---

## Step 5: Generate TLS Certificates

OpenSearch encrypts both its REST API and node transport with TLS. Create a private certificate
authority and issue the node certificate and the security admin certificate from it.

!!! danger "Never use the OpenSearch demo certificates"
    The OpenSearch image can install demo certificates on first start. Their private keys,
    including the demo admin certificate (`CN=kirk`), are published with OpenSearch: anyone who
    can reach port 9200 or 9300 can use them to take over the security configuration. This guide
    disables the demo installer and uses only the certificates created here.

```bash
cd ~/orcastra/certs
set -a; . ../.env; set +a

# 1. Certificate authority (10 years)
openssl genpkey -quiet -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -out root-ca-key.pem
openssl req -x509 -new -key root-ca-key.pem -sha256 -days 3650 -out root-ca.pem \
  -subj "/O=Orcastra/OU=logging/CN=Orcastra Logging Root CA" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
  -addext "keyUsage=critical,keyCertSign,cRLSign"

# 2. Node certificate (825 days). The SANs are every name clients use to reach OpenSearch.
openssl genpkey -quiet -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out node-key.pem
openssl req -new -key node-key.pem -subj "/O=Orcastra/OU=logging/CN=opensearch-node1" -out node.csr
cat > node.ext << EOF
basicConstraints=CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=DNS:opensearch,DNS:localhost,DNS:${LOGS_DOMAIN},IP:127.0.0.1,IP:${VM3_PRIVATE_IP}
EOF
openssl x509 -req -in node.csr -CA root-ca.pem -CAkey root-ca-key.pem -CAcreateserial \
  -sha256 -days 825 -extfile node.ext -out node.pem

# 3. Security admin certificate (used only by securityadmin.sh, never mounted into the node)
openssl genpkey -quiet -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out admin-key.pem
openssl req -new -key admin-key.pem -subj "/O=Orcastra/OU=logging/CN=orcastra-logging-admin" -out admin.csr
printf 'basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=clientAuth\n' > admin.ext
openssl x509 -req -in admin.csr -CA root-ca.pem -CAkey root-ca-key.pem -CAcreateserial \
  -sha256 -days 825 -extfile admin.ext -out admin.pem

rm -f node.csr admin.csr node.ext admin.ext
openssl verify -CAfile root-ca.pem node.pem admin.pem

# The container user (uid 1000) reads only the node certificate, its key and the CA.
chown 1000:1000 node.pem node-key.pem
chmod 600 node-key.pem admin-key.pem root-ca-key.pem
chmod 644 root-ca.pem node.pem admin.pem
cd ~/orcastra
```

Both `node.pem` and `admin.pem` should report `OK`.

!!! info "Why the public hostname is in the node certificate"
    The OpenSearch Dashboards security plugin forwards the browser's `Host` header on some of its
    own calls to OpenSearch, so those calls verify the node certificate against `LOGS_DOMAIN`.
    Without that SAN, Dashboards logs `ERR_TLS_CERT_ALTNAME_INVALID` and its read-only tenant check
    fails.

!!! danger "Back up the CA key"
    Copy `certs/root-ca-key.pem` to offline storage. Without it, renewing a certificate means
    creating a new CA and redistributing it to every client. Renew the node and admin
    certificates before they expire (825 days) by repeating parts 2 and 3 with the existing CA.

`certs/root-ca.pem` is public: VM 2 and VM 4 use it to verify this server.

---

## Step 6: Create Docker Compose

```bash
cat > docker-compose.yml << 'EOF'
x-logging: &logging
  driver: json-file
  options:
    max-size: "50m"
    max-file: "5"

services:
  opensearch:
    image: opensearchproject/opensearch:3.5.0
    container_name: opensearch
    restart: always
    environment:
      - "OPENSEARCH_JAVA_OPTS=-Xms4g -Xmx4g"
      - bootstrap.memory_lock=true
      # Never install the demo certificates or demo users.
      - DISABLE_INSTALL_DEMO_CONFIG=true
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
      - ./certs/node.pem:/usr/share/opensearch/config/certs/node.pem:ro
      - ./certs/node-key.pem:/usr/share/opensearch/config/certs/node-key.pem:ro
      - ./certs/root-ca.pem:/usr/share/opensearch/config/certs/root-ca.pem:ro
    ports:
      # REST API for Fluent Bit on VM 2 / VM 4. 9300 (node transport) is not published: a single
      # node has no peers, and an open transport port is attack surface only.
      - "9200:9200"
    networks:
      - opensearch-net
    healthcheck:
      # 200 or 401 proves the TLS API is serving, without putting a password in the container config.
      test: ["CMD-SHELL", "code=$$(curl -s -o /dev/null -w '%{http_code}' --cacert config/certs/root-ca.pem https://localhost:9200/); [ \"$$code\" = 200 ] || [ \"$$code\" = 401 ]"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 90s
    logging: *logging

  opensearch-dashboards:
    image: opensearchproject/opensearch-dashboards:3.5.0
    container_name: opensearch-dashboards
    restart: always
    volumes:
      - ./config/opensearch_dashboards.yml:/usr/share/opensearch-dashboards/config/opensearch_dashboards.yml:ro
      # Secrets (kibanaserver password, and later the SSO client secret) live in the keystore (Step 7).
      - ./config/opensearch_dashboards.keystore:/usr/share/opensearch-dashboards/config/opensearch_dashboards.keystore:ro
      - ./certs/root-ca.pem:/usr/share/opensearch-dashboards/config/certs/root-ca.pem:ro
    ports:
      # Private network only: the Cloudflare tunnel connector (VM 4) reaches Dashboards here.
      - "${VM3_PRIVATE_IP:?set VM3_PRIVATE_IP in .env}:5601:5601"
      - "127.0.0.1:5601:5601"
    networks:
      - opensearch-net
    depends_on:
      opensearch:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "curl -s -o /dev/null -w '%{http_code}' http://localhost:5601/api/status | grep -qE '^(200|401)$'"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 90s
    logging: *logging

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

## Step 7: Create Configuration Files

### OpenSearch Configuration

```bash
cat > config/opensearch.yml << 'EOF'
cluster.name: orcastra-logging
node.name: opensearch-node1

network.host: 0.0.0.0
http.port: 9200

discovery.type: single-node

# Security - TLS (certificates from Step 5)
plugins.security.ssl.transport.pemcert_filepath: certs/node.pem
plugins.security.ssl.transport.pemkey_filepath: certs/node-key.pem
plugins.security.ssl.transport.pemtrustedcas_filepath: certs/root-ca.pem
plugins.security.ssl.transport.enforce_hostname_verification: false
plugins.security.ssl.http.enabled: true
plugins.security.ssl.http.pemcert_filepath: certs/node.pem
plugins.security.ssl.http.pemkey_filepath: certs/node-key.pem
plugins.security.ssl.http.pemtrustedcas_filepath: certs/root-ca.pem
plugins.security.allow_unsafe_democertificates: false
# The first start initializes the security index from the files mounted in Step 6.
plugins.security.allow_default_init_securityindex: true

# Security - node and admin identities (certificates from Step 5)
plugins.security.nodes_dn:
  - "CN=opensearch-node1,OU=logging,O=Orcastra"
plugins.security.authcz.admin_dn:
  - "CN=orcastra-logging-admin,OU=logging,O=Orcastra"

# Security - Features
plugins.security.audit.type: internal_opensearch
# One security audit index per month (the default is one per day, which alone adds about 365
# shards a year to a single node).
plugins.security.audit.config.index: "'security-auditlog-'YYYY.MM"
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
    Each user must have a **unique** bcrypt hash. Reusing the same hash across
    multiple users is a critical security violation: any compromised credential
    grants access to every account that shares the hash.

  Generate each hash locally on the VM, then paste the results into
  `config/internal_users.yml`. Keep the hash generation step explicit in the
  docs so the deployment does not depend on helper files from another repo.

  **Generate hashes locally:**

    ```bash
    # Option 1: Using OpenSearch container
    docker run -it --rm opensearchproject/opensearch:3.5.0 bash -c \
      "plugins/opensearch-security/tools/hash.sh -p 'YOUR_PASSWORD'"

    # Option 2: Using Python
    python3 -c "import bcrypt; print(bcrypt.hashpw(b'YOUR_PASSWORD', \
      bcrypt.gensalt(rounds=12)).decode().replace('\$2b\$', '\$2y\$'))"
    ```

    Repeat that command three times with three different passwords:

    - `OPENSEARCH_ADMIN_PASSWORD`
    - `AUDIT_VIEWER_PASSWORD`
    - `KIBANASERVER_PASSWORD` (the Dashboards password from Step 2)

    (`FLUENTBIT_PASSWORD` is **not** hashed here - the `fluentbit` user is created
    via the Security API in Step 11; its plaintext password lives in `.env` and is
    pushed to VM 2 / VM 4.)

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
  description: "Admin user for Orcastra logging (break-glass sign-in)"

# `fluentbit` is intentionally NOT defined here, it is created via the Security
# API in Step 11. Seeding it here too would re-apply a placeholder hash on any
# security-config reload and break log shipping (401). Keep it API-managed only.

audit_viewer:
  hash: "<BCRYPT_HASH_OF_AUDIT_VIEWER_PASSWORD>"
  reserved: false
  backend_roles:
    - "audit_reader"
  description: "Read-only access to audit logs"

kibanaserver:
  hash: "<BCRYPT_HASH_OF_DASHBOARDS_PASSWORD>"
  reserved: true
  description: "OpenSearch Dashboards internal user"
EOF
```

!!! danger "Replace the placeholders before deploying"
    The heredoc above writes literal `<BCRYPT_HASH_OF_*>` placeholders. Edit
    `config/internal_users.yml` and replace each with the matching bcrypt hash
    you generated above, then verify none remain **before** `docker compose up`:

    ```bash
    grep -n '<BCRYPT_HASH' config/internal_users.yml \
      && echo "STOP: placeholders still present - replace them first" \
      || echo "OK: all hashes substituted"
    ```

    If a placeholder is left, OpenSearch stores the literal string as that user's
    credential, `admin`/`audit_viewer`/`kibanaserver` can never authenticate and
    Steps 8-12 fail with `401`. (`fluentbit` is (re)created via the API in Step 11,
    so only it survives a missed substitution.)

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
    - "cluster_composite_ops"
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
        - "indices:admin/mapping/auto_put"

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

# Marker role for read-only Dashboards users (opensearch_security.readonly_mode.roles).
# It grants nothing by itself; see Step 13.
logs_readonly_ui:
  reserved: false
EOF
```

!!! danger "`cluster_composite_ops` is required, without it, log ingestion silently 403s"
    The `/_bulk` endpoint used by Fluent Bit (and every single-document write,
    which OpenSearch wraps into a bulk op) is authorized at **cluster scope**
    first: `indices:data/write/bulk` must be granted via `cluster_permissions`,
    not just at index level. Omit `cluster_composite_ops` and `fluentbit` will
    authenticate and **read** fine, but every **write** fails with
    `security_exception: no permissions for [indices:data/write/bulk]` - even
    though the index-level `crud` grant looks correct. See
    [Troubleshooting](../operations/troubleshooting.md).

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
server.customResponseHeaders:
  X-Content-Type-Options: "nosniff"
  X-Frame-Options: "SAMEORIGIN"
  Referrer-Policy: "strict-origin-when-cross-origin"

opensearch.hosts: ["https://opensearch:9200"]
# Verify the node certificate against the CA from Step 5 (hostname included).
opensearch.ssl.verificationMode: full
opensearch.ssl.certificateAuthorities: ["/usr/share/opensearch-dashboards/config/certs/root-ca.pem"]
opensearch.username: "kibanaserver"
# opensearch.password comes from the keystore below, never from this file.
opensearch.requestHeadersAllowlist: ["authorization", "securitytenant"]

opensearch_security.multitenancy.enabled: true
# Global first: dashboards imported in Step 12 land in the tenant every user sees.
opensearch_security.multitenancy.tenants.preferred: ["Global", "Private"]
opensearch_security.readonly_mode.roles: ["logs_readonly_ui"]
opensearch_security.cookie.secure: true

logging.dest: stdout
logging.quiet: true
EOF
```

Store the `kibanaserver` password in the Dashboards keystore. The password goes in over stdin, so
it never appears in a file, the process list or the container environment:

```bash
set -a; . ./.env; set +a
tmp="$(mktemp -d)"; chown 1000:1000 "$tmp"     # Dashboards runs as uid 1000
docker run --rm -i -v "$tmp:/out" --entrypoint bash opensearchproject/opensearch-dashboards:3.5.0 -c '
  set -e; read -r pw
  bin/opensearch-dashboards-keystore create --silent >/dev/null
  printf %s "$pw" | bin/opensearch-dashboards-keystore add --stdin --silent opensearch.password
  cp config/opensearch_dashboards.keystore /out/' <<< "$OPENSEARCH_DASHBOARDS_PASSWORD"
install -o 1000 -g 1000 -m 600 "$tmp/opensearch_dashboards.keystore" config/
rm -rf "$tmp"
```

!!! note "`cookie.secure` and HTTPS"
    `opensearch_security.cookie.secure` is `true`: browsers then send the session cookie only over
    HTTPS, which is how users reach Dashboards through the tunnel in
    [Domain Setup](../operations/domain-setup.md). Before the tunnel exists, use an SSH tunnel and
    open `http://localhost:5601` (browsers treat `localhost` as a secure context):

    ```bash
    ssh -L 5601:127.0.0.1:5601 root@<VM3_PRIVATE_IP>
    ```

    Opening `http://<VM3_PRIVATE_IP>:5601` directly makes the login loop, because the browser
    withholds the secure cookie over plain HTTP.


---

## Step 8: Start OpenSearch

```bash
docker compose up -d
```

Verify both containers are healthy:

```bash
docker compose ps
```

Both should show `healthy` status after approximately 90 seconds. Then check that the node serves
your certificate and accepts the admin password:

```bash
OS="curl -s --cacert certs/root-ca.pem -u admin:$OPENSEARCH_PASS https://localhost:9200"
$OS/_cluster/health | python3 -m json.tool | grep status
echo | openssl s_client -connect localhost:9200 -CAfile certs/root-ca.pem -verify_return_error 2>/dev/null | grep "Verify return code"
```

Expect `"status": "green"` and `Verify return code: 0 (ok)`. The commands in the remaining steps
use `$OS`; if you open a new shell, set it again (and `OPENSEARCH_PASS` from `.env`).

---

## Step 9: Create Index Templates

Index templates and the Vault ingest pipeline must exist before the first log arrives: an index
created without its template keeps the wrong mapping until it is deleted. VM 2 may already be
buffering Vault audit logs, which is why the Fluent Bit account is only created in Step 11.

!!! info "Index layout: daily for short retention, monthly for long retention"
    A single node allows at most 1000 shards. Access (90 days) and app logs (30 days) use one
    index per day; audit and Vault audit logs, kept for 3 years, use one index per month. With
    retention in place the cluster settles at roughly 200 shards. One index per day for the
    3-year logs would exceed the limit within the first year and stop all ingestion.

### Vault Audit Ingest Pipeline

```bash
$OS/_ingest/pipeline/vault-audit-parse -X PUT \
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
        "source": "if (ctx._parsed instanceof Map) { for (def entry : ctx._parsed.entrySet()) { if (entry.getKey() != '\''time'\'') { ctx[entry.getKey()] = entry.getValue(); } } } if (ctx.request instanceof Map && ctx.request.namespace instanceof Map) { ctx.request.namespace_id = ctx.request.namespace.get('\''id'\''); ctx.request.remove('\''namespace'\''); } if (ctx.auth instanceof Map) { ctx.auth.remove('\''policy_results'\''); } if (ctx.request instanceof Map) { ctx.request.remove('\''mount_running_version'\''); ctx.request.remove('\''mount_class'\''); ctx.request.remove('\''mount_point'\''); }"
      }
    },
    {
      "remove": {
        "description": "Drop the raw line only once it was parsed; a line that is not JSON keeps it",
        "field": "log",
        "if": "ctx._parsed instanceof Map",
        "ignore_missing": true
      }
    },
    {
      "remove": {
        "field": "_parsed",
        "ignore_missing": true
      }
    }
  ]
}'
```

Should return `{"acknowledged":true}`.

### Vault Audit Index Template

```bash
$OS/_index_template/vault-audit-template -X PUT \
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
          "dynamic": false,
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
          "dynamic": false,
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
            "remote_address": { "type": "keyword" },
            "remote_port": { "type": "keyword" }
          }
        },
        "response": {
          "dynamic": false,
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
$OS/_index_template/orcastra-audit-template -X PUT \
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
          "dynamic": false,
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
          "dynamic": false,
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
$OS/_index_template/orcastra-access-template -X PUT \
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
          "dynamic": false,
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
          "dynamic": false,
          "properties": {
            "ip": { "type": "keyword" },
            "proxy_ip": { "type": "keyword" },
            "ip_source": { "type": "keyword" },
            "proxy_trusted": { "type": "boolean" },
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

### Orcastra App and Security Audit Index Templates

These two have no fixed mapping, but they must not get the default replica: on a single node a
replica can never be assigned, so the cluster turns yellow and the shard count doubles.

```bash
$OS/_index_template/orcastra-app-template -X PUT \
  -H "Content-Type: application/json" \
  -d '{
  "index_patterns": ["orcastra-app-*"],
  "template": {
    "settings": { "number_of_shards": 1, "number_of_replicas": 0 },
    "mappings": { "properties": { "@timestamp": { "type": "date" } } }
  },
  "priority": 100,
  "version": 1
}'

$OS/_index_template/security-auditlog-template -X PUT \
  -H "Content-Type: application/json" \
  -d '{
  "index_patterns": ["security-auditlog-*"],
  "template": {
    "settings": { "number_of_shards": 1, "number_of_replicas": 0 }
  },
  "priority": 100,
  "version": 1
}'
```

### Verify Templates

```bash
$OS/_ingest/pipeline/vault-audit-parse | python3 -m json.tool | head -3
$OS/_index_template | python3 -c "import sys,json; print(sorted(t['name'] for t in json.load(sys.stdin)['index_templates'] if not t['name'].startswith('tenant')))"
```

The second command should list `orcastra-access-template`, `orcastra-app-template`,
`orcastra-audit-template`, `security-auditlog-template` and `vault-audit-template`.

!!! tip "Authentication Errors"
    If you see `Expecting value: line 1 column 1 (char 0)`, the password variable may have been
    lost. Set it again from `.env` and rebuild `$OS`:

    ```bash
    set -a; . ./.env; set +a; OPENSEARCH_PASS="$OPENSEARCH_ADMIN_PASSWORD"
    OS="curl -s --cacert certs/root-ca.pem -u admin:$OPENSEARCH_PASS https://localhost:9200"
    ```

---

## Step 10: Create ISM Retention Policies

Index State Management (ISM) enforces retention automatically. Without it, indices
grow unbounded. Each policy attaches to new indices through its `ism_template`
(matched by index pattern) at creation time, so the indices Fluent Bit writes pick up their
lifecycle with no manual step. Create the policies now, before any logs flow.

| Logs | Index | Retention | Read-only after |
|---|---|---|---|
| Access | `orcastra-access-YYYY.MM.DD` | 90 days | 2 days |
| App | `orcastra-app-YYYY.MM.DD` | 30 days | 2 days |
| Audit | `orcastra-audit-YYYY.MM` | 3 years | 35 days |
| Vault audit | `vault-audit-YYYY.MM` | 3 years | 35 days |
| Security plugin audit | `security-auditlog-YYYY.MM` | 1 year | 35 days |

Every index is snapshotted to the `orcastra-archive` repository before it is deleted.

!!! warning "Never make an index read-only while it is still written"
    The read-only step (`warm`) comes only after the period an index covers is over: a daily
    index after 2 days, a monthly index after 35 days. Later log lines buffered by Fluent Bit
    (for example after an outage) must arrive before that point, or they are rejected. Monthly
    indices are deleted at retention plus one month, so every document in them is kept for at
    least the full retention period.

### Register the Snapshot Repository

The `archive` state in each policy snapshots an index before deletion, so the
repository must exist first. The compose file already mounts the archive volume at
`/usr/share/opensearch/snapshots` and `opensearch.yml` registers it as `path.repo`.

```bash
$OS/_snapshot/orcastra-archive -X PUT \
  -H "Content-Type: application/json" \
  -d '{
  "type": "fs",
  "settings": {
    "location": "/usr/share/opensearch/snapshots",
    "compress": true,
    "max_snapshot_bytes_per_sec": "40mb",
    "max_restore_bytes_per_sec": "40mb"
  }
}'
```

Should return `{"acknowledged":true}`.

!!! note "Snapshots stay on this VM"
    The repository is a directory on the same disk. It protects against mistakes and bad
    changes, not against losing the VM. Copy `/opt/opensearch/archive` to off-host storage for
    disaster recovery.

### Access Logs Policy (90 days, daily indices)

```bash
$OS/_plugins/_ism/policies/orcastra-access-policy -X PUT \
  -H "Content-Type: application/json" \
  -d '{
  "policy": {
    "description": "orcastra-access daily indices - 90 day retention with archive before delete",
    "default_state": "hot",
    "states": [
      { "name": "hot", "actions": [], "transitions": [
        { "state_name": "warm", "conditions": { "min_index_age": "2d" } }
      ] },
      { "name": "warm", "actions": [ { "read_only": {} }, { "force_merge": { "max_num_segments": 1 } } ], "transitions": [
        { "state_name": "archive", "conditions": { "min_index_age": "89d" } }
      ] },
      { "name": "archive", "actions": [ { "snapshot": { "repository": "orcastra-archive", "snapshot": "{{ctx.index}}" } } ], "transitions": [
        { "state_name": "delete", "conditions": { "min_index_age": "90d" } }
      ] },
      { "name": "delete", "actions": [ { "delete": {} } ], "transitions": [] }
    ],
    "ism_template": [ { "index_patterns": ["orcastra-access-*"], "priority": 100 } ]
  }
}'
```

### App Logs Policy (30 days, daily indices)

```bash
$OS/_plugins/_ism/policies/orcastra-app-policy -X PUT \
  -H "Content-Type: application/json" \
  -d '{
  "policy": {
    "description": "orcastra-app daily indices - 30 day retention with archive before delete",
    "default_state": "hot",
    "states": [
      { "name": "hot", "actions": [], "transitions": [
        { "state_name": "warm", "conditions": { "min_index_age": "2d" } }
      ] },
      { "name": "warm", "actions": [ { "read_only": {} }, { "force_merge": { "max_num_segments": 1 } } ], "transitions": [
        { "state_name": "archive", "conditions": { "min_index_age": "29d" } }
      ] },
      { "name": "archive", "actions": [ { "snapshot": { "repository": "orcastra-archive", "snapshot": "{{ctx.index}}" } } ], "transitions": [
        { "state_name": "delete", "conditions": { "min_index_age": "30d" } }
      ] },
      { "name": "delete", "actions": [ { "delete": {} } ], "transitions": [] }
    ],
    "ism_template": [ { "index_patterns": ["orcastra-app-*"], "priority": 100 } ]
  }
}'
```

### Audit Logs Policy (3 years, monthly indices)

```bash
$OS/_plugins/_ism/policies/orcastra-audit-policy -X PUT \
  -H "Content-Type: application/json" \
  -d '{
  "policy": {
    "description": "orcastra-audit monthly indices - 3 year retention with archive before delete",
    "default_state": "hot",
    "states": [
      { "name": "hot", "actions": [], "transitions": [
        { "state_name": "warm", "conditions": { "min_index_age": "35d" } }
      ] },
      { "name": "warm", "actions": [ { "read_only": {} }, { "force_merge": { "max_num_segments": 1 } } ], "transitions": [
        { "state_name": "archive", "conditions": { "min_index_age": "1125d" } }
      ] },
      { "name": "archive", "actions": [ { "snapshot": { "repository": "orcastra-archive", "snapshot": "{{ctx.index}}" } } ], "transitions": [
        { "state_name": "delete", "conditions": { "min_index_age": "1126d" } }
      ] },
      { "name": "delete", "actions": [ { "delete": {} } ], "transitions": [] }
    ],
    "ism_template": [ { "index_patterns": ["orcastra-audit-*"], "priority": 100 } ]
  }
}'
```

### Vault Audit Policy (3 years, monthly indices)

```bash
$OS/_plugins/_ism/policies/vault-audit-policy -X PUT \
  -H "Content-Type: application/json" \
  -d '{
  "policy": {
    "description": "vault-audit monthly indices - 3 year retention with archive before delete",
    "default_state": "hot",
    "states": [
      { "name": "hot", "actions": [], "transitions": [
        { "state_name": "warm", "conditions": { "min_index_age": "35d" } }
      ] },
      { "name": "warm", "actions": [ { "read_only": {} }, { "force_merge": { "max_num_segments": 1 } } ], "transitions": [
        { "state_name": "archive", "conditions": { "min_index_age": "1125d" } }
      ] },
      { "name": "archive", "actions": [ { "snapshot": { "repository": "orcastra-archive", "snapshot": "{{ctx.index}}" } } ], "transitions": [
        { "state_name": "delete", "conditions": { "min_index_age": "1126d" } }
      ] },
      { "name": "delete", "actions": [ { "delete": {} } ], "transitions": [] }
    ],
    "ism_template": [ { "index_patterns": ["vault-audit-*"], "priority": 100 } ]
  }
}'
```

### Security Audit Log Policy (1 year, monthly indices)

```bash
$OS/_plugins/_ism/policies/security-auditlog-policy -X PUT \
  -H "Content-Type: application/json" \
  -d '{
  "policy": {
    "description": "security-auditlog monthly indices - 1 year retention with archive before delete",
    "default_state": "hot",
    "states": [
      { "name": "hot", "actions": [], "transitions": [
        { "state_name": "warm", "conditions": { "min_index_age": "35d" } }
      ] },
      { "name": "warm", "actions": [ { "read_only": {} }, { "force_merge": { "max_num_segments": 1 } } ], "transitions": [
        { "state_name": "archive", "conditions": { "min_index_age": "395d" } }
      ] },
      { "name": "archive", "actions": [ { "snapshot": { "repository": "orcastra-archive", "snapshot": "{{ctx.index}}" } } ], "transitions": [
        { "state_name": "delete", "conditions": { "min_index_age": "396d" } }
      ] },
      { "name": "delete", "actions": [ { "delete": {} } ], "transitions": [] }
    ],
    "ism_template": [ { "index_patterns": ["security-auditlog-*"], "priority": 100 } ]
  }
}'
```

### Verify Policies

```bash
$OS/_plugins/_ism/policies \
  | python3 -c "import sys,json; print('\n'.join(sorted(p['_id'] for p in json.load(sys.stdin)['policies'])))"
```

Should list all five: `orcastra-access-policy`, `orcastra-app-policy`, `orcastra-audit-policy`,
`security-auditlog-policy`, `vault-audit-policy`.

### Keep System Indices Without Replicas

ISM writes its own history to `.opendistro-ism-managed-index-history-*` and rolls that index over
every day, by default with one replica. Give the history indices no replica, or the cluster turns
yellow again every day:

```bash
$OS/_cluster/settings -X PUT \
  -H "Content-Type: application/json" \
  -d '{
  "persistent": { "plugins.index_state_management.history.number_of_replicas": 0 }
}'
```

Should return `"acknowledged":true`.

Creating the first policy makes the ISM plugin create its own index, `.opendistro-ism-config`, with
one replica. A single node can never assign it, so the cluster turns yellow. It is a protected
system index: even the `admin` user is refused, and only the admin certificate from Step 5 may
change it:

```bash
ADM="curl -s --cacert certs/root-ca.pem --cert certs/admin.pem --key certs/admin-key.pem https://localhost:9200"
for idx in $($OS/_cat/indices?h=index,rep\&expand_wildcards=all | awk '$2 > 0 {print $1}'); do
  echo "$idx: $($ADM/$idx/_settings -X PUT -H "Content-Type: application/json" -d '{"index.number_of_replicas": 0}')"
done
$OS/_cluster/health | python3 -m json.tool | grep -E '"status"|unassigned_shards"'
```

Expect `"status": "green"` and `"unassigned_shards": 0`. Run the loop again whenever the cluster
turns yellow after a plugin creates another system index.

!!! note "Attachment timing"
    ISM attaches a policy only to indices created **after** the policy exists. The security
    plugin may already have created this month's `security-auditlog-*` index during startup;
    attach the policy to it once:

    ```bash
    $OS/_plugins/_ism/add/security-auditlog-* -X POST -H "Content-Type: application/json" \
      -d '{"policy_id": "security-auditlog-policy"}'
    ```

    The same command, with the matching policy, covers any other index that existed before its
    policy.

---

## Step 11: Create Fluent Bit User

Generate a password for the Fluent Bit service account:

```bash
FLUENTBIT_PASS="$(openssl rand -hex 16)"
echo "Fluent Bit password: $FLUENTBIT_PASS"
echo "FLUENTBIT_PASSWORD=$FLUENTBIT_PASS" >> .env
```

!!! danger "Save This Password"
    The Fluent Bit password is required on both **VM 2** (Vault audit forwarding) and **VM 4** (Dashboard log forwarding).

Create the user. From this moment VM 2 and VM 4 can write, so Steps 9 and 10 must be done first:

```bash
$OS/_plugins/_security/api/internalusers/fluentbit -X PUT \
  -H "Content-Type: application/json" \
  -d "{\"password\":\"$FLUENTBIT_PASS\",\"backend_roles\":[\"log_writer\"]}"
```

Copy the CA certificate to VM 2 and VM 4 so their Fluent Bit can verify this server
([VM 2](vm2-vault.md) and [VM 4](vm4-dashboard.md) show where it goes):

```bash
cat certs/root-ca.pem    # public, safe to copy
```

---

## Step 12: Import Dashboard Templates

<!-- Maintainers: the five files under docs/assets/opensearch-dashboards/ are copied from
     orcastra-cmp (config/opensearch-dashboards/*.ndjson and scripts/setup_opensearch_dashboards.sh,
     at f5b8986). When a dashboard changes, replace the file and regenerate SHA256SUMS in the same
     commit: `cd docs/assets/opensearch-dashboards && sha256sum *.ndjson setup_opensearch_dashboards.sh > SHA256SUMS`.
     The copy-and-paste blocks below include the files at build time, so they never drift. -->

Import four pre-built dashboards and their index patterns:

- **Orcastra Logs Overview**, combined view of all log types
- **Orcastra Access Logs**, HTTP request monitoring and latency tracking
- **Orcastra Activity & Audit Logs**, security compliance and user activity
- **Vault Security Audit**, vault operations and secret access patterns

You need five files: the import script `setup_opensearch_dashboards.sh` and four ndjson dashboard
exports in `config/opensearch-dashboards/`. Get them either way below; both give byte-identical
files.

```bash
cd ~/orcastra
mkdir -p config/opensearch-dashboards
```

=== "Download"

    The files are published with this documentation. Download them and verify the checksums:

    ```bash
    cd ~/orcastra
    BASE="https://docs.orcastra.io/en/latest/assets/opensearch-dashboards"
    curl -fsSLO "$BASE/setup_opensearch_dashboards.sh"
    curl -fsSLO "$BASE/SHA256SUMS"
    for f in access-logs-dashboard-v3 audit-logs-dashboard-v3 logs-overview-dashboard vault-audit-dashboard; do
      curl -fsSL -o "config/opensearch-dashboards/$f.ndjson" "$BASE/$f.ndjson"
    done
    (cp setup_opensearch_dashboards.sh config/opensearch-dashboards/ && cd config/opensearch-dashboards \
      && sha256sum -c ../../SHA256SUMS && rm setup_opensearch_dashboards.sh)
    ```

    Every line must end in `OK`. A failed check means the download was incomplete: run it again.

=== "Copy and paste"

    For a VM without internet access. Expand each file, copy the whole block with the copy button
    in its top-right corner, and paste it into the VM 3 shell (in `~/orcastra`). Each block writes
    one complete file (the `sed` drops the blank line the page adds before the closing marker).

    ??? example "Import script: `./setup_opensearch_dashboards.sh`"

        ```bash
        sed '${/^$/d}' > ./setup_opensearch_dashboards.sh << 'ORCASTRA_EOF'
        --8<-- "assets/opensearch-dashboards/setup_opensearch_dashboards.sh"
        ORCASTRA_EOF
        ```

    ??? example "Orcastra Access Logs: `config/opensearch-dashboards/access-logs-dashboard-v3.ndjson`"

        ```bash
        sed '${/^$/d}' > config/opensearch-dashboards/access-logs-dashboard-v3.ndjson << 'ORCASTRA_EOF'
        --8<-- "assets/opensearch-dashboards/access-logs-dashboard-v3.ndjson"
        ORCASTRA_EOF
        ```

    ??? example "Orcastra Activity & Audit Logs: `config/opensearch-dashboards/audit-logs-dashboard-v3.ndjson`"

        ```bash
        sed '${/^$/d}' > config/opensearch-dashboards/audit-logs-dashboard-v3.ndjson << 'ORCASTRA_EOF'
        --8<-- "assets/opensearch-dashboards/audit-logs-dashboard-v3.ndjson"
        ORCASTRA_EOF
        ```

    ??? example "Orcastra Logs Overview: `config/opensearch-dashboards/logs-overview-dashboard.ndjson`"

        ```bash
        sed '${/^$/d}' > config/opensearch-dashboards/logs-overview-dashboard.ndjson << 'ORCASTRA_EOF'
        --8<-- "assets/opensearch-dashboards/logs-overview-dashboard.ndjson"
        ORCASTRA_EOF
        ```

    ??? example "Vault Security Audit: `config/opensearch-dashboards/vault-audit-dashboard.ndjson`"

        ```bash
        sed '${/^$/d}' > config/opensearch-dashboards/vault-audit-dashboard.ndjson << 'ORCASTRA_EOF'
        --8<-- "assets/opensearch-dashboards/vault-audit-dashboard.ndjson"
        ORCASTRA_EOF
        ```

    Then confirm all five files are complete; each line must end in `OK`:

    ```bash
    cd ~/orcastra/config/opensearch-dashboards
    cp ../../setup_opensearch_dashboards.sh . && sha256sum -c << 'EOF'
    --8<-- "assets/opensearch-dashboards/SHA256SUMS"
    EOF
    rm setup_opensearch_dashboards.sh; cd ~/orcastra
    ```

Run the import:

```bash
chmod +x setup_opensearch_dashboards.sh
./setup_opensearch_dashboards.sh \
  --url http://localhost:5601 \
  --password "$OPENSEARCH_PASS" \
  --dashboard-dir config/opensearch-dashboards
```

The import runs as `admin`, whose preferred tenant is **Global** (Step 7), so every user sees
the dashboards. Objects saved in a user's Private tenant are visible only to that user.

!!! tip "Password Variable Issue"
    If you see `[ERROR] Admin password is required`, use the literal password instead:

    ```bash
    ./setup_opensearch_dashboards.sh \
      --url http://localhost:5601 \
      --password "your-actual-password" \
      --dashboard-dir config/opensearch-dashboards
    ```

---

## Step 13 (Recommended): Sign In with Authentik

By default people sign in to Dashboards with the internal `admin` and `audit_viewer` accounts.
Signing in with Authentik (VM 1) gives every person their own identity, central offboarding, and
an audit trail by name. The internal `admin` account stays as the break-glass login.

### Create the Authentik Application

In the Authentik admin interface:

1. **Directory → Groups**: create `opensearch-admins` (full access) and `opensearch-viewers`
   (read-only), and add the people who need access.
2. **Applications → Providers → Create → OAuth2/OpenID Provider**:
    - **Name:** `OpenSearch Dashboards Provider`
    - **Authorization flow:** `default-provider-authorization-implicit-consent`
    - **Client type:** Confidential
    - **Redirect URIs (Strict):** `https://<LOGS_DOMAIN>/auth/openid/login` and `https://<LOGS_DOMAIN>`
    - **Signing key:** `authentik Self-signed Certificate`
    - **Scopes:** `openid`, `email`, `profile`, `offline_access`
    - **Include claims in id_token:** enabled (the `groups` claim comes from the `profile` scope)
3. **Applications → Applications → Create**: name `OpenSearch Dashboards`, slug
   `opensearch-dashboards`, provider `OpenSearch Dashboards Provider`.
4. Open the application → **Policy / Group / User Bindings**: bind `opensearch-admins` and
   `opensearch-viewers`, so people outside both groups are refused by Authentik.
5. Note the provider's **Client ID** and **Client Secret**.

### Configure OpenSearch

The security index was initialized from files at first start; later changes are applied with
`securityadmin.sh` and the admin certificate from Step 5. Write the authentication config, add
the group mappings, then apply the three files:

```bash
AUTHENTIK_DOMAIN="<AUTHENTIK_DOMAIN>"      # for example sso.example.com
CLIENT_ID="<CLIENT_ID>"

cat > config/config.yml << EOF
---
_meta:
  type: "config"
  config_version: 2
config:
  dynamic:
    http:
      anonymous_auth_enabled: false
      xff:
        enabled: false
    authc:
      # Internal users: Fluent Bit, kibanaserver and the break-glass admin.
      basic_internal_auth_domain:
        http_enabled: true
        transport_enabled: true
        order: 0
        http_authenticator:
          type: "basic"
          challenge: false
        authentication_backend:
          type: "intern"
      # People, through Authentik. Tokens are bound to this provider: every Authentik provider
      # can share one signing key, so a signature check alone would accept other apps' tokens.
      openid_auth_domain:
        http_enabled: true
        transport_enabled: false
        order: 1
        http_authenticator:
          type: "openid"
          challenge: false
          config:
            subject_key: "preferred_username"
            roles_key: "groups"
            openid_connect_url: "https://${AUTHENTIK_DOMAIN}/application/o/opensearch-dashboards/.well-known/openid-configuration"
            required_issuer: "https://${AUTHENTIK_DOMAIN}/application/o/opensearch-dashboards/"
            required_audience: "${CLIENT_ID}"
            jwt_clock_skew_tolerance_seconds: 30
        authentication_backend:
          type: "noop"
EOF

cat > config/roles_mapping.yml << 'EOF'
---
_meta:
  type: "rolesmapping"
  config_version: 2

all_access:
  reserved: false
  backend_roles:
    - "admin"
    - "opensearch-admins"
  description: "Break-glass admin and the Authentik opensearch-admins group"

log_writer:
  reserved: false
  backend_roles:
    - "log_writer"

audit_reader:
  reserved: false
  backend_roles:
    - "audit_reader"

audit_admin:
  reserved: false
  backend_roles:
    - "admin"

kibana_server:
  reserved: true
  users:
    - "kibanaserver"

# Authentik opensearch-viewers: read every log index, use Dashboards in read-only mode.
readall:
  reserved: false
  backend_roles:
    - "opensearch-viewers"

kibana_user:
  reserved: false
  backend_roles:
    - "opensearch-viewers"

logs_readonly_ui:
  reserved: false
  backend_roles:
    - "opensearch-viewers"
EOF
```

Apply the three files:

```bash
for t in config:config.yml roles:roles.yml rolesmapping:roles_mapping.yml; do
  docker run --rm --network orcastra_opensearch-net \
    -v "$PWD/certs:/certs:ro" -v "$PWD/config:/work:ro" --user 0 \
    --entrypoint /usr/share/opensearch/plugins/opensearch-security/tools/securityadmin.sh \
    opensearchproject/opensearch:3.5.0 \
    -h opensearch -p 9200 -icl -cacert /certs/root-ca.pem -cert /certs/admin.pem -key /certs/admin-key.pem \
    -f "/work/${t#*:}" -t "${t%%:*}" | grep -E "Done with success|ERR"
done
```

Each of the three should print `Done with success`.

!!! warning "Do not apply `internal_users.yml` this way"
    The `fluentbit` user exists only through the API (Step 11). Applying the users file with
    `securityadmin.sh` would replace all internal users and break log shipping.

### Configure Dashboards

Add the client secret and a cookie encryption password to the Dashboards keystore from Step 7:

```bash
CLIENT_SECRET="<CLIENT_SECRET>"
COOKIE_PASSWORD="$(openssl rand -base64 48 | tr -d '\n')"

tmp="$(mktemp -d)"; install -o 1000 -g 1000 -m 600 config/opensearch_dashboards.keystore "$tmp/"; chown 1000:1000 "$tmp"
docker run --rm -i -v "$tmp:/out" --entrypoint bash opensearchproject/opensearch-dashboards:3.5.0 -c '
  set -e; read -r secret; read -r cookie
  cp /out/opensearch_dashboards.keystore config/
  printf %s "$secret" | bin/opensearch-dashboards-keystore add --stdin --silent --force opensearch_security.openid.client_secret
  printf %s "$cookie" | bin/opensearch-dashboards-keystore add --stdin --silent --force opensearch_security.cookie.password
  bin/opensearch-dashboards-keystore list
  cp config/opensearch_dashboards.keystore /out/' <<< "$CLIENT_SECRET
$COOKIE_PASSWORD"
install -o 1000 -g 1000 -m 600 "$tmp/opensearch_dashboards.keystore" config/
rm -rf "$tmp"
```

The listing should show `opensearch.password`, `opensearch_security.openid.client_secret` and
`opensearch_security.cookie.password`. Then add the sign-in settings:

```bash
set -a; . ./.env; set +a
cat >> config/opensearch_dashboards.yml << EOF

# Sign-in through Authentik; the password form stays for the break-glass admin.
opensearch_security.auth.type: ["openid", "basicauth"]
opensearch_security.auth.multiple_auth_enabled: true
opensearch_security.openid.connect_url: "https://${AUTHENTIK_DOMAIN}/application/o/opensearch-dashboards/.well-known/openid-configuration"
opensearch_security.openid.client_id: "${CLIENT_ID}"
opensearch_security.openid.scope: "openid profile email offline_access"
# Cloudflare terminates TLS, so Dashboards must be told its public URL.
opensearch_security.openid.base_redirect_url: "https://${LOGS_DOMAIN}"
opensearch_security.openid.logout_url: "https://${AUTHENTIK_DOMAIN}/application/o/opensearch-dashboards/end-session/"
opensearch_security.openid.refresh_tokens: true
opensearch_security.ui.openid.login.buttonname: "Log in with SSO"
opensearch_security.cookie.isSameSite: "Lax"
opensearch_security.session.ttl: 28800000
EOF
```

Then recreate Dashboards and confirm the SSO login redirects to Authentik:

```bash
docker compose up -d --force-recreate opensearch-dashboards
until [ "$(docker inspect -f '{{.State.Health.Status}}' opensearch-dashboards)" = healthy ]; do sleep 5; done
curl -s -o /dev/null -w '%{redirect_url}\n' "http://127.0.0.1:5601/auth/openid/login?nextUrl=%2F"
```

The printed URL should start with `https://<AUTHENTIK_DOMAIN>/application/o/authorize/` and
contain `redirect_uri=https%3A%2F%2F<LOGS_DOMAIN>%2Fauth%2Fopenid%2Flogin`.

!!! warning "Dashboards does not start while Authentik is unreachable"
    With OpenID enabled, Dashboards fetches Authentik's discovery document at startup and exits
    if it cannot. A running Dashboards is not affected by an Authentik outage, but a restart
    during one takes the UI down, password login included. To get back in, set
    `opensearch_security.auth.type: "basicauth"` in `config/opensearch_dashboards.yml`, recreate
    the container, sign in as `admin`, and restore the setting once Authentik is back. The REST
    API never depends on Authentik.

---

## Output Summary

After completing VM 3 setup, you should have the following values saved:

| Value | Used On | Environment Variable (VM 4) |
|---|---|---|
| OpenSearch Admin Password | VM 3 (admin operations, break-glass sign-in) | - |
| Dashboards Password | VM 3 (internal user) | - |
| Fluent Bit Password | VM 2, VM 4 | `OPENSEARCH_PASSWORD` |
| OpenSearch IP | VM 2, VM 4 | `OPENSEARCH_HOST` |
| CA certificate (`certs/root-ca.pem`) | VM 2, VM 4 (TLS verification) | - |
| CA private key (`certs/root-ca-key.pem`) | offline backup only | - |

---

**Next:** [VM 4 - Orcastra CMP](vm4-dashboard.md)

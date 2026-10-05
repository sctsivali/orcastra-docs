# Logging Architecture

## Three-Tier Log Separation

Orcastra implements a structured logging pipeline that separates logs into three tiers with different retention policies:

| Log Type | Index Pattern | Retention | Purpose |
|---|---|---|---|
| **Access Logs** | `orcastra-access-*` | 90 days | HTTP request/response tracking |
| **Audit Logs** | `orcastra-audit-*` | 3 years | Security and compliance events |
| **Application Logs** | `orcastra-app-*` | 30 days | Debug and operational logs |
| **Vault Audit** | `vault-audit-*` | 3 years | Vault API operation history |

---

## Pipeline Architecture

```mermaid
graph LR
    subgraph VM4["VM 4 - Dashboard"]
        BE[Backend stdout]
        FE[Frontend stdout]
        DOCKER[Docker Log Driver]
        FB4[Fluent Bit]
        BE --> DOCKER
        FE --> DOCKER
        DOCKER --> FB4
    end

    subgraph VM2["VM 2 - Vault"]
        VAULT[Vault Audit Log]
        FB2[Fluent Bit]
        VAULT -->|/var/log/vault/audit.log| FB2
    end

    subgraph VM3["VM 3 - OpenSearch"]
        OS[OpenSearch]
        OSD[OpenSearch Dashboards]
        OS --> OSD
    end

    FB4 -->|HTTPS :9200| OS
    FB2 -->|HTTPS :9200| OS
```

---

## Fluent Bit Processing

### VM 4 - Dashboard Sidecar

The Fluent Bit container on VM 4 reads Docker container logs and routes them:

```
Docker container stdout → /var/lib/docker/containers/*/*.log
                        ↓
                   [INPUT: tail]
                        ↓
                   [FILTER: nest] - lift nested "log" field
                        ↓
                   [FILTER: modify] - add environment, cluster, collector tags
                        ↓
                   [FILTER: rewrite_tag] - route by log_type:
                        ├── log_type=access → tag: log.access
                        ├── log_type=audit  → tag: log.audit
                        └── level=*         → tag: log.app
                        ↓
                   [OUTPUT: opensearch] - write to OpenSearch indices
```

### Tag-Based Routing

| Source Field | Value | Rewritten Tag | OpenSearch Index |
|---|---|---|---|
| `log_type` | `access` | `log.access` | `orcastra-access-YYYY.MM.DD` |
| `log_type` | `audit` | `log.audit` | `orcastra-audit-YYYY.MM` (monthly) |
| `level` | any | `log.app` | `orcastra-app-YYYY.MM.DD` |
| `message` | any (fallback) | `log.app` | `orcastra-app-YYYY.MM.DD` |

### VM 2 - Vault Log Forwarding

Fluent Bit on VM 2 is installed as a system service (not Docker). It tails the Vault audit log file and forwards each entry to OpenSearch (`vault-audit-YYYY.MM`, one index per month), verifying VM 3's certificate against the logging CA.

### Reliability and Backpressure

Fluent Bit is configured for enterprise-grade durability so multi-day OpenSearch outages do not silently drop logs:

| Setting | Value | Why |
|---|---|---|
| `Retry_Limit` | `no_limits` (every output, both the VM 4 and VM 2 forwarders) | Audit logs must never be dropped (compliance); access/app and the VM 2 Vault forwarder inherit the same policy. |
| `storage.type filesystem` (per input) | enabled | Buffered chunks survive container restarts. |
| `storage.backlog.mem_limit` | `512M` | Headroom for in-flight retries during transient slowness. |
| `storage.total_limit_size` (per output) | audit `8G`, app `4G`, access `2G` | Per-stream disk backlog ceiling, audit gets the largest budget. |
| `net.connect_timeout` / `net.keepalive` | `10s` / on | Detect stalled OpenSearch connections quickly. |
| `HC_Errors_Count` / `HC_Retry_Failure_Count` | `5` over `60s` | Fluent Bit `/api/v1/health` flips red on shipping failures, Docker marks the container unhealthy and restarts it. |

The logging healthcheck is documented as a self-contained helper script in [Operations → Troubleshooting](../operations/troubleshooting.md#fluent-bit-cannot-write-to-opensearch). Copy it to each VM and wire it to cron or your alerting system.

---

## OpenSearch Index Management

### Index Templates

Index templates are configured on VM 3 to define field mappings and settings:

- **`orcastra-access-template`** maps HTTP fields: `method`, `path`, `status_code`, `latency_ms`, `client.ip`, `client.user_agent`
- **`orcastra-audit-template`** maps audit fields: `action`, `category`, `actor.user_id`, `target.type`, `target.id`, `result`
- **`vault-audit-template`** maps Vault fields: `type`, `auth.client_token`, `request.operation`, `request.path`
- **`orcastra-app-template`** and **`security-auditlog-template`** set one shard and no replica

Every template uses one shard and no replica: on a single node a replica can never be assigned.

### ISM (Index State Management) Policies

Retention runs automatically through five ISM policies created during VM 3 setup
(see [Step 10](../deployment/vm3-opensearch.md#step-10-create-ism-retention-policies)).
Each policy attaches to new indices through its `ism_template` (matched by index
pattern) at creation time, so the indices Fluent Bit writes pick up their lifecycle
with no manual step. States advance by index age (`min_index_age`), and every index
is snapshotted to the `orcastra-archive` repository before it is deleted.

Short-retention logs use one index per day; the 3-year logs use one index per month. A single
node allows at most 1000 shards, and one index per day for 3 years would pass that limit in
the first year and stop ingestion. With this layout the cluster settles at roughly 200 shards.

An index becomes read-only only after the period it covers is over (2 days for a daily index,
35 days for a monthly one), so late lines buffered by Fluent Bit still land. Monthly indices are
deleted at retention plus one month, which keeps every document for the full retention period.

=== "Access Logs (90 days, daily)"

    ```
    hot     0-2d       ingesting
    warm    2-89d      read-only, force_merge to 1 segment
    archive 89-90d     snapshot to orcastra-archive
    delete  90d+       delete
    ```

=== "App Logs (30 days, daily)"

    ```
    hot     0-2d       ingesting
    warm    2-29d      read-only, force_merge to 1 segment
    archive 29-30d     snapshot to orcastra-archive
    delete  30d+       delete
    ```

=== "Audit Logs (3 years, monthly)"

    ```
    hot     0-35d       ingesting (the month plus a margin)
    warm    35-1125d    read-only, force_merge to 1 segment
    archive 1125-1126d  snapshot to orcastra-archive
    delete  1126d+      delete
    ```

=== "Vault Audit (3 years, monthly)"

    ```
    hot     0-35d       ingesting (the month plus a margin)
    warm    35-1125d    read-only, force_merge to 1 segment
    archive 1125-1126d  snapshot to orcastra-archive
    delete  1126d+      delete
    ```

=== "Security Audit Log (1 year, monthly)"

    ```
    hot     0-35d       ingesting (the month plus a margin)
    warm    35-395d     read-only, force_merge to 1 segment
    archive 395-396d    snapshot to orcastra-archive
    delete  396d+       delete
    ```

The policy definitions are in [VM 3 Step 10](../deployment/vm3-opensearch.md#step-10-create-ism-retention-policies).
Because attachment is by index pattern, the policy must exist before the first matching index is
created; the deployment order applies policies before log forwarding starts.

---

## OpenSearch Security Model

### Users

| User | Role | Purpose |
|---|---|---|
| `admin` | All access | Administrative operations, dashboard import |
| `fluentbit` | `log_writer` | Write-only access to `orcastra-*` and `vault-audit-*` indices |
| `audit_viewer` | `audit_reader` | Read-only access to audit indices |
| `kibanaserver` | (built-in) | OpenSearch Dashboards internal user |

!!! warning "Per-user unique bcrypt hashes"
    Every internal user MUST have a unique bcrypt hash. Do not depend on a repo
    helper script being present on the target VM. Generate each hash locally,
    then write `internal_users.yml` by hand from the deployment guide.

### Fluent Bit Writer Role

```yaml
log_writer:
  cluster_permissions:
    - cluster_composite_ops    # bulk writes authorize at cluster scope first
    - cluster_monitor
    - "cluster:admin/ingest/pipeline/put"
    - "cluster:admin/ingest/pipeline/get"
    - "indices:admin/template/get"
    - "indices:admin/template/put"
  index_permissions:
    - index_patterns: ["orcastra-access-*", "orcastra-audit-*", "orcastra-app-*", "vault-audit-*"]
      allowed_actions: ["crud", "create_index", "manage", "indices:admin/mapping/auto_put"]
```

---

## Dashboard Templates

Pre-built OpenSearch Dashboards are imported during VM 3 setup:

| Dashboard | Description |
|---|---|
| Access Logs | HTTP request analytics, status codes, latency, top endpoints |
| Audit Logs | Security event timeline, user actions, RBAC changes |
| Logs Overview | Combined view across all log types |
| Vault Audit | Vault API operations, secret access, authentication events |

# Component Architecture

## System Overview

```mermaid
graph TB
    subgraph VM1["VM 1 — Authentik"]
        AUTH_SERVER[Authentik Server]
        AUTH_WORKER[Authentik Worker]
        AUTH_PG[(PostgreSQL)]
        AUTH_REDIS[(Redis)]
        AUTH_SERVER --> AUTH_PG
        AUTH_SERVER --> AUTH_REDIS
        AUTH_WORKER --> AUTH_PG
        AUTH_WORKER --> AUTH_REDIS
    end

    subgraph VM2["VM 2 — Vault"]
        VAULT[HashiCorp Vault]
        VAULT_FB[Fluent Bit]
        VAULT --> VAULT_FB
    end

    subgraph VM3["VM 3 — OpenSearch"]
        OS_NODE[OpenSearch Node]
        OS_DASH[OpenSearch Dashboards]
        OS_DASH --> OS_NODE
    end

    subgraph VM4["VM 4 — Dashboard"]
        FE[Next.js Frontend]
        BE[FastAPI Backend]
        PG[(PostgreSQL)]
        RD[(Redis)]
        FB[Fluent Bit]
        FE --> BE
        BE --> PG
        BE --> RD
        BE --> FB
        FE --> FB
    end

    FE -->|OIDC| AUTH_SERVER
    BE -->|Token Validation| AUTH_SERVER
    BE -->|Secrets / PKI| VAULT
    FB -->|Logs| OS_NODE
    VAULT_FB -->|Audit Logs| OS_NODE
```

---

## VM Specifications

| VM | Role | vCPU | RAM | Storage | Key Services |
|---|---|---|---|---|---|
| VM 1 | Identity Provider | 2 | 4 GB | 40 GB | Authentik Server, Worker, PostgreSQL, Redis |
| VM 2 | Secrets Manager | 2 | 2 GB | 20 GB | HashiCorp Vault, Fluent Bit |
| VM 3 | Log Analytics | 4 | 16 GB | 100 GB | OpenSearch, OpenSearch Dashboards |
| VM 4 | Application | 4 | 8 GB | 60 GB | Next.js, FastAPI, PostgreSQL, Redis, Fluent Bit |

**Total:** 12 vCPU, 30 GB RAM, 220 GB Storage

---

## Service Inventory

### VM 1 — Authentik

| Container | Image | Port | Purpose |
|---|---|---|---|
| `server` | `ghcr.io/goauthentik/server` | 9000 | OIDC Provider, Admin UI |
| `worker` | `ghcr.io/goauthentik/server` | — | Background task processing |
| `postgresql` | `docker.io/library/postgres:16-alpine` | 5432 | Authentik data store |
| `redis` | `docker.io/library/redis:alpine` | 6379 | Session cache, task queue |

### VM 2 — Vault

| Process | Package | Port | Purpose |
|---|---|---|---|
| `vault` | HashiCorp Vault (apt) | 8200 | Secret management, PKI CA |
| `fluent-bit` | Fluent Bit (apt) | — | Vault audit log forwarding |

### VM 3 — OpenSearch

| Container | Image | Port | Purpose |
|---|---|---|---|
| `opensearch-node1` | `opensearchproject/opensearch:2.20.1` | 9200 | Search & analytics engine |
| `opensearch-dashboards` | `opensearchproject/opensearch-dashboards:2.20.1` | 5601 | Visualization UI |

### VM 4 — Orcastra Dashboard

| Container | Image | Port | Purpose |
|---|---|---|---|
| `postgres` | `postgres:17-alpine` | 5432 | Application database |
| `redis` | `redis:8-alpine` | 6379 | Cache, rate limiting |
| `backend` | `svlct/orcastra-dashboard:backend-*` | 8765 → 4050 | REST API (FastAPI) |
| `frontend` | `svlct/orcastra-dashboard:frontend-*` | 4321 → 2025 | Web UI (Next.js) |
| `fluent-bit` | `fluent/fluent-bit:4.2.2-debug` | 2020 | Log collection sidecar |

---

## Inter-VM Communication

```mermaid
sequenceDiagram
    participant Browser
    participant FE as Frontend (VM4)
    participant BE as Backend (VM4)
    participant Auth as Authentik (VM1)
    participant Vault as Vault (VM2)
    participant OS as OpenSearch (VM3)

    Browser->>FE: Access dashboard
    FE->>Auth: OIDC redirect
    Auth-->>Browser: Login page
    Browser->>Auth: Credentials
    Auth-->>FE: Authorization code
    FE->>Auth: Exchange code for tokens
    Auth-->>FE: ID Token + Access Token
    FE->>BE: API request (Bearer token)
    BE->>Auth: Validate token (JWKS)
    BE->>Vault: Fetch secrets / Issue certs
    Vault-->>BE: Secret data
    BE-->>FE: API response
    BE->>OS: Logs (via Fluent Bit)
```

---

## Data Flow

### Request Path

```
Browser → Frontend (Next.js) → Backend (FastAPI) → PostgreSQL / Redis / Vault
```

### Authentication Path

```
Browser → Frontend → Authentik (OIDC) → Frontend (callback) → Backend (token validation)
```

### Logging Path

```
Backend/Frontend (stdout) → Docker log driver → Fluent Bit (tail) → OpenSearch
Vault (audit log file) → Fluent Bit (tail) → OpenSearch
```

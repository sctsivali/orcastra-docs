# Architecture Overview

This page describes the high-level architecture of the Orcastra platform, including component interactions, data flow, and network topology.

## System Architecture

```mermaid
graph TB
    subgraph "Browser"
        U[User]
    end

    subgraph "VM 4 — Orcastra Dashboard"
        FE[Frontend<br/>Next.js :4321]
        BE[Backend<br/>FastAPI :8765]
        PG[(PostgreSQL)]
        RD[(Redis)]
        FB1[Fluent Bit<br/>Sidecar]
    end

    subgraph "VM 1 — Authentik"
        AK[Authentik<br/>:9000]
        AK_PG[(PostgreSQL)]
        AK_RD[(Redis)]
    end

    subgraph "VM 2 — Vault"
        VT[Vault<br/>:8200]
        FB2[Fluent Bit<br/>Native]
    end

    subgraph "VM 3 — OpenSearch"
        OS[OpenSearch<br/>:9200]
        OSD[Dashboards<br/>:5601]
    end

    U -->|HTTPS/HTTP| FE
    U -->|SSO Login| AK
    FE -->|API Calls| BE
    BE --> PG
    BE --> RD
    BE -->|Auth Validation| AK
    BE -->|Secrets & PKI| VT
    FB1 -->|Access/Audit/App Logs| OS
    FB2 -->|Vault Audit Logs| OS
    OSD --> OS
```

## Component Responsibilities

### VM 1 — Authentik (Identity Provider)

- **Role:** Single Sign-On (SSO) and identity management
- **Protocol:** OAuth2/OpenID Connect
- **Key Functions:**
    - User authentication and session management
    - Role group management (`role_admin`, `role_partner`, `role_tenant`)
    - API token issuance for automated group sync
- **Technology:** Authentik (Docker), PostgreSQL, Redis

### VM 2 — Vault (Secret Management)

- **Role:** Secret storage and PKI certificate authority
- **Key Functions:**
    - KV v2 secret engine for cluster credentials
    - PKI intermediate CA for TLS certificate issuance
    - Audit logging forwarded to OpenSearch via Fluent Bit
- **Technology:** HashiCorp Vault (native), Fluent Bit (native)

### VM 3 — OpenSearch (Logging & Analytics)

- **Role:** Centralized log aggregation and dashboards
- **Key Functions:**
    - Receives logs from VM 2 (Vault audits) and VM 4 (application logs)
    - Pre-built dashboards: Access Logs, Audit Logs, Logs Overview, Vault Audit
    - Index lifecycle management with retention policies
- **Technology:** OpenSearch (Docker), OpenSearch Dashboards (Docker)

### VM 4 — Orcastra Dashboard (Application)

- **Role:** The main web application and API backend
- **Key Functions:**
    - Multi-cluster LXD management UI
    - REST API for cluster operations, user management, and reporting
    - Fluent Bit sidecar for structured log shipping
- **Technology:** Next.js (Frontend), FastAPI (Backend), PostgreSQL, Redis, Fluent Bit (Docker)

## Data Flow

### Authentication Flow

```mermaid
sequenceDiagram
    participant User
    participant Frontend as VM 4: Frontend
    participant Authentik as VM 1: Authentik
    participant Backend as VM 4: Backend

    User->>Frontend: Access dashboard
    Frontend->>Authentik: Redirect to SSO login
    User->>Authentik: Enter credentials
    Authentik->>Frontend: Return OAuth2 tokens
    Frontend->>Backend: API request + JWT
    Backend->>Authentik: Validate token
    Backend->>Frontend: Response
```

### Logging Pipeline

```mermaid
graph LR
    subgraph "VM 4"
        BE_LOG[Backend Logs] --> FB_VM4[Fluent Bit]
        FE_LOG[Frontend Logs] --> FB_VM4
    end

    subgraph "VM 2"
        VAULT_LOG[Vault Audit Log] --> FB_VM2[Fluent Bit]
    end

    subgraph "VM 3"
        FB_VM4 -->|orcastra-access-*| OS[OpenSearch]
        FB_VM4 -->|orcastra-audit-*| OS
        FB_VM4 -->|orcastra-app-*| OS
        FB_VM2 -->|vault-audit-*| OS
    end
```

### Log Index Retention

| Index Pattern | Source | Retention |
|---|---|---|
| `orcastra-access-*` | HTTP access logs | 90 days |
| `orcastra-audit-*` | Activity & audit events | 3 years |
| `orcastra-app-*` | Application logs | 30 days |
| `vault-audit-*` | Vault operations | 30 days |

## Network Topology

```
┌─────────────────────────────────────────────────────────────┐
│                      LXD Host Server                        │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │  VM 1    │  │  VM 2    │  │  VM 3    │  │  VM 4    │   │
│  │ Authentik│  │  Vault   │  │OpenSearch│  │Dashboard │   │
│  │  :9000   │  │  :8200   │  │:9200/:5601│ │:4321/:8765│  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘   │
│       │              │              │              │         │
│       └──────────────┴──────────────┴──────────────┘         │
│                    LXD Bridge Network                        │
└─────────────────────────────────────────────────────────────┘
                           │
                    Port Forwarding
                           │
                    ┌──────┴──────┐
                    │   Internet  │
                    │  / Browser  │
                    └─────────────┘
```

## RBAC Model

| Role | Group | Access Level |
|---|---|---|
| **Admin** | `role_admin` | Full system-wide access — all clusters, users, settings |
| **Partner** | `role_partner` | Cluster owner — manages own clusters, organizations, tenants |
| **Tenant** | `role_tenant` | End user — access to assigned projects only |

Each user belongs to exactly one role group in Authentik. If assigned to multiple groups, the highest-privilege role takes effect.

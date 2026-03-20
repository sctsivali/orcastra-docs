# Getting Started

Welcome to the Orcastra platform deployment documentation. This section covers everything you need to know before deploying the system.

## What is Orcastra?

Orcastra is an operations center dashboard designed for organizations managing multi-cluster LXD infrastructure. It provides:

- **Centralized Management** — Manage multiple LXD clusters, containers, and virtual machines from a single dashboard
- **Identity & Access Management** — SSO authentication with role-based access control (Admin, Partner, Tenant)
- **Secret Management** — Secure credential storage and PKI certificate management via HashiCorp Vault
- **Audit & Compliance** — Comprehensive audit logging with 3-year retention for regulatory compliance
- **Real-time Monitoring** — Access logs, performance metrics, and operational dashboards via OpenSearch

## Deployment Overview

The platform is deployed across four virtual machines in an on-premises LXD environment:

```mermaid
graph LR
    A[VM 1<br/>Authentik<br/>SSO] --> D[VM 4<br/>Orcastra<br/>Dashboard]
    B[VM 2<br/>Vault<br/>Secrets] --> D
    C[VM 3<br/>OpenSearch<br/>Logging] --> D
    B -->|Audit Logs| C
```

## Sections

<div class="grid cards" markdown>

-   **[Prerequisites](prerequisites.md)**

    Hardware, software, and account requirements.

-   **[Architecture Overview](architecture.md)**

    System design, data flow, and component interactions.

-   **[Quick Start](quick-start.md)**

    Condensed checklist for experienced administrators.

</div>

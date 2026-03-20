---
hide:
  - navigation
---

# Orcastra Documentation

**Operations center for multi-cluster LXD management, secret management, PKI, RBAC, and centralized logging.**

---

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } **Getting Started**

    ---

    Understand the system architecture, prerequisites, and get a quick overview of the deployment process.

    [:octicons-arrow-right-24: Getting Started](getting-started/index.md)

-   :material-server:{ .lg .middle } **Deployment Guide**

    ---

    Step-by-step instructions for deploying each component across four VMs in an on-premises environment.

    [:octicons-arrow-right-24: Deployment](deployment/index.md)

-   :material-wrench:{ .lg .middle } **Operations**

    ---

    Domain configuration, networking, verification, and troubleshooting guides for day-to-day operations.

    [:octicons-arrow-right-24: Operations](operations/index.md)

-   :material-shield-lock:{ .lg .middle } **Architecture**

    ---

    Deep dive into system components, security model, and the centralized logging pipeline.

    [:octicons-arrow-right-24: Architecture](architecture/index.md)

</div>

---

## Platform Overview

Orcastra Dashboard is a full-stack platform that manage infrastructure across multiple clusters with:

- **Single Sign-On (SSO)** via Authentik with role-based access control (RBAC)
- **Secret Management & PKI** via HashiCorp Vault
- **Centralized Logging** via OpenSearch with Fluent Bit log collection
- **Operations Dashboard** with real-time monitoring, session management, and audit trails

## Deployment Model

The platform deploys across four virtual machines, each hosting a dedicated component:

| VM | Component | Purpose | Resources |
|---|---|---|---|
| VM 1 | **Authentik** | SSO & Identity Provider | 2 vCPU, 4 GB RAM, 40 GB |
| VM 2 | **Vault** | Secret Management & PKI | 2 vCPU, 2 GB RAM, 20 GB |
| VM 3 | **OpenSearch** | Centralized Logging | 4 vCPU, 16 GB RAM, 100 GB |
| VM 4 | **Dashboard** | Orcastra Web Application | 4 vCPU, 8 GB RAM, 60 GB |

!!! tip "Deployment Order"
    Deploy in order: **VM 1 → VM 2 → VM 3 → VM 4**. Each VM depends on the previous one for configuration values (tokens, passwords, URLs).

## Quick Links

| Document | Description |
|---|---|
| [Prerequisites](getting-started/prerequisites.md) | Required infrastructure and accounts |
| [VM 1 — Authentik](deployment/vm1-authentik.md) | SSO provider setup |
| [VM 2 — Vault](deployment/vm2-vault.md) | Secret engine & PKI setup |
| [VM 3 — OpenSearch](deployment/vm3-opensearch.md) | Log aggregation & dashboards |
| [VM 4 — Dashboard](deployment/vm4-dashboard.md) | Application deployment |
| [Troubleshooting](operations/troubleshooting.md) | Common issues & fixes |

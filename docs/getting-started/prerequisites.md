# Prerequisites

Before beginning the deployment, ensure you have the following infrastructure and accounts ready.

## Infrastructure Requirements

### Virtual Machines

You need four VMs with the following minimum specifications:

| VM | Role | vCPU | RAM | Storage | OS |
|---|---|---|---|---|---|
| VM 1 | Authentik (SSO) | 2 | 4 GB | 40 GB | Ubuntu 22.04+ |
| VM 2 | Vault (Secrets) | 2 | 2 GB | 20 GB | Ubuntu 22.04+ |
| VM 3 | OpenSearch (Logging) | 4 | 16 GB | 100 GB | Ubuntu 22.04+ |
| VM 4 | Orcastra Dashboard | 4 | 8 GB | 60 GB | Ubuntu 22.04+ |

### LXD Configuration

All LXD containers require the following security settings:

1. Navigate to **Security Policies** in the instance configuration
2. Set **Privileged (Containers only)** → `Allow`
3. Set **Nesting (Containers only)** → `Allow`
4. Save changes

!!! warning "VM 3 — Additional Host Configuration"
    The LXD **host server** (not the container) requires increased virtual memory mapping for OpenSearch:

    ```bash
    sudo sysctl -w vm.max_map_count=262144
    echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
    sudo sysctl -p
    ```

### Networking

- All four VMs must be able to communicate with each other over the LXD network
- The following ports must be accessible (via LXD port forwarding or direct access):

| Service | Port | VM | Direction |
|---|---|---|---|
| Authentik | 9000 | VM 1 | Inbound (browser + VM 4) |
| Vault | 8200 | VM 2 | Inbound (VM 4) |
| OpenSearch | 9200 | VM 3 | Inbound (VM 2 + VM 4) |
| OpenSearch Dashboards | 5601 | VM 3 | Inbound (browser) |
| Orcastra Frontend | 4321 | VM 4 | Inbound (browser) |
| Orcastra Backend | 8765 | VM 4 | Inbound (browser) |

## Software Requirements

All VMs require Docker with the Compose plugin. The Docker installation steps are included in each VM's deployment guide.

| Software | Version | Required On |
|---|---|---|
| Docker Engine | 24.0+ | VM 1, VM 3, VM 4 |
| Docker Compose Plugin | 2.20+ | VM 1, VM 3, VM 4 |
| Vault | 1.15+ | VM 2 (native install) |
| Fluent Bit | 3.0+ | VM 2 (native install) |

## Accounts & Credentials

During deployment, you will generate and collect the following credentials. **Keep them secure** — they are required across VMs.

| Credential | Generated On | Used On | Description |
|---|---|---|---|
| Authentik admin password | VM 1 | VM 1 | `akadmin` account password |
| OAuth2 Client ID | VM 1 | VM 4 | OIDC provider client identifier |
| OAuth2 Client Secret | VM 1 | VM 4 | OIDC provider client secret |
| Authentik API Token | VM 1 | VM 4 | Token for group sync (optional) |
| Vault Unseal Keys (×5) | VM 2 | VM 2 | Required to unseal Vault after restart |
| Vault Root Token | VM 2 | VM 2 | Initial root access token |
| Vault Dashboard Token | VM 2 | VM 4 | Scoped token for Orcastra |
| OpenSearch Admin Password | VM 3 | VM 3 | Admin access to OpenSearch |
| OpenSearch Dashboards Password | VM 3 | VM 3 | `kibanaserver` internal user |
| Fluent Bit Password | VM 3 | VM 2, VM 4 | Log writer service account |
| PostgreSQL Password | VM 4 | VM 4 | Database access |
| NextAuth Secret | VM 4 | VM 4 | Session encryption key |
| Redis Encryption Key | VM 4 | VM 4 | Cache encryption (Fernet) |
| Secret Key | VM 4 | VM 4 | Backend application secret |

!!! danger "Credential Security"
    Store all credentials in a secure password manager. Never commit them to version control. The deployment scripts generate strong random values — use them as-is.

## Optional Requirements

| Feature | Requirement |
|---|---|
| Custom domain (e.g., `app.orcastra.io`) | Cloudflare account with DNS management |
| HTTPS via Cloudflare Tunnel | Cloudflare Zero Trust (free tier) |
| Persistent iptables rules | `iptables-persistent` package on VM 4 |

## Deployment Order

!!! tip "Follow This Order"
    Each VM depends on credentials and configuration from previous VMs:

    ```
    VM 1 (Authentik) → VM 2 (Vault) → VM 3 (OpenSearch) → VM 4 (Dashboard)
    ```

    You **cannot** deploy VM 4 without first completing VMs 1–3, as the Dashboard `.env` requires values from all three.

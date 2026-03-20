# Quick Start

A condensed deployment checklist for experienced administrators. For detailed instructions, see the full [Deployment Guide](../deployment/index.md).

## Deployment Checklist

### VM 1 - Authentik

- [ ] Install Docker
- [ ] Deploy Authentik via `docker compose`
- [ ] Create admin account at `http://<VM1_IP>:9000/if/flow/initial-setup/`
- [ ] Create OAuth2/OIDC Provider (`Orcastra Dashboard Provider`)
- [ ] Create Application (`orcastra-dashboard`)
- [ ] Create role groups: `role_admin`, `role_partner`, `role_tenant`
- [ ] Assign `akadmin` to `role_admin`
- [ ] Create API token for group sync
- [ ] **Save:** Client ID, Client Secret, API Token, Issuer URL

### VM 2 - Vault

- [ ] Install Vault (native package)
- [ ] Configure listener on `0.0.0.0:8200` (TLS disabled for internal use)
- [ ] Initialize and unseal Vault (3 of 5 keys)
- [ ] Enable KV v2 secret engine at `secret/`
- [ ] Setup PKI: Root CA → Intermediate CA → LXD role
- [ ] Create `orcastra-policy` and scoped dashboard token
- [ ] Enable file audit device at `/var/log/vault/audit.log`
- [ ] Configure logrotate for audit logs
- [ ] Install Fluent Bit (native) to forward audit logs to VM 3
- [ ] **Save:** Dashboard Token, Unseal Keys, Root Token

### VM 3 - OpenSearch

- [ ] Install Docker
- [ ] Generate passwords (admin, dashboards, fluent-bit)
- [ ] Deploy OpenSearch + Dashboards via `docker compose`
- [ ] Create Fluent Bit internal user via Security API
- [ ] Import dashboard templates (4 ndjson files)
- [ ] Create Vault audit ingest pipeline and index template
- [ ] Create Orcastra access and audit index templates
- [ ] **Save:** Admin Password, Dashboards Password, Fluent Bit Password

### VM 4 - Orcastra Dashboard

- [ ] Install Docker
- [ ] Create configuration files (Fluent Bit, Docker Compose)
- [ ] Generate secrets (PostgreSQL, NextAuth, Redis encryption, secret key)
- [ ] Create `.env` with all values from VMs 1–3
- [ ] Pull and start containers via `docker compose -f docker-compose.prod.yml up -d`
- [ ] Configure iptables for Docker→Authentik connectivity (if using LXD)
- [ ] Verify login and dashboard functionality
- [ ] *(Optional)* Configure Cloudflare Tunnel for custom domain

## Environment Variables Summary

The `.env` file on VM 4 requires values from all three preceding VMs:

```ini
# From VM 1 (Authentik)
AUTHENTIK_ISSUER=http://<VM1_IP>:9000/application/o/orcastra-dashboard/
AUTHENTIK_CLIENT_ID=<from_step_1>
AUTHENTIK_CLIENT_SECRET=<from_step_1>
AUTHENTIK_API_TOKEN=<from_step_4>

# From VM 2 (Vault)
VAULT_ADDR=http://<VM2_IP>:8200
VAULT_TOKEN=<dashboard_token>

# From VM 3 (OpenSearch)
OPENSEARCH_HOST=<VM3_IP>
OPENSEARCH_PASSWORD=<fluentbit_password>

# Generated on VM 4
POSTGRES_PASSWORD=<generated>
NEXTAUTH_SECRET=<generated>
SECRET_KEY=<generated>
REDIS_ENCRYPTION_KEY=<generated>
```

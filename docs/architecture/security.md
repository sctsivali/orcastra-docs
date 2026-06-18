# Security Architecture

## Authentication - Authentik OIDC

Orcastra uses Authentik as a centralized identity provider via the OpenID Connect (OIDC) protocol.

### Flow

1. User accesses the dashboard → redirected to Authentik login
2. Authentik authenticates the user and issues an **ID Token** + **Access Token**
3. Frontend stores the session via NextAuth.js
4. Backend validates the token against Authentik's JWKS endpoint on every API request

### Token Validation

The backend verifies:

- **Signature** via Authentik's JWKS (JSON Web Key Set) endpoint
- **Audience** must match `AUTHENTIK_CLIENT_ID`
- **Issuer** must match `AUTHENTIK_ISSUER`
- **Expiration** rejects expired tokens

---

## Authorization - Role-Based Access Control

The backend maps Authentik groups to a strict three-tier role model (`backend/app/core/rbac.py`):

| Role | Scope |
|---|---|
| Admin | System-wide: all clusters, users, settings, and logs |
| Partner | Cluster owner: own clusters, projects, and tenants |
| Tenant | End user: assigned projects only |

A role is granted by placing the user in the matching Authentik group. The canonical group names are `role_admin`, `role_partner`, and `role_tenant`; the resolver also accepts the plural aliases `orcastra-admins`, `orcastra-partners`, `orcastra-tenants` and the bare `admin` / `partner` / `tenant`. Any group beginning with `role_` is read as a role claim. A user whose groups match none of these falls back to Tenant (least privilege).

Groups are delivered in the OIDC `groups` claim and resolved on every request.

---

## Secrets Management - HashiCorp Vault

### Secret Engines

| Engine | Path | Purpose |
|---|---|---|
| KV v2 | `secret/` | Application secrets (API keys, credentials) |
| PKI Root CA | `pki/` | Root certificate authority |
| PKI Intermediate CA | `pki_int/` | Issues certificates for services |

### Access Policy

The backend uses a scoped Vault token with the `orcastra-policy` policy:

```hcl
# KV v2 - application secrets (clusters, app, integrations, user keys)
path "secret/data/clusters/*"         { capabilities = ["create", "read", "update", "delete", "list"] }
path "secret/metadata/clusters/*"     { capabilities = ["list", "read", "delete"] }
path "secret/data/orcastra/*"         { capabilities = ["create", "read", "update"] }
path "secret/data/integrations/*"     { capabilities = ["create", "read", "update", "delete"] }
path "secret/metadata/integrations/*" { capabilities = ["list", "read", "delete"] }
path "secret/data/my_keys/*"          { capabilities = ["create", "read", "update", "delete", "list"] }
path "secret/metadata/my_keys/*"      { capabilities = ["list", "read", "delete"] }

# PKI - issue LXD client certificates
path "pki_int/issue/lxd" { capabilities = ["create", "update"] }
path "pki_int/certs"     { capabilities = ["list"] }
```

---

## Encryption

### In Transit

| Connection | Encryption | Notes |
|---|---|---|
| Browser → Frontend | HTTPS (via Cloudflare Tunnel) | Automatic with domain setup |
| Frontend → Backend | HTTP (internal Docker network) | Same VM, bridge network |
| Backend → Vault | HTTP | Internal LXD network |
| Backend → OpenSearch | HTTPS (self-signed) | TLS verify disabled |
| Fluent Bit → OpenSearch | HTTPS (self-signed) | TLS verify disabled |

### At Rest

| Data | Encryption | Location |
|---|---|---|
| Vault secrets | AES-256-GCM (Vault's barrier) | VM 2 filesystem |
| PostgreSQL data | Docker volume (unencrypted) | VM 4 filesystem |
| Redis cache | Fernet symmetric encryption | VM 4 (application-level) |
| OpenSearch indices | Unencrypted at rest | VM 3 filesystem |

!!! tip "Production Hardening"
    For production environments, consider enabling:

    - TLS between all internal services (Vault, OpenSearch)
    - Disk-level encryption (LUKS) on all VMs
    - OpenSearch encryption at rest plugin

---

## Audit Logging

### Vault Audit

Vault's file audit device logs every API operation:

```
/var/log/vault/audit.log
```

Logs are:

- Rotated via `logrotate` (daily, 90 days retention)
- Forwarded to OpenSearch via Fluent Bit on VM 2
- Indexed as `vault-audit-*` with a 3-year ISM retention policy (`vault-audit-policy`)

### Application Audit

The backend generates structured audit logs for:

- User authentication events (login, logout, token refresh)
- RBAC operations (role changes, permission checks)
- Data modifications (CRUD operations on nodes, secrets)
- Administrative actions (settings changes, user management)

These are indexed in OpenSearch as `orcastra-audit-*` with a 3-year ISM retention policy (`orcastra-audit-policy`).

---

## Network Security

### Minimal Attack Surface

- No public-facing ports when using Cloudflare Tunnel
- Internal services (PostgreSQL, Redis) are not exposed outside Docker networks
- Vault listens on `0.0.0.0:8200` but only accessible within LXD network

### Container Security

The Docker Compose configuration includes:

```yaml
security_opt:
  - no-new-privileges:true  # Prevent privilege escalation
tmpfs:
  - /tmp:mode=1777,size=100m  # Limit temp filesystem
```

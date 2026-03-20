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

- **Signature** - via Authentik's JWKS (JSON Web Key Set) endpoint
- **Audience** - must match `AUTHENTIK_CLIENT_ID`
- **Issuer** - must match `AUTHENTIK_ISSUER`
- **Expiration** - rejects expired tokens

---

## Authorization - Role-Based Access Control

Three Authentik groups map to application roles:

| Authentik Group | Dashboard Role | Permissions |
|---|---|---|
| `orcastra-admin` | Admin | Full access: manage users, nodes, settings, view all logs |
| `orcastra-operator` | Operator | Operational access: manage nodes, view logs, limited settings |
| `orcastra-viewer` | Viewer | Read-only: view dashboards and logs |

Groups are assigned in the Authentik admin panel and passed to the application via OIDC token claims.

---

## Secrets Management - HashiCorp Vault

### Secret Engines

| Engine | Path | Purpose |
|---|---|---|
| KV v2 | `secret/` | Application secrets (API keys, credentials) |
| PKI Root CA | `pki/` | Root certificate authority |
| PKI Intermediate CA | `pki_int/` | Issues certificates for services |

### Access Policy

The backend uses a scoped Vault token with the `orcastra-dashboard` policy:

```hcl
# KV v2 - read/write application secrets
path "secret/data/*"     { capabilities = ["create", "read", "update", "delete", "list"] }
path "secret/metadata/*" { capabilities = ["list", "read", "delete"] }

# PKI - issue and manage certificates
path "pki_int/issue/lxd" { capabilities = ["create", "update"] }
path "pki_int/certs"     { capabilities = ["list"] }
path "pki_int/revoke"    { capabilities = ["create", "update"] }
path "pki/cert/ca"       { capabilities = ["read"] }
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
- Indexed as `vault-audit-*` with 1-year ISM policy

### Application Audit

The backend generates structured audit logs for:

- User authentication events (login, logout, token refresh)
- RBAC operations (role changes, permission checks)
- Data modifications (CRUD operations on nodes, secrets)
- Administrative actions (settings changes, user management)

These are indexed in OpenSearch as `orcastra-audit-*` with a 3-year retention ISM policy.

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

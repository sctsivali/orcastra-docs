# Orcastra Mini

**Single-host build of Orcastra CMP. The control plane and HashiCorp Vault run on
one machine, and operators sign in with TLS client certificates instead of an external
identity provider.**

**Current release: Mini 1.0.0 (CMP 1.0.2).** The dashboard footer shows the same line.

---

Orcastra Mini is the same product as full Orcastra CMP, packaged for sites that
cannot run a four-VM topology. Every CMP 1.0.2 feature is present (3-tier RBAC, instance
lifecycle, console and terminal, monitoring, Migration Center, integration keys, add-ons and
MCP) except the ones that need an identity provider or a marketplace federation:
**Settings > User Groups** (roles are set on the Operators page) and organizations, owner keys
and purchases synced from OrcaHub. Mini removes the two heaviest dependencies:

- No Authentik. Operators authenticate with a TLS client certificate (trust-on-first-use,
  the same model the LXD/Incus web UI uses).
- No OpenSearch. The audit trail is an append-only, hash-chained PostgreSQL table.

Mini is CMP plus one edition package. The backend runs it because the Compose file sets
`ORCASTRA_EDITION=mini`, and the published frontend image is built for the Mini edition.

<div class="grid cards" markdown>

-   :material-flash:{ .lg .middle } **Automated Install**

    ---

    One command runs the whole deployment: checks, secrets, certificates, Vault, and the first
    admin.

    [:octicons-arrow-right-24: Automated Install](automated-install.md)

-   :material-update:{ .lg .middle } **Upgrading**

    ---

    Move a 1.0.0-RC1 host to 1.0.0 with one command, with a backup and automatic rollback.

    [:octicons-arrow-right-24: Upgrading](upgrading.md)

-   :material-sitemap:{ .lg .middle } **Architecture**

    ---

    The single-host topology, request path, ports, and security model.

    [:octicons-arrow-right-24: Architecture](architecture.md)

-   :material-rocket-launch:{ .lg .middle } **Quick Start**

    ---

    Deploy the stack end to end: configuration, Vault, the first admin, and login.

    [:octicons-arrow-right-24: Quick Start](quick-start.md)

-   :material-certificate:{ .lg .middle } **Certificate Authentication**

    ---

    Sign-in, operators and roles, renewal, revocation, sessions, and administrator recovery.

    [:octicons-arrow-right-24: Certificate Authentication](certificate-auth.md)

-   :material-cog:{ .lg .middle } **Configuration**

    ---

    The environment variables that drive the mini profile.

    [:octicons-arrow-right-24: Configuration](configuration.md)

-   :material-wrench:{ .lg .middle } **Operations & Troubleshooting**

    ---

    Backups, the audit log, Vault unseal, logs, and fixes for common issues.

    [:octicons-arrow-right-24: Operations](operations.md)

</div>

---

## What changed since 1.0.0-RC1

Mini 1.0.0 is built on CMP 1.0.2, so it carries every CMP change since the release candidate.
The changes specific to Mini:

- **Operators with email identities.** The Identities screen is replaced by
  **Administration > Operators**. Each operator has an email address (how organizations,
  integration keys and the audit log know the person), a role, a status, and one or more
  certificates. An upgrade gives every RC1 identity an email; see [Upgrading](upgrading.md).
- **Partners issue Tenant certificates.** A Partner issues and manages Tenant operators for the
  organizations it runs, and nothing else.
- **Self-service renewal.** Any operator can renew their certificate from **My Certificate** in
  the user menu during the 30 days before it expires. The old certificate keeps working until
  the new one is used.
- **Expiry warnings.** A banner on every page warns 14 days before the signed-in certificate
  expires, the Operators page marks expiring certificates, and Host Health counts them.
- **Sessions bound to the certificate.** A session token is accepted only together with the
  certificate it was issued for. Revoking an operator or a certificate, changing a role, or
  deleting an operator ends the affected sessions on the next request, and open consoles and
  terminals close within a minute.
- **Audit log tools.** **Administration > Audit Log** verifies the hash chain, exports CSV or
  JSON, and applies an optional retention period (`AUDIT_DB_RETENTION_DAYS`).
- **First administrator on the sign-in page.** While no Administrator exists and
  `BOOTSTRAP_ADMIN_TOKEN` is set, the sign-in page offers to enroll the presented certificate as
  the first Administrator, with an email and a display name.
- **Header search.** The search box in the header (++ctrl+k++, or ++cmd+k++ on macOS) finds
  pages and the instances, projects, clusters and operators your role can see. The shortcut
  is left to the terminal or console while one has the keyboard.
- **Host Health checks for Mini.** **Certificate Sign-In** and **Audit Log** replace the identity
  provider and logging pipeline checks, and an Administrator can unseal Vault from the dashboard.
- **Administrator recovery.** A host command issues a new Administrator certificate when no
  Administrator can sign in.
- **Upgrade, rollback and backup** in the installer (`--upgrade`, `--rollback`, `--backup`).
- **OrcaHub federation hidden.** Marketplace federation and **Settings > User Groups** are not
  offered; integration keys, add-ons and MCP work as in CMP.

## Mini compared with the full version

| Concern | Mini (single host) | Full (multi-host) |
|---|---|---|
| Hosts | One (Dashboard + Vault) | Up to four (Dashboard, Authentik, Vault, OpenSearch) |
| Operator sign-in | TLS client certificate | Authentik OAuth2/OIDC SSO |
| Roles | Set on the Operators page | Authentik groups (**Settings > User Groups**) |
| Marketplace federation (OrcaHub) | Not available | Available |
| Audit log | Hash-chained PostgreSQL table | Fluent Bit to OpenSearch |
| Central logging | None (stdout + Postgres audit) | OpenSearch with dashboards |
| Public surface | One HTTPS port through nginx (client certificates) | Per the four-VM topology |
| Best for | Edge sites, single-tenant labs, air-gapped or small installs | Multi-organization, multi-region, centralized compliance |

The full deployment is documented under [Getting Started](../getting-started/index.md) and
[Deployment](../deployment/index.md). This section covers only the mini profile.

## When to choose Mini

!!! tip "Pick Mini when"
    - You manage one or a few clusters from a single operations host.
    - You do not need centralized log retention beyond the in-app audit trail.
    - You want certificate-based access without standing up an identity provider.
    - The environment is air-gapped or resource-constrained.

!!! note "Pick the full version when"
    You need SSO across many users, organization-scoped tenancy at scale, or centralized
    log aggregation and dashboards. The two share a data model, so a later move is a
    configuration change, not a migration.

## Source and artifacts

| Resource | Location |
|---|---|
| Container images | `svlct/orcastra-dashboard-mini` (tags `backend-1.0.0`, `frontend-1.0.0`) |
| Release | Mini 1.0.0, built on CMP 1.0.2 |
| Installer | `installer-v1.0.0` (see [Automated Install](automated-install.md)) |
| Deployment | The [Automated Install](automated-install.md), or pull the images and run the Compose file from the [Quick Start](quick-start.md) |

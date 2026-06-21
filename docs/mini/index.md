# Orcastra Mini

**Single-host build of the Orcastra Dashboard. The control plane and HashiCorp Vault run on
one machine, and operators sign in with TLS client certificates instead of an external
identity provider.**

---

Orcastra Mini is the same product as the full Orcastra Dashboard, packaged for sites that
cannot run a four-VM topology. It keeps the 3-tier RBAC, instance lifecycle, console and
terminal, and monitoring, but removes the two heaviest dependencies:

- No Authentik. Operators authenticate with a TLS client certificate (trust-on-first-use,
  the same model the LXD/Incus web UI uses).
- No OpenSearch. The audit trail is an append-only, hash-chained PostgreSQL table.

Both integrations stay feature-flagged in the code, not deleted, so a site can move to the
full multi-host deployment later without a rewrite. The profile is selected with a single
setting, `AUTH_MODE=client-cert`.

<div class="grid cards" markdown>

-   :material-flash:{ .lg .middle } **Automated Install**

    ---

    One command runs the whole deployment: checks, secrets, certificates, Vault, and the first
    admin.

    [:octicons-arrow-right-24: Automated Install](automated-install.md)

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

    How trust-on-first-use, identity enrollment, revocation, and sessions work.

    [:octicons-arrow-right-24: Certificate Authentication](certificate-auth.md)

-   :material-cog:{ .lg .middle } **Configuration**

    ---

    The environment variables that drive the mini profile.

    [:octicons-arrow-right-24: Configuration](configuration.md)

-   :material-wrench:{ .lg .middle } **Operations & Troubleshooting**

    ---

    Day-2 tasks, the published images, and fixes for common issues.

    [:octicons-arrow-right-24: Operations](operations.md)

</div>

---

## Mini compared with the full version

| Concern | Mini (single host) | Full (multi-host) |
|---|---|---|
| Hosts | One (Dashboard + Vault) | Up to four (Dashboard, Authentik, Vault, OpenSearch) |
| Operator sign-in | TLS client certificate | Authentik OAuth2/OIDC SSO |
| Audit log | Hash-chained PostgreSQL table | Fluent Bit to OpenSearch |
| Central logging | None (stdout + Postgres audit) | OpenSearch with dashboards |
| Public surface | One HTTPS port through nginx (mTLS) | Per the four-VM topology |
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
| Container images | `svlct/orcastra-dashboard-mini` (tags `backend-1.0.0-RC1`, `frontend-1.0.0-RC1`) |
| Release | `mini-v1.0.0-RC1` (release candidate) |
| Deployment | Pull the images and run the Compose file from the [Quick Start](quick-start.md) |

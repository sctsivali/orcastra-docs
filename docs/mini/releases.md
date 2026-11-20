# Releases

Orcastra Mini releases, newest first. Each release names the Orcastra CMP release it is built on.
The release notice in the dashboard reads the same list from
[`mini-manifest.json`](../releases/mini-manifest.json).

## 1.0.0 {#1-0-0}

Released 2026-11-20. Built on CMP 1.0.2. Images `svlct/orcastra-dashboard-mini:backend-1.0.0` and
`svlct/orcastra-dashboard-mini:frontend-1.0.0`; installer `installer-v1.0.0`.

Upgrade from 1.0.0-RC1 with one command: see [Upgrading](upgrading.md). The upgrade backs up the
database, the deployment files and the data volumes first, and restores them if any step fails.

### New

- Every CMP 1.0.2 feature, except the ones that need an identity provider or OrcaHub: Settings >
  User Groups and OrcaHub federation are hidden. Integration keys, add-ons and MCP work as in CMP.
- Each operator has an email, a role and one or more certificates. Administrators issue any role;
  a Partner issues Tenant certificates for the organizations it runs. Deleting an operator keeps
  its email reserved, and the operator can be restored.
- A session works only with the certificate it was signed in with, and ends on the next request
  after a revoke, a role change or a delete.
- Operators renew their own certificate from My Certificate in the 30 days before it expires, and
  every page shows a notice from 14 days before.
- The first Administrator can enroll on the sign-in page.
- The audit log can be verified, exported as CSV or JSON, and kept for a set number of days.
- The Search box in the header, or Ctrl+K and Cmd+K, finds the pages, instances, projects,
  clusters and operators you can see.
- Host Health checks certificate sign-in and the audit log, and Vault can be unsealed from the
  dashboard after a reboot.
- The installer adds `--upgrade`, `--rollback` and `--backup`, `--no-pull` for hosts without
  registry access, and `--https-bind`, `--uploads-dir` and `--vault-host-port`.

### Changed from 1.0.0-RC1 {#changed-from-rc1}

- Operators sign in with the certificates they have. Each gets an email during the upgrade
  (a mapping you choose, a display name that is an email, or `<name>@users.orcastra.internal`).
- RC1 Partners keep running the organizations they belonged to. A Partner now sees each
  cluster's `default` project and the projects its organizations' tenant policies name.
- Promoting an operator revokes the certificates it holds; issue a new one after the change.
- The backend mounts `config/backend` only; nginx's key and template are no longer visible to it.
- Routine Vault reads by the backend are no longer written to the audit log.

### Fixed from 1.0.0-RC1 {#fixed-from-rc1}

- Cluster display names and other node settings are kept across restarts; the names RC1 lost
  are restored from each cluster's registration.
- An Administrator sees every cluster's instances (RC1 showed an Administrator without
  organization memberships none).

## 1.0.0-RC1 {#1-0-0-rc1}

Released 2026-06-20. The first release candidate: TLS client-certificate sign-in, a local identity
store and a hash-chained audit log on one host.

# Operations & Troubleshooting

Day-2 tasks for a running Orcastra Mini host. The commands assume an installer deployment in
`/opt/orcastra-mini` with the default `CONTAINER_PREFIX` (`orcastra-mini`); adjust both if yours
differ. For a manual deployment, run the `docker compose` commands from its directory.

## Vault unseal after a restart

Vault comes up sealed after any restart of its container (host reboot, `docker restart`, or a
recreate) and must be unsealed before cluster data and certificate issuance work. Sign-in keeps
working while Vault is sealed: it reads only the database. Every page shows a Vault notice
until Vault is unsealed.

=== "From the dashboard"

    An Administrator signs in and selects **Unseal Vault**, either on the Vault notice or on the
    Vault row of **Cluster > Host Health**, then submits key shares one at a time until Vault
    reports it is unsealed. Several Administrators can each submit their own share and watch
    the progress.

    - The shares pass through the backend to Vault and are never stored or logged.
    - Each submission is written to the audit log, and submissions are rate-limited per account.
    - The option is on by default. `VAULT_UNSEAL_UI_ENABLED=false` in `.env` turns it off.

=== "On the host"

    ```bash
    cd /opt/orcastra-mini
    docker compose -p orcastra-mini exec -e VAULT_ADDR=http://127.0.0.1:8200 vault \
      vault operator unseal <unseal-key>
    # repeat with distinct keys up to the threshold (3 of 5 by default)
    ```

    Use this path when no Administrator can sign in. The installer prints the equivalent
    `docker exec -it orcastra-mini-vault vault operator unseal -address=http://127.0.0.1:8200`,
    run once per key.

!!! tip "Confirm state"
    `docker compose -p orcastra-mini exec -e VAULT_ADDR=http://127.0.0.1:8200 vault vault status`
    should show `Sealed: false`. (Vault runs over HTTP internally, so the CLI needs
    `VAULT_ADDR=http://...`.)

An installer deployment in convenience mode (`--convenience --auto-unseal-unit`) unseals Vault
on boot with a systemd unit; see [Automated Install](automated-install.md#vault-key-handling).

## Managing operators

From **Administration > Operators**, Administrators (and Partners, for the Tenants of the
organizations they run):

- **Issue Certificate** to create an operator; the `.p12` and its import password are shown once.
- **Issue Another Certificate**, or **Revoke Certificate** for one certificate.
- **Revoke** and **Reactivate**, **Delete** and **Restore** an operator.
- Change a role (Administrators only).

Changes take effect on the operator's next request. See
[Certificate Authentication](certificate-auth.md#the-operators-page) for each action, the
last-administrator guard and renewal.

When no Administrator can sign in, issue a new Administrator certificate on the host with the
[recovery command](certificate-auth.md#recovering-when-no-administrator-can-sign-in).

## Audit log

The audit trail is an append-only, hash-chained PostgreSQL table. Each row's hash covers the
previous row's hash, so an edited, reordered or deleted row breaks verification. Sign-in,
certificate and operator changes, exports and pruning are written synchronously and fail
closed; other events are queued and written in the background. The backend's own routine Vault
reads (a successful read or list with no operator behind it, every few seconds) are not
recorded; reads an operator caused, failed calls and every Vault write are.

**Administration > Audit Log** (Administrators only):

- Choose a time range (last 24 hours to last 365 days). The page reads up to the newest 5,000
  events in the range; search, filter by actor, action and result, and sort columns. Narrow the
  range or export to read more.
- A line above the table shows how many rows are kept and the retention in force.

### Verify

**Verify Integrity** recomputes the whole chain. It reports an intact chain with the number of
rows verified, or names the first broken row. It also detects rows deleted from the end, by
comparing the current row count and last id against the state the previous successful
verification recorded. Each verification is itself audited.

The same check runs from the host, without moving the recorded state:

```bash
docker exec orcastra-mini-backend python -m app.editions.mini.cli verify
```

It prints the number of operators, whether an Administrator can sign in, and whether the audit
chain is valid. `--json` prints the same as JSON. The exit code is `3` when the chain does not
verify.

!!! warning "A broken chain"
    Export the log right away and keep the export before anything else changes.

### Export

**Export CSV** and **Export JSON** download the events of the selected time range, up to
100,000 rows, as `orcastra-mini-audit-<UTC timestamp>.csv` or `.json`. Each row includes its
`prev_hash`, `row_hash` and `chain_version`. The export is written to the audit log before it
runs; if that write fails, the export does not run.

The JSON export holds the exact values the chain hashes. The CSV export is for spreadsheets: a
cell that starts with `=`, `+`, `-`, `@`, a tab or a carriage return is written with a leading
`'` so a spreadsheet shows it as text instead of running it, so check the chain against the JSON
export.

The hashes prove the order and content of the rows to anyone who keeps an earlier export, but
someone who can write to the database could rewrite the chain from that point on. Keep regular
JSON exports (or at least the latest `row_hash`) outside the host to detect that.

### Retention

With `AUDIT_DB_RETENTION_DAYS=0` (the default) every row is kept. With a positive value, rows
older than that many days are removed once a day. The cut is recorded, so verification still
covers the rows that remain, and the removal itself is written to the audit log as
`audit.prune`.

!!! note "External anchor"
    The chain proves rows were not altered, but a determined database-level attacker who can
    edit both the table and the anchor is outside its guarantee. For stronger assurance,
    export the log regularly, or record the head hash and row count that verification
    returns, to an append-only, off-host location.

Host Health's **Audit Log** check reports whether the background writer is running and counts
dropped events and failed writes.

## Backups

The PostgreSQL volume holds application state and the audit log. Vault holds the PKI and
secrets. Keep the Vault unseal keys offline, separate from the backups.

```bash
curl -fsSL https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/installer/get.sh | bash -s -- --backup
```

`--backup` runs while the stack keeps serving. It writes
`/opt/orcastra-mini/backups/<UTC timestamp>-<version>-manual/` with:

| File | Contents |
|---|---|
| `db.dump` | `pg_dump` of the database in PostgreSQL custom format |
| `files.tar.gz` | `.env`, `docker-compose.yml`, `config/` and `install-state.json` |
| `manifest.json` | The release and project the backup belongs to |

It does not include Vault's data, and `--rollback` does not restore it: restore it with
`pg_restore` (see [A `--backup` dump](#a-backup-dump)). Before changing releases, use
[`--upgrade`](upgrading.md), which also stops the stack and takes cold copies of the
`postgres-data`, `vault-data` and `redis-data` volumes. The backup directory is mode `700` and
holds secrets (`.env`), so protect it like `.env`.

## Restoring a backup

### An upgrade backup

`--rollback` restores a backup taken by `--upgrade` (those hold the volume copies) and starts the
release it was taken from:

```bash
curl -fsSL https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/installer/get.sh | bash -s -- \
  --rollback 20261120T101500Z-1.0.0-RC1-to-1.0.0
```

It stops the stack, replaces the data volumes and the deployment files with the backup's,
starts the stack, and waits for the backend to become healthy. Everything changed since the
backup (operators, audit rows, clusters) is replaced. It asks for confirmation; add
`--assume-yes` for an unattended run. Without a name it uses the newest upgrade backup.
`--backup` backups are not offered: they hold no volume copies.

Restarting the stack seals Vault. If Vault was unsealed before the rollback, the installer
unseals it again with the keys in `vault-init.json` (convenience mode) or, in an interactive run,
asks for the unseal keys (press Enter to skip). Otherwise it prints the command to unseal on the
host. See [Upgrading](upgrading.md#rolling-back) for the rollback an upgrade does on its own.

### A `--backup` dump

A `--backup` backup is restored by hand with `pg_restore`. Restore it onto the release it was
taken from (its `manifest.json` names it). Replace the prefix (`orcastra-mini`), the database
user and name (`POSTGRES_USER` and `POSTGRES_DB` in `.env`, `orcastra` by default) and the backup
directory with your own values:

```bash
cd /opt/orcastra-mini
docker compose -p orcastra-mini stop backend frontend
docker exec -i orcastra-mini-postgres pg_restore -U orcastra -d orcastra --clean --if-exists \
  < backups/<backup-directory>/db.dump
docker compose -p orcastra-mini start backend frontend
```

`--clean --if-exists` drops each object before recreating it from the dump, so the database
ends as it was when the dump was taken. If you also need the deployment files, extract
`files.tar.gz` from the same directory into the install directory.

## Logs

```bash
cd /opt/orcastra-mini
docker compose -p orcastra-mini logs --tail 200 backend
docker compose -p orcastra-mini logs --tail 200 nginx
```

- Every container logs to Docker's `json-file` driver, rotated at 50 MB with 5 files kept.
- Audit events also appear on the backend's standard output.
- nginx keeps WebSocket query strings, which carry single-use tickets, out of its access log.
- The installer writes its own transcript to `/opt/orcastra-mini/install.log` with secrets
  redacted.

## Directories the backend writes

The backend runs as uid `1000` and writes into two host directories:

| Host directory | In the backend | What it writes |
|---|---|---|
| `config/backend/` in the deployment directory | `/app/config` | Node display names (`node_metadata.json`) and the `.p12` files of the [recovery command](certificate-auth.md#recovering-when-no-administrator-can-sign-in). It also reads the upgrade's `mini-email-map.json` from here. |
| `UPLOADS_DIR` (default `/var/orcastra/uploads`) | `/app/uploads` | Image and ISO uploads |

Both must belong to uid `1000`. The installer creates `config/backend/` and sets the owner to
`1000:1000` and mode `750` on both, on every install and `--upgrade`. The rest of `config/`
(nginx's template and private key, `vault.hcl`) is not mounted into the backend and stays owned
by root. For a manual deployment, or if uploads fail or node display names are not kept, run
from the deployment directory:

```bash
sudo mkdir -p config/backend /var/orcastra/uploads
sudo chown 1000:1000 config/backend /var/orcastra/uploads && sudo chmod 750 config/backend /var/orcastra/uploads
```

Use your `UPLOADS_DIR` if it differs.

## Container images

Prebuilt images are published to Docker Hub at `svlct/orcastra-dashboard-mini`, with
component-prefixed tags:

| Tag | Component |
|---|---|
| `backend-1.0.0` | FastAPI backend |
| `frontend-1.0.0` | Next.js frontend |
| `backend-rc`, `frontend-rc` | Always the latest release candidate |

```bash
docker pull svlct/orcastra-dashboard-mini:backend-1.0.0
docker pull svlct/orcastra-dashboard-mini:frontend-1.0.0
```

The Compose file also runs `postgres:17-alpine`, `redis:8-alpine`, `hashicorp/vault:1.17`,
`nginx:1.27-alpine` and `willfarrell/autoheal:1.2.0`. On a host without registry access, load
all of them with `docker load` and run the installer with `--no-pull`.

The [Quick Start](quick-start.md) gives the full pull-based deployment using these images.
Both read all secrets from environment variables at runtime, and the frontend swaps its API
URL placeholder on start, so the same image works on any host.

## Troubleshooting

| Symptom | Cause | Resolution |
|---|---|---|
| Certificate issuance fails with `503` ("check that it is unsealed") | Vault is sealed | Unseal Vault (see above). |
| Cluster pages show a Vault notice after a reboot | Vault is sealed | Unseal Vault from the dashboard or the host. |
| Issuance fails with "notAfter beyond the expiration of the CA" | CA TTL equals the leaf TTL | Provision the CA at a long TTL (for example 10 years) and the role at a shorter maximum (for example 1 year). |
| `cert-bootstrap` returns `403` | The bootstrap token is wrong, or `BOOTSTRAP_ADMIN_TOKEN` is unset | Set the token in `.env`, recreate the backend, and send the same value. |
| `cert-bootstrap` returns `409` | An Administrator already exists, or the certificate or email is already enrolled | Ask an Administrator to issue a certificate, or use the [recovery command](certificate-auth.md#recovering-when-no-administrator-can-sign-in). |
| `cert-bootstrap` returns `401` | No trusted client certificate reached the backend | Present the certificate (`--cert`/`--key`) and confirm `AUTH_PROXY_SECRET` is the same for nginx, the frontend and the backend (all read it from `.env`). |
| The sign-in page says the certificate is not enrolled | The certificate belongs to no operator | Send the fingerprint to an Administrator and import a certificate issued from the Operators page. |
| The sign-in page says the certificate was revoked or has expired | The certificate or its operator is revoked, deleted or expired | Import a new certificate, or close every browser window and choose another one. |
| Signed out after switching certificates in the browser | Sessions belong to the certificate they started with | Sign in again with the certificate you want. |
| Host Health: no Administrator can sign in | Every Administrator certificate is revoked or expired | Run the [recovery command](certificate-auth.md#recovering-when-no-administrator-can-sign-in) on the host. |
| Host Health: the bootstrap token is still set | `BOOTSTRAP_ADMIN_TOKEN` was not cleared after enrollment | Blank it in `.env` and recreate the backend. |
| Backend exits with "Orcastra Mini refuses to start" | A secret is too short, `LOCAL_JWT_SECRET` equals `SECRET_KEY`, or Vault is disabled | Fix the value the message names in `.env` and recreate the backend. |
| Image or ISO uploads fail, node display names are not kept, or the recovery command cannot write its file | `UPLOADS_DIR` or `config/` does not belong to uid `1000` | Fix the owner (see [Directories the backend writes](#directories-the-backend-writes)). |
| The chosen public port conflicts | Another service uses it | Change `HTTPS_PORT` (and the three URL values), then recreate. |

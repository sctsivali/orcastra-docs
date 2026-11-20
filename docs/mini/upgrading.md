# Upgrading

**One installer command moves an Orcastra Mini 1.0.0-RC1 deployment to 1.0.0: it backs up,
migrates, verifies, and rolls back on its own if anything fails.**

---

The upgrade runs on the Mini host as root, against the deployment the installer created in
`/opt/orcastra-mini` (pass `--install-dir` for another directory). It needs that directory's
`.env` and `docker-compose.yml`.

## Before you start

- **Disk space.** The filesystem that holds the install directory needs free space of at least
  2.2 times the combined size of the `postgres-data`, `vault-data` and `redis-data` volumes, plus
  3 GiB. The upgrade checks this first and stops if there is not enough.
- **The database must be running.** The upgrade reads it before it changes anything.
- **Decide each operator's email.** 1.0.0 identifies every operator by an email address, and the
  address cannot be changed afterwards. See [Operator emails](#operator-emails).
- **Have the Vault unseal keys at hand** (guided mode). The restart seals Vault, and the installer
  can unseal it again for you; see [Vault after the restart](#vault-after-the-restart).
- **Plan a short outage.** The stack stops when the backup starts and comes back on 1.0.0 (or on
  RC1 after a rollback). The installer warns that the dashboard is down for a few minutes.
- **Hosts without registry access** load the images first; see
  [Air-gapped hosts](#air-gapped-hosts).

## Run the upgrade

```bash
curl -fsSL https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/installer/get.sh | bash -s -- --upgrade
```

The installer upgrades to its own release (1.0.0); `--version-tag` names another one. A target
older than the running release is refused: use `--rollback` to go back.

Useful options with `--upgrade`:

| Flag | Purpose |
|---|---|
| `--email-map FILE` | Emails for existing operators, by certificate fingerprint (see below) |
| `--no-pull` | Use images already loaded on the host instead of pulling |
| `--install-dir DIR` | The deployment directory, if not `/opt/orcastra-mini` |
| `-y`, `--assume-yes` | Skip the confirmation before the backup |
| `--non-interactive` | Ask nothing; generated emails are kept for operators without a mapped address |
| `--image-backend`, `--image-frontend` | Run images from a mirror instead of `svlct/orcastra-dashboard-mini` |

## What the upgrade does

The upgrade prints eight steps:

1. **Check the host.** Free disk space (see above) and that the database container is running.
   It also notes whether Vault is unsealed, to leave it that way after the restart.
2. **Operator emails.** If the database still has RC1 identities (no email column yet), it lists
   the email each operator will sign in with and where it came from. In an interactive run it
   asks for an address for each generated one. It then asks to confirm the upgrade, unless
   `--assume-yes` is set.
3. **Back up.** A `pg_dump` of the database and an archive of the deployment files, then it stops
   the stack and takes cold copies of the three data volumes. See [Backups](#backups).
4. **Merge settings.** `.env` keeps every existing line and value. The upgrade sets
   `APP_VERSION` to the target, replaces `LOCAL_JWT_SECRET` or `AUTH_PROXY_SECRET` with a new
   random value if either is missing or shorter than 32 characters, and appends each key of the
   1.0.0 template that the file lacks, with its template value (keys whose template value is a
   placeholder are skipped), under a `# Added by the upgrade to 1.0.0` comment. From an RC1
   `.env` written by the installer or the Quick Start this adds `HTTPS_BIND`, `UPLOADS_DIR`,
   `CERT_SELF_RENEW_ENABLED`, `VAULT_PKI_ROLE` and `AUDIT_DB_RETENTION_DAYS`.
   Chosen emails are written to `config/mini-email-map.json` (mode `600`, owned by uid `1000`,
   the backend's user) for the migration.
5. **Write the deployment files.** A new `docker-compose.yml` and `config/nginx/mini.conf`.
   `config/vault/vault.hcl` is written only if it is missing. The uploads directory
   (`UPLOADS_DIR`) is created if needed, and it and the `config/` directory itself are given to
   uid `1000` with mode `750`. RC1 left both owned by root, so the backend could not save uploads
   or node display names there. The files inside `config/` keep their owner; nginx's private key
   stays root's.
6. **Images.** `docker compose pull`, or nothing with `--no-pull`.
7. **Start and migrate.** `docker compose up -d --remove-orphans`. The backend recognises the
   RC1 database and brings it under schema migrations, applies the CMP migrations since RC1 and
   the Mini ones (operator emails, several certificates per operator, the audit-log retention
   marker), and becomes healthy. The step fails at once if the backend refuses the database or
   its settings or keeps exiting, and otherwise waits up to 20 minutes. Restarting the stack
   seals Vault; see [Vault after the restart](#vault-after-the-restart).
8. **Verify.** The backend reports its operators, whether an Administrator can sign in, and the
   audit chain. The upgrade requires the database to be at the release's schema, the audit chain
   to verify (rows written by RC1 verify together with rows written after the upgrade), and the
   HTTPS port to answer the sign-in status request.

On success it prints the backup name and the command to return to RC1. The applied email map is
moved into the backup directory as `mini-email-map.applied.json`.

## Vault after the restart

The upgrade restarts Vault, which seals it. The installer starts Vault first and, if Vault was
unsealed when the upgrade began, unseals it again before the rest of the stack starts:

- in convenience mode, with the keys in `vault-init.json`;
- otherwise, in an interactive run, it asks for the unseal keys (press Enter to skip).

If Vault stays sealed, the installer says so and prints how to unseal it: from Host Health in
the dashboard, or on the host once per key:

```bash
docker exec -it orcastra-mini-vault vault operator unseal -address=http://127.0.0.1:8200
```

Clusters stay unreachable until Vault is unsealed. Sign-in does not need Vault.

## Operator emails

Each RC1 identity gets an email by the first rule that applies:

1. the address given for its certificate fingerprint in the `--email-map` file, or typed at the
   prompt (shown as `chosen`);
2. its display name, when that is a valid email address no other operator uses (`display name`);
3. otherwise `<label>@users.orcastra.internal`, where the label is the first part of the
   certificate's common name, with `-2`, `-3` and so on added if two operators would collide
   (`generated`).

Two operators can never share an address; the upgrade stops if a map would make them.

The `--email-map` file is a JSON object of certificate fingerprint (the SHA-256 of the
certificate in hex, without colons, any case) to email address:

```json
{
  "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08": "ana@example.com",
  "60303ae22b998861bce3b28f33eec1be758a213c86c93c076dbe9f558c11c752": "ops-lead@example.com"
}
```

To list the RC1 identities with their fingerprints:

```bash
cd /opt/orcastra-mini
docker compose -p orcastra-mini exec -T postgres psql -U orcastra -d orcastra \
  -c "SELECT fingerprint, common_name, display_name FROM local_identities ORDER BY id"
```

RC1 recorded organization memberships, organizations' creators, cluster registrations,
preferences, tag visibility and tags' creators under an operator's display name. The migration
moves those rows to the operator's new email when exactly one operator had that display name.
When several shared it, the rows stay as they were, grant access to nobody until the operator is
added again, and the backend log names them. Audit rows are never rewritten: they are part of
the hash chain, so RC1 events keep the names RC1 recorded. The backend log also prints one line
per operator, `operator identity <id> (<common name>) signs in as <email>`.

In RC1 the role of an organization membership decided nothing: a Partner ran every organization
it belonged to. In 1.0.0 a Partner runs an organization it created or holds a Partner
membership in, so the migration turns each RC1 Partner's memberships into Partner memberships.
A Partner keeps issuing Tenant certificates for those organizations and managing their Tenants.

## Air-gapped hosts

With `--no-pull` the upgrade uses the images already on the host. Load them first with
`docker load -i <file>`:

- `svlct/orcastra-dashboard-mini:backend-1.0.0`
- `svlct/orcastra-dashboard-mini:frontend-1.0.0`
- `willfarrell/autoheal:1.2.0` (new in 1.0.0; RC1 did not run it)

`postgres:17-alpine`, `redis:8-alpine`, `hashicorp/vault:1.17` and `nginx:1.27-alpine` are the
same images RC1 used. If an image is missing, the start step fails and the upgrade rolls back.

```bash
curl -fsSL https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/installer/get.sh | bash -s -- --upgrade --no-pull
```

On a host that cannot reach GitHub either, copy the installer's `orcastra-mini-install.pyz`
release asset over and run it directly:

```bash
sudo python3 orcastra-mini-install.pyz --upgrade --no-pull
```

## Rolling back

**Automatically.** If any step after the backup fails, the upgrade stops the stack, restores the
three volumes and the deployment files from the backup, starts the previous release, and waits
for its backend to become healthy. It then reports that the upgrade failed and that the
previous release is running again with the data it had, and keeps the backup. The host is never
left between two releases. Vault is unsealed again as described above; RC1 cannot unseal from
the dashboard, so the installer prints only the host command.

**By hand.** To return to RC1 after a successful upgrade:

```bash
curl -fsSL https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/installer/get.sh | bash -s -- \
  --rollback 20261120T101500Z-1.0.0-RC1-to-1.0.0
```

`--rollback` accepts only backups taken by `--upgrade`, and with no name uses the newest one. It
asks for confirmation (`--assume-yes` skips it) because everything changed since the backup,
including operators, audit rows and clusters, is replaced by the backup. Vault is unsealed again
as in an automatic rollback. A `--backup` backup is restored by hand instead (see
[Operations](operations.md#restoring-a-backup)).

## Backups

Each upgrade writes `/opt/orcastra-mini/backups/<UTC timestamp>-<from>-to-<to>/`, for example
`20261120T101500Z-1.0.0-RC1-to-1.0.0/`, mode `700`:

| File | Contents |
|---|---|
| `db.dump` | `pg_dump` of the database in PostgreSQL custom format, for your records or a restore on another host |
| `files.tar.gz` | `.env`, `docker-compose.yml`, `config/` and `install-state.json` |
| `postgres-data.tar`, `vault-data.tar`, `redis-data.tar` | Cold copies of the data volumes, taken with the stack stopped; this is what a rollback restores |
| `manifest.json` | The release the backup was taken from, the target release, the project and the volumes copied |
| `mini-email-map.applied.json` | The emails chosen for existing operators, after a successful upgrade that used them |

The backup holds `.env` and Vault's data, so protect it like the Vault unseal keys. Nothing
removes old backups; delete them when you no longer need to roll back.

## After the upgrade

- **Every operator signs in again with their existing certificate.** Certificates carry over;
  RC1 session tokens are not accepted, because 1.0.0 sessions are bound to the certificate.
- **Emails are final.** The Operators page does not change an operator's email. To give an
  operator with a generated address a real one, issue a new operator with that address, add it
  to the organizations it needs, and delete the old operator.
- **RC1 identities that were not active** (pending or revoked) are revoked operators after the
  upgrade.
- **Existing settings are kept.** An RC1 `.env` keeps `LOCAL_JWT_TTL_SECONDS=3600`; new 1.0.0
  installs use `900`. Lower it if you want the new default. `AUTH_MODE` and
  `NEXT_PUBLIC_AUTH_MODE` stay in the file and are no longer read.
- **Cluster display names come back.** RC1 could not save display names (its config directory
  belonged to root), so they were lost at every restart. Once Vault answers, 1.0.0 copies each
  cluster's display name from its registration; names set later are kept in
  `config/backend/node_metadata.json`.
- **Partners see fewer projects.** As in CMP, a Partner now sees each cluster's `default`
  project and the projects named by the tenant policies of the organizations it runs. RC1
  showed a Partner every project on its clusters. An Administrator still sees every project.
- **The audit log records what people do.** The backend's own routine Vault reads (the cluster
  registry and cluster credentials, every few seconds) are no longer recorded; reads an operator
  caused, failures and every write still are.
- **Check Host Health.** The **Certificate Sign-In** check warns while `BOOTSTRAP_ADMIN_TOKEN` is
  still set and while only one Administrator can sign in.
- **Learn the new tools.** Partners can now issue Tenant certificates, operators renew from
  **My Certificate**, and the audit log can be verified and exported. See
  [Certificate Authentication](certificate-auth.md) and [Operations](operations.md).

!!! note "Deployments built by hand"
    A deployment created from the [Quick Start](quick-start.md) can be upgraded the same way with
    `--install-dir <its directory>`, provided its Compose project name equals `CONTAINER_PREFIX`
    (the Quick Start's `.env` sets `COMPOSE_PROJECT_NAME` for that).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `not enough free disk` | Free space in the install directory's filesystem, or move the install to a larger disk, then run `--upgrade` again. Nothing was changed. |
| `the database is not running` | Start it (`docker compose -p orcastra-mini up -d postgres` from the install directory) and run `--upgrade` again. |
| `two operators would share one email` | Give them different addresses in the map or at the prompt. |
| `REFUSING TO AUTO-STAMP` in the backend output | The database is not a complete RC1 database (for example half a restore). The upgrade rolled back; restore a complete RC1 backup before trying again. |
| `the audit chain does not verify after the upgrade` | The upgrade rolled back. Verify the chain on RC1 (**Administration > Audit Log**) and export it before trying again. |
| `the new images could not be pulled` | The upgrade rolled back. Check registry access, or load the images and use `--no-pull`. |

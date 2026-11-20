# Automated Install

**One command brings up the whole stack: preflight checks, optional Docker install, secret and
certificate generation, Vault init/unseal/PKI, the first admin, and a health check.** The
[Quick Start](quick-start.md) does the same steps by hand and stays the reference for
understanding or customizing each one.

---

## What it does

The installer is a single self-contained program (Python standard library only, no packages to
install). It runs the [Quick Start](quick-start.md) sequence in order and stops on the first
real problem with a specific remediation:

| Step | Action |
|---|---|
| Preflight | OS, architecture, RAM/disk/CPU, Docker + Compose v2, privilege, `openssl`/`curl`, and that the HTTPS port and `127.0.0.1:8200` are free |
| Docker | If absent on Ubuntu/Debian, installs Docker Engine + the Compose plugin from the official apt repository (after you confirm) |
| Registry | Confirms the images are pullable, and runs `docker login` then retries if a pull is denied. With `--no-pull`, confirms the backend and frontend images are already on the host |
| Configuration | Detects the host address and asks you to confirm it, then derives the cert SAN and all three URLs from that one value |
| Secrets | Generates every secret (`openssl rand` equivalents), keeps `LOCAL_JWT_SECRET` separate from `SECRET_KEY`, and keeps the database password in sync |
| TLS | Writes a self-signed server certificate whose SAN matches the host (or installs one you supply) |
| Files | Writes `docker-compose.yml`, `config/nginx/mini.conf`, `config/vault/vault.hcl`, and `.env` (mode 600), creates the uploads directory, and gives it and the `config/` directory itself to uid 1000 (the backend's user), mode 750 |
| Start | `docker compose pull` (skipped with `--no-pull`) then `up -d` |
| Vault | Initializes, unseals, and configures the PKI (`pki_int`, role `lxd`) with the CA outliving the leaf certificates |
| Admin | Enrolls the first administrator and closes the one-shot bootstrap window |
| Verify | Confirms every service is healthy, the HTTPS sign-in page is served, and the certificate SAN matches |

## Prerequisites

- Ubuntu 20.04+ or Debian 11+ (other distributions: follow the [Quick Start](quick-start.md)).
- Root. The bootstrap runs the installer through `sudo` when you start it as another user.
- Network access to Docker Hub. The `svlct/orcastra-dashboard-mini` images are public, so no
  Docker Hub account is needed. Without registry access, see [Air-gapped hosts](#air-gapped-hosts).

## Run it

```bash
curl -fsSL https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/installer/get.sh | bash
```

The bootstrap ensures `python3` is present, downloads the installer, verifies its checksum, and
runs it. To pass options, append them after `--`:

```bash
curl -fsSL https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/installer/get.sh | bash -s -- --host dash.example.com
```

Interactive runs detect the host IP and ask you to confirm it. The address you confirm becomes
the certificate SAN and the `NEXTAUTH_URL` / `NEXT_PUBLIC_API_URL` / `CORS_ORIGINS` values, so
they cannot drift apart.

Questions are read from your terminal, so they work through the `curl | bash` pipe.

!!! note "Registry login"
    The images pull anonymously. If a pull is ever denied (a private mirror, or a rate limit),
    the installer runs `docker login` and follows its prompt (including the web device-code
    flow), then retries. In `--non-interactive` mode, run `docker login` yourself first.

## The first administrator

Enrollment is trust-on-first-use: the first certificate to present the one-time bootstrap token
becomes admin, then the window closes. The installer offers two paths.

=== "Default: from your workstation"

    The installer writes `bootstrap-admin.sh` and prints how to run it on the machine with your
    browser. The admin private key is generated there and never touches the server.

    ```bash
    scp <user>@<server>:/opt/orcastra-mini/bootstrap-admin.sh .
    ./bootstrap-admin.sh          # generates admin.p12 and enrolls you
    ```

    After you sign in, close the window on the server:

    ```bash
    /opt/orcastra-mini/close-bootstrap.sh
    ```

    The helper enrolls without an email, so the Administrator signs in as
    `<admin-cn>@users.orcastra.internal` (`admin@users.orcastra.internal` with the default
    `--admin-cn admin`). To choose the email, enroll on the sign-in page instead: create and
    import a certificate as in [Quick Start step 7](quick-start.md#7-bootstrap-the-first-administrator),
    open the dashboard, and fill in the enrollment form with the bootstrap token
    (`BOOTSTRAP_ADMIN_TOKEN` in `/opt/orcastra-mini/.env`) and your email. Then run
    `close-bootstrap.sh`.

=== "`--quick`: on the server"

    The installer generates the admin certificate in memory (`/dev/shm`), enrolls through nginx
    on `127.0.0.1`, hands you `admin.p12` with a one-time password, shreds the key material, and
    closes the window automatically. Convenient when the operator and the admin are the same
    person on one trusted host. `--admin-email` sets the Administrator's email (default
    `<admin-cn>@users.orcastra.internal`).

    ```bash
    curl -fsSL https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/installer/get.sh | bash -s -- --quick --admin-email you@example.com
    ```

The email cannot be changed after enrollment. Import `admin.p12` into your browser, open the
dashboard URL, and select the certificate when prompted. Then issue certificates for the other
operators from **Administration > Operators**.

## Vault key handling

Vault is sealed on every start. How its unseal keys are handled is a deliberate choice:

=== "Guided (default)"

    The five unseal keys and the root token are shown once and **never written to disk**. Store
    them offline. After any reboot Vault starts sealed, and cluster data and certificate
    issuance wait until it is unsealed. Sign-in still works, so an Administrator can unseal it
    from the dashboard (**Unseal Vault** on the Vault notice or in Host Health), or on the host:

    ```bash
    docker compose -p orcastra-mini -f /opt/orcastra-mini/docker-compose.yml \
      exec -e VAULT_ADDR=http://127.0.0.1:8200 vault \
      vault operator unseal <key>     # repeat with 3 distinct keys
    ```

=== "Convenience (`--convenience`)"

    The keys and token are written to `vault-init.json` (mode 600), and with
    `--auto-unseal-unit` a systemd unit unseals Vault on boot.

    !!! danger "Security trade-off"
        Anyone who can read `vault-init.json` can unseal Vault and mint administrator
        certificates. Use it only on a trusted, disk-encrypted host, and prefer guided mode for
        anything exposed.

## Unattended installs

Drive the installer from flags, an answer file, or the environment, with no prompts:

```bash
curl -fsSL https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/installer/get.sh | bash -s -- \
  --non-interactive --assume-yes \
  --host 192.0.2.10 --https-port 6969 \
  --convenience --quick
```

An answer file is flat `KEY=value`. The same keys also work as environment variables with an
`ORCASTRA_` prefix (`ORCASTRA_HOST=dash.example.com`). Precedence is CLI flag, then answer
file, then environment, then default:

```ini
HOST=dash.example.com
HOST_IS_IP=false
HTTPS_PORT=6969
HTTPS_BIND=0.0.0.0
UPLOADS_DIR=/var/orcastra/uploads
CONTAINER_PREFIX=orcastra-mini
CONVENIENCE=true
QUICK=true
ASSUME_YES=true
```

```bash
... | bash -s -- --answers ./install.answers
```

Useful options:

| Flag | Purpose |
|---|---|
| `--host`, `--host-is-ip` / `--host-is-dns` | Set the address and its certificate SAN type |
| `--https-port`, `--container-prefix` | Published port and container/volume namespace |
| `--https-bind` | The host address nginx listens on (default `0.0.0.0`, every interface; `127.0.0.1` for tunnel-only access) |
| `--vault-host-port` | The loopback port Vault is published on (default `8200`); pass a free one when the host already runs its own Vault |
| `--uploads-dir` | Host directory for image and ISO uploads (default `/var/orcastra/uploads`) |
| `--admin-cn`, `--admin-email` | Common name of the first Administrator's certificate (default `admin`) and, with `--quick`, its email |
| `--version-tag` | The release to install (default `1.0.0`) |
| `--no-pull` | Use images already loaded on the host (see [Air-gapped hosts](#air-gapped-hosts)) |
| `--image-backend` / `--image-frontend` | Run images from a mirror instead of `svlct/orcastra-dashboard-mini` |
| `--server-cert` / `--server-key` | Use your own TLS certificate instead of self-signed |
| `--tunnel` | Reach the host over an SSH tunnel (`https://localhost`) |
| `--dry-run` | Show every step and the files it would write, change nothing |
| `--stop-after write` | Generate the files only; start the stack yourself |
| `--skip-docker-install` | Never touch apt; fail if Docker is missing |

## Air-gapped hosts

With `--no-pull` the installer uses images already on the host instead of pulling them. Load
every image the Compose file runs with `docker load -i <file>` first:
`svlct/orcastra-dashboard-mini:backend-1.0.0`, `svlct/orcastra-dashboard-mini:frontend-1.0.0`,
`postgres:17-alpine`, `redis:8-alpine`, `hashicorp/vault:1.17`, `nginx:1.27-alpine` and
`willfarrell/autoheal:1.2.0`. The installer stops early if the backend or frontend image is
missing.

```bash
sudo python3 orcastra-mini-install.pyz --no-pull --host dash.example.com
```

Copy the installer's `orcastra-mini-install.pyz` release asset to the host when it cannot reach
GitHub; `get.sh` runs the same file with the flags you pass.

## Upgrade, backup and rollback

The same installer maintains an existing deployment:

| Flag | Purpose |
|---|---|
| `--upgrade` | Upgrade to the installer's release (or `--version-tag`), with a backup and automatic rollback on failure. See [Upgrading](upgrading.md). |
| `--email-map FILE` | With `--upgrade` from 1.0.0-RC1: JSON of certificate fingerprint to email for existing operators |
| `--backup` | Dump the database and save the deployment files, without stopping the stack. Restored by hand with `pg_restore` |
| `--rollback [BACKUP]` | Restore a backup taken by `--upgrade` (default: the newest) and start the release it came from |

Backups are written under `<install-dir>/backups/`. See [Operations](operations.md#backups) for
what each kind contains and how `--rollback` restores it.

## Re-running and removal

The installer records its progress, so a second run skips completed steps and continues from the
first incomplete one. It never overwrites existing secrets (rotate explicitly with
`--rotate-secret NAME`), and it backs up any file before replacing it.

- `--repair` re-validates every step and fixes what is broken (cert SAN, sealed Vault, drift).
- `--from <phase>` / `--only <phase>` re-run a specific part.
- `--uninstall` stops the stack and removes the generated files; data volumes are kept unless you
  add `--purge-volumes` and confirm. The Vault keys file is never deleted automatically.

```bash
curl -fsSL https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/installer/get.sh | bash -s -- \
  --uninstall --install-dir /opt/orcastra-mini
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Stops at preflight with a FAIL row | Read the remediation printed under the row; fix it and re-run |
| `Registry authentication required` | A pull was denied (a mirror, or Docker Hub rate limits). Run `docker login`, then re-run |
| Backend never becomes healthy | Check `VAULT_TOKEN` in `.env` and `docker compose -p orcastra-mini logs backend` |
| Browser rejects the certificate | The SAN must match the URL host (`IP:` for an address, `DNS:` for a name); re-run with the right `--host` |
| Clusters show a Vault notice or certificate issuance fails after a reboot | Vault is sealed: unseal it from the dashboard or on the host (guided mode) |
| `--no-pull was given but ... is not on this host` | Load the images with `docker load -i <file>` first, or run without `--no-pull` |

The full transcript is written to `<install-dir>/install.log` with all secrets redacted.

!!! tip "Prefer the manual path to learn the internals"
    The [Quick Start](quick-start.md) walks through every file and command the installer runs,
    and the [Operations](operations.md) guide covers day-2 tasks. The installer is the fast path;
    the manual guide is the map.

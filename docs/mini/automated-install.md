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
| Registry | Confirms the images are pullable; for the private repository it runs `docker login` and retries |
| Configuration | Detects the host address and asks you to confirm it, then derives the cert SAN and all three URLs from that one value |
| Secrets | Generates every secret (`openssl rand` equivalents), keeps `LOCAL_JWT_SECRET` separate from `SECRET_KEY`, and keeps the database password in sync |
| TLS | Writes a self-signed server certificate whose SAN matches the host (or installs one you supply) |
| Files | Writes `docker-compose.yml`, `config/nginx/mini.conf`, `config/vault/vault.hcl`, and `.env` (mode 600) |
| Start | `docker compose pull` then `up -d` |
| Vault | Initializes, unseals, and configures the PKI (`pki_int`, role `lxd`) with the CA outliving the leaf certificates |
| Admin | Enrolls the first administrator and closes the one-shot bootstrap window |
| Verify | Confirms every service is healthy, the HTTPS sign-in page is served, and the certificate SAN matches |

## Prerequisites

- Ubuntu 20.04+ or Debian 11+ (other distributions: follow the [Quick Start](quick-start.md)).
- Root, or a user in the `docker` group (installing Docker itself needs root/sudo).
- A Docker Hub account with access to the `svlct/orcastra-dashboard-mini` images.

## Run it

```bash
curl -fsSL https://docs.orcastra.io/installer/get.sh | bash
```

The bootstrap ensures `python3` is present, downloads the installer, verifies its checksum, and
runs it. To pass options, append them after `--`:

```bash
curl -fsSL https://docs.orcastra.io/installer/get.sh | bash -s -- --host dash.example.com
```

Interactive runs detect the host IP and ask you to confirm it. The address you confirm becomes
the certificate SAN and the `NEXTAUTH_URL` / `NEXT_PUBLIC_API_URL` / `CORS_ORIGINS` values, so
they cannot drift apart.

!!! note "Private images"
    The images are distributed through a private Docker Hub repository. When a pull is denied,
    the installer runs `docker login` and follows the prompt (including the web device-code
    flow), then retries. In `--non-interactive` mode, run `docker login` yourself first.

## The first administrator

Enrollment is trust-on-first-use: the first certificate to present the one-time bootstrap token
becomes admin, then the window closes. The installer offers two paths.

=== "Default - from your workstation"

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

=== "`--quick` - on the server"

    The installer generates the admin certificate in memory (`/dev/shm`), enrolls through nginx
    on `127.0.0.1`, hands you `admin.p12` with a one-time password, shreds the key material, and
    closes the window automatically. Convenient when the operator and the admin are the same
    person on one trusted host.

    ```bash
    curl -fsSL https://docs.orcastra.io/installer/get.sh | bash -s -- --quick
    ```

Import `admin.p12` into your browser, open the dashboard URL, and select the certificate when
prompted. Then issue partner and tenant identities from **Administration -> Identities**.

## Vault key handling

Vault is sealed on every start. How its unseal keys are handled is a deliberate choice:

=== "Guided (default)"

    The five unseal keys and the root token are shown once and **never written to disk**. Store
    them offline. After any reboot, unseal Vault before sign-in works:

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

Drive the installer from flags, an answer file, or environment - no prompts:

```bash
curl -fsSL https://docs.orcastra.io/installer/get.sh | bash -s -- \
  --non-interactive --assume-yes \
  --host 10.0.0.5 --https-port 6969 \
  --convenience --quick
```

An answer file is flat `KEY=value` (precedence: CLI flag, then answer file, then default):

```ini
HOST=dash.example.com
HOST_IS_IP=false
HTTPS_PORT=6969
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
| `--server-cert` / `--server-key` | Use your own TLS certificate instead of self-signed |
| `--tunnel` | Reach the host over an SSH tunnel (`https://localhost`) |
| `--dry-run` | Show every step and the files it would write, change nothing |
| `--stop-after write` | Generate the files only; start the stack yourself |
| `--skip-docker-install` | Never touch apt; fail if Docker is missing |

## Re-running and removal

The installer records its progress, so a second run skips completed steps and continues from the
first incomplete one. It never overwrites existing secrets (rotate explicitly with
`--rotate-secret NAME`), and it backs up any file before replacing it.

- `--repair` re-validates every step and fixes what is broken (cert SAN, sealed Vault, drift).
- `--from <phase>` / `--only <phase>` re-run a specific part.
- `--uninstall` stops the stack and removes the generated files; data volumes are kept unless you
  add `--purge-volumes` and confirm. The Vault keys file is never deleted automatically.

```bash
curl -fsSL https://docs.orcastra.io/installer/get.sh | bash -s -- \
  --uninstall --install-dir /opt/orcastra-mini
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Stops at preflight with a FAIL row | Read the remediation printed under the row; fix it and re-run |
| `Registry authentication required` | Run `docker login` with an account that can pull `svlct/orcastra-dashboard-mini` |
| Backend never becomes healthy | Check `VAULT_TOKEN` in `.env` and `docker compose -p orcastra-mini logs backend` |
| Browser rejects the certificate | The SAN must match the URL host (`IP:` for an address, `DNS:` for a name); re-run with the right `--host` |
| Sign-in fails after a reboot | Vault is sealed - unseal it (guided mode) |

The full transcript is written to `<install-dir>/install.log` with all secrets redacted.

!!! tip "Prefer the manual path to learn the internals"
    The [Quick Start](quick-start.md) walks through every file and command the installer runs,
    and the [Operations](operations.md) guide covers day-2 tasks. The installer is the fast path;
    the manual guide is the map.

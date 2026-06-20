# Quick Start

This guide deploys Orcastra Mini end to end on a single host: configuration, the stack,
Vault, the first administrator, and login. Commands assume the repository root and a host
reachable at `https://your-host.example.com:6969`.

## Prerequisites

- Docker with the Compose plugin.
- `openssl` (for the nginx server certificate and the first admin certificate).
- LXD/Incus/MicroCloud nodes with the API enabled, if you intend to manage real clusters.
- No Authentik and no OpenSearch are required.

## 1. Get the source

```bash
git clone -b orcastra-mini https://github.com/sctsivali/orcastra-dashboard.git
cd orcastra-dashboard
```

## 2. Configure the environment

```bash
cp .env.example .env
```

The template already defaults to the mini profile (`AUTH_MODE=client-cert`,
`HTTPS_PORT=6969`, `CONTAINER_PREFIX=orcastra-mini`, `AUDIT_DB_ENABLED=true`). Set the
required secrets:

```ini
POSTGRES_PASSWORD=<set a strong value>
SECRET_KEY=<openssl rand -hex 32>
LOCAL_JWT_SECRET=<openssl rand -hex 32>   # must differ from SECRET_KEY
AUTH_PROXY_SECRET=<openssl rand -hex 32>
BOOTSTRAP_ADMIN_TOKEN=<openssl rand -hex 32>   # one-time, blanked after first admin
VAULT_TOKEN=<set after Vault is initialised, step 5>

NEXTAUTH_URL=https://your-host.example.com:6969
NEXT_PUBLIC_API_URL=https://your-host.example.com:6969
CORS_ORIGINS=https://your-host.example.com:6969
```

See [Configuration](configuration.md) for the full reference.

!!! warning "Keep secrets out of version control"
    `.env` is gitignored. Never commit real secrets; the template ships placeholders only.

## 3. Provide the nginx TLS server certificate

nginx terminates TLS on the public port. A self-signed certificate is fine for a single host.

```bash
mkdir -p config/nginx/certs
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout config/nginx/certs/server.key \
  -out config/nginx/certs/server.crt \
  -subj "/CN=your-host.example.com" \
  -addext "subjectAltName=DNS:your-host.example.com,IP:<host-ip>"
```

## 4. Start the stack

```bash
make up-mini    # builds and starts postgres, redis, vault, backend, frontend, nginx
make ps-mini    # check status
```

??? tip "Run the published images instead of building"
    To pull prebuilt images rather than build locally, point the backend and frontend
    services at the published tags and run compose directly:

    ```yaml
    # docker-compose.mini.yml (replace each service's build: block)
    backend:
      image: svlct/orcastra-dashboard-mini:backend-1.0.0-RC1
    frontend:
      image: svlct/orcastra-dashboard-mini:frontend-1.0.0-RC1
    ```

    ```bash
    docker compose -f docker-compose.mini.yml up -d
    ```

## 5. Initialise and unseal Vault

Vault starts sealed. Initialise once, then unseal after every restart (manual, by design).

```bash
# Initialise (save the unseal keys and root token somewhere safe and offline)
docker compose -f docker-compose.mini.yml exec vault vault operator init

# Unseal (repeat with 3 different unseal keys)
docker compose -f docker-compose.mini.yml exec vault vault operator unseal <key-1>
docker compose -f docker-compose.mini.yml exec vault vault operator unseal <key-2>
docker compose -f docker-compose.mini.yml exec vault vault operator unseal <key-3>
```

Enable the PKI used to issue client certificates. The issuing CA must outlive the
certificates it signs, so give the CA a long TTL and the role a shorter maximum:

```bash
docker compose -f docker-compose.mini.yml exec vault sh -c '
  export VAULT_TOKEN=<root-token> ;
  vault secrets enable -path=secret -version=2 kv ;
  vault secrets enable -path=pki_int pki ;
  vault secrets tune -max-lease-ttl=87600h pki_int ;
  vault write pki_int/root/generate/internal common_name="Orcastra Mini CA" ttl=87600h ;
  vault write pki_int/roles/lxd allow_any_name=true max_ttl=8760h ;
'
```

Put a token that can use `pki_int` into `.env` as `VAULT_TOKEN` (the root token works to
start; a scoped token is better for production), then restart the backend:

```bash
docker compose -f docker-compose.mini.yml up -d backend
```

!!! danger "CA TTL must exceed leaf TTL"
    If the CA and the role share the same TTL, issuance fails with
    "notAfter would result in a value beyond the expiration of the CA". The 10-year CA and
    1-year role above avoid this.

## 6. Bootstrap the first administrator

Certificate enrollment is trust-on-first-use. Generate a client certificate, then exchange
the one-time bootstrap token for the admin role. The first certificate to bootstrap becomes
admin, after which the window closes.

```bash
# Self-signed client cert (the app trusts by fingerprint, not by CA)
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout admin.key -out admin.crt -subj "/CN=admin"
openssl pkcs12 -export -inkey admin.key -in admin.crt -out admin.p12   # set a password

# Enroll as admin (presents the cert to nginx, which forwards it to the backend)
curl -sk https://your-host.example.com:6969/api/v1/auth/cert-bootstrap \
  --cert admin.crt --key admin.key \
  -H 'Content-Type: application/json' \
  -d '{"bootstrap_token":"<BOOTSTRAP_ADMIN_TOKEN>"}'
```

After it succeeds, close the window: blank `BOOTSTRAP_ADMIN_TOKEN` in `.env` and restart the
backend.

```bash
docker compose -f docker-compose.mini.yml up -d backend
```

## 7. Sign in and issue the other identities

1. Import `admin.p12` into your browser or OS keychain.
2. Open `https://your-host.example.com:6969` and select the certificate when prompted.
3. As admin, open **Administration -> Identities -> Issue Identity**. Choose a username, a
   role (partner or tenant), and a validity period. A password-protected `.p12` downloads and
   the one-time import password is shown once. Deliver both to the user out of band.
4. The user imports the `.p12` and signs in the same way.

Revoke, re-activate, or change a role from the same screen. Changes take effect on the
identity's next request. See [Certificate Authentication](certificate-auth.md) for the full
lifecycle and [Operations](operations.md) for day-2 tasks.

## Verify the deployment

```bash
make ps-mini    # all services healthy
```

- Browsing to the host serves the sign-in page over HTTPS.
- After importing a certificate, the dashboard loads and the role gates the navigation
  (admins see Administration; partners and tenants see a restricted set).
- **Administration -> Audit Log -> Verify integrity** reports a valid chain.

# Troubleshooting

Common issues and solutions organized by symptom.

---

## Authentication Issues

### "Configuration Error" on Login Page

The frontend cannot reach Authentik for OIDC discovery.

**Diagnosis:**

```bash
# Test from inside the frontend container
docker exec orcastra-dashboard-frontend sh -c \
  "wget -qO- --timeout=5 http://<LXD_HOST_IP>:9000/ 2>&1 | head -3"
```

**Solutions:**

| Symptom | Cause | Fix |
|---|---|---|
| `wget` times out | iptables DNAT rules missing | Follow [VM 4 Step 8](../deployment/vm4-dashboard.md#step-8-fix-docker-to-authentik-connectivity) |
| `wget` works but still errors | Wrong OIDC config | Check `AUTHENTIK_ISSUER`, `AUTHENTIK_CLIENT_ID`, `AUTHENTIK_CLIENT_SECRET` in `.env` |
| Works after restart, breaks on reboot | iptables rules not persisted | Install `iptables-persistent` and run `netfilter-persistent save` |

After any `.env` change:

```bash
docker compose -f docker-compose.prod.yml restart frontend backend
```

---

### Login Redirects to Wrong URL

**Check:** Ensure `NEXTAUTH_URL` in `.env` matches the actual URL you use to access the dashboard:

- IP access: `NEXTAUTH_URL=http://<IP>:4321`
- Domain access: `NEXTAUTH_URL=https://app.orcastra.io`

**Check:** Ensure the Authentik Provider's **Redirect URI** includes your callback URL:
```
http://<IP>:4321/api/auth/callback/authentik
```
or for domain:
```
https://app.orcastra.io/api/auth/callback/authentik
```

---

## API & Frontend Issues

### CORS Errors in Browser Console

The browser is hitting an incorrect API URL.

1. Open browser DevTools → Console → check the URL in the error
2. If it shows a wrong IP/port → fix `NEXT_PUBLIC_API_URL` in `.env`
3. Restart:
   ```bash
   docker compose -f docker-compose.prod.yml up -d frontend
   ```
   The `entrypoint.sh` re-injects the correct URL from `.env` on every start.

---

### "Connection Issue - Unable to Reach the Server" Banner

The frontend can reach Authentik (login works) but cannot reach the backend API.

1. Verify `NEXT_PUBLIC_API_URL` in `.env` points to `http://<LXD_HOST_IP>:8765`
2. Check backend health:
   ```bash
   curl -s http://localhost:8765/health
   ```
3. Restart frontend:
   ```bash
   docker compose -f docker-compose.prod.yml up -d frontend
   ```

---

## Database Issues

### Tables Not Created

The backend auto-creates tables on startup via SQLAlchemy `create_all`.

1. Check backend logs:
   ```bash
   docker logs orcastra-dashboard-backend --tail 30
   ```
2. Verify `DATABASE_URL` in `.env` matches `POSTGRES_USER` and `POSTGRES_PASSWORD`
3. Restart:
   ```bash
   docker compose -f docker-compose.prod.yml restart backend
   ```

!!! tip "Password Encoding"
    If `POSTGRES_PASSWORD` contains special characters (`+`, `/`, `=`), the `DATABASE_URL` connection string will fail. Use `openssl rand -hex 16` (not `-base64`) to avoid this issue.

---

## Docker Issues

### Containers Fail to Start on LXD

```
Error response from daemon: failed to mount...
```

This happens when the Docker storage driver is incompatible with LXD.

**Fix:**

```bash
apt-get update && apt-get install fuse-overlayfs -y

cat > /etc/docker/daemon.json <<EOF
{
  "storage-driver": "fuse-overlayfs"
}
EOF

systemctl restart docker
```

For VM 4 specifically, `vfs` is used instead:

```json
{
  "storage-driver": "vfs"
}
```

---

### DNS Resolution Failures Inside Containers

```
curl: (6) Could not resolve host: download.docker.com
```

LXD containers may have intermittent DNS issues.

**Fix:** Re-run the failed command. If persistent, configure a static DNS resolver:

```bash
echo "nameserver 8.8.8.8" >> /etc/resolv.conf
```

---

## OpenSearch Issues

### Cluster Shows Yellow/Red Status

```bash
curl -sk https://localhost:9200/_cluster/health?pretty \
  -u admin:<ADMIN_PASSWORD>
```

- **Yellow:** Single-node cluster with replicas configured - expected for single-node deployments.
- **Red:** Shard allocation failures - check disk space and container health.

### Fluent Bit Cannot Write to OpenSearch

**Quick check (recommended) — run the bundled healthcheck:**

```bash
# On VM 4 (Fluent Bit side)
./scripts/logging_healthcheck.sh fluentbit

# On VM 3 (OpenSearch side)
OPENSEARCH_ADMIN_PASSWORD=... ./scripts/logging_healthcheck.sh opensearch
```

Exit codes: `0` healthy, `1` warning, `2` critical. The script reports auth
status, today's index presence, recent doc count, blocked indices, and Fluent
Bit retry counters.

**Manual diagnosis:**

1. Check Fluent Bit logs:
   ```bash
   docker logs orcastra-dashboard-fluent-bit --tail 30
   ```
2. Verify `OPENSEARCH_HOST` is the **IP address only** (no `http://` prefix)
3. Verify `OPENSEARCH_PASSWORD` matches the `fluentbit` user password set on VM 3
4. Check OpenSearch is accepting connections:
   ```bash
   curl -sk https://<VM3_IP>:9200 -u fluentbit:<PASSWORD>
   ```

#### Symptom: `Authentication finally failed for fluentbit from <VM4_IP>` in OpenSearch logs

This is a **password drift** between VMs — the password Fluent Bit sends no
longer matches the `fluentbit` user in OpenSearch. Logs are silently dropped
once `Retry_Limit` is reached, so dashboards stop updating without an error.

**Recovery (run on VM 3):**

```bash
# Get the password Fluent Bit is currently sending (run on VM 4 first)
# docker exec orcastra-dashboard-fluent-bit env | grep OPENSEARCH_PASSWORD

# Reset the OpenSearch user to match
curl -sk -u admin:$OPENSEARCH_ADMIN_PASSWORD \
  -X PUT "https://localhost:9200/_plugins/_security/api/internalusers/fluentbit" \
  -H "Content-Type: application/json" \
  -d '{
    "password": "<PWD_FROM_VM4>",
    "backend_roles": ["log_writer"],
    "description": "Fluent Bit service account for log ingestion"
  }'

# Verify
curl -sk -u "fluentbit:<PWD_FROM_VM4>" https://localhost:9200/_cluster/health?pretty
```

See the full runbook in `docs/LOGGING_RUNBOOK.md` (in the `orcastra-dashboard`
repository) for additional scenarios (disk watermark, mapping conflicts, ISM
retry).

---

## Vault Issues

### Vault is Sealed After Reboot

Vault seals itself on every restart and requires unsealing with 3 of the 5 unseal keys.

```bash
export VAULT_ADDR='http://127.0.0.1:8200'
vault operator unseal  # Paste Key 1
vault operator unseal  # Paste Key 2
vault operator unseal  # Paste Key 3
vault status           # Verify: Sealed = false
```

!!! danger "Unseal Keys"
    Store unseal keys securely and separately. If 3+ keys are lost, Vault data is **permanently inaccessible**.

---

### Node Registration Returns HTTP 503

The Register Node page shows **"Registration Failed — HTTP 503"**. This means the backend cannot communicate with Vault.

**Diagnosis — run on VM 4 (Dashboard):**

```bash
# Step 1: Check Vault health from INSIDE the backend container
docker exec orcastra-dashboard-backend python3 -c "
import requests, os
vault_addr = os.environ.get('VAULT_ADDR', 'NOT SET')
print(f'VAULT_ADDR={vault_addr}')
try:
    resp = requests.get(f'{vault_addr}/v1/sys/health', timeout=5)
    print(f'Health: {resp.status_code} - {resp.json()}')
except Exception as e:
    print(f'UNREACHABLE: {e}')
"

# Step 2: Verify the Vault token is still valid
docker exec orcastra-dashboard-backend python3 -c "
import requests, os
vault_addr = os.environ.get('VAULT_ADDR')
vault_token = os.environ.get('VAULT_TOKEN')
resp = requests.get(f'{vault_addr}/v1/auth/token/lookup-self',
    headers={'X-Vault-Token': vault_token}, timeout=5)
print(f'Status: {resp.status_code}')
if resp.ok:
    data = resp.json()['data']
    print(f'Expire: {data.get(\"expire_time\", \"never\")}')
    print(f'Policies: {data.get(\"policies\")}')
else:
    print(f'TOKEN INVALID: {resp.json()}')
"
```

**Common causes and fixes:**

| Step 1 Result | Step 2 Result | Cause | Fix |
|---|---|---|---|
| `UNREACHABLE: Connection refused` | — | Backend can't reach Vault network | Check `VAULT_ADDR` in `.env`, verify LXD port forwarding (see [Networking](networking.md)) |
| Health `200` | `403 permission denied` | **Vault token expired or revoked** | Generate a new token (see below) |
| Health `503` | — | Vault is sealed | Unseal Vault (see above) |
| `VAULT_ADDR=NOT SET` | — | Missing env var | Check `.env` has `VAULT_ADDR` and `VAULT_ENABLED=true` |

**Fix: Generate a new Vault token**

On **VM 2 (Vault)**:

```bash
export VAULT_ADDR='http://127.0.0.1:8200'
vault login   # Enter root token when prompted

# Create a long-lived token with the dashboard policy
vault token create \
  -orphan \
  -display-name="orcastra-dashboard" \
  -policy="orcastra-dashboard" \
  -ttl=0
```

Copy the new `token` value (starts with `hvs.`). On **VM 4 (Dashboard)**:

```bash
# Update .env with the new token
sed -i 's|VAULT_TOKEN=.*|VAULT_TOKEN=<NEW_TOKEN>|' ~/.env

# Restart backend to pick up the new token
docker compose -f docker-compose.prod.yml restart backend
```

!!! tip "Prevent Token Expiry"
    Use `-ttl=0` when creating the token to prevent it from expiring.
    Non-root tokens have a default TTL (usually 768h / 32 days) and **will expire silently**.
    Always verify token validity after Vault restarts or maintenance.

!!! info "Vault Token Lifecycle"
    Tokens can become invalid for several reasons:

    - **Expired** — TTL elapsed (most common, check `expire_time` from lookup)
    - **Revoked** — Admin manually revoked, or parent token was revoked
    - **Vault restart** — Non-persistent tokens are lost on restart (in-memory storage only)
    - **Sealed** — Vault sealed = all tokens temporarily unusable until unsealed

---

### Vault Connectivity from Docker Containers

Docker containers on VM 4 run inside a Docker bridge network, which is itself inside an LXD container. This double-NAT means containers may not be able to reach other LXD containers by their private IP.

**Architecture:**
```
LXD Host (142.x.x.x)
└── lxdbr0 bridge (10.1.1.0/24)
    ├── prod-vault (10.1.1.39)         ← Vault HTTP :8200
    └── prod-orcastra-dashboard (10.1.1.X)
        └── Docker bridge (172.17.0.0/16)
            ├── backend container      ← needs to reach 10.1.1.39
            ├── frontend, postgres, redis
```

**Test connectivity:**
```bash
# From LXD container (should work)
curl -s http://10.1.1.39:8200/v1/sys/health

# From Docker container (may fail if routing is broken)
docker exec orcastra-dashboard-backend python3 -c "
import requests
resp = requests.get('http://10.1.1.39:8200/v1/sys/health', timeout=5)
print(resp.status_code, resp.json())
"
```

If the first works but the second doesn't, Docker containers can't route to the LXD bridge. Fix with iptables DNAT (see [Networking — Docker Network Routing](networking.md#docker-network-routing-vm-4)).

---

## Monitoring & WebSocket Issues

### Monitoring Dashboard Shows 401 (Unauthorized)

The Monitoring page loads but metric cards show 0 and the console shows `GET /api/v1/monitoring/dashboard 401`.

**Cause:** The JWT access token has expired and automatic refresh failed (Authentik may have been temporarily unreachable).

**Quick fix:** Logout and login again to get a fresh token:

1. Click your user avatar → **Sign Out**
2. Login again via Authentik
3. If sign-out doesn't work, clear cookies for `app.orcastra.io` and reload

**If 401 persists after re-login:**

```bash
# Check if backend can reach Authentik JWKS endpoint
docker exec orcastra-dashboard-backend python3 -c "
import os, requests
issuer = os.environ.get('AUTHENTIK_ISSUER', '')
print(f'AUTHENTIK_ISSUER={issuer}')
resp = requests.get(f'{issuer}.well-known/openid-configuration', timeout=5)
print(f'OIDC Discovery: {resp.status_code}')
jwks_uri = resp.json().get('jwks_uri')
resp2 = requests.get(jwks_uri, timeout=5)
print(f'JWKS: {resp2.status_code}, keys={len(resp2.json().get(\"keys\", []))}')
"

# Check if frontend can reach Authentik for token refresh
docker exec orcastra-dashboard-frontend sh -c \
  'wget -qO- --timeout=5 https://sso.orcastra.io/ 2>&1 | head -3'
```

| Symptom | Cause | Fix |
|---|---|---|
| OIDC Discovery fails | Backend can't reach Authentik | Check DNS / iptables DNAT rules for Authentik |
| JWKS returns 0 keys | Authentik provider misconfigured | Verify the OIDC Provider in Authentik admin has signing keys assigned |
| Frontend can't reach Authentik | Token refresh fails silently | Fix iptables DNAT (see [VM 4 Step 8](../deployment/vm4-dashboard.md#step-8-fix-docker-to-authentik-connectivity)) |
| Everything reachable, still 401 | `AUTHENTIK_AUDIENCE` mismatch | Ensure `AUTHENTIK_CLIENT_ID` in `.env` matches the Authentik Provider's Client ID |

---

### WebSocket Connection Failures (Monitoring Real-Time)

Console shows repeated `WebSocket connection to 'wss://api.orcastra.io/api/v1/monitoring/ws' failed`.

**Possible causes:**

=== "Reverse Proxy Not Forwarding WebSocket"

    If you use Nginx, Caddy, or Cloudflare Tunnel in front of the backend, ensure WebSocket upgrade is enabled:

    **Nginx:**
    ```nginx
    location /api/v1/monitoring/ws {
        proxy_pass http://backend:4050;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }
    ```

    **Caddy:**
    ```caddyfile
    # Caddy automatically handles WebSocket upgrade - no extra config needed
    reverse_proxy backend:4050
    ```

    **Cloudflare Tunnel:**
    Ensure **WebSockets** is enabled in the Cloudflare dashboard under **Network** settings for your zone.

=== "Token Not Being Passed"

    The monitoring WebSocket requires authentication via a `?token=` query parameter (browsers cannot set headers on WebSocket connections).

    If using an older frontend image that doesn't pass the token, rebuild and redeploy:
    ```bash
    # On dev VM
    docker compose build frontend
    docker tag <image> svlct/orcastra-dashboard:frontend-latest
    docker push svlct/orcastra-dashboard:frontend-latest

    # On production VM
    docker compose -f docker-compose.prod.yml pull frontend
    docker compose -f docker-compose.prod.yml up -d frontend
    ```

=== "Backend Auth Middleware Blocking WebSocket"

    Older backend versions used HTTP-based auth (HTTPBearer) at the router level, which breaks WebSocket handshakes. The fix separates WebSocket endpoints into a dedicated router with query-parameter auth.

    If you see this in backend logs:
    ```
    TypeError: HTTPBearer.__call__() missing 1 required positional argument: 'request'
    ```
    Update the backend image:
    ```bash
    docker compose -f docker-compose.prod.yml pull backend
    docker compose -f docker-compose.prod.yml up -d backend
    ```

---

## Service Status Summary Command

Run this single command on VM 4 to check all critical integrations at once:

```bash
docker exec orcastra-dashboard-backend python3 -c "
import os, requests

# Vault
vault_addr = os.environ.get('VAULT_ADDR', 'NOT SET')
vault_token = os.environ.get('VAULT_TOKEN', '')
print('=== VAULT ===')
try:
    r = requests.get(f'{vault_addr}/v1/sys/health', timeout=3)
    sealed = r.json().get('sealed', 'unknown')
    print(f'  Health: {r.status_code} (sealed={sealed})')
    r2 = requests.get(f'{vault_addr}/v1/auth/token/lookup-self',
        headers={'X-Vault-Token': vault_token}, timeout=3)
    if r2.ok:
        exp = r2.json()['data'].get('expire_time', 'never')
        print(f'  Token: VALID (expires: {exp})')
    else:
        print(f'  Token: INVALID ({r2.status_code})')
except Exception as e:
    print(f'  UNREACHABLE: {e}')

# Authentik
issuer = os.environ.get('AUTHENTIK_ISSUER', '')
print('\n=== AUTHENTIK ===')
try:
    r = requests.get(f'{issuer}.well-known/openid-configuration', timeout=5)
    print(f'  OIDC Discovery: {r.status_code}')
except Exception as e:
    print(f'  UNREACHABLE: {e}')

# PostgreSQL (via backend health)
print('\n=== BACKEND ===')
try:
    r = requests.get('http://localhost:4050/health', timeout=3)
    print(f'  Health: {r.status_code} - {r.json()}')
except Exception as e:
    print(f'  UNREACHABLE: {e}')

# Redis
redis_url = os.environ.get('REDIS_URL', '')
print(f'\n=== REDIS ===')
print(f'  URL: {redis_url}')
try:
    import redis as r
    client = r.from_url(redis_url, socket_timeout=3)
    client.ping()
    print(f'  Status: CONNECTED')
except Exception as e:
    print(f'  Status: {e}')
"
```

---

## Quick Diagnostic Commands

```bash
# Check all container status
docker compose -f docker-compose.prod.yml ps

# View logs for a specific service
docker compose -f docker-compose.prod.yml logs <service> --tail 50

# Restart a specific service
docker compose -f docker-compose.prod.yml restart <service>

# Full restart (all services)
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml up -d

# Check disk space (common cause of failures)
df -h

# Check memory usage
free -h

# Check Docker disk usage
docker system df
```

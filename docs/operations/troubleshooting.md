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

### "Connection Issue — Unable to Reach the Server" Banner

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

- **Yellow:** Single-node cluster with replicas configured — expected for single-node deployments.
- **Red:** Shard allocation failures — check disk space and container health.

### Fluent Bit Cannot Write to OpenSearch

1. Check Fluent Bit logs:
   ```bash
   docker logs orcastra-dashboard-fluent-bit --tail 30
   ```
2. Verify `OPENSEARCH_HOST` is the **IP address only** (no `http://` prefix)
3. Verify `OPENSEARCH_PASSWORD` matches the `fluentbit` user password set in VM 3
4. Check OpenSearch is accepting connections:
   ```bash
   curl -sk https://<VM3_IP>:9200 -u fluentbit:<PASSWORD>
   ```

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

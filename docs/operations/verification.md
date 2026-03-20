# Verification & Testing

After deploying all four VMs, verify the complete system end-to-end.

---

## Pre-Flight Checklist

Confirm each VM's services are running:

=== "VM 1 — Authentik"

    ```bash
    # On VM 1 (or via LXD host IP)
    curl -s http://<VM1_IP>:9000/if/flow/initial-setup/ | head -5
    ```
    Should return HTML (the Authentik UI).

=== "VM 2 — Vault"

    ```bash
    export VAULT_ADDR='http://127.0.0.1:8200'
    vault status
    ```
    Verify: `Sealed = false`, `Initialized = true`.

=== "VM 3 — OpenSearch"

    ```bash
    curl -sk https://localhost:9200 \
      -u admin:<ADMIN_PASSWORD>
    ```
    Should return JSON with `"cluster_name"`, `"status"`, etc.

    ```bash
    curl -sk https://localhost:5601/api/status \
      -u admin:<ADMIN_PASSWORD>
    ```
    Should return OpenSearch Dashboards status.

=== "VM 4 — Dashboard"

    ```bash
    curl -s http://localhost:8765/health
    ```
    Should return a 200 OK response.

    ```bash
    docker compose -f docker-compose.prod.yml ps
    ```
    All containers should show `healthy` or `running`.

---

## End-to-End Login Flow

This is the primary verification — it tests Authentik, the frontend, and the backend together.

1. Open browser: `https://app.orcastra.io` (or `http://<VM4_IP_OR_LXD_HOST_IP>:4321`)
2. You should see the Orcastra login page
3. Click **Sign in** → you'll be redirected to Authentik
4. Login with your `akadmin` account (or any Authentik user)
5. After login, you should see the Orcastra Dashboard homepage

!!! success "If all steps pass"
    The system is fully operational. Authentication, API calls, and the frontend are all working correctly.

---

## Service Health Checks

### Backend API

```bash
curl -s http://<VM4_IP>:8765/health | python3 -m json.tool
```

### Container Status

```bash
docker compose -f docker-compose.prod.yml ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
```

Expected output — all containers should be `Up` and `healthy`:

```
NAME                                STATUS                  PORTS
orcastra-dashboard-backend          Up (healthy)            0.0.0.0:8765->4050/tcp
orcastra-dashboard-fluent-bit       Up (healthy)            
orcastra-dashboard-frontend         Up (healthy)            0.0.0.0:4321->2025/tcp
orcastra-dashboard-postgres         Up (healthy)            0.0.0.0:5432->5432/tcp
orcastra-dashboard-redis            Up (healthy)            0.0.0.0:6381->6379/tcp
```

### Container Logs

```bash
# Backend logs (look for "Application startup complete")
docker logs orcastra-dashboard-backend --tail 20

# Frontend logs (look for "Ready on http://0.0.0.0:2025")
docker logs orcastra-dashboard-frontend --tail 20

# Fluent Bit logs (look for healthy output pipeline)
docker logs orcastra-dashboard-fluent-bit --tail 20
```

---

## Logging Pipeline Verification

### Check Fluent Bit is Forwarding

```bash
# Fluent Bit health
curl -s http://localhost:2020/api/v1/health

# Fluent Bit metrics
curl -s http://localhost:2020/api/v1/metrics | head -20
```

### Check OpenSearch Indices

```bash
curl -sk https://<VM3_IP>:9200/_cat/indices?v \
  -u admin:<ADMIN_PASSWORD> \
  | grep orcastra
```

You should see indices like:

```
green open orcastra-access-2025.01.30   ...
green open orcastra-audit-2025.01.30    ...
green open orcastra-app-2025.01.30      ...
```

---

## Vault Connectivity

```bash
# From VM 4 backend container
docker exec orcastra-dashboard-backend python -c "
import urllib.request
req = urllib.request.urlopen('http://<VM2_IP>:8200/v1/sys/health')
print(req.read().decode()[:100])
"
```

Should return JSON with `"initialized": true, "sealed": false`.

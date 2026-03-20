# Domain Setup - Cloudflare Tunnel

!!! info "Optional"
    This step is optional. You can access the dashboard directly via IP:port. This guide enables custom domain access (e.g., `app.orcastra.io`) with automatic HTTPS via Cloudflare Tunnel.

## Architecture

```
Browser → https://app.orcastra.io  → Cloudflare Edge → Tunnel → VM4 → localhost:4321
Browser → https://api.orcastra.io  → Cloudflare Edge → Tunnel → VM4 → localhost:8765
Browser → https://sso.orcastra.io  → Cloudflare Edge → Tunnel → VM4 → VM1:9000
Browser → https://logs.orcastra.io → Cloudflare Edge → Tunnel → VM4 → VM3:5601
```

Cloudflare Tunnel creates a secure outbound connection from your server to Cloudflare's edge - no open inbound ports needed. All traffic gets HTTPS automatically.

## Prerequisites

- Domain (e.g., `orcastra.io`) with DNS managed by Cloudflare
- Cloudflare Zero Trust account (free tier is sufficient)

---

## Step 1: Create Tunnel in Cloudflare Dashboard

1. Go to [Cloudflare Zero Trust](https://one.dash.cloudflare.com) → **Networks** → **Tunnels**
2. Click **Create a tunnel** → select **Cloudflared** → name it (e.g., `orcastra-production`)
3. Copy the **install token** - you'll need it in the next step

---

## Step 2: Install cloudflared on VM 4

Run these on VM 4 (the Dashboard VM):

```bash
# Add Cloudflare GPG key
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-public-v2.gpg \
  | sudo tee /usr/share/keyrings/cloudflare-public-v2.gpg >/dev/null

# Add repository
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-public-v2.gpg] https://pkg.cloudflare.com/cloudflared any main' \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list

# Install
sudo apt-get update && sudo apt-get install cloudflared
```

---

## Step 3: Configure Public Hostnames

In the Cloudflare Dashboard, go to your tunnel → **Public Hostname** tab. Add these routes:

| Subdomain | Domain | Service | Notes |
|-----------|--------|---------|-------|
| `app` | `orcastra.io` | `http://localhost:4321` | Frontend (VM 4) |
| `api` | `orcastra.io` | `http://localhost:8765` | Backend API (VM 4) |
| `sso` | `orcastra.io` | `http://<VM1_PRIVATE_IP>:9000` | Authentik (VM 1) |
| `logs` | `orcastra.io` | `https://<VM3_PRIVATE_IP>:5601` | OpenSearch Dashboards (VM 3) |

!!! tip "Important Settings"
    - **`api.orcastra.io`:** Click "Additional application settings" → enable **WebSockets**
    - **`logs.orcastra.io`:** Set Type = HTTPS, and enable **No TLS Verify** (OpenSearch uses self-signed certificates)
    - If a subdomain already has a DNS record, **delete it first** in DNS settings

---

## Step 4: Update Authentik Redirect URI

In the Authentik admin panel (`https://sso.orcastra.io/if/admin/`):

1. Go to **Applications** → **Providers** → **Orcastra Dashboard Provider**
2. Edit **Redirect URIs/Origins** - add:
   ```
   https://app.orcastra.io/api/auth/callback/authentik
   ```
3. Keep the old `http://<IP>:4321/...` redirect URI as fallback
4. Click **Update**

---

## Step 5: Update Environment File on VM 4

Edit `.env` and update these values:

```ini
# === Frontend ===
NEXT_PUBLIC_API_URL=https://api.orcastra.io

# === Authentik ===
AUTHENTIK_ISSUER=https://sso.orcastra.io/application/o/orcastra-dashboard/
NEXT_PUBLIC_AUTHENTIK_LOGOUT_URL=https://sso.orcastra.io/application/o/orcastra-dashboard/end-session/
AUTHENTIK_API_URL=https://sso.orcastra.io

# === NextAuth ===
NEXTAUTH_URL=https://app.orcastra.io

# === CORS ===
CORS_ORIGINS=https://app.orcastra.io
```

Leave all other values (VAULT_ADDR, DATABASE_URL, etc.) unchanged - they use internal IPs.

---

## Step 6: Restart Services

```bash
docker compose -f docker-compose.prod.yml up -d frontend backend
```

The `entrypoint.sh` script automatically replaces `NEXT_PUBLIC_*` URLs on every container start.

---

## Step 7: Verify Domain Access

1. Open `https://app.orcastra.io` → should show the dashboard
2. Click **Sign in** → should redirect to `https://sso.orcastra.io/...`
3. After login → should redirect back to `https://app.orcastra.io`
4. Open browser DevTools → Console → **no CORS errors**
5. API calls should go to `https://api.orcastra.io/api/v1/...`
6. Open `https://logs.orcastra.io` → should show OpenSearch Dashboards

!!! tip "Closing Raw IP Access"
    After connecting the domain, you can optionally close raw IP:port access by removing the LXD port forwards for ports `4321` and `8765`. Test domain-only access first before removing port forwards.

!!! note "iptables Rules After Domain Setup"
    With the domain setup, the frontend container resolves `sso.orcastra.io` via DNS → Cloudflare → tunnel → VM1:9000. The iptables rules from [VM 4 Step 8](../deployment/vm4-dashboard.md#step-8-fix-docker-to-authentik-connectivity) may no longer be needed. Test by removing the rules and restarting - if login still works, they're not needed.

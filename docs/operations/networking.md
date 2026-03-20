# Networking

## LXD Port Forwarding

All VMs run inside LXD containers with private IP addresses. To make services accessible from outside the LXD host, configure port forwarding.

### Setup Procedure

1. In the LXD UI, go to **Networking** → **Networks** → select your network (e.g., `lxdbr0`)
2. Click **Forwards** → **Create Forward**
3. **Listen Address:** the LXD host's public/reachable IP
4. Click **Add Port** and forward each VM's ports as listed below
5. Click **Create**

### Port Forwarding Table

| VM | Service | Listen Port | Target IP | Target Port |
|---|---|---|---|---|
| VM 1 | Authentik | `9000` | `<VM1_PRIVATE_IP>` | `9000` |
| VM 2 | Vault | `8200` | `<VM2_PRIVATE_IP>` | `8200` |
| VM 3 | OpenSearch API | `9200` | `<VM3_PRIVATE_IP>` | `9200` |
| VM 3 | OpenSearch Dashboards | `5601` | `<VM3_PRIVATE_IP>` | `5601` |
| VM 4 | Frontend | `4321` | `<VM4_PRIVATE_IP>` | `4321` |
| VM 4 | Backend API | `8765` | `<VM4_PRIVATE_IP>` | `8765` |

---

## LXD Instance Security Policy

Each VM instance requires the following LXD security settings:

1. Go to the instance → **Security Policies**
2. Set **Privileged (Containers only)** → `Allow`
3. Set **Nesting (Containers only)** → `Allow`
4. Save changes

!!! warning
    These settings must be configured **before** installing Docker. Docker requires `nesting` to run containers inside LXD containers.

---

## Docker Network Routing (VM 4)

Docker containers on VM 4 cannot directly reach LXD port-forwarded IPs. This is because Docker's bridge network doesn't know how to route to the LXD host IP. See [VM 4 — Step 8](../deployment/vm4-dashboard.md#step-8-fix-docker-to-authentik-connectivity) for the iptables DNAT rules required.

### Summary of Required Routes

```
Docker containers (VM4) → LXD Host IP:9000 → VM1 Private IP:9000  (Authentik)
Docker containers (VM4) → LXD Host IP:8200 → VM2 Private IP:8200  (Vault)
```

---

## Firewall Considerations

### Required Ports (Inbound)

| Port | Protocol | Service | Source |
|---|---|---|---|
| `9000` | TCP | Authentik | LXD Host / Browsers |
| `8200` | TCP | Vault | LXD Host / VM 4 |
| `9200` | TCP | OpenSearch API | VM 4 (Fluent Bit) |
| `5601` | TCP | OpenSearch Dashboards | LXD Host / Browsers |
| `4321` | TCP | Frontend | LXD Host / Browsers |
| `8765` | TCP | Backend API | LXD Host / Browsers |

### Internal-Only Ports

These ports are **not** forwarded externally — they are used only within their respective Docker networks:

| Port | Service | VM |
|---|---|---|
| `5432` | PostgreSQL | VM 4 |
| `6379` | Redis | VM 4 |
| `24224` | Fluent Bit Forward | VM 4 |
| `2020` | Fluent Bit Health | VM 4 |
